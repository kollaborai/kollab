"""Competing profile activations must leave profile and provider in step (#585).

``LLMService.switch_profile`` (the ``/model`` path) and
``LocalStateService.set_active_profile`` (the state RPC path) both pick a
profile and initialize the request provider. Run at the same time they used
to interleave, so a switch could report success and select profile ``first``
while the request provider stayed on the other profile's ``beta``.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.local import LocalStateService
from kollabor_ai.profile_manager import LLMProfile


class _Profiles:
    """Profile registry with the calls both entry points make."""

    def __init__(self, *profiles):
        self.profiles = {p.name: p for p in profiles}
        self.active = profiles[0].name

    def get_profile(self, name):
        return self.profiles.get(name)

    def get_active_profile(self):
        return self.profiles[self.active]

    def get_profile_names(self):
        return list(self.profiles)

    def set_active_profile(self, name, persist=False):
        if name not in self.profiles:
            return False
        self.active = name
        return True


class _Api:
    """Request provider holder; ``gates`` pauses a named profile's init."""

    def __init__(self):
        self.provider = None
        self.gates = {}
        self.reached = {}

    async def reinitialize_provider(self, profile):
        gate = self.gates.get(profile.name)
        self.provider = profile.provider
        if gate is not None:
            self.reached[profile.name].set()
            await gate.wait()
        return True


def _rig():
    first = LLMProfile(name="first", provider="alpha", model="m1")
    second = LLMProfile(name="second", provider="beta", model="m2")
    llm = MagicMock()
    llm._provider_lock = asyncio.Lock()
    llm._provider_transition_owner = None
    llm.provider_transition = lambda: LLMService.provider_transition(llm)
    llm.profile_manager = _Profiles(first, second)
    llm.api_service = _Api()
    llm.api_service.provider = "alpha"
    llm.event_bus = None
    llm._provider_registry.get_provider = MagicMock(
        side_effect=lambda cfg: _done(SimpleNamespace(provider_name="x", model="m"))
    )
    llm._load_native_tools = lambda: None
    state = LocalStateService(llm, llm.profile_manager)
    return llm, state


async def _done(value):
    return value


@pytest.mark.asyncio
async def test_competing_activations_leave_profile_and_provider_in_step():
    llm, state = _rig()
    api = llm.api_service
    api.gates["first"] = asyncio.Event()
    api.reached["first"] = asyncio.Event()

    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        switch = asyncio.create_task(LLMService.switch_profile(llm, "first"))
        # First switch has initialized provider alpha and is paused before it
        # selects its profile.
        await asyncio.wait_for(api.reached["first"].wait(), 2)

        activate = asyncio.create_task(state.set_active_profile("second"))
        for _ in range(20):
            await asyncio.sleep(0)

        api.gates["first"].set()
        switched = await asyncio.wait_for(switch, 2)
        snapshot = await asyncio.wait_for(activate, 2)

    assert switched is True
    assert snapshot.name == "second"
    selected = llm.profile_manager.get_active_profile()
    assert selected.name == "second"
    # The request provider is the selected profile's provider.
    assert api.provider == selected.provider == "beta"


@pytest.mark.asyncio
async def test_cancelled_switch_restores_previous_provider():
    llm, state = _rig()
    api = llm.api_service
    api.gates["second"] = asyncio.Event()
    api.reached["second"] = asyncio.Event()

    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        switch = asyncio.create_task(LLMService.switch_profile(llm, "second"))
        await asyncio.wait_for(api.reached["second"].wait(), 2)
        switch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await switch
        # Lock is free again and the old profile still has its own provider.
        assert not llm._provider_lock.locked()

    assert llm.profile_manager.get_active_profile().name == "first"
    assert api.provider == "alpha"


@pytest.mark.asyncio
async def test_nested_activation_in_same_task_does_not_deadlock():
    llm, state = _rig()

    async def nested(profile):
        # Hub announcement stand-in: activates another profile from inside a
        # running switch, in the same task.
        await state.set_active_profile("second")

    llm._announce_hub_model_switch = None

    async def announce(self_, profile, previous_model):
        await nested(profile)

    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ), patch.object(LLMService, "_announce_hub_model_switch", announce):
        ok = await asyncio.wait_for(LLMService.switch_profile(llm, "first"), 2)

    assert ok is True
    assert llm.profile_manager.get_active_profile().name == "second"
    assert llm.api_service.provider == "beta"
