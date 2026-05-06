"""
OpenRouter model metadata fetcher with TTL cache.

Fetches model metadata from the OpenRouter /api/v1/models endpoint
and caches it to dynamically cap max_tokens based on each model's
context_length and max_completion_tokens.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from ..pricing_registry import ModelPricing, PricingRegistry

logger = logging.getLogger(__name__)

# Default TTL for cached metadata (1 hour)
DEFAULT_CACHE_TTL = 3600

# Safety margin to account for input token estimation error
SAFETY_MARGIN = 4096

# OpenRouter models endpoint
MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"


class OpenRouterModelInfo:
    """
    Fetches and caches OpenRouter model metadata.

    Provides context_length and max_completion_tokens for a given model ID
    so the provider can dynamically cap max_tokens to avoid 400 errors.
    """

    def __init__(self, cache_ttl: int = DEFAULT_CACHE_TTL):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_timestamp: float = 0.0
        self._cache_ttl = cache_ttl
        self._lock = asyncio.Lock()

    def _is_cache_stale(self) -> bool:
        """Check if the cache has expired."""
        if not self._cache:
            return True
        return (time.monotonic() - self._cache_timestamp) > self._cache_ttl

    async def warm_cache(self) -> None:
        """
        Pre-fetch all model metadata. Call this during provider initialization
        so the cache is warm by the time the first API call happens.

        Failures are logged but never raised — a cold cache is acceptable.
        """
        await self._fetch_metadata()

    async def get_model_limits(
        self, model_id: str
    ) -> Dict[str, Optional[int]]:
        """
        Get context_length and max_completion_tokens for a model.

        Returns:
            Dict with keys:
                - context_length: int or None
                - max_completion_tokens: int or None
        """
        if self._is_cache_stale():
            await self._fetch_metadata()

        model_data = self._cache.get(model_id)
        if model_data:
            return {
                "context_length": model_data.get("context_length"),
                "max_completion_tokens": model_data.get("max_completion_tokens"),
            }

        logger.warning(
            f"No metadata found for model '{model_id}', "
            f"using conservative defaults"
        )
        return {
            "context_length": None,
            "max_completion_tokens": None,
        }

    def compute_effective_max_tokens(
        self,
        config_max_tokens: int,
        model_id: str,
        input_token_count: int = 0,
    ) -> int:
        """
        Compute the effective max_tokens to use, capped by model limits.

        Accounts for input tokens so that input + output doesn't exceed
        the model's context_length.

        This is a synchronous convenience method that uses cached data only.
        For fresh data, call get_model_limits() first.

        Args:
            config_max_tokens: The configured max_tokens value
            model_id: The model identifier
            input_token_count: Estimated number of tokens in the input messages

        Returns:
            The effective max_tokens to send in the API request
        """
        model_data = self._cache.get(model_id)
        if not model_data:
            logger.debug(
                f"No cached metadata for '{model_id}', "
                f"using config max_tokens={config_max_tokens}"
            )
            return config_max_tokens

        context_length = model_data.get("context_length")
        max_completion_tokens = model_data.get("max_completion_tokens")

        effective = config_max_tokens

        # Cap by context_length minus safety margin, accounting for input
        if context_length is not None:
            context_cap = context_length - SAFETY_MARGIN - input_token_count
            if context_cap < effective:
                effective = context_cap

        # Cap by model's max_completion_tokens if set
        if max_completion_tokens is not None and max_completion_tokens < effective:
            effective = max_completion_tokens

        # Ensure we never go below a reasonable minimum
        effective = max(effective, 256)

        if effective != config_max_tokens:
            logger.info(
                f"Capped max_tokens from {config_max_tokens} to {effective} "
                f"(model={model_id}, context_length={context_length}, "
                f"input_tokens={input_token_count}, "
                f"max_completion_tokens={max_completion_tokens}, "
                f"safety_margin={SAFETY_MARGIN})"
            )

        return effective

    def get_pricing(self, model_id: str) -> Optional[ModelPricing]:
        """Get cached pricing for a model. Delegates to PricingRegistry."""
        return PricingRegistry().get_pricing("openrouter", model_id)

    async def list_models(self) -> list[Dict[str, Any]]:
        """Return available OpenRouter models from the cached metadata."""
        if self._is_cache_stale():
            await self._fetch_metadata()
        return [
            {"id": model_id, **metadata}
            for model_id, metadata in sorted(self._cache.items())
        ]

    async def _fetch_metadata(self) -> None:
        """
        Fetch model metadata from OpenRouter API.

        Uses a lock to prevent concurrent fetches. Failures are logged
        but never raised — stale/empty cache is acceptable.
        """
        if self._lock.locked():
            # Another fetch is in progress, wait for it
            logger.debug("Metadata fetch already in progress, waiting...")
            async with self._lock:
                return

        async with self._lock:
            # Double-check after acquiring lock
            if not self._is_cache_stale():
                return

            try:
                logger.debug("Fetching OpenRouter model metadata...")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(MODELS_ENDPOINT)
                    response.raise_for_status()

                data = response.json()
                models = data.get("data", [])

                new_cache: Dict[str, Dict[str, Any]] = {}
                for model in models:
                    model_id = model.get("id")
                    if model_id:
                        pricing_data = model.get("pricing", {})
                        new_cache[model_id] = {
                            "context_length": model.get("context_length"),
                            "max_completion_tokens": model.get(
                                "max_completion_tokens"
                            ),
                            "supported_parameters": model.get(
                                "supported_parameters", []
                            ),
                            "pricing": {
                                "prompt": pricing_data.get("prompt"),
                                "completion": pricing_data.get("completion"),
                                "input_cache_read": pricing_data.get(
                                    "input_cache_read"
                                ),
                            },
                        }

                self._cache = new_cache
                self._cache_timestamp = time.monotonic()

                # Register all pricing into PricingRegistry
                registry = PricingRegistry()
                registry.clear_provider("openrouter")
                for mid, mdata in new_cache.items():
                    p = mdata.get("pricing", {})
                    prompt = p.get("prompt")
                    completion = p.get("completion")
                    if prompt is None or completion is None:
                        continue
                    try:
                        prompt_f = float(prompt)
                        completion_f = float(completion)
                        if prompt_f <= 0 or completion_f <= 0:
                            continue
                        # Calculate cache_discount from API if available
                        cache_read_price = p.get("input_cache_read")
                        cache_discount = 0.5  # default
                        if cache_read_price is not None:
                            try:
                                cache_read_f = float(cache_read_price)
                                if cache_read_f > 0 and prompt_f > 0:
                                    cache_discount = cache_read_f / prompt_f
                            except (ValueError, TypeError):
                                pass
                        registry.register_provider_pricing(
                            "openrouter",
                            mid,
                            ModelPricing(
                                prompt_per_token=prompt_f,
                                completion_per_token=completion_f,
                                cache_discount=cache_discount,
                            ),
                        )
                    except (ValueError, TypeError):
                        continue

                logger.info(
                    f"OpenRouter metadata cache refreshed: "
                    f"{len(new_cache)} models loaded"
                )

            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"OpenRouter metadata fetch failed "
                    f"(HTTP {e.response.status_code}): {e}"
                )
            except httpx.TimeoutException:
                logger.warning(
                    "OpenRouter metadata fetch timed out, "
                    "using existing cache or defaults"
                )
            except Exception as e:
                logger.warning(
                    f"OpenRouter metadata fetch failed: {e}. "
                    f"Using existing cache or defaults."
                )
