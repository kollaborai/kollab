"""A voice talk-over leaves the part of the reply that was played in the history."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.local import LocalStateService
from kollabor_agent.queue_processor import QueueProcessor


class FakeManager:
    def __init__(self) -> None:
        self.added = []

    def add_message(self, *, role, content, parent_uuid=None, metadata=None):
        self.added.append((role, content, parent_uuid, metadata))
        return f"manager-{len(self.added)}"


class FakeLogger:
    def __init__(self, fail: bool = False) -> None:
        self.fail, self.logged = fail, []

    async def log_assistant_message(self, content, parent_uuid, **kwargs):
        if self.fail:
            raise OSError("disk full")
        self.logged.append((content, parent_uuid, kwargs))
        return f"log-{len(self.logged)}"


class FakeLLM:
    """The slice of LLMService the daemon touches, with its real history adder."""

    _add_conversation_message = LLMService._add_conversation_message

    def __init__(self, logger: FakeLogger | None = None) -> None:
        self.conversation_history = []
        self.current_parent_uuid = "user-uuid"
        self.conversation_manager = FakeManager()
        self.conversation_logger = logger or FakeLogger()
        self.api_service = SimpleNamespace(model="m1")


pytestmark = pytest.mark.asyncio


def service(llm) -> LocalStateService:
    return LocalStateService(llm, profile_manager=object(), event_bus=None)


async def test_a_talk_over_keeps_the_played_text_once_per_turn():
    llm = FakeLLM()
    state = service(llm)

    assert await state.record_interrupted_voice_reply("turn-1", "  So the build  ") == {
        "recorded": True
    }
    [message] = llm.conversation_history
    assert (message.role, message.content) == ("assistant", "So the build")
    assert message.metadata == {"interrupted": True, "source": "voice", "turn_id": "turn-1"}
    assert llm.conversation_manager.added[0][3] == message.metadata
    # logged for /resume and the engine's disk-seeded history, under the user's turn
    assert llm.conversation_logger.logged == [("So the build", "user-uuid", {"model": "m1"})]
    assert llm.current_parent_uuid == "log-1"  # the next message threads under it

    assert await state.record_interrupted_voice_reply("turn-1", "So the build") == {
        "recorded": False,
        "duplicate": True,
    }
    assert len(llm.conversation_history) == 1  # a repeat post adds nothing
    assert await state.record_interrupted_voice_reply("turn-2", "Okay then") == {"recorded": True}
    assert [m.metadata["turn_id"] for m in llm.conversation_history] == ["turn-1", "turn-2"]


async def test_nothing_played_or_no_turn_adds_nothing():
    llm = FakeLLM()
    state = service(llm)
    for turn_id, text in (("turn-1", ""), ("turn-1", " \n "), ("turn-1", None), ("", "words")):
        assert await state.record_interrupted_voice_reply(turn_id, text) == {"recorded": False}
    assert llm.conversation_history == [] and llm.conversation_logger.logged == []
    assert await service(None).record_interrupted_voice_reply("turn-1", "words") == {
        "recorded": False
    }


async def test_a_log_failure_still_keeps_the_message_in_the_history():
    llm = FakeLLM(FakeLogger(fail=True))
    assert await service(llm).record_interrupted_voice_reply("turn-1", "So the") == {
        "recorded": True
    }
    assert [m.content for m in llm.conversation_history] == ["So the"]
    assert llm.current_parent_uuid == "user-uuid"  # not lost to the failed log


def voice_reply(text, turn_id="turn-1"):
    from kollabor_events.data_models import ConversationMessage

    return ConversationMessage(
        role="assistant", content=text, metadata={"voice_turn_id": turn_id}
    )


async def test_a_talk_over_after_the_reply_finished_cuts_it_in_place():
    # a fast model finishes long before the voice does: the reply is in history
    llm = FakeLLM()
    reply = voice_reply("So the build is green. Ship it.")
    llm.conversation_history.append(reply)
    state = service(llm)

    assert await state.record_interrupted_voice_reply("turn-1", "So the build is") == {
        "recorded": True,
        "replaced": True,
    }
    assert llm.conversation_history == [reply]  # one copy, same message
    assert reply.content == "So the build is"
    assert reply.metadata["interrupted"] and reply.metadata["turn_id"] == "turn-1"
    assert await state.record_interrupted_voice_reply("turn-1", "So the build is") == {
        "recorded": False,
        "duplicate": True,
    }


CALL = {"tool_calls": [{"id": "t1", "type": "function", "function": {"name": "weather"}}]}


def tool_turn():
    """A finished reply that used a tool: its prose, the call, the result, the answer."""
    from kollabor_events.data_models import ConversationMessage

    llm = FakeLLM()
    first = voice_reply("Let me check the weather.")
    first.metadata.update(CALL)
    result = ConversationMessage(role="tool", content="20C sunny", metadata={"tool_call_id": "t1"})
    last = voice_reply("**It's 20** and sunny.")
    llm.conversation_history.extend([first, result, last])
    return llm, first, result, last


async def test_a_talk_over_cuts_the_reply_it_stopped_in_and_drops_the_prose_after_it():
    llm, first, result, last = tool_turn()
    state = service(llm)
    assert await state.record_interrupted_voice_reply("turn-1", "Let me check") == {
        "recorded": True,
        "replaced": True,
    }
    # never heard: the answer goes; the call and its result stay, the tool ran
    assert llm.conversation_history == [first, result]
    assert first.content == "Let me check" and first.metadata["interrupted"]
    assert first.metadata["tool_calls"] == CALL["tool_calls"]


async def test_a_talk_over_in_the_answer_keeps_the_prose_before_it_whole():
    llm, first, result, last = tool_turn()
    played = "Let me check the weather. It's 20"
    assert (await service(llm).record_interrupted_voice_reply("turn-1", played))["replaced"]
    assert llm.conversation_history == [first, result, last]
    assert (first.content, last.content) == ("Let me check the weather.", "It's 20")
    assert "interrupted" not in first.metadata and last.metadata["turn_id"] == "turn-1"


async def test_a_common_last_word_never_starts_the_next_reply():
    llm = FakeLLM()
    first, last = voice_reply("Checking the build now."), voice_reply("The build is green.")
    first.metadata.update(CALL)
    llm.conversation_history.extend([first, last])
    played = "Checking the build now. The"
    assert (await service(llm).record_interrupted_voice_reply("turn-1", played))["replaced"]
    assert llm.conversation_history == [first]  # not a reply reading "The"
    assert first.content == played


async def test_a_talk_over_before_any_audio_takes_the_unheard_reply_away():
    llm, first, result, last = tool_turn()
    assert await service(llm).record_interrupted_voice_reply("turn-1", "") == {
        "recorded": True,
        "replaced": True,
    }
    assert llm.conversation_history == [first, result] and first.content == ""
    plain = FakeLLM()
    plain.conversation_history.append(voice_reply("All done."))
    assert (await service(plain).record_interrupted_voice_reply("turn-1", " "))["recorded"]
    assert plain.conversation_history == []


async def test_a_repeated_phrase_or_a_short_reply_never_starts_inside_the_one_before():
    llm = FakeLLM()
    first, last = voice_reply("Checking now, checking now."), voice_reply("Checking now.")
    first.metadata.update(CALL)
    llm.conversation_history.extend([first, last])
    played = "Checking now, checking now. Checking now"
    assert (await service(llm).record_interrupted_voice_reply("turn-1", played))["replaced"]
    assert (first.content, last.content) == ("Checking now, checking now.", "Checking now")

    llm = FakeLLM()
    first, last = voice_reply("Sure, sure."), voice_reply("Sure.")
    first.metadata.update(CALL)
    llm.conversation_history.extend([first, last])
    assert (await service(llm).record_interrupted_voice_reply("turn-1", "Sure, sure. Sure"))["replaced"]
    assert (first.content, last.content) == ("Sure, sure.", "Sure")


class Turns:
    """A queue processor's turn lock: the talked-over turn still holds it."""

    def __init__(self, open_turn: str | None = None, timeout: float = 20.0) -> None:
        import asyncio

        self._turn_lock = asyncio.Lock()
        self._open_voice_turn = open_turn  # a voice turn with rounds still to run
        self.timeout = timeout

    def between_turns(self, voice_turn=None):
        return QueueProcessor.between_turns(self, voice_turn, timeout=self.timeout)


async def test_a_talk_over_during_a_tool_waits_for_the_turn_to_unwind():
    import asyncio

    llm = FakeLLM()
    llm._queue_processor = turns = Turns()
    await turns._turn_lock.acquire()  # the turn is running its tool
    cut = asyncio.create_task(service(llm).record_interrupted_voice_reply("turn-1", "Let me"))
    await asyncio.sleep(0.05)
    assert not cut.done() and llm.conversation_history == []  # nothing lands mid-turn
    # The cancelled tool returns: the turn writes its call row, then lets go.
    reply = voice_reply("Let me check.")
    reply.metadata.update(CALL)
    llm.conversation_history.append(reply)
    turns._turn_lock.release()
    assert (await cut)["replaced"]
    assert llm.conversation_history == [reply] and reply.content == "Let me"  # one copy


async def test_the_cut_reaches_the_conversation_log(tmp_path, monkeypatch):
    from kollabor_ai.conversation_logger import KollaborConversationLogger
    from kollabor_ai.conversation_manager import ConversationManager

    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")
    log = KollaborConversationLogger(tmp_path / "conversations")
    llm, first, result, last = tool_turn()
    llm.conversation_logger = log
    parent = await log.log_user_message("weather?", parent_uuid=None)
    parent = await log.log_assistant_message(
        "Let me check the weather.", parent, voice_turn_id="turn-1",
        tool_calls=[{"id": "t1", "name": "weather", "input": {}}],
    )
    answer = await log.log_assistant_message("**It's 20** and sunny.", parent, voice_turn_id="turn-1")
    await log.log_user_message("thanks", parent_uuid=answer)

    assert (await service(llm).record_interrupted_voice_reply("turn-1", "Let me check"))["replaced"]
    config = SimpleNamespace(
        _conversations_dir=tmp_path / "conversations", get=lambda key, default=None: default
    )
    loaded = ConversationManager(config, log)._load_from_jsonl(log.session_file)["messages"]
    replies = [(m["role"], m["content"]) for m in loaded]
    assert ("assistant", "Let me check") in replies
    assert not any("sunny" in content for _, content in replies)  # /resume brings no answer back
    thanks = next(m for m in loaded if m["content"] == "thanks")
    assert thanks["parent_uuid"] == parent  # threads under the reply that stays


async def test_a_cut_waits_out_the_rounds_its_turn_still_has_to_run():
    import asyncio

    llm, first, result, last = tool_turn()
    llm._queue_processor = turns = Turns(open_turn="turn-1")  # its tools ran: one more round
    cut = asyncio.create_task(service(llm).record_interrupted_voice_reply("turn-1", "Let me check"))
    await asyncio.sleep(0.05)
    assert not cut.done()  # the lock is free between rounds, but the turn is not over
    turns._open_voice_turn = "turn-2"  # the next turn took over
    assert (await cut)["replaced"] and llm.conversation_history == [first, result]


async def test_a_turn_that_never_ends_drops_the_cut_and_frees_the_lock():
    llm, first, result, last = tool_turn()
    llm._queue_processor = turns = Turns(open_turn="turn-1", timeout=0.05)
    assert await service(llm).record_interrupted_voice_reply("turn-1", "Let me check") == {
        "recorded": False,
        "busy": True,
    }
    assert llm.conversation_history == [first, result, last] and last.content.endswith("sunny.")
    assert not turns._turn_lock.locked()


async def logged_tool_turn(tmp_path, monkeypatch):
    """tool_turn() with its replies in a real conversation log, the answer logged last."""
    from kollabor_ai.conversation_logger import KollaborConversationLogger

    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")
    log = KollaborConversationLogger(tmp_path / "conversations")
    llm, first, result, last = tool_turn()
    llm.conversation_logger = log
    user = await log.log_user_message("weather?", parent_uuid=None)
    call = await log.log_assistant_message(
        "Let me check the weather.", user, voice_turn_id="turn-1",
        tool_calls=[{"id": "t1", "name": "weather", "input": {}}],
    )
    await log.log_system_message(
        "20C sunny", call, subtype="tool_result", tool_use_id="t1",
        tool_result={"content": "20C sunny", "tool_type": "weather"},
    )
    llm.current_parent_uuid = await log.log_assistant_message(
        "**It's 20** and sunny.", call, voice_turn_id="turn-1"
    )
    return llm, log, call


async def test_a_dropped_reply_leaves_no_dangling_parent_or_empty_block(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    llm, log, call = await logged_tool_turn(tmp_path, monkeypatch)
    assert (await service(llm).record_interrupted_voice_reply("turn-1", ""))["replaced"]
    assert llm.current_parent_uuid == call  # the dropped answer is no one's parent
    records = [json.loads(line) for line in Path(log.session_file).read_text().splitlines()]
    [kept] = [r for r in records if r.get("voice_turn_id") == "turn-1"]
    assert kept["message"]["content"] == [{"type": "tool_use", "id": "t1", "name": "weather", "input": {}}]
    assert [p.name for p in Path(log.session_file).parent.iterdir() if p.suffix == ".tmp"] == []


async def test_a_resumed_cut_turn_is_a_valid_anthropic_request(tmp_path, monkeypatch):
    from kollabor_ai.api_communication_service import APICommunicationService
    from kollabor_ai.conversation_manager import ConversationManager
    from kollabor_ai.providers.anthropic_provider import AnthropicProvider
    from kollabor_ai.providers.models import AnthropicConfig

    llm, log, call = await logged_tool_turn(tmp_path, monkeypatch)
    assert (await service(llm).record_interrupted_voice_reply("turn-1", ""))["replaced"]
    await log.log_user_message("thanks", parent_uuid=llm.current_parent_uuid)

    config = SimpleNamespace(
        _conversations_dir=tmp_path / "conversations", get=lambda key, default=None: default
    )
    loaded = ConversationManager(config, log)._load_from_jsonl(log.session_file)["messages"]
    messages = APICommunicationService._prepare_messages(SimpleNamespace(
        _strip_leading_orphans=APICommunicationService._strip_leading_orphans
    ), loaded)
    provider = AnthropicProvider(
        AnthropicConfig(api_key="k", model="claude-x", max_tokens=16, base_url="https://example.com")
    )
    sent = provider._prepare_request(messages)["messages"]
    assert [m["role"] for m in sent] == ["user"]  # the tool-only reply has nothing to send
    for message in sent:
        blocks = message["content"]
        assert blocks and all(b.get("text") for b in blocks if b.get("type") == "text")
