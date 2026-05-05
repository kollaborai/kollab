"""
Cost calculator for LLM token usage.

Uses PricingRegistry to look up per-token pricing and calculates
cost based on provider-specific token accounting rules.
"""

import logging

from .pricing_registry import PricingRegistry

logger = logging.getLogger(__name__)

# providers where prompt_tokens INCLUDES cache_read_tokens as a subset.
# gemini is NOT here: its usageMetadata reports cachedContentTokenCount
# separately from promptTokenCount, so treating it as inclusive would
# double-discount cache reads (undercounting cost) once caching is wired up.
_OPENAI_FAMILY = {
    "openai", "openai_responses", "azure_openai", "custom", "openrouter",
}


def _ensure_defaults_loaded() -> None:
    PricingRegistry().load_defaults()


def calculate_cost(
    provider_type: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    _ensure_defaults_loaded()
    registry = PricingRegistry()
    pricing = registry.get_pricing(provider_type, model)

    if pricing is None:
        return 0.0

    if provider_type in _OPENAI_FAMILY:
        unique_prompt = max(0, prompt_tokens - cache_read_tokens)
        cost = (
            unique_prompt * pricing.prompt_per_token
            + completion_tokens * pricing.completion_per_token
            + cache_read_tokens
            * pricing.prompt_per_token
            * pricing.cache_discount
        )
    elif provider_type in ("anthropic", "gemini"):
        # anthropic: prompt_tokens already includes cache_read (transformers.py:606)
        # gemini: cache_read_tokens reported separately; currently always 0
        # (gemini_transformer doesn't extract cachedContentTokenCount yet)
        cost = (
            prompt_tokens * pricing.prompt_per_token
            + completion_tokens * pricing.completion_per_token
            + cache_read_tokens
            * pricing.prompt_per_token
            * pricing.cache_discount
        )
    else:
        cost = 0.0

    return cost
