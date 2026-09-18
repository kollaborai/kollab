"""Gate: the model registry must stay well-formed and current, plus the
lookup helpers that read it.

Runs scripts/validate_models.py. Fails if models.json is malformed or has gone
stale (see MAX_AGE_DAYS there), forcing the registry to be kept up to date.
"""

import json
import subprocess
import sys
from pathlib import Path

from kollabor_ai.model_registry import (
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
    # Models the app actually runs must exist so the budget guard can resolve
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
