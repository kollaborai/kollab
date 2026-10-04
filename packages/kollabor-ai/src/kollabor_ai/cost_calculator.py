"""
Cost calculator for LLM token usage.

Uses PricingRegistry to look up per-token pricing and calculates
cost based on provider-specific token accounting rules.
"""

import logging

from .pricing_registry import PricingRegistry

logger = logging.getLogger(__name__)

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

    # Every provider's prompt_tokens INCLUDES its cache reads: OpenAI-style
    # cached_tokens, Anthropic (transformers sum input + cache writes + cache
    # reads) and Gemini (promptTokenCount includes cachedContentTokenCount).
    # So bill the uncached part at list price and the cached part at the
    # discount -- never both on the same token.
    # ponytail: cache writes bill at list price; Anthropic's write premium
    # (1.25x for 5m, 2x for 1h) needs cache_creation_tokens plumbed here.
    unique_prompt = max(0, prompt_tokens - cache_read_tokens)
    return (
        unique_prompt * pricing.prompt_per_token
        + completion_tokens * pricing.completion_per_token
        + cache_read_tokens * pricing.prompt_per_token * pricing.cache_discount
    )
