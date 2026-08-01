"""
Pricing registry for LLM cost calculation.

Singleton registry that providers feed pricing into at init time.
Cost calculator reads from it without touching provider internals.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from kollabor_config.config_utils import resolve_global_path

logger = logging.getLogger(__name__)

BUNDLED_PRICING_PATH = Path(__file__).parent / "default_pricing.json"


@dataclass(frozen=True)
class ModelPricing:
    """Per-token pricing for a single model."""

    prompt_per_token: float
    completion_per_token: float
    cache_discount: float = 0.5


class PricingRegistry:
    """Singleton registry of model pricing by provider."""

    _instance: Optional["PricingRegistry"] = None
    _pricing: Dict[str, Dict[str, ModelPricing]]
    _defaults_loaded: bool

    def __new__(cls) -> "PricingRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pricing = {}
            cls._instance._defaults_loaded = False
        return cls._instance

    def register_provider_pricing(
        self, provider_type: str, model_id: str, pricing: ModelPricing
    ) -> None:
        if provider_type not in self._pricing:
            self._pricing[provider_type] = {}
        self._pricing[provider_type][model_id] = pricing

    def get_pricing(self, provider_type: str, model_id: str) -> Optional[ModelPricing]:
        stripped = model_id.split("/")[-1] if "/" in model_id else model_id
        provider_pricing = self._pricing.get(provider_type)
        if not provider_pricing:
            return self._pricing_from_any_provider(model_id, stripped)

        # exact match
        if model_id in provider_pricing:
            return provider_pricing[model_id]

        # openrouter namespace strip: "openai/gpt-4o" -> "gpt-4o"
        if stripped != model_id and stripped in provider_pricing:
            return provider_pricing[stripped]

        # segment-based prefix match (most specific wins)
        model_segments = stripped.split("-")
        candidates = []
        for registered_id, pricing in provider_pricing.items():
            reg_segments = registered_id.split("-")
            if len(model_segments) < len(reg_segments):
                continue
            # all full segments before the last must match exactly
            if (
                reg_segments[:-1]
                and model_segments[: len(reg_segments) - 1] != reg_segments[:-1]
            ):
                continue
            # last segment: prefix match to handle dots (glm-5 matches glm-5.1)
            last_reg = reg_segments[-1]
            last_model = model_segments[len(reg_segments) - 1]
            if last_model.startswith(last_reg):
                # sort keys: segment count, then last-segment length.
                # prevents gpt-4 silently matching gpt-4o-mini when gpt-4o is also registered.
                candidates.append(
                    (len(reg_segments), len(last_reg), registered_id, pricing)
                )
        if candidates:
            candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
            return candidates[0][3]

        return self._pricing_from_any_provider(model_id, stripped)

    def _pricing_from_any_provider(
        self, model_id: str, stripped: str
    ) -> Optional[ModelPricing]:
        """Last resort: the same model id registered under another provider.

        Rates belong to the model, not the transport -- GLM served through an
        Anthropic-compatible gateway costs what Z.AI charges. Exact ids only,
        so no cross-vendor prefix guessing. Returning None here means the cost
        display silently reads zero, which is worse than a vendor rate.

        Deterministic: prefers the provider the model registry says owns this
        model, then alphabetical order. Load order must not decide the rate,
        and a genuine disagreement between providers is logged rather than
        silently resolved.
        """
        candidates: Dict[str, ModelPricing] = {}
        for provider_type, models in self._pricing.items():
            for candidate in (model_id, stripped):
                if candidate in models:
                    candidates[provider_type] = models[candidate]
                    break
        if not candidates:
            return None

        preferred = self._registry_provider_for(
            model_id
        ) or self._registry_provider_for(stripped)
        order = sorted(candidates)
        if preferred in candidates:
            order = [preferred, *(p for p in order if p != preferred)]

        rates = {
            (p.prompt_per_token, p.completion_per_token) for p in candidates.values()
        }
        if len(rates) > 1:
            logger.warning(
                "pricing for %s differs between providers %s — using '%s'",
                model_id,
                sorted(candidates),
                order[0],
            )
        else:
            logger.debug(
                "pricing for %s resolved from provider '%s'", model_id, order[0]
            )
        return candidates[order[0]]

    @staticmethod
    def _registry_provider_for(model_id: str) -> Optional[str]:
        """The provider the model registry assigns to this exact model id."""
        try:
            from kollabor_ai.model_registry import get_model_registry

            info = get_model_registry().get("models", {}).get(model_id)
            if isinstance(info, dict):
                provider = str(info.get("provider") or "").lower()
                return provider or None
        except Exception:  # registry is best-effort
            pass
        return None

    def load_from_file(self, path: Path) -> None:
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load pricing from {path}: {e}")
            return

        # file format: per-million rates (matches how providers publish pricing)
        #   "prompt_per_million":       dollars per 1M input tokens
        #   "completion_per_million":   dollars per 1M output tokens
        #   "cache_read_per_million":   dollars per 1M cache-hit tokens (optional,
        #                               defaults to 10% of prompt rate)
        for provider_type, models in data.items():
            if not isinstance(models, dict):
                continue
            for model_id, pricing_data in models.items():
                if not isinstance(pricing_data, dict):
                    continue
                try:
                    prompt_rate = float(pricing_data["prompt_per_million"])
                    completion_rate = float(pricing_data["completion_per_million"])
                    # cache_read defaults to 10% of prompt if unspecified
                    cache_rate = float(
                        pricing_data.get("cache_read_per_million", prompt_rate * 0.1)
                    )
                    cache_discount = (
                        cache_rate / prompt_rate if prompt_rate > 0 else 0.5
                    )
                    self.register_provider_pricing(
                        provider_type,
                        model_id,
                        ModelPricing(
                            prompt_per_token=prompt_rate / 1_000_000,
                            completion_per_token=completion_rate / 1_000_000,
                            cache_discount=cache_discount,
                        ),
                    )
                except (KeyError, ValueError, TypeError):
                    logger.debug(
                        f"Skipping invalid pricing for {provider_type}/{model_id}"
                    )

    def load_from_model_registry(self) -> None:
        """Seed pricing from ``bundles/data/models.json``.

        The model registry carries pricing for every model it knows and is
        staleness-gated by scripts/validate_models.py, so it is the source of
        truth. Without this, a model added to the registry has no pricing at
        all here and its cost display silently reads zero.
        """
        from kollabor_ai.model_registry import (
            PROVIDER_MODEL_SOURCE,
            get_model_registry,
        )

        # A surface that serves another provider's models shares its rates.
        aliases: Dict[str, tuple] = {}
        for surface, source in PROVIDER_MODEL_SOURCE.items():
            aliases.setdefault(source, ())
            aliases[source] = (*aliases[source], surface)

        for model_id, info in get_model_registry().get("models", {}).items():
            if not isinstance(info, dict):
                continue
            prompt_rate = info.get("pricing_in")
            completion_rate = info.get("pricing_out")
            if prompt_rate is None or completion_rate is None:
                continue
            provider = str(info.get("provider") or "").lower()
            if not provider:
                continue
            cached = info.get("pricing_cached_in")
            try:
                prompt_rate = float(prompt_rate)
                completion_rate = float(completion_rate)
                cache_rate = float(cached) if cached is not None else prompt_rate * 0.1
            except (TypeError, ValueError):
                continue
            pricing = ModelPricing(
                prompt_per_token=prompt_rate / 1_000_000,
                completion_per_token=completion_rate / 1_000_000,
                cache_discount=(cache_rate / prompt_rate if prompt_rate > 0 else 0.5),
            )
            for provider_type in (provider, *aliases.get(provider, ())):
                self.register_provider_pricing(provider_type, model_id, pricing)

    def load_defaults(self) -> None:
        if self._defaults_loaded:
            return
        # Legacy hand-maintained rates first: they cover models the registry
        # does not list (gpt-4o, o1, o3-mini) but have drifted on the ones it
        # does, so the registry overwrites them below.
        self.load_from_file(BUNDLED_PRICING_PATH)
        self.load_from_model_registry()

        # user override
        user_path = resolve_global_path("pricing.json")
        if user_path.exists():
            self.load_from_file(user_path)

        self._defaults_loaded = True

    def clear_provider(self, provider_type: str) -> None:
        self._pricing.pop(provider_type, None)

    def clear(self) -> None:
        self._pricing.clear()
        self._defaults_loaded = False

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
