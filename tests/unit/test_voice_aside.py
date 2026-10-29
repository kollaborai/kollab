"""Words said while a turn runs get an answer now; the turn keeps going (#388)."""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "packages/kollabor-voice/src")
)

from kollabor.llm import voice_aside, voice_turn
from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.voice_aside import VoiceAside, quick_model_reply
from kollabor_agent.yes_approval import YesApprovals


def events():
    started = time.time() - 30
    return [
        {"type": "tool_start", "ts": started, "tool_id": "a", "tool_name": "terminal"}
    ]


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, **fields):
        self.events.append((kind, fields))


def model(reply):
    calls = []

    async def ask(snapshot, text):
        calls.append((snapshot, text))
        if isinstance(reply, BaseException):
            raise reply
        if reply == "slow":
            await asyncio.sleep(5)
        return reply

    ask.calls = calls
    return ask


def test_an_answer_is_the_voice_turns_whole_reply():
    publish, ask = Recorder(), model("ANSWER The tests are still running.")
    done = asyncio.run(VoiceAside().answer("what's up", "t1", events(), ask, publish))
    assert done is True
    answer = {"text": "The tests are still running.", "request_id": "t1"}
    # One event only this voice turn's stream reads: never a response or
    # turn_complete, which every listener takes for the running turn's.
    assert publish.events == [("voice_aside", {**answer, "end_turn": True})]
    snapshot, text = ask.calls[0]
    assert "- terminal: running for 30 s" in snapshot and text == "what's up"


def test_forward_says_its_line_and_queues_the_words():
    publish, ask = Recorder(), model("FORWARD I'll tell it to run the linter next.")
    aside = VoiceAside()
    done = asyncio.run(aside.answer("also lint", "t2", events(), ask, publish))
    assert done is False
    line = {"text": "I'll tell it to run the linter next.", "request_id": "t2"}
    assert publish.events == [("voice_aside", {**line, "end_turn": False})]


@pytest.mark.parametrize("reply", ["Sure, it runs.", None, RuntimeError("x"), "slow"])
def test_no_usable_reply_says_the_newest_tool_and_queues_the_words(reply, monkeypatch):
    monkeypatch.setattr(voice_aside, "ASIDE_TIMEOUT_S", 0.05)
    publish = Recorder()
    aside = VoiceAside()
    assert (
        asyncio.run(aside.answer("hey", "t3", events(), model(reply), publish)) is False
    )
    [(kind, fields)] = publish.events
    assert kind == "voice_aside" and fields["request_id"] == "t3"
    assert fields["end_turn"] is False
    assert fields["text"] == (
        "Still working: terminal has been running for 30 seconds. I'll pass that on."
    )


def test_one_quick_model_call_at_a_time():
    async def scenario():
        gate, calls = asyncio.Event(), []

        async def ask(snapshot, text):
            calls.append(text)
            await gate.wait()
            return "ANSWER Done soon."

        aside, publish = VoiceAside(), Recorder()
        first = asyncio.create_task(aside.answer("one", "t1", events(), ask, publish))
        await asyncio.sleep(0)  # the first call holds the slot
        assert await aside.answer("two", "t2", events(), ask, publish) is False
        gate.set()
        assert await first is True
        return calls, publish.events

    calls, published = asyncio.run(scenario())
    assert calls == ["one"]
    assert published[0][0] == "voice_aside" and published[0][1]["request_id"] == "t2"


def test_at_most_ten_quick_model_calls_in_five_minutes():
    clock = [1000.0]
    aside = VoiceAside(clock=lambda: clock[0])
    publish, ask = Recorder(), model("ANSWER Still going.")
    for _ in range(12):
        asyncio.run(aside.answer("status?", "t", events(), ask, publish))
    assert len(ask.calls) == 10
    clock[0] += 301
    asyncio.run(aside.answer("status?", "t", events(), ask, publish))
    assert len(ask.calls) == 11


class Profile:
    def __init__(self, provider="openai"):
        self.provider = provider

    def get_provider(self):
        return self.provider

    def to_dict(self):
        return {
            "name": "router",
            "provider": self.provider,
            "model": "big-model",
            "effort": "high",
            "max_tokens": 32000,
        }


def test_the_quick_model_answers_on_a_provider_of_its_own(monkeypatch):
    from kollabor_ai.providers import registry

    seen = {}

    class Provider:
        async def call(self, messages):
            seen["messages"] = messages
            return SimpleNamespace(get_text_content=lambda: "ANSWER Almost there.")

    async def get_provider(config):
        seen["config"] = config
        return Provider()

    quick = SimpleNamespace(provider_profile="router", model="fast-model")
    monkeypatch.setattr(voice_turn, "_quick_loadout", lambda api, pm: quick)
    monkeypatch.setattr(registry, "create_config_from_profile", lambda data: dict(data))
    monkeypatch.setattr(
        registry.ProviderRegistry, "get_provider", staticmethod(get_provider)
    )
    api = SimpleNamespace(_provider="the running turn's provider")
    pm = SimpleNamespace(
        get_profile=lambda name: Profile() if name == "router" else None
    )
    reply = asyncio.run(quick_model_reply(api, pm, "SNAP", "how is it going"))
    assert reply == "ANSWER Almost there."
    assert seen["config"]["model"] == "fast-model"
    assert seen["config"]["max_tokens"] == 400 and seen["config"]["effort"] == "low"
    assert seen["messages"][1]["content"] == "SNAP\n\nThe user said: how is it going"
    assert api._provider == "the running turn's provider"


def test_no_quick_model_or_an_acp_one_makes_no_call(monkeypatch):
    pm = SimpleNamespace(get_profile=lambda name: Profile(provider="acp"))
    monkeypatch.setattr(voice_turn, "_quick_loadout", lambda api, pm: None)
    assert asyncio.run(quick_model_reply(SimpleNamespace(), pm, "S", "hi")) is None
    quick = SimpleNamespace(provider_profile="acp1", model="m")
    monkeypatch.setattr(voice_turn, "_quick_loadout", lambda api, pm: quick)
    assert asyncio.run(quick_model_reply(SimpleNamespace(), pm, "S", "hi")) is None


def coordinator(ask_open=False, **fields):
    approvals = SimpleNamespace(has_open_ask=lambda: ask_open)
    state = {"is_processing": True, "cancel_processing": False, **fields}
    return SimpleNamespace(
        tool_executor=SimpleNamespace(yes_approvals=approvals), **state
    )


def test_an_aside_only_while_a_turn_runs_and_no_ask_is_open():
    due = LLMService._voice_aside_due
    origin = {"source": "livekit_voice", "turn_id": "t1"}
    assert due(coordinator(), origin) is True
    assert due(coordinator(is_processing=False), origin) is False
    assert due(coordinator(cancel_processing=True), origin) is False
    assert due(coordinator(ask_open=True), origin) is False
    assert due(coordinator(), {**origin, "voice_hidden": True}) is False
    assert due(coordinator(), {"source": "livekit_voice"}) is False


def test_an_ask_is_open_until_a_voice_one_runs_out():
    approvals = YesApprovals()
    assert approvals.has_open_ask() is False
    approvals._asked = {"key": "make deploy"}
    approvals._asked_until = 0.0  # a typed ask never runs out
    assert approvals.has_open_ask() is True
    approvals._asked_until = time.monotonic() + 60
    assert approvals.has_open_ask() is True
    approvals._asked_until = time.monotonic() - 1
    assert approvals.has_open_ask() is False
