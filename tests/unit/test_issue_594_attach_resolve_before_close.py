"""Issue #594: attach `--llm` must resolve the name BEFORE touching the harness.

Daemon-side ``LocalStateService.set_active_profile`` used to close a running
ACP orchestrator before discovering that the requested name was absent --
both for an unknown name and for a name that is a loadout rather than a
profile. These tests pin the resolve-then-close ordering: an unresolvable
name raises its error while the running harness stays untouched, and a valid
loadout name switches the harness to the loadout's provider profile.

All collaborators are in-process fakes: no real ACP adapter is started and
no processes are enumerated.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kollabor.state.local import LocalStateService
from kollabor_ai.loadout_manager import Loadout


def _profile(name, provider):
    return SimpleNamespace(name=name, provider=provider, model=f"{name}-model")


class _FakeProfileManager:
    """Records the calls set_active_profile makes; no real config touched."""

    def __init__(self, profiles, active_name, late_profile=None):
        self.profiles = {p.name: p for p in profiles}
        self.active_name = active_name
        self.late_profile = late_profile
        self.set_calls = []
        self.update_calls = []
        self.reload_calls = 0

    def get_profile(self, name):
        return self.profiles.get(name)

    def get_active_profile(self):
        return self.profiles.get(self.active_name)

    def get_profile_names(self):
        return list(self.profiles)

    def set_active_profile(self, name, persist=True):
        self.set_calls.append((name, persist))
        if name in self.profiles:
            self.active_name = name
            return True
        return False

    def update_profile(self, name, **kwargs):
        self.update_calls.append((name, kwargs))
        return name in self.profiles

    def reload(self):
        self.reload_calls += 1
        if self.late_profile is not None:
            self.profiles.setdefault(self.late_profile.name, self.late_profile)


class _FakeOrchestrator:
    """ACP harness stand-in; close/start are awaited, never run anything."""

    def __init__(self):
        self.close = AsyncMock()
        self.start = AsyncMock()


def _state(pm, orch=None):
    bus = SimpleNamespace(
        get_service=lambda name: orch if name == "acp_orchestrator" else None
    )
    llm = SimpleNamespace(
        api_service=SimpleNamespace(reinitialize_provider=AsyncMock(return_value=True))
    )
    return LocalStateService(llm_service=llm, profile_manager=pm, event_bus=bus)


@pytest.mark.asyncio
async def test_unknown_name_does_not_close_running_harness():
    pm = _FakeProfileManager(
        [_profile("review-acp", "acp"), _profile("default", "anthropic")],
        "review-acp",
    )
    orch = _FakeOrchestrator()
    state = _state(pm, orch)
    with patch("kollabor_ai.loadout_manager.LoadoutManager") as lm_cls:
        lm_cls.return_value.resolve.return_value = (None, ["review-acp"])
        with pytest.raises(ValueError, match="profile not found"):
            await state.set_active_profile("unknown-name")
    orch.close.assert_not_awaited()
    assert pm.set_calls == []


@pytest.mark.asyncio
async def test_named_acp_loadout_switches_harness():
    acp_target = _profile("claude-acp", "acp")
    pm = _FakeProfileManager([acp_target, _profile("default", "anthropic")], "default")
    orch = _FakeOrchestrator()
    state = _state(pm, orch)
    loadout = Loadout(
        name="review-acp", provider_profile="claude-acp", model="claude-x"
    )
    with patch("kollabor_ai.loadout_manager.LoadoutManager") as lm_cls:
        lm_cls.return_value.resolve.return_value = (loadout, [])
        snap = await state.set_active_profile("review-acp")
    assert snap.name == "claude-acp"
    orch.close.assert_awaited_once()
    orch.start.assert_awaited_once_with(acp_target)
    assert pm.set_calls and pm.set_calls[-1][0] == "claude-acp"
    assert pm.active_name == "claude-acp"
    # An ACP loadout owns model/effort: nothing may be layered onto the
    # provider profile in memory.
    assert not any(
        name == "claude-acp" and "model" in kwargs
        for name, kwargs in pm.update_calls
    )


@pytest.mark.asyncio
async def test_named_non_acp_loadout_switches_and_layers_model():
    current_acp = _profile("review-acp", "acp")
    target = _profile("openai-direct", "openai")
    pm = _FakeProfileManager([current_acp, target], "review-acp")
    orch = _FakeOrchestrator()
    state = _state(pm, orch)
    loadout = Loadout(
        name="fast-gpt", provider_profile="openai-direct", model="gpt-5.2",
        temperature=0.2,
    )
    with patch("kollabor_ai.loadout_manager.LoadoutManager") as lm_cls:
        lm_cls.return_value.resolve.return_value = (loadout, [])
        snap = await state.set_active_profile("fast-gpt")
    assert snap.name == "openai-direct"
    orch.close.assert_awaited_once()  # legit switch away from the ACP harness
    assert pm.active_name == "openai-direct"
    layered = [kw for name, kw in pm.update_calls if name == "openai-direct"]
    assert layered and layered[-1].get("model") == "gpt-5.2"
    assert layered[-1].get("save_to_config") is False


@pytest.mark.asyncio
async def test_dangling_loadout_errors_without_closing_harness():
    pm = _FakeProfileManager(
        [_profile("claude-acp", "acp"), _profile("default", "anthropic")],
        "claude-acp",
    )
    orch = _FakeOrchestrator()
    state = _state(pm, orch)
    loadout = Loadout(
        name="review-acp", provider_profile="gone-acp", model="claude-x"
    )
    with patch("kollabor_ai.loadout_manager.LoadoutManager") as lm_cls:
        lm_cls.return_value.resolve.return_value = (loadout, [])
        with pytest.raises(ValueError, match="profile not found: 'gone-acp'"):
            await state.set_active_profile("review-acp")
    orch.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_profile_switch_still_closes_harness():
    current_acp = _profile("review-acp", "acp")
    target = _profile("claude-code", "anthropic")
    pm = _FakeProfileManager([current_acp, target], "review-acp")
    orch = _FakeOrchestrator()
    state = _state(pm, orch)
    snap = await state.set_active_profile("claude-code")
    assert snap.name == "claude-code"
    orch.close.assert_awaited_once()
    assert pm.active_name == "claude-code"


@pytest.mark.asyncio
async def test_profile_found_after_reload_activates():
    """Session-only OAuth login stores the profile late; one reload finds it."""
    late = _profile("openai-oauth", "openai-oauth")
    pm = _FakeProfileManager(
        [_profile("default", "anthropic")], "default", late_profile=late
    )
    state = _state(pm, None)
    snap = await state.set_active_profile("openai-oauth")
    assert snap.name == "openai-oauth"
    assert pm.reload_calls == 1
