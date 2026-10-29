"""The voice quick model: a loadout name that may run on another provider."""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "packages/kollabor-voice/src")
)

from kollabor_voice.responder import RESPONDER_KEY

from kollabor.llm.voice_turn import voice_provider

LOADOUTS_KEY = "kollabor.llm.loadouts"


class Cfg:
    def __init__(self, **d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


class Conf:
    def __init__(self, model):
        self.model = model

    def model_copy(self, update):
        return Conf(update["model"])


class Prov:
    def __init__(self, name, model):
        self.provider_name = name
        self.model = model
        self.config = Conf(model)

    async def call(self, *a, **k):  # pragma: no cover - shape only
        return "ok"


class Profile:
    def __init__(self, name, provider, model):
        self.name = name
        self._provider = provider
        self._model = model

    def get_provider(self):
        return self._provider

    def get_api_key(self):
        return "key"

    def get_model(self):
        return self._model


class FakeApiService:
    def __init__(self, active, profiles):
        self.profiles = profiles
        self._profile = active
        self._provider = Prov(active.get_provider(), active.get_model())
        self.reinits = []

    @property
    def config(self):
        return self._config

    async def reinitialize_provider(self, profile):
        self.reinits.append(profile.name)
        self._profile = profile
        self._provider = Prov(profile.get_provider(), profile.get_model())
        return True


def make_world(responder="", loadouts=None, extra=None):
    profiles = {
        "main": Profile("main", "anthropic", "big-chat"),
        "orr": Profile("orr", "openrouter", "old-or-model"),
        **(extra or {}),
    }
    api = FakeApiService(profiles["main"], profiles)
    api._config = Cfg(
        **{
            RESPONDER_KEY: responder,
            LOADOUTS_KEY: loadouts or {},
        }
    )
    pm = type(
        "PM",
        (),
        {
            "list_profiles": staticmethod(lambda: list(profiles.values())),
            "get_profile": staticmethod(lambda name: profiles.get(name)),
            "get_active_profile": staticmethod(lambda: profiles["main"]),
        },
    )()
    return api, pm


@pytest.mark.asyncio
async def test_quick_model_runs_on_its_own_provider_and_model():
    # Chat on anthropic; the quick model is an OpenRouter loadout.
    api, pm = make_world(
        responder="fast-or",
        loadouts={"fast-or": {"provider_profile": "orr", "model": "x-1b"}},
    )
    seen = {}
    async with voice_provider(api, pm, {}):
        seen["provider"] = api._provider.provider_name
        seen["model"] = api._provider.model
        seen["profile"] = api._profile.name
        # The voice-origin profile id never overrides the quick model.
        seen["reinits"] = list(api.reinits)
    assert seen == {
        "provider": "openrouter",
        "model": "x-1b",
        "profile": "orr",
        "reinits": ["orr"],
    }
    # After the turn, the chat is exactly where it was.
    assert api._profile.name == "main"
    assert api._provider.provider_name == "anthropic"
    assert api._provider.model == "big-chat"
    assert api.reinits[-1] == "main"


@pytest.mark.asyncio
async def test_same_provider_quick_model_swaps_model_only():
    api, pm = make_world(
        responder="fast-or",
        loadouts={"fast-or": {"provider_profile": "main", "model": "mini-chat"}},
    )
    async with voice_provider(api, pm, {}):
        assert api._profile.name == "main"  # no profile switch
        assert api._provider.model == "mini-chat"
    assert api._provider.model == "big-chat"
    assert api.reinits == []  # no reinitialize for a same-profile model swap


@pytest.mark.asyncio
async def test_unset_keeps_today_behavior_and_voice_profile_flow():
    api, pm = make_world()
    async with voice_provider(api, pm, {"voice_provider_profile_id": "orr"}):
        assert api._profile.name == "orr"  # the engine per-turn profile still works
        assert api._provider.model == "old-or-model"
    assert api._profile.name == "main"

    api2, pm2 = make_world()
    async with voice_provider(api2, pm2, {}):
        assert api2._profile.name == "main"
        assert api2._provider.model == "big-chat"
    assert api2.reinits == []


@pytest.mark.asyncio
async def test_unknown_quick_model_falls_back_to_active_profile():
    api, pm = make_world(responder="no-such-loadout")
    async with voice_provider(api, pm, {}):
        assert api._profile.name == "main"
        assert api._provider.model == "big-chat"
    assert api.reinits == []


def test_imports_without_event_loop():
    # voice_provider is importable without an app (no hidden service lookups).
    assert callable(voice_provider)


# -- the Settings → Voice panel field and its save routing -------------------


class FakeConfigService:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.saved = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save_key(self, key, value, save_target=None):
        self.values[key] = value
        self.saved.append((key, value, save_target))
        return True


def make_panel_ctx(tmp_path, monkeypatch, responder="", loadouts=None, extra=None):
    from types import SimpleNamespace

    from plugins.voice_plugin import VoicePlugin

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "KOLLAB_PROMPT_OVERRIDES_DIR", str(tmp_path / "prompt-overrides")
    )
    _, pm = make_world(responder=responder, loadouts=loadouts, extra=extra)
    config_service = FakeConfigService(
        {
            RESPONDER_KEY: responder,
            LOADOUTS_KEY: loadouts or {},
        }
    )
    plugin = VoicePlugin(
        event_bus=SimpleNamespace(get_service=lambda _: None), config={}
    )
    plugin._lease = {"epoch": "owner", "token": "token", "stream_id": "stream"}
    ctx = SimpleNamespace(
        _event_bus=SimpleNamespace(get_service=lambda _: plugin),
        _profile_manager=pm,
        _llm_service=SimpleNamespace(config=config_service),
    )
    return ctx, plugin, config_service


@pytest.mark.asyncio
async def test_panel_lists_quick_model_across_providers(tmp_path, monkeypatch):
    from kollabor.panels.voice import VoicePanel

    ctx, plugin, _ = make_panel_ctx(
        tmp_path,
        monkeypatch,
        responder="fast-or",
        loadouts={"fast-or": {"provider_profile": "orr", "model": "x-1b"}},
    )
    described = await VoicePanel().describe(ctx, {})
    fields = {f["path"]: f for s in described["sections"] for f in s["fields"]}
    quick = fields["quick_model"]
    assert quick["type"] == "dropdown"
    assert quick["value"] == "fast-or"
    assert quick["options"][0] == ""
    assert quick["option_labels"][""] == "Same as Chat"
    assert "fast-or" in quick["options"]
    assert "orr" in quick["option_labels"]["fast-or"]


@pytest.mark.asyncio
async def test_panel_save_routes_quick_model_to_config_not_preferences(
    tmp_path, monkeypatch
):
    from kollabor_voice.speech_settings import (
        load_speech_preferences as load_prefs,
    )

    from kollabor.panels.voice import VoicePanel

    ctx, plugin, config_service = make_panel_ctx(
        tmp_path,
        monkeypatch,
        loadouts={"fast-or": {"provider_profile": "orr", "model": "x-1b"}},
    )
    before = load_prefs()
    result = await VoicePanel().act(
        ctx, "save", {"changes": {"quick_model": "fast-or"}, "target": "device"}
    )
    assert result["ok"], result
    assert config_service.saved == [(RESPONDER_KEY, "fast-or", "global")]
    assert load_prefs() == before  # never a speech preference

    back = await VoicePanel().act(
        ctx, "save", {"changes": {"quick_model": ""}, "target": "device"}
    )
    assert back["ok"]
    assert config_service.saved[-1] == (RESPONDER_KEY, "", "global")


@pytest.mark.asyncio
async def test_panel_rejects_unknown_quick_model_and_survives_missing_llm(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from kollabor.panels.voice import VoicePanel
    from plugins.voice_plugin import VoicePlugin

    ctx, plugin, _ = make_panel_ctx(tmp_path, monkeypatch)
    bad = await VoicePanel().act(
        ctx, "save", {"changes": {"quick_model": "nope"}, "target": "device"}
    )
    assert not bad["ok"] and "quick_model" in bad["errors"]

    # A ctx whose bus cannot reach llm_service (terminal fallback): the field
    # still renders, and saving it reports configuration being unavailable.
    plugin2 = VoicePlugin(
        event_bus=SimpleNamespace(get_service=lambda _: None), config={}
    )
    ctx2 = SimpleNamespace(
        _event_bus=SimpleNamespace(get_service=lambda _: plugin2),
    )
    described = await VoicePanel().describe(ctx2, {})
    fields = {f["path"]: f for s in described["sections"] for f in s["fields"]}
    assert fields["quick_model"]["options"] == [""]  # Same as Chat only


AGENT_CLI = {"agent": Profile("agent", "acp", "agent-cli")}
AGENT_LOADOUT = {"agent-q": {"provider_profile": "agent", "model": "agent-cli"}}


@pytest.mark.asyncio
async def test_agent_cli_quick_model_keeps_the_chat_model():
    api, pm = make_world(responder="agent-q", loadouts=AGENT_LOADOUT, extra=AGENT_CLI)
    async with voice_provider(api, pm, {}):
        assert api._profile.name == "main"
        assert api._provider.model == "big-chat"
    assert api.reinits == []


@pytest.mark.asyncio
async def test_panel_never_offers_an_agent_cli_as_quick_model(tmp_path, monkeypatch):
    from kollabor.panels.voice import VoicePanel

    loadouts = {
        **AGENT_LOADOUT,
        "fast-or": {"provider_profile": "orr", "model": "x-1b"},
    }
    ctx, _, _ = make_panel_ctx(
        tmp_path, monkeypatch, loadouts=loadouts, extra=AGENT_CLI
    )
    described = await VoicePanel().describe(ctx, {})
    fields = {f["path"]: f for s in described["sections"] for f in s["fields"]}
    assert "agent-q" not in fields["quick_model"]["options"]
    assert "fast-or" in fields["quick_model"]["options"]


@pytest.mark.asyncio
async def test_a_failing_model_swap_still_puts_the_chat_profile_back(monkeypatch):
    import kollabor_ai.fast_model as fast_model

    def broken(provider, model):
        raise RuntimeError("no copy")

    monkeypatch.setattr(fast_model, "with_model", broken)
    api, pm = make_world(
        responder="fast-or",
        loadouts={"fast-or": {"provider_profile": "orr", "model": "x-1b"}},
    )
    with pytest.raises(RuntimeError):
        async with voice_provider(api, pm, {}):
            pass
    assert api._profile.name == "main"


@pytest.mark.asyncio
async def test_a_saved_agent_cli_quick_model_shows_as_same_as_chat(
    tmp_path, monkeypatch
):
    from kollabor.panels.voice import VoicePanel

    ctx, _, _ = make_panel_ctx(
        tmp_path,
        monkeypatch,
        responder="agent-q",
        loadouts=AGENT_LOADOUT,
        extra=AGENT_CLI,
    )
    described = await VoicePanel().describe(ctx, {})
    fields = {f["path"]: f for s in described["sections"] for f in s["fields"]}
    assert fields["quick_model"]["value"] == ""
    assert "agent-q" not in fields["quick_model"]["options"]


@pytest.mark.asyncio
async def test_voice_prompt_section_shows_engine_defaults(tmp_path, monkeypatch):
    from kollabor_engine.voice_settings import DEFAULT_PROMPT, PERSONALITIES

    from kollabor.panels.voice import PERSONALITY_LABELS, VoicePanel

    ctx, _, _ = make_panel_ctx(tmp_path, monkeypatch)
    described = await VoicePanel().describe(ctx, {})
    titles = [section["title"] for section in described["sections"]]
    assert titles.index("Voice Prompt") == titles.index("Voice Preferences") + 1
    fields = {f["path"]: f for s in described["sections"] for f in s["fields"]}
    assert fields["voice_prompt"]["type"] == "textarea"
    assert fields["voice_prompt"]["label"] == "Instructions"
    assert fields["voice_prompt"]["value"] == DEFAULT_PROMPT
    assert fields["voice_personality"]["value"] == "max_gen_z"
    assert fields["voice_personality"]["option_labels"]["max_gen_z"] == "Max Gen Z"
    assert list(PERSONALITY_LABELS) == list(PERSONALITIES)
    assert fields["voice_reasoning"]["value"] == "off"
    assert fields["voice_reasoning"]["option_labels"] == {
        "off": "Off (Fastest)",
        "low": "Low",
    }


@pytest.mark.asyncio
async def test_voice_prompt_saves_where_the_engine_reads_it(tmp_path, monkeypatch):
    from kollabor_engine.voice_settings import DEFAULT_PROMPT, VoiceSettingsService
    from kollabor_voice.speech_settings import (
        load_speech_preferences as load_prefs,
    )

    from kollabor.panels.voice import VoicePanel

    ctx, _, config_service = make_panel_ctx(tmp_path, monkeypatch)
    before = load_prefs()
    changes = {
        "voice_prompt": "Keep it under ten words.",
        "voice_personality": "neutral",
        "voice_reasoning": "low",
    }
    result = await VoicePanel().act(
        ctx, "save", {"changes": changes, "target": "device"}
    )
    assert result["ok"], result
    assert ("voice.livekit.personality", "neutral", "global") in config_service.saved
    assert ("voice.livekit.reasoning", "low", "global") in config_service.saved
    assert load_prefs() == before  # never a speech preference
    engine = VoiceSettingsService(config=config_service)
    assert engine.get_prompt(None) == {
        "text": "Keep it under ten words.",
        "personality": "neutral",
    }
    assert engine.reasoning() == "low"
    assert engine.overlay(None).startswith("Keep it under ten words.\n")
    fields = {f["path"]: f for s in result["panel"]["sections"] for f in s["fields"]}
    assert fields["voice_prompt"]["value"] == "Keep it under ten words."

    cleared = await VoicePanel().act(
        ctx, "save", {"changes": {"voice_prompt": ""}, "target": "device"}
    )
    assert cleared["ok"], cleared
    assert engine.get_prompt(None)["text"] == DEFAULT_PROMPT
    assert engine.get_prompt(None)["personality"] == "neutral"  # kept


@pytest.mark.asyncio
async def test_voice_prompt_refuses_bad_values(tmp_path, monkeypatch):
    from kollabor.panels.voice import VoicePanel

    ctx, _, config_service = make_panel_ctx(tmp_path, monkeypatch)
    changes = {
        "voice_prompt": "x" * (32 * 1024 + 1),
        "voice_personality": "pirate",
        "voice_reasoning": "high",
    }
    result = await VoicePanel().act(
        ctx, "save", {"changes": changes, "target": "device"}
    )
    assert not result["ok"]
    assert set(result["errors"]) == set(changes)
    assert config_service.saved == []
    assert not list((tmp_path / "prompt-overrides").rglob("*.md"))
