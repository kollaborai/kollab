"""F3: native role spawn with type= must not take the profile of a bundle it does not launch."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from plugins.hub.plugin import HubPlugin


class _Agents:
    def get_agent(self, name):
        if name == "coder":
            return SimpleNamespace(name="coder", profile="")
        if name == "research":
            return SimpleNamespace(name="research", profile="deepseek")
        return None


class _Profiles:
    active_profile_name = "openai-oauth"

    def get_profile(self, name):
        if name in {"openai-oauth", "deepseek"}:
            return SimpleNamespace(provider="openai", get_provider=lambda: "openai")
        return None


class _Orch:
    def __init__(self):
        self.calls = []

    async def spawn(self, **kw):
        self.calls.append(kw)
        return True


class _Bus:
    def __init__(self):
        self.orch = SimpleNamespace(orchestrator=_Orch())

    def get_service(self, name):
        return {"agent_orchestrator": self.orch, "agent_manager": _Agents(),
                "profile_manager": _Profiles()}.get(name)


@pytest.mark.asyncio
async def test_type_override_profile_matches_launched_bundle():
    bus = _Bus()
    plugin = HubPlugin(event_bus=bus)
    plugin._identity = SimpleNamespace(identity="koordinator", profile="", is_coordinator=True)
    plugin._presence = SimpleNamespace(scan_all_presence=lambda: [])
    personas = [SimpleNamespace(name="jasper", agent="coder")]
    with (
        patch("kollabor_ai.loadout_manager.LoadoutManager.resolve", return_value=(None, [])),
        patch("plugins.hub.plugin.find_persona", side_effect=lambda n: {p.name: p for p in personas}.get(n)),
        patch("plugins.hub.plugin.roster_personas", return_value=personas),
    ):
        await plugin._handle_spawn_command({"name": "coder", "agent_type_override": "research", "task": "t"})
    call = bus.orch.orchestrator.calls[0]
    # whichever bundle launches, its profile (or the parent's) must be the one passed
    expected = "deepseek" if call["agent_type"] == "research" else "openai-oauth"
    assert call["profile"] == expected
