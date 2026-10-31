"""Model fallback: a turn the model can't serve moves the agent to its fallback once."""

import asyncio
from types import SimpleNamespace

import pytest

import kollabor_ai.loadout_manager as loadout_module
from kollabor.llm.usage_limits import failure_kind
from kollabor_events.models import EventType
from kollabor_tui.display_tap import DisplayTap
from plugins.model_fallback_plugin import ModelFallbackPlugin


@pytest.mark.parametrize(
    "message, kind",
    [
        ("Usage limit reached for 5 hour. Your limit will reset at 14:00", "usage"),
        ("Insufficient balance or no resource package. Please recharge.", "usage"),
        ("claude-code: Claude AI usage limit reached", "usage"),
        ("codex-cli: You've hit your usage limit.", "usage"),
        ("You exceeded your current quota, please check your plan", "usage"),
        ("Rate limit exceeded. Please try again later.", "rate"),
        ("claude-code: Authentication required. Log in using this harness's CLI.", "login"),
        ("prompt is too long: 250000 tokens > 200000 maximum", None),
        ("Request exceeds the model's context window", None),
        ("Connection reset by peer", None),
        ("ValueError at line 429", None),
        ("", None),
    ],
)
def test_failure_kind(message, kind):
    assert failure_kind(message) == kind


class _Profile:
    def __init__(self, name, model, provider="openai"):
        self.name, self.model, self.provider = name, model, provider

    def get_model(self):
        return self.model

    def get_provider(self):
        return self.provider


class _Profiles:
    def __init__(self, *profiles, active):
        self.profiles = {p.name: p for p in profiles}
        self.active = active

    def get_active_profile(self):
        return self.profiles[self.active]

    def get_profile(self, name):
        return self.profiles.get(name)


class _Loadouts:
    """Stands in for LoadoutManager. Like it, it knows loadout names
    ("<profile>-loadout"), never bare profile names."""

    activated = []

    def __init__(self, profile_manager, config=None):
        self.pm = profile_manager

    def resolve(self, query):
        profile = self.pm.get_profile(query.removesuffix("-loadout"))
        if profile is None or not query.endswith("-loadout"):
            return None, []
        return SimpleNamespace(name=query, provider_profile=profile.name, model=profile.model), []

    async def activate(self, loadout, event_bus=None):
        _Loadouts.activated.append(loadout.name)
        self.pm.active = loadout.provider_profile
        return loadout


class _Config:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, path, default=None):
        return self.values.get(path, default)

    def save_key(self, path, value, save_target=None):
        self.values[path] = value
        return True


class _Bus:
    def __init__(self, services):
        self.services, self.emitted, self.hooks = services, [], []

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service

    async def register_hook(self, hook):
        self.hooks.append(hook)

    async def fire(self, event_type):
        for hook in self.hooks:
            if hook.event_type == event_type:
                await hook.callback({}, None)

    async def emit_with_hooks(self, event_type, data, source):
        self.emitted.append((event_type, data))


async def _agent(monkeypatch, config, fallback_provider="openai", tap_at_init=True):
    monkeypatch.setattr(loadout_module, "LoadoutManager", _Loadouts)
    _Loadouts.activated = []
    tap = DisplayTap()
    profiles = _Profiles(
        _Profile("glm", "glm-5.3-flash"),
        _Profile("codex", "gpt-6.1-terra", provider=fallback_provider),
        active="glm",
    )
    bus = _Bus({
        "profile_manager": profiles,
        "hub_plugin": SimpleNamespace(_identity=SimpleNamespace(identity="lapis")),
    })
    if tap_at_init:
        bus.register_service("display_tap", tap)
    plugin = ModelFallbackPlugin("model_fallback", bus, None, _Config(config))
    await plugin.initialize()
    await plugin.register_hooks()
    bus.register_service("display_tap", tap)  # the hub starts after plugin files
    return plugin, bus, tap, profiles


async def _native_turn_fails(plugin, tap, message):
    await plugin._on_request_error({}, None)
    tap.publish({"type": "error", "message": message})
    tap.publish({"type": "turn_complete"})
    for _ in range(5):
        await asyncio.sleep(0)
    if plugin._task is not None:
        await plugin._task


def _continues(bus):
    return [e for e, _ in bus.emitted if e == EventType.TRIGGER_LLM_CONTINUE]


@pytest.mark.asyncio
async def test_a_usage_limit_switches_once_and_runs_the_turn_again(monkeypatch):
    plugin, bus, tap, profiles = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})

    await _native_turn_fails(plugin, tap, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == ["codex"] and profiles.active == "codex"
    assert len(_continues(bus)) == 1
    notice = [e for e in tap.get_snapshot() if e["type"] == "model_fallback"]
    assert notice[0]["message"] == (
        "glm-5.3-flash hit its usage limit. Switched to codex and resent your message."
    )

    # The fallback fails too: no second hop, no loop.
    await _native_turn_fails(plugin, tap, "You've hit your usage limit.")
    assert _Loadouts.activated == ["codex"] and len(_continues(bus)) == 1


@pytest.mark.asyncio
async def test_other_errors_and_unset_fallbacks_never_switch(monkeypatch):
    plugin, bus, tap, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    await _native_turn_fails(plugin, tap, "Connection reset by peer")
    # A slash command's error is no failed turn, whatever it says.
    tap.publish({"type": "error", "message": "Rate limit exceeded."})
    tap.publish({"type": "turn_complete"})
    await asyncio.sleep(0)
    assert _Loadouts.activated == []

    plugin, bus, tap, _ = await _agent(monkeypatch, {
        "plugins.model_fallback.loadout": "codex",
        "plugins.model_fallback.agents.lapis": "off",
    })
    await _native_turn_fails(plugin, tap, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == [] and plugin.choice() == ""


@pytest.mark.asyncio
async def test_an_acp_turn_moved_to_an_acp_harness_asks_for_the_message(monkeypatch):
    plugin, bus, tap, _ = await _agent(
        monkeypatch, {"plugins.model_fallback.agents.lapis": "codex"}, fallback_provider="acp"
    )
    tap.publish({"type": "error", "message": "claude-code: Claude AI usage limit reached", "generation": 2})
    tap.publish({"type": "turn_complete"})
    for _ in range(5):
        await asyncio.sleep(0)
    await plugin._task
    assert _Loadouts.activated == ["codex"] and _continues(bus) == []
    notice = [e for e in tap.get_snapshot() if e["type"] == "model_fallback"]
    assert notice[0]["message"].endswith("Switched to codex. Send your message again.")


@pytest.mark.asyncio
async def test_set_choice_saves_this_agents_setting(monkeypatch):
    plugin, _, _, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    assert plugin.set_choice("glm")
    assert plugin.config.values["plugins.model_fallback.agents.lapis"] == "glm"
    assert plugin.choice() == "glm"


@pytest.mark.asyncio
async def test_a_tap_the_hub_creates_after_this_plugin_is_watched_from_startup(monkeypatch):
    # Discovery starts *_plugin.py files before plugin packages like hub/, so
    # the tap is not there at initialize. An ACP turn has no request-error hook.
    plugin, bus, tap, _ = await _agent(
        monkeypatch, {"plugins.model_fallback.loadout": "codex"}, tap_at_init=False
    )
    await bus.fire(EventType.SYSTEM_STARTUP)
    tap.publish({"type": "error", "message": "Usage limit reached for 5 hour.", "generation": 1})
    tap.publish({"type": "turn_complete"})
    for _ in range(5):
        await asyncio.sleep(0)
    if plugin._task is not None:
        await plugin._task
    assert _Loadouts.activated == ["codex"]


@pytest.mark.asyncio
async def test_a_tap_the_hub_replaces_is_followed(monkeypatch):
    # The hub can initialize twice; turns then publish to its second tap.
    plugin, bus, first, _ = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex"})
    second = DisplayTap()
    bus.register_service("display_tap", second)
    await bus.fire(EventType.SYSTEM_STARTUP)
    await _native_turn_fails(plugin, second, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == ["codex"]
    first.publish({"type": "error", "message": "Usage limit reached."})
    assert plugin._failed_message is None  # no longer listening to the old tap



@pytest.mark.asyncio
async def test_a_fallback_names_a_loadout_or_a_profile(monkeypatch):
    # `openai-oauth` is a provider profile, not a loadout: it failed live with
    # "not found" until profiles resolved the way `kollab --llm` reads them.
    plugin, bus, tap, profiles = await _agent(monkeypatch, {"plugins.model_fallback.loadout": "codex-loadout"})
    await _native_turn_fails(plugin, tap, "Usage limit reached for 5 hour.")
    assert _Loadouts.activated == ["codex-loadout"] and profiles.active == "codex"
    assert plugin.resolve("codex").provider_profile == "codex"  # a bare profile name
    assert plugin.resolve("nonesuch") is None
