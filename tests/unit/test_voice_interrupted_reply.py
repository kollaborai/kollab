"""A voice talk-over leaves the part of the reply that was played in the history."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.local import LocalStateService


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
