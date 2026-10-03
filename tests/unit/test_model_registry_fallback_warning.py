"""A model missing from the registry warns once, so a silent 128K fallback is visible."""

import logging

from kollabor_ai import model_registry


def test_unlisted_model_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(
        model_registry,
        "_REGISTRY",
        {
            "models": {"known-model": {"context_window": 200000}},
            "provider_defaults": {"openai": {"context_window": 128000}},
        },
    )
    monkeypatch.setattr(model_registry, "_WARNED_UNLISTED", set(), raising=False)

    with caplog.at_level(logging.WARNING, logger=model_registry.logger.name):
        assert model_registry.resolve_context_window("mystery-9", "openai") == 128000
        assert model_registry.resolve_context_window("mystery-9", "openai") == 128000
        assert model_registry.resolve_context_window("known-model-v2", "openai") == 200000
        assert model_registry.resolve_context_window("", "openai") == 128000

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "mystery-9" in warnings[0] and "128000" in warnings[0]
