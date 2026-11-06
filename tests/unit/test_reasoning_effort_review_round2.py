"""Review round 2 on #559.

Finding 1: ``kollab --llm an --effort minimal`` (attach drain, ``set_active_profile``
state RPC) refused the unsent level only after the daemon had already switched to
the Anthropic profile and saved it as active. The refusal comes before any switch.
Finding 2: a loadout that stores ``none``/``minimal`` stopped working once its
profile became an Anthropic one: ``activate`` raised, and the attach layer dropped
the whole preset. A loadout behaves like a profile: the unsent level reads back as
unset and the model and other fields are still applied.
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.state.local import LocalStateService
from kollabor_ai import LLMProfile, ProfileManager
from kollabor_ai.loadout_manager import Loadout, LoadoutManager

NEW_LEVELS = ("none", "minimal")


class _Config:
    def __init__(self, loadouts=None):
        self.values = {"kollabor": {"llm": {"loadouts": dict(loadouts or {})}}}

    def get(self, key_path, default=None):
        current = self.values
        for part in key_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def save_key(self, key_path, value, save_target=None):
        current = self.values
        parts = key_path.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
        return True


class _Manager(ProfileManager):
    def __init__(self, loadouts=None):
        self._profiles = {
            "oa": LLMProfile(name="oa", provider="openai", model="gpt-5.6"),
            "an": LLMProfile(
                name="an",
                provider="anthropic",
                model="claude-x",
                api_key="test-key",
                auth_type="api_key",
            ),
        }
        self._invalid_profile_errors = {}
        self._active_profile_name = "oa"
        self.config = _Config(loadouts)
        self.activations = []

    def save_profile_values_to_config(self, profile, *args, **kwargs):
        return True

    def reload(self):
        # the fake registry is the whole registry; do not re-read the real config
        return True

    def set_active_profile(self, name, persist=True, **kwargs):
        self.activations.append((name, persist))
        if name not in self._profiles:
            return False
        self._active_profile_name = name
        return True


@pytest.mark.parametrize("level", NEW_LEVELS)
def test_launch_effort_refused_before_the_daemon_switches(level):
    pm = _Manager()
    service = LocalStateService(Mock(), pm, event_bus=None)

    with pytest.raises(ValueError, match=level):
        asyncio.run(service.set_active_profile("an", persist=True, effort=level))

    assert pm.activations == []
    assert pm.get_active_profile().name == "oa"


@pytest.mark.parametrize("level", NEW_LEVELS)
def test_launch_effort_refused_before_a_loadout_layer_is_applied(level):
    pm = _Manager(
        {"fast": {"provider_profile": "an", "model": "claude-y", "temperature": 0.2}}
    )
    service = LocalStateService(Mock(), pm, event_bus=None)

    with pytest.raises(ValueError, match=level):
        asyncio.run(service.set_active_profile("fast", effort=level))

    assert pm.activations == []
    assert pm.get_profile("an").model == "claude-x"
    assert pm.get_profile("an").temperature != 0.2


@pytest.mark.parametrize("level", NEW_LEVELS)
def test_activate_applies_a_loadout_whose_level_the_provider_never_sends(level):
    # stored while the loadout's profile was OpenAI-shaped, then the profile moved
    pm = _Manager()
    loadout = Loadout(
        name="fast",
        provider_profile="an",
        model="claude-y",
        temperature=0.2,
        effort=level,
    )

    result = asyncio.run(LoadoutManager(pm).activate(loadout))

    assert result is not None
    profile = pm.get_profile("an")
    assert profile.model == "claude-y"
    assert profile.temperature == 0.2
    assert profile.effort == ""
    assert profile.get_effort() == ""
    assert pm.activations[-1][0] == "an"


@pytest.mark.parametrize("level", NEW_LEVELS)
def test_activate_still_sends_the_level_to_an_openai_shaped_profile(level):
    pm = _Manager()
    loadout = Loadout(
        name="fast", provider_profile="oa", model="gpt-5.7", effort=level
    )

    assert asyncio.run(LoadoutManager(pm).activate(loadout)) is not None
    assert pm.get_profile("oa").get_effort() == level


@pytest.mark.parametrize("level", NEW_LEVELS)
def test_attach_layer_keeps_the_preset_when_its_level_is_unsent(level):
    pm = _Manager(
        {
            "fast": {
                "provider_profile": "an",
                "model": "claude-y",
                "temperature": 0.2,
                "effort": level,
            }
        }
    )
    llm = Mock()  # activation now awaits the provider re-initialization (#832)
    llm.api_service.reinitialize_provider = AsyncMock(return_value=True)
    service = LocalStateService(llm, pm, event_bus=None)

    asyncio.run(service.set_active_profile("fast"))

    profile = pm.get_profile("an")
    assert profile.model == "claude-y"
    assert profile.temperature == 0.2
    assert profile.get_effort() == ""
