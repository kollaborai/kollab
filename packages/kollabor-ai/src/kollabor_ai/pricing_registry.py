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

    def get_pricing(
        self, provider_type: str, model_id: str
    ) -> Optional[ModelPricing]:
        provider_pricing = self._pricing.get(provider_type)
        if not provider_pricing:
            return None

        # exact match
        if model_id in provider_pricing:
            return provider_pricing[model_id]

        # openrouter namespace strip: "openai/gpt-4o" -> "gpt-4o"
        stripped = model_id.split("/")[-1] if "/" in model_id else model_id
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
            if reg_segments[:-1] and model_segments[: len(reg_segments) - 1] != reg_segments[:-1]:
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
                    completion_rate = float(
                        pricing_data["completion_per_million"]
                    )
                    # cache_read defaults to 10% of prompt if unspecified
                    cache_rate = float(
                        pricing_data.get(
                            "cache_read_per_million", prompt_rate * 0.1
                        )
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

    def load_defaults(self) -> None:
        if self._defaults_loaded:
            return
        self.load_from_file(BUNDLED_PRICING_PATH)

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
