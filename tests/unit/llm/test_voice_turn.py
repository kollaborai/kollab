"""A voice turn's model calls get the voice settings; every other call is untouched."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from kollabor.llm import llm_coordinator as coordinator_module
from kollabor.llm.llm_coordinator import LLMService as LLMCoordinator
from kollabor.llm.streaming_handler import StreamingHandler
from kollabor.llm.voice_turn import voice_provider, with_overlay
from kollabor_events.data_models import ConversationMessage
from tests.unit.llm.voice_admission_helpers import make_voice_origin_context, revoke_voice_admission_context


@dataclass
class Config:
    effort: str | None = "high"


class FakeApi:
    """Records what the model call saw: history, profile and reasoning effort."""

    def __init__(self) -> None:
        self._profile = SimpleNamespace(name="chat")
        self._provider = SimpleNamespace(config=Config())
        self.calls: list[dict] = []
        self.reinitialized: list[str] = []

    async def reinitialize_provider(self, profile) -> bool:
        self.reinitialized.append(profile.name)
        self._profile = profile
        self._provider = SimpleNamespace(config=Config())
        return True

    async def call_llm(self, conversation_history, **_):
        self.calls.append(
            {
                "history": conversation_history,
                "effort": self._provider.config.effort,
                "profile": self._profile.name,
            }
        )
        return "ok"


def handler(api, origin):
    profiles = SimpleNamespace(get_profile=lambda name: SimpleNamespace(name=name))
    return StreamingHandler(
        api,
        SimpleNamespace(is_streaming_active=lambda: False),
        SimpleNamespace(update_thinking=lambda *_: None),
        origin_context=lambda: origin,
        profile_manager=lambda: profiles,
    )


async def call(streaming, history):
    event = asyncio.Event()
    event.set()
    return await streaming.call_llm(history, None, event, lambda: False)


def history():
    return [
        ConversationMessage(role="system", content="You are Lapis."),
        ConversationMessage(role="user", content="hi"),
    ]


@pytest.mark.asyncio
async def test_voice_turn_gets_overlay_and_reasoning_for_that_call_only():
    api, stored = FakeApi(), history()
    # voice settings ride on the context next to the capability's identity fields
    origin = {
        **make_voice_origin_context(),
        "voice_prompt_overlay": "Speak out loud.",
        "voice_reasoning": "off",
    }
    try:
        await call(handler(api, origin), stored)
        sent = api.calls[0]
        assert sent["history"][0].content == "You are Lapis.\n\nSpeak out loud."
        assert sent["effort"] is None  # reasoning off for the voice call
        assert stored[0].content == "You are Lapis."  # the stored history never changes
        assert api._provider.config.effort == "high"  # and chat's effort comes back
    finally:
        revoke_voice_admission_context(origin)


@pytest.mark.asyncio
async def test_voice_reasoning_low_is_low():
    api = FakeApi()
    origin = {**make_voice_origin_context(), "voice_reasoning": "low"}
    try:
        await call(handler(api, origin), history())
        assert api.calls[0]["effort"] == "low"
    finally:
        revoke_voice_admission_context(origin)


@pytest.mark.asyncio
async def test_normal_chat_is_untouched():
    api, stored = FakeApi(), history()
    await call(handler(api, None), stored)
    # a context that merely claims to be voice has no capability behind it
    forged = {"source": "livekit_voice", "voice_prompt_overlay": "Speak.", "voice_reasoning": "off"}
    await call(handler(api, forged), stored)
    for sent in api.calls:
        assert sent["history"] is stored and sent["effort"] == "high" and sent["profile"] == "chat"
    assert api.reinitialized == []


@pytest.mark.asyncio
async def test_voice_profile_applies_to_the_voice_call_and_chat_profile_returns():
    api = FakeApi()
    # the voice profile is part of the capability's identity, so it is bound to it
    origin = make_voice_origin_context(voice_provider_profile_id="fast-voice")
    try:
        await call(handler(api, origin), history())
        assert api.calls[0]["profile"] == "fast-voice"
        assert api.reinitialized == ["fast-voice", "chat"]
        assert api._profile.name == "chat"
    finally:
        revoke_voice_admission_context(origin)


@pytest.mark.asyncio
async def test_voice_profile_equal_to_the_active_one_switches_nothing():
    api = FakeApi()
    async with voice_provider(api, None, {"voice_provider_profile_id": "chat"}):
        pass
    assert api.reinitialized == []


def test_overlay_without_a_system_prompt_adds_one_and_empty_overlay_changes_nothing():
    bare = [{"role": "user", "content": "hi"}]
    assert with_overlay(bare, "Speak.")[0] == {"role": "system", "content": "Speak."}
    assert with_overlay(bare, "") is bare


# --- the hidden greeting turn ------------------------------------------------------


def coordinator(origin, published):
    fake = SimpleNamespace(
        conversation_history=[],
        conversation_manager=SimpleNamespace(add_message=lambda **kw: kw["metadata"]),
        event_bus=object(),
        _queue_processor=SimpleNamespace(active_origin_context=origin),
    )
    fake._is_hidden_voice_message = lambda m: LLMCoordinator._is_hidden_voice_message(fake, m)
    return fake


@pytest.fixture
def published(monkeypatch):
    events = []
    monkeypatch.setattr(
        coordinator_module, "publish_semantic", lambda bus, kind, **kw: events.append(kind)
    )
    return events


def test_hidden_voice_turn_instruction_is_marked_and_not_mirrored(published):
    origin = {**make_voice_origin_context("greet"), "voice_hidden": True}
    try:
        fake = coordinator(origin, published)
        LLMCoordinator._add_conversation_message(fake, "user", "Greet the user.")
        assert fake.conversation_history[0].metadata["hidden"] is True
        assert published == []  # the web mirror is never told about it
        # the reply to it is an ordinary message
        LLMCoordinator._add_conversation_message(fake, "assistant", "Hey!")
        assert "hidden" not in fake.conversation_history[1].metadata
    finally:
        revoke_voice_admission_context(origin)


def test_ordinary_and_spoken_user_messages_are_never_hidden(published):
    spoken = make_voice_origin_context("spoken")
    try:
        for origin in (None, spoken, {"source": "livekit_voice", "voice_hidden": True}):
            fake = coordinator(origin, published)
            LLMCoordinator._add_conversation_message(fake, "user", "hello")
            assert "hidden" not in fake.conversation_history[0].metadata
        assert published == ["user_message"] * 3
    finally:
        revoke_voice_admission_context(spoken)
