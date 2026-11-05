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

    def __init__(self, effort: str | None = "high") -> None:
        self._effort = effort  # what every profile's model config starts with
        self._profile = SimpleNamespace(name="chat")
        self._provider = SimpleNamespace(config=Config(effort))
        self.calls: list[dict] = []
        self.reinitialized: list[str] = []

    async def reinitialize_provider(self, profile) -> bool:
        self.reinitialized.append(profile.name)
        self._profile = profile
        self._provider = SimpleNamespace(config=Config(self._effort))
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
        # no effort field would be the model's default, so "off" is the lowest level
        assert sent["effort"] == "low"
        assert stored[0].content == "You are Lapis."  # the stored history never changes
        assert api._provider.config.effort == "high"  # and chat's effort comes back
    finally:
        revoke_voice_admission_context(origin)


@pytest.mark.asyncio
async def test_voice_reasoning_off_sends_no_effort_when_chat_sends_none():
    api = FakeApi(effort=None)  # a model that may not take an effort field
    origin = {**make_voice_origin_context(), "voice_reasoning": "off"}
    try:
        await call(handler(api, origin), history())
        assert api.calls[0]["effort"] is None
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


class ModelProfile:
    """A profile the real /model setter can edit, as ProfileManager hands it out."""

    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model

    def get_model(self) -> str:
        return self.model

    def get_provider(self) -> str:
        return "fake"


class ModelProfiles:
    """Just enough ProfileManager for the setter: edit a model, get the active profile back."""

    def __init__(self, active: ModelProfile) -> None:
        self.active = active

    def get_active_profile(self) -> ModelProfile:
        return self.active

    def update_profile(self, name: str, model: str, **_) -> bool:
        # A fresh profile object per edit, like a config reload
        self.active = ModelProfile(name, model)
        return True


class ModelApi(FakeApi):
    """FakeApi whose provider remembers the model its profile chose."""

    def __init__(self) -> None:
        super().__init__()
        self._profile = ModelProfile("chat", "original")
        self._provider = SimpleNamespace(config=Config("high"), model="original")

    async def reinitialize_provider(self, profile) -> bool:
        self.reinitialized.append(f"{profile.name}:{profile.get_model()}")
        self._profile = profile
        self._provider = SimpleNamespace(config=Config("high"), model=profile.get_model())
        return True


def model_setter(api, profiles):
    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    async def no_tools() -> None:
        return None

    llm = SimpleNamespace(api_service=api, _load_native_tools=no_tools)
    return ModelCommandHandler(None, None, profile_manager=profiles, llm_service=llm)


@pytest.mark.asyncio
async def test_voice_cleanup_does_not_restore_over_a_model_choice_made_mid_turn():
    """#584: /model alpha finishes while a voice turn is paused before cleanup."""
    api = ModelApi()
    profiles = ModelProfiles(api._profile)
    setter = model_setter(api, profiles)
    in_turn, resume = asyncio.Event(), asyncio.Event()

    async def voice_turn() -> None:
        voice = {"voice_provider_profile_id": "fast-voice"}
        pm = SimpleNamespace(get_profile=lambda name: ModelProfile(name, "voice-model"))
        async with voice_provider(api, pm, voice):
            in_turn.set()
            await resume.wait()  # paused before cleanup

    turn = asyncio.create_task(voice_turn())
    await in_turn.wait()
    result = await setter._set_active_profile_model("alpha")
    assert result.success
    assert (api._profile.get_model(), api._provider.model) == ("alpha", "alpha")

    resume.set()
    await turn

    # the selected model and the request provider still agree
    assert api._profile.get_model() == "alpha"
    assert api._provider.model == "alpha"


@pytest.mark.asyncio
async def test_voice_cleanup_still_restores_chat_when_nothing_changed_mid_turn():
    api = ModelApi()
    original = api._profile
    pm = SimpleNamespace(get_profile=lambda name: ModelProfile(name, "voice-model"))
    async with voice_provider(api, pm, {"voice_provider_profile_id": "fast-voice"}):
        assert api._provider.model == "voice-model"
    assert api._profile is original
    assert api._provider.model == "original"


def real_api_service(tmp_path, chat, monkeypatch):
    """The real APICommunicationService; only the provider registry is faked, and it
    caches one provider per profile, as ProviderRegistry.get_provider does per config."""
    from unittest.mock import MagicMock

    from kollabor_ai import api_communication_service as service_module

    config = MagicMock()
    config.get = lambda key, default=None: default
    cache: dict[str, SimpleNamespace] = {}

    async def get_provider(provider_config):
        name = provider_config["name"]
        if name not in cache:
            cache[name] = SimpleNamespace(
                provider_name=name, model=name, config=Config("high")
            )
        return cache[name]

    monkeypatch.setattr(service_module, "create_config_from_profile", lambda data: data)
    monkeypatch.setattr(
        service_module.ProviderRegistry, "get_provider", staticmethod(get_provider)
    )
    api = service_module.APICommunicationService(config, tmp_path, chat)
    api._profile = chat
    api._provider = cache.setdefault(
        chat.name, SimpleNamespace(provider_name=chat.name, model=chat.name, config=Config("high"))
    )
    return api


def stored_profile(name: str):
    from unittest.mock import MagicMock

    profile = MagicMock()
    profile.name = name
    profile.provider = "openai"
    profile.auth_type = "api_key"
    profile.get_provider.return_value = "openai"
    profile.to_dict.return_value = {"name": name}
    return profile


@pytest.mark.asyncio
async def test_voice_cleanup_keeps_a_switch_to_the_profile_the_turn_runs_on(tmp_path, monkeypatch):
    """#584: /llm fast-voice mid-turn hands the service the very objects the turn installed."""
    chat, fast = stored_profile("chat"), stored_profile("fast-voice")
    api = real_api_service(tmp_path, chat, monkeypatch)
    profiles = SimpleNamespace(get_profile=lambda name: {"fast-voice": fast}.get(name))
    in_turn, resume = asyncio.Event(), asyncio.Event()

    async def voice_turn() -> None:
        async with voice_provider(api, profiles, {"voice_provider_profile_id": "fast-voice"}):
            in_turn.set()
            await resume.wait()

    turn = asyncio.create_task(voice_turn())
    await in_turn.wait()
    assert api._profile is fast
    before = api._provider
    assert await api.reinitialize_provider(fast)  # the user's switch, as switch_profile does
    assert api._provider is before  # same profile, same cached provider: nothing to tell it apart

    resume.set()
    await turn

    assert api._profile is fast
    assert api._provider.provider_name == "fast-voice"


@pytest.mark.asyncio
async def test_voice_cleanup_restores_chat_after_an_oauth_rebuild_mid_turn(tmp_path, monkeypatch):
    """A 401 refresh rebuilds the provider on its own; the user chose nothing, so chat returns."""
    from kollabor_ai import api_communication_service as service_module

    chat, fast = stored_profile("chat"), stored_profile("fast-voice")
    fast.auth_type = "oauth"
    fast.api_key = "old"
    api = real_api_service(tmp_path, chat, monkeypatch)
    built = []

    async def new_provider(provider_config):  # a new token builds a new provider
        provider = SimpleNamespace(
            provider_name=provider_config["name"], model="rebuilt", config=Config("high")
        )
        built.append(provider)
        return provider

    monkeypatch.setattr(
        service_module.ProviderRegistry, "get_provider", staticmethod(new_provider)
    )

    async def refresh() -> None:
        fast.api_key = f"token-{len(built)}-{fast.api_key}"  # every refresh rotates the key

    monkeypatch.setattr(api, "_refresh_oauth_token", refresh)
    profiles = SimpleNamespace(get_profile=lambda name: {"fast-voice": fast}.get(name))

    async with voice_provider(api, profiles, {"voice_provider_profile_id": "fast-voice"}):
        assert api._profile is fast
        built.clear()
        rejected = fast.api_key
        assert await api._refresh_oauth_after_401(rejected)  # the 401 path, not a user choice
        assert api._provider is built[-1]

    assert api._profile is chat
    assert api._provider.provider_name == "chat"
