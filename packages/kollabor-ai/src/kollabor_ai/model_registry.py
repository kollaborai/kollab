"""Shared model registry — per-model context windows and output limits.

Loaded once from ``bundles/data/models.json`` (with ``~/.kollab/models.json``
merged on top). Used to set a provider config's ``context_window`` from the
model in use so the context-budget guard bounds every request to the real
per-model window instead of a one-size default.

Single source of truth: both ``create_config_from_profile`` and the context
compaction plugin resolve windows through here.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kollabor_config.config_utils import resolve_global_path

logger = logging.getLogger(__name__)

# Provider surfaces with no registry entries of their own because they serve
# another provider's models: the ChatGPT/codex backend and Azure deployments
# both run OpenAI models. Used for model listings and for pricing.
PROVIDER_MODEL_SOURCE = {
    "openai_responses": "openai",
    "azure_openai": "openai",
}


def load_model_registry() -> Dict[str, Any]:
    """Load and merge the model registry.

    Resolution order:
      1. ``bundles/data/models.json`` (bundled defaults — repo tree or install)
      2. ``~/.kollab/models.json`` (user overrides, merged on top)
    """
    registry: Dict[str, Any] = {"models": {}, "provider_defaults": {}}

    bundled_candidates = [
        # dev tree: this file is packages/kollabor-ai/src/kollabor_ai/...
        Path(__file__).resolve().parents[4] / "bundles" / "data" / "models.json",
        resolve_global_path("bundles", "data", "models.json"),
    ]
    for path in bundled_candidates:
        try:
            if path.exists():
                with open(path) as handle:
                    registry = json.load(handle)
                break
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load bundled model registry %s: %s", path, exc)

    user_path = resolve_global_path("models.json")
    try:
        if user_path.exists():
            with open(user_path) as handle:
                user_data = json.load(handle)
            if "models" in user_data:
                registry.setdefault("models", {}).update(user_data["models"])
            if "provider_defaults" in user_data:
                registry.setdefault("provider_defaults", {}).update(
                    user_data["provider_defaults"]
                )
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load user model overrides %s: %s", user_path, exc)

    return registry


_REGISTRY: Optional[Dict[str, Any]] = None


def get_model_registry() -> Dict[str, Any]:
    """Return the merged registry, loading it once."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_model_registry()
    return _REGISTRY


def _best_match(model: str, field: str) -> Optional[Any]:
    """Longest-prefix registry lookup for a single field.

    ``gpt-5.6-terra-2026-07-09`` matches the ``gpt-5.6-terra`` entry rather
    than ``gpt-5.6``, because the longest matching entry name wins. Entries
    missing the field are skipped, so a specific entry never shadows a more
    general one that actually carries the value.
    """
    model_l = (model or "").lower()
    if not model_l:
        return None

    best: Optional[Any] = None
    best_len = 0
    for name, info in get_model_registry().get("models", {}).items():
        if not model_l.startswith(name.lower()) or len(name) <= best_len:
            continue
        value = info.get(field)
        if value is not None:
            best = value
            best_len = len(name)
    return best


def resolve_context_window(model: str, provider: Optional[str] = None) -> Optional[int]:
    """Resolve a model's context window.

    Longest-prefix match against the registry (so ``glm-4.7`` matches an entry
    named ``glm-4.7``), then the provider default. ``None`` if neither resolves
    — callers fall back to the config default.
    """
    window = _best_match(model, "context_window")
    if window:
        return int(window)

    if provider:
        defaults = (
            get_model_registry().get("provider_defaults", {}).get(provider.lower())
        )
        if defaults and defaults.get("context_window"):
            return int(defaults["context_window"])

    return None


def supports_sampling(model: str) -> bool:
    """Whether a model accepts temperature/top_p/top_k.

    Newer reasoning models reject sampling params with a 400 -- every Claude
    from 4.7 on, plus Fable/Mythos 5. The registry marks those with
    ``supports_sampling: false``; anything unmarked is assumed to accept them,
    so an unknown model keeps the old behavior.
    """
    return _best_match(model, "supports_sampling") is not False


def registry_provider_for(provider: str) -> str:
    """Map a kollab provider onto the one whose models it serves.

    The ChatGPT/codex backend and Azure deployments both run OpenAI models, so
    they have no entries of their own. Without this they resolve to nothing —
    no offline picker rows, no pricing.
    """
    provider_l = (provider or "").lower()
    return PROVIDER_MODEL_SOURCE.get(provider_l, provider_l)


def list_models_for_provider(
    provider: str, include_retired: bool = False
) -> List[Tuple[str, Dict[str, Any]]]:
    """Registry entries for one provider, in registry (curated) order.

    Powers the ``/model`` picker so every provider has options offline —
    providers without a live listing API would otherwise show nothing but the
    current model. Surfaces that serve another provider's models resolve
    through :func:`registry_provider_for`.
    """
    provider_l = registry_provider_for(provider)
    if not provider_l:
        return []

    out: List[Tuple[str, Dict[str, Any]]] = []
    for name, info in get_model_registry().get("models", {}).items():
        if not isinstance(info, dict):
            continue
        if str(info.get("provider") or "").lower() != provider_l:
            continue
        if info.get("retired") and not include_retired:
            continue
        out.append((name, info))
    return out
