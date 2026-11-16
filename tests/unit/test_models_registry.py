"""Gate: the model registry must stay well-formed and current, plus the
lookup helpers that read it.

Runs scripts/validate_models.py. Fails if models.json is malformed or has gone
stale (see MAX_AGE_DAYS there), forcing the registry to be kept up to date.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import kollabor_ai.model_registry as model_registry
from kollabor_ai.model_registry import (
    get_model_registry,
    list_models_for_provider,
    resolve_context_window,
    resolve_default_model,
    supports_sampling,
    supports_vision,
)
from kollabor_ai.providers.registry import create_config_from_profile

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "bundles" / "data" / "models.json"


def test_registry_passes_validator():
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "validate_models.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_actively_used_models_present():
    # Models the app actually runs must exist so compaction can resolve
    # their real context window instead of falling back to the default.
    models = json.loads(REGISTRY.read_text())["models"]
    for expected in ("glm-4.7", "glm-5.2"):
        assert expected in models, f"{expected} missing from registry"


def test_longest_prefix_wins_per_point_release():
    # grok-4.5 is 500K; the shorter grok-4 entry is 2M. A missing point-release
    # entry silently resolves the wrong window, so keep this honest.
    assert resolve_context_window("grok-4.5") == 500000
    assert resolve_context_window("grok-4") == 2000000
    # dated/suffixed ids still resolve through the longest matching entry
    assert resolve_context_window("gpt-5.6-terra-2026-07-09") == 1050000


def test_supports_sampling_flags_reasoning_models():
    # Claude 4.7+ rejects temperature/top_p/top_k with a 400.
    for model in ("claude-opus-5", "claude-opus-4-7", "claude-sonnet-5"):
        assert supports_sampling(model) is False, model
    # Older models still accept them, and an unknown model must not be
    # silently stripped of its sampling params.
    for model in ("claude-opus-4-6", "claude-sonnet-4-6", "some-future-model"):
        assert supports_sampling(model) is True, model


def test_vision_capability_is_explicit_and_provider_scoped():
    assert supports_vision("gpt-5.6-luna", "openai") is True
    assert supports_vision("gpt-5.6-luna", "openai_responses") is True
    assert supports_vision("gpt-5.6-luna", "custom") is False
    assert supports_vision("model-that-is-not-cataloged", "openai") is False


def test_openai_default_model_comes_from_catalog():
    assert resolve_default_model("openai") == "gpt-5.6-luna"
    config = create_config_from_profile({"provider": "openai", "api_key": "sk-test"})
    assert config.model == "gpt-5.6-luna"


def test_list_models_for_provider_skips_retired():
    models = json.loads(REGISTRY.read_text())["models"]
    retired = {n for n, m in models.items() if m.get("retired")}
    assert retired, "registry has no retired entries — test would be vacuous"

    for provider in ("openai", "anthropic", "gemini", "custom"):
        names = [name for name, _ in list_models_for_provider(provider)]
        assert names, f"{provider} has no registry models to seed the picker"
        assert not (retired & set(names)), provider
        assert set(names) & set(models)  # names are real registry keys

    with_retired = [
        name for name, _ in list_models_for_provider("openai", include_retired=True)
    ]
    assert retired & set(with_retired)
    assert list_models_for_provider("") == []


def test_surfaces_without_own_entries_resolve_through_openai():
    # The codex backend and Azure deployments serve OpenAI models and have no
    # entries of their own, so the picker showed them nothing offline.
    openai_models = {name for name, _ in list_models_for_provider("openai")}
    assert openai_models
    for surface in ("openai_responses", "azure_openai"):
        assert {
            name for name, _ in list_models_for_provider(surface)
        } == openai_models, surface


def test_vendor_prefixed_model_strings_resolve_the_underlying_entry():
    # OpenRouter (and similar aggregators) hand out "vendor/model" strings and
    # free-tier suffixes. The catalog stores the underlying model names, so a
    # prefixed string that matches nothing must fall back to the stripped
    # name instead of silently degrading to provider defaults.
    assert resolve_context_window("openai/gpt-5.6-terra") == 1050000
    assert supports_vision("openai/gpt-5.6-luna", "openrouter") is True
    assert supports_vision("anthropic/claude-opus-5", "openrouter") is True
    assert resolve_context_window("zai/glm-5.2") is not None

    # Free/nitro style suffixes strip too.
    assert supports_vision("openai/gpt-5.6-luna:free", "openrouter") is True

    # Native-provider scoping is untouched: no prefix, same answer as before.
    assert supports_vision("gpt-5.6-luna", "custom") is False
    assert supports_vision("gpt-5.6-luna", "openai") is True

    # A prefixed string whose vendor-stripped name is unknown stays unsupported.
    assert supports_vision("openrouter/no-such-model", "openrouter") is False


def test_exact_entry_silence_blocks_prefix_inheritance():
    """A catalog entry whose name matches exactly owns its field's silence.

    The refreshed catalog adds short-generation entries (models.dev ships
    ``gpt-5`` with supports_sampling=false). Longest-prefix inheritance must
    not leak that into a newer generation whose own entry omits the field:
    ``gpt-5.6`` accepts sampling per its curated entry's silence.
    """
    registry = get_model_registry().get("models", {})
    assert "gpt-5" in registry and registry["gpt-5"].get("supports_sampling") is False
    assert registry["gpt-5.6"].get("supports_sampling") is None
    assert supports_sampling("gpt-5.6") is True
    assert supports_sampling("gpt-5") is False
    # Prefix inheritance still applies when no exact entry exists.
    assert resolve_context_window("gpt-5.6-terra-2026-07-09") == 1050000


def test_vendor_prefix_must_match_the_model_provider():
    assert supports_vision("openai/gpt-6-luna", "openrouter") is True
    assert supports_vision("anthropic/gpt-6-luna", "openrouter") is False
    assert supports_vision("unknown-vendor/gpt-6-luna", "openrouter") is False


@pytest.mark.parametrize(
    "catalog",
    [
        [],
        {"models": None, "provider_defaults": {}},
        {"models": {"gpt-6": 123}, "provider_defaults": {}},
        {"models": {}, "provider_defaults": None},
        {"models": {"gpt-6": {"context_window": "invalid"}}, "provider_defaults": {}},
        {"models": {}, "provider_defaults": {"openai": {"context_window": "invalid"}}},
    ],
)
def test_malformed_catalog_shapes_use_safe_defaults(monkeypatch, catalog):
    monkeypatch.setattr(model_registry, "_REGISTRY", catalog)

    assert supports_sampling("gpt-6-luna-latest") is True
    assert supports_vision("gpt-6-luna-latest", "openai") is False
    assert resolve_context_window("gpt-6-luna-latest", "openai") is None


def test_exact_prefixed_entry_silence_blocks_stripped_fallback(monkeypatch):
    monkeypatch.setattr(
        model_registry,
        "_REGISTRY",
        {
            "models": {
                "openrouter/silent-model": {"provider": "openrouter"},
                "silent-model": {
                    "provider": "openai",
                    "supports_vision": True,
                },
            },
            "provider_defaults": {},
        },
    )

    assert supports_vision("openrouter/silent-model", "openrouter") is False
