"""An agent leaves the hub mesh and rejoins it without a restart: the web
UI's Properties switch, and /hub leave and /hub join in the terminal."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor_agent.runtime import AgentRuntime
from plugins.hub.plugin import HubPlugin


class _EventBus:
    def __init__(self):
        self.services = {}
        self.hooks = {}

    async def register_hook(self, hook):
        self.hooks[hook.name] = hook
        return True

    async def unregister_hook(self, plugin_name, hook_name):
        return self.hooks.pop(hook_name, None) is not None

    def get_service(self, name):
        return self.services.get(name)


def _plugin(*, solo=False, coordinator=False):
    bus = _EventBus()
    plugin = HubPlugin(event_bus=bus)
    plugin._identity = AgentRuntime(
        name="lapis", identity="lapis", agent_id="lapis-id", solo=solo
    )
    plugin._identity.is_coordinator = coordinator
    plugin._presence = MagicMock()
    plugin._election = MagicMock()
    plugin._election.try_become_coordinator.return_value = True
    return plugin, bus


@pytest.mark.asyncio
async def test_leaving_takes_off_every_hook_the_mesh_added():
    plugin, bus = _plugin(coordinator=True)
    await plugin._register_mesh_hooks()
    # The list set_solo removes is the list _register_mesh_hooks adds.
    assert set(bus.hooks) == set(HubPlugin._MESH_HOOKS)
    relay = MagicMock(close=AsyncMock())
    plugin._relay_agent = relay

    await plugin.set_solo(True)

    assert plugin._solo is True
    assert bus.hooks == {}
    plugin._election.release.assert_called_once()
    assert plugin._identity.is_coordinator is False
    relay.close.assert_awaited_once()
    assert plugin._relay_agent is None
    plugin._presence.heartbeat.assert_called_once()


@pytest.mark.asyncio
async def test_joining_puts_the_hooks_back_and_restarts_the_relay(monkeypatch):
    plugin, bus = _plugin(solo=True)
    resumed = asyncio.Event()

    async def resume():
        resumed.set()

    monkeypatch.setattr(plugin, "_resume_relay", resume)

    await plugin.set_solo(False)
    await asyncio.wait_for(resumed.wait(), 1)

    assert plugin._solo is False
    assert set(bus.hooks) == set(HubPlugin._MESH_HOOKS)
    assert plugin._identity.is_coordinator is True
    plugin._presence.heartbeat.assert_called_once()


@pytest.mark.asyncio
async def test_setting_the_state_it_is_in_changes_nothing():
    plugin, bus = _plugin()

    await plugin.set_solo(False)

    assert bus.hooks == {}
    plugin._presence.heartbeat.assert_not_called()


@pytest.mark.asyncio
async def test_an_agent_off_the_hub_cannot_send():
    plugin, _ = _plugin(solo=True)

    for handler in (plugin._handle_hub_msg_tool, plugin._handle_hub_broadcast_tool):
        result = await handler({"id": "t1", "to": "ruby", "message": "hi"})
        assert result.success is False
        assert result.error == "This agent is off the hub, so it cannot message other agents."


@pytest.mark.asyncio
async def test_hub_leave_and_join_go_through_the_state_service():
    plugin, bus = _plugin()
    state = MagicMock(set_hub_participation=AsyncMock())
    bus.services["state_service"] = state

    left = await plugin._handle_hub_command("leave")
    joined = await plugin._handle_hub_command("join")

    assert left == "this agent is off the hub: your other agents no longer see it (/hub join to come back)"
    assert joined == "this agent is on the hub"
    assert [call.args for call in state.set_hub_participation.await_args_list] == [
        (False,),
        (True,),
    ]


@pytest.mark.asyncio
async def test_the_state_service_flips_solo_then_rebuilds_the_prompt():
    calls = []
    hub = MagicMock(set_solo=AsyncMock(side_effect=lambda solo: calls.append(("solo", solo))))
    llm = MagicMock(rebuild_system_prompt=lambda: calls.append(("prompt",)))
    bus = _EventBus()
    bus.services["hub_plugin"] = hub
    service = LocalStateService(llm_service=llm, profile_manager=MagicMock(), event_bus=bus)

    assert await service.set_hub_participation(False) == {"hub": False}
    # The hub sections leave the prompt only once the agent is solo.
    assert calls == [("solo", True), ("prompt",)]


@pytest.mark.asyncio
async def test_without_a_hub_the_rpc_says_so():
    rpc = MagicMock()
    service = LocalStateService(llm_service=MagicMock(), profile_manager=MagicMock(), event_bus=_EventBus())
    register_state_handlers(rpc, service)
    handlers = {call.args[0]: call.args[1] for call in rpc.register.call_args_list}
    handler = handlers["state.set_hub_participation"]

    assert await handler({"enabled": "off"}) == {"error": "enabled must be true or false"}
    assert await handler({"enabled": False}) == {"error": "this agent runs no hub"}
