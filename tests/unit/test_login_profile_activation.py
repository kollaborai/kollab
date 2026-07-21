"""OAuth login must activate the daemon-owned profile in attach mode."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.commands.system_commands.handlers.login import LoginCommandHandler


@pytest.mark.asyncio
async def test_login_uses_state_service_for_session_only_activation(monkeypatch):
    """The attached client's shadow LLMService must not receive the switch."""
    tokens = SimpleNamespace(
        access_token="access-token",
        account_id="acct-123",
        expires_at=9999999999.0,
    )

    class FakeProfileManager:
        def __init__(self):
            self._profiles = {}

        def get_profile(self, name):
            return self._profiles.get(name)

    profile_manager = FakeProfileManager()
    state_service = MagicMock()
    state_service.set_active_profile = AsyncMock()
    local_llm = MagicMock()
    local_llm.switch_profile = AsyncMock()

    class FakeAltView:
        def __init__(self):
            self.result_error = None
            self.result_tokens = tokens
            self.result_models = ["gpt-5.4"]
            self.result_best_model = "gpt-5.4"
            self.result_make_default = False

    login_altview_module = importlib.import_module("plugins.altview.login_altview")
    monkeypatch.setattr(login_altview_module, "LoginAltView", FakeAltView)

    stack = MagicMock()
    stack.push = AsyncMock()
    event_bus = MagicMock()
    event_bus.get_service.side_effect = lambda name: {
        "state_service": state_service,
        "altview_stack_manager": stack,
        "renderer": MagicMock(),
    }.get(name)

    handler = LoginCommandHandler(
        command_registry=MagicMock(),
        event_bus=event_bus,
        profile_manager=profile_manager,
        llm_service=local_llm,
    )

    result = await handler._login_openai()

    assert result.success is True
    state_service.set_active_profile.assert_awaited_once_with(
        "openai-oauth", persist=False
    )
    local_llm.switch_profile.assert_not_awaited()
