"""Regression coverage for Google API key redaction in setup diagnostics."""

import logging

import pytest

from kollabor.panels import setup as setup_panel


@pytest.mark.asyncio
async def test_connection_redacts_unsubmitted_google_key_from_message_and_log(
    monkeypatch, caplog
):
    submitted_key = "submitted-test-key"
    google_key = "AIza" + "x" * 35

    class FailingProviderRegistry:
        @staticmethod
        async def create_provider(config):
            raise RuntimeError(f"provider rejected another key {google_key}")

    import kollabor_ai.providers.registry as registry

    monkeypatch.setattr(registry, "ProviderRegistry", FailingProviderRegistry)
    caplog.set_level(logging.INFO, logger=setup_panel.__name__)

    ok, message = await setup_panel.test_connection(
        "anthropic", "claude-test", submitted_key, "https://api.anthropic.com"
    )

    assert not ok
    assert "[REDACTED-GOOGLE-API-KEY]" in message
    assert google_key not in message
    assert "setup: connection test failed" in caplog.text
    assert google_key not in caplog.text
