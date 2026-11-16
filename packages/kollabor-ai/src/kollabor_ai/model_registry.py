"""Shared model registry — per-model context windows and output limits.

Loaded once from ``bundles/data/models.json`` (with ``~/.kollab/models.json``
merged on top). Used to set a provider config's ``context_window`` from the
model in use so compaction and the tool-output budget work against the real
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
                    loaded = json.load(handle)
                if not isinstance(loaded, dict):
                    logger.warning("Ignoring malformed model registry root in %s", path)
                    continue
                registry = loaded
                for section in ("models", "provider_defaults"):
                    if not isinstance(registry.get(section), dict):
                        logger.warning(
                            "Ignoring malformed %s section in model registry %s",
                            section,
                            path,
                        )
                        registry[section] = {}
                break
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load bundled model registry %s: %s", path, exc)

    user_path = resolve_global_path("models.json")
    try:
        if user_path.exists():
            with open(user_path) as handle:
                user_data = json.load(handle)
            if not isinstance(user_data, dict):
                logger.warning("Ignoring malformed user model registry root in %s", user_path)
            else:
                for section in ("models", "provider_defaults"):
                    overrides = user_data.get(section)
                    if section not in user_data:
                        continue
                    if not isinstance(overrides, dict):
                        logger.warning(
                            "Ignoring malformed %s section in user model registry %s",
                            section,
                            user_path,
                        )
                        continue
                    current = registry.get(section)
                    if not isinstance(current, dict):
                        current = {}
                        registry[section] = current
                    current.update(overrides)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load user model overrides %s: %s", user_path, exc)

    return registry


_REGISTRY: Optional[Dict[str, Any]] = None


# Models already reported as missing from the registry (one warning per model).
_WARNED_UNLISTED: set[str] = set()


def get_model_registry() -> Dict[str, Any]:
    """Return the merged registry, loading it once."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_model_registry()
    return _REGISTRY


def _registry_section(name: str) -> Dict[str, Any]:
    """Return a catalog mapping, treating malformed roots/sections as empty."""
    registry = get_model_registry()
    if not isinstance(registry, dict):
        return {}
    section = registry.get(name)
    return section if isinstance(section, dict) else {}


# Aggregator surfaces hand out "vendor/model" (and "vendor/model:tag")
# strings. The vendor segment tells us the underlying model's family, and
# tags like ":free" are billing variants, so both strip before lookup.
_KNOWN_VENDORS = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "gemini",
        "zai",
        "zhipuai",
        "xai",
        "deepseek",
        "qwen",
        "mistralai",
        "meta-llama",
        "moonshotai",
        "amazon",
        "microsoft",
        "inclusionai",
        "minimax",
    }
)

# Native provider represented by a vendor prefix in aggregator model IDs.
# Other vendors are handled by an exact OpenRouter catalog entry; they must
# not inherit capabilities from an unrelated unprefixed model with the same
# suffix.
_VENDOR_REGISTRY_PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
    "gemini": "gemini",
}


def _lookup_candidates(model: str, provider: Optional[str] = None) -> List[str]:
    """Candidate forms of a model string, most specific first.

    The full string always tries first. Aggregator-prefixed strings
    (``vendor/model`` where vendor is known, or the provider surface is
    openrouter) also try the stripped name and the suffix-stripped name, so
    ``openai/gpt-5.6-luna:free`` resolves ``gpt-5.6-luna``.
    """
    model_l = (model or "").lower().strip()
    if not model_l:
        return []
    candidates = [model_l]
    if "/" in model_l:
        vendor, _, rest = model_l.partition("/")
        vendor = vendor.strip()
        rest = rest.strip()
        if rest and (provider == "openrouter" or vendor in _KNOWN_VENDORS):
            if ":" in rest:
                candidates.append(rest.rsplit(":", 1)[0])
            candidates.append(rest)
    if ":" in candidates[0] and "/" not in model_l:
        # Bare ":free"-style suffix without a vendor segment.
        stripped = model_l.rsplit(":", 1)[0]
        if stripped:
            candidates.append(stripped)
    return candidates


def _best_match(model: str, field: str) -> Optional[Any]:
    """Longest-prefix registry lookup for a single field.

    ``gpt-5.6-terra-2026-07-09`` matches the ``gpt-5.6-terra`` entry rather
    than ``gpt-5.6``, because the longest matching entry name wins. An exact
    catalog entry stops the search: when the model string names a real entry
    that omits the field, its silence is authoritative (the default applies)
    and shorter-generation prefixes never leak their values in (a new
    ``gpt-5`` entry must not turn ``gpt-5.6`` into a no-sampling model).
    """
    model_l = (model or "").lower()
    if not model_l:
        return None

    registry_models = _registry_section("models")
    exact = registry_models.get(model_l)
    if isinstance(exact, dict):
        return exact.get(field)

    best: Optional[Any] = None
    best_len = 0
    for name, info in registry_models.items():
        if not isinstance(name, str) or not isinstance(info, dict):
            continue
        if not model_l.startswith(name.lower()) or len(name) <= best_len:
            continue
        value = info.get(field)
        if value is not None:
            best = value
            best_len = len(name)
    return best


def _lookup_field(model: str, field: str) -> Optional[Any]:
    """Resolve a field for a model string, aggregator prefixes included.

    Tries :func:`_lookup_candidates` in order and returns the first hit, so
    an exact catalog id always wins over its stripped fallback form.
    """
    for candidate in _lookup_candidates(model):
        best = _best_match(candidate, field)
        if best is not None:
            return best
    return None


def _positive_int(value: Any) -> Optional[int]:
    """Convert a positive integer-like catalog value, rejecting malformed data."""
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def resolve_context_window(model: str, provider: Optional[str] = None) -> Optional[int]:
    """Resolve a model's context window.

    Longest-prefix match against the registry (so ``glm-4.7`` matches an entry
    named ``glm-4.7``), then the provider default. ``None`` if neither resolves
    — callers fall back to the config default.
    """
    window = _positive_int(_lookup_field(model, "context_window"))
    if window is not None:
        return window

    fallback: Optional[int] = None
    if provider:
        defaults = _registry_section("provider_defaults").get(provider.lower())
        if isinstance(defaults, dict):
            fallback = _positive_int(defaults.get("context_window"))

    if model and model not in _WARNED_UNLISTED:
        _WARNED_UNLISTED.add(model)
        logger.warning(
            "Model %r has no context_window in the model registry; using %s. Add it "
            "to bundles/data/models.json so compaction thresholds match its real window.",
            model,
            f"the {provider} default of {fallback}" if fallback else "the config default",
        )
    return fallback


def supports_sampling(model: str) -> bool:
    """Whether a model accepts temperature/top_p/top_k.

    Newer reasoning models reject sampling params with a 400 -- every Claude
    from 4.7 on, plus Fable/Mythos 5. The registry marks those with
    ``supports_sampling: false``; anything unmarked is assumed to accept them,
    so an unknown model keeps the old behavior.
    """
    return _lookup_field(model, "supports_sampling") is not False


def supports_thinking(model: str) -> bool:
    """Whether a model reasons, so it takes an effort level. Unmarked models are assumed to."""
    return _lookup_field(model, "supports_thinking") is not False


# What every effort API takes, for a model the registry gives no levels for.
DEFAULT_EFFORT_LEVELS = ("low", "medium", "high")


def effort_levels(model: str) -> Optional[List[str]]:
    """The effort levels a model takes: ``[]`` for none, None when the registry does not say.

    ``effort_levels`` lists them where a family differs: Anthropic added xhigh
    and max with later models, and Claude Haiku 4.5 takes no effort at all. A
    model that does not reason (``supports_thinking: false``) takes none.
    """
    if not supports_thinking(model):
        return []
    levels = _lookup_field(model, "effort_levels")
    return [str(level) for level in levels] if isinstance(levels, list) else None


def thinking_default(model: str) -> Optional[str]:
    """What a model does with no ``thinking`` field: ``"adaptive"`` or None.

    ``"adaptive"`` marks the Claude models that think by default but hide the
    text (``display: "omitted"``) unless a request asks for the summary.
    Unmarked models (thinking off by default, or not Claude) get nothing.
    """
    value = _lookup_field(model, "thinking_default")
    return value if isinstance(value, str) else None


def _vision_match(model: str, provider_l: Optional[str]) -> Optional[bool]:
    """Longest-prefix supports_vision lookup, optionally provider-scoped."""
    model_l = (model or "").lower()
    registry_models = _registry_section("models")
    exact = registry_models.get(model_l)
    if isinstance(exact, dict) and not (
        provider_l and str(exact.get("provider") or "").lower() != provider_l
    ):
        # Exact entry: its answer (bool or silent default) is final.
        value = exact.get("supports_vision")
        return value if isinstance(value, bool) else None
    best_len = 0
    result: Optional[bool] = None
    for name, info in registry_models.items():
        if not isinstance(name, str) or not isinstance(info, dict):
            continue
        if provider_l and str(info.get("provider") or "").lower() != provider_l:
            continue
        if model_l.startswith(name.lower()) and len(name) > best_len:
            value = info.get("supports_vision")
            if isinstance(value, bool):
                best_len = len(name)
                result = value
    return result


def supports_vision(model: str, provider: Optional[str] = None) -> bool:
    """Whether the catalog explicitly allows image input for a model.

    Unknown models are treated as unsupported so a pasted image is never sent
    to an endpoint that has not declared a vision contract. Provider surfaces
    that serve OpenAI models (Azure and the Responses API) use the OpenAI
    catalog through :func:`registry_provider_for`. Aggregator model strings
    (``openrouter`` and known ``vendor/model`` prefixes) fall back to the
    stripped underlying entry, whose vendor names the real provider.
    """
    provider_l = registry_provider_for(provider or "") if provider else None
    candidates = _lookup_candidates(model, provider.lower() if provider else None)
    if not candidates:
        return False
    exact = _registry_section("models").get(candidates[0])
    if exact is not None:
        if not isinstance(exact, dict):
            return False
        exact_provider = str(exact.get("provider") or "").lower()
        if provider_l and exact_provider != provider_l:
            return False
        # An exact entry owns its silence as well as an explicit bool. It must
        # not fall through to a less-specific stripped model name.
        return exact.get("supports_vision") is True
    result = _vision_match(candidates[0], provider_l)
    if result is not None or len(candidates) == 1:
        return result is True
    # A stripped model name is only safe when its vendor identifies the same
    # provider as the matching catalog entry. OpenRouter's exact vendor/model
    # entries were checked above; unknown vendors cannot borrow a native
    # provider's capability from a coincidentally matching suffix.
    fallback_provider = provider_l
    if "/" in (model or ""):
        vendor = model.lower().split("/", 1)[0].strip()
        vendor_provider = _VENDOR_REGISTRY_PROVIDERS.get(vendor)
        if not vendor_provider:
            return False
        if provider_l not in (None, "openrouter", vendor_provider):
            return False
        fallback_provider = vendor_provider
    for candidate in candidates[1:]:
        scoped = _vision_match(candidate, fallback_provider)
        if scoped is not None:
            return scoped is True
    return False


def resolve_default_model(provider: str) -> Optional[str]:
    """Resolve the catalog-declared default model for a provider."""
    defaults = _registry_section("provider_defaults").get((provider or "").lower(), {})
    default = defaults.get("default_model") if isinstance(defaults, dict) else None
    return str(default) if default else None


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
    for name, info in _registry_section("models").items():
        if not isinstance(name, str):
            continue
        if not isinstance(info, dict):
            continue
        if str(info.get("provider") or "").lower() != provider_l:
            continue
        if info.get("retired") and not include_retired:
            continue
        out.append((name, info))
    return out
