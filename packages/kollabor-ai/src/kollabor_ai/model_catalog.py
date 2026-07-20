"""Provider model catalog lookup.

Best-effort "what models does this provider offer?" used by the ``/model``
picker. Dispatches by provider to whatever live listing exists:

    * OpenAI OAuth (ChatGPT codex backend) -> query_codex_models
    * OpenRouter                            -> OpenRouter /models catalog
    * Anthropic                            -> Anthropic /v1/models (needs API key)

Providers without a listing API (generic OpenAI-compatible endpoints, custom)
return an empty list -- the picker falls back to the models it already knows
from saved profiles plus free-form entry.

Never raises: any failure (network, auth, timeout, missing key) yields an
empty list so the picker still opens.
"""

import asyncio
import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0


async def list_provider_models(
    profile: Any, timeout: float = DEFAULT_TIMEOUT
) -> List[Dict[str, str]]:
    """Return the active provider's catalog as ``[{"id", "note"}]``.

    Args:
        profile: The active LLMProfile (provides provider, auth_type, tokens).
        timeout: Max seconds to wait for a live catalog fetch.

    Returns:
        List of ``{"id": model_id, "note": short_descriptor}`` dicts, or an
        empty list when the provider has no listing API or the fetch fails.
    """
    if profile is None:
        return []

    provider = ""
    try:
        provider = (profile.get_provider() or "").lower()
    except Exception:
        provider = str(getattr(profile, "provider", "") or "").lower()

    auth_type = str(getattr(profile, "auth_type", "") or "").lower()

    try:
        if auth_type == "oauth" or provider == "openai_responses":
            return await asyncio.wait_for(_openai_oauth_models(), timeout=timeout)
        if provider == "openrouter":
            return await asyncio.wait_for(_openrouter_models(), timeout=timeout)
        if provider == "anthropic":
            return await asyncio.wait_for(
                _anthropic_models(profile), timeout=timeout
            )
    except asyncio.TimeoutError:
        logger.warning("model catalog fetch timed out for provider=%s", provider)
    except Exception as e:  # noqa: BLE001 - catalog must never break the picker
        logger.warning("model catalog fetch failed for provider=%s: %s", provider, e)

    return []


async def _openai_oauth_models() -> List[Dict[str, str]]:
    """Codex backend models for the OpenAI OAuth (ChatGPT) profile."""
    from kollabor_ai.oauth import OAuthTokenStorage
    from kollabor_ai.oauth.openai_oauth import query_codex_models

    storage = OAuthTokenStorage()
    tokens = await storage.load_tokens("openai", auto_refresh=True)
    if not tokens:
        return []

    model_ids = await query_codex_models(tokens.access_token, tokens.account_id)
    return [{"id": mid, "note": "codex"} for mid in model_ids if mid]


async def _openrouter_models() -> List[Dict[str, str]]:
    """Live OpenRouter catalog with context length + tool support notes."""
    from kollabor_ai.providers.openrouter_model_info import OpenRouterModelInfo

    models = await OpenRouterModelInfo().list_models()
    result: List[Dict[str, str]] = []
    for model in models:
        model_id = str(model.get("id") or "")
        if not model_id:
            continue
        ctx = model.get("context_length")
        params = model.get("supported_parameters") or []
        supports_tools = "tools" in params or "tool_choice" in params
        ctx_note = f"{ctx:,} ctx" if isinstance(ctx, int) else "openrouter"
        tool_note = "tools" if supports_tools else "no tools"
        result.append({"id": model_id, "note": f"{ctx_note} • {tool_note}"})
    return result


# Anthropic models endpoint. Requires an API key; returns only models the
# account can access. No standard pagination beyond a single page here.
ANTHROPIC_MODELS_ENDPOINT = "https://api.anthropic.com/v1/models"
ANTHROPIC_API_VERSION = "2023-06-01"


async def _anthropic_models(profile: Any) -> List[Dict[str, str]]:
    """Live Anthropic catalog with context length + tool support notes.

    Reads the API key from the profile (keyring / env / config). A missing
    key yields an empty list so the picker still opens.
    """
    api_key = ""
    try:
        api_key = (profile.get_api_key() or "").strip()
    except Exception:
        api_key = ""

    if not api_key:
        logger.warning("model catalog: no Anthropic API key, skipping live fetch")
        return []

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(ANTHROPIC_MODELS_ENDPOINT, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Anthropic model catalog fetch failed (HTTP %s): %s",
            e.response.status_code,
            e,
        )
        return []
    except httpx.TimeoutException:
        logger.warning("Anthropic model catalog fetch timed out")
        return []
    except Exception as e:  # noqa: BLE001 - never break the picker
        logger.warning("Anthropic model catalog fetch failed: %s", e)
        return []

    result: List[Dict[str, str]] = []
    for model in data.get("data", []):
        model_id = str(model.get("id") or "")
        if not model_id:
            continue
        # Anthropic /v1/models does not expose context_length or a tool flag;
        # surface what we have and let the picker fall back to known metadata.
        display_name = str(model.get("display_name") or model_id)
        result.append(
            {"id": model_id, "note": f"anthropic • {display_name}"}
        )
    return result
