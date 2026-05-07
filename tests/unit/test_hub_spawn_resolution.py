"""Unit tests for hub spawn identity/type resolution."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from plugins.hub.plugin import HubPlugin


class _FakePresence:
    def scan_all_presence(self):
        return []


class _FakeAgentManager:
    def get_agent(self, name):
        if name == "research":
            return SimpleNamespace(name="research", profile="")
        return None


class _FakeOrchestrator:
    def __init__(self):
        self.calls = []

    async def spawn(self, **kwargs):
        self.calls.append(kwargs)
        return True


class _FakeOrchestratorPlugin:
    def __init__(self):
        self.orchestrator = _FakeOrchestrator()


class _FakeEventBus:
    def __init__(self):
        self.orchestrator_plugin = _FakeOrchestratorPlugin()
        self.agent_manager = _FakeAgentManager()

    def get_service(self, name):
        if name == "agent_orchestrator":
            return self.orchestrator_plugin
        if name == "agent_manager":
            return self.agent_manager
        return None


class TestHubSpawnResolution(IsolatedAsyncioTestCase):
    async def test_agent_bundle_name_uses_free_pool_identity(self):
        """`research` is an agent bundle, not a pool identity."""
        event_bus = _FakeEventBus()
        plugin = HubPlugin(event_bus=event_bus)
        plugin._identity = SimpleNamespace(
            identity="koordinator",
            profile="deepseek",
            is_coordinator=True,
        )
        plugin._presence = _FakePresence()

        result = await plugin._handle_spawn_command(
            {"name": "research", "task": "review this project"}
        )

        call = event_bus.orchestrator_plugin.orchestrator.calls[0]
        self.assertIn("Created agent 'lapis' (agent type: research)", result)
        self.assertEqual(call["name"], "lapis")
        self.assertEqual(call["identity"], "lapis")
        self.assertEqual(call["agent_type"], "research")
        self.assertEqual(call["task"], "review this project")
