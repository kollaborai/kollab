"""Model changes must reach the daemon-backed state service immediately."""

import asyncio
import unittest
from types import SimpleNamespace

from kollabor.commands.system_commands.handlers.model import ModelCommandHandler


class _Profile:
    name = "openrouter-2"
    provider = "openrouter"
    model = "old-model"

    def get_provider(self):
        return self.provider

    def get_model(self):
        return self.model


class _ProfileManager:
    def __init__(self):
        self.profile = _Profile()

    def get_active_profile(self):
        return self.profile

    def update_profile(self, name, **kwargs):
        assert name == self.profile.name
        self.profile.model = kwargs["model"]
        return True


class _StateService:
    def __init__(self):
        self.calls = []

    async def set_active_profile(self, name, **kwargs):
        self.calls.append((name, kwargs))


class TestModelProfileHotReload(unittest.TestCase):
    def test_model_update_requests_daemon_profile_reload(self):
        pm = _ProfileManager()
        state = _StateService()
        event_bus = SimpleNamespace(
            get_service=lambda name: state if name == "state_service" else None
        )
        handler = ModelCommandHandler(
            command_registry=None,
            event_bus=event_bus,
            profile_manager=pm,
            llm_service=None,
        )

        result = asyncio.run(handler._set_active_profile_model("z-ai/glm-5.2", True))

        self.assertTrue(result.success)
        self.assertEqual(pm.profile.model, "z-ai/glm-5.2")
        self.assertEqual(
            state.calls,
            [("openrouter-2", {"reload_profile": True})],
        )


if __name__ == "__main__":
    unittest.main()
