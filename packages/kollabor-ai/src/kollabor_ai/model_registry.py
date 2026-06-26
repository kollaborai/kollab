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
from typing import Any, Dict, Optional

from kollabor_config.config_utils import resolve_global_path

logger = logging.getLogger(__name__)


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


def resolve_context_window(
    model: str, provider: Optional[str] = None
) -> Optional[int]:
    """Resolve a model's context window.

    Longest-prefix match against the registry (so ``glm-4.7`` matches an entry
    named ``glm-4.7``), then the provider default. ``None`` if neither resolves
    — callers fall back to the config default.
    """
    registry = get_model_registry()
    model_l = (model or "").lower()

    best_window: Optional[int] = None
    best_len = 0
    for name, info in registry.get("models", {}).items():
        if model_l.startswith(name.lower()) and len(name) > best_len:
            window = info.get("context_window")
            if window:
                best_window = int(window)
                best_len = len(name)
    if best_window is not None:
        return best_window

    if provider:
        defaults = registry.get("provider_defaults", {}).get(provider.lower())
        if defaults and defaults.get("context_window"):
            return int(defaults["context_window"])

    return None
