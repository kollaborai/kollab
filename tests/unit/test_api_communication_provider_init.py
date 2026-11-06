"""Regression tests for safe provider-initialization diagnostics."""

import logging
from types import SimpleNamespace

import pytest

import kollabor_ai.api_communication_service as api_module


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
async def test_provider_initialization_log_omits_provider_secret(
    error_type, monkeypatch, caplog
):
    secret = "AIzaSyDUMMY_PROVIDER_SECRET_20261106"
    profile = SimpleNamespace(
        name="redaction-test",
        auth_type="api_key",
        to_dict=lambda: {"provider": "openai", "api_key": secret},
    )
    service = api_module.APICommunicationService.__new__(
        api_module.APICommunicationService
    )
    service._profile = profile
    service._provider = None
    service._provider_error = None

    async def fail_provider_initialization(_config):
        raise error_type(f"provider failure included credential {secret}")

    class FailingProviderRegistry:
        get_provider = staticmethod(fail_provider_initialization)

    monkeypatch.setattr(api_module, "create_config_from_profile", lambda _: {})
    monkeypatch.setattr(api_module, "ProviderRegistry", FailingProviderRegistry)
    caplog.set_level(logging.WARNING, logger=api_module.__name__)

    await service._initialize_provider()

    assert secret not in caplog.text
    assert error_type.__name__ in caplog.text
