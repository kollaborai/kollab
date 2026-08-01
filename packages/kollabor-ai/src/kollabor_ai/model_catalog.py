"""Provider model catalog lookup.

Best-effort "what models does this provider offer?" used by the ``/model``
picker. Dispatches by provider to whatever live listing exists:

    * OpenAI OAuth (ChatGPT codex backend) -> query_codex_models
    * OpenRouter                            -> OpenRouter /models catalog
    * Anthropic                            -> Anthropic /v1/models (needs API key)
    * OpenAI + OpenAI-compatible (custom,   -> {base_url}/models (needs API key
      local servers, xAI, Z.AI, Kimi)          unless the server is keyless)

Providers without a listing API return an empty list -- the picker falls back
to the models it already knows: the current model, same-provider saved
profiles, the bundled registry, plus free-form entry.

Never raises: any failure (network, auth, timeout, missing key) yields an
empty list so the picker still opens.
"""

import asyncio
import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0

# Providers that speak the OpenAI wire protocol, so GET {base_url}/models
# returns {"data": [{"id": ...}]}. "custom" covers xAI, Z.AI, Kimi, and local
# servers (Ollama, LM Studio, vLLM, llama.cpp).
OPENAI_COMPATIBLE_PROVIDERS = {"openai", "custom", "local"}
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


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
            return await asyncio.wait_for(_anthropic_models(profile), timeout=timeout)
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return await asyncio.wait_for(
                _openai_compatible_models(profile), timeout=timeout
            )
    except asyncio.TimeoutError:
        logger.warning("model catalog fetch timed out for provider=%s", provider)
    except Exception as e:  # noqa: BLE001 - catalog must never break the picker
        logger.warning("model catalog fetch failed for provider=%s: %s", provider, e)

    return []


async def _openai_oauth_models() -> List[Dict[str, str]]:
    """Codex backend models for the OpenAI OAuth (ChatGPT) profile.

    The backend reports context window and supported effort levels per model,
    so the picker can show both instead of a bare "codex" tag.
    """
    from kollabor_ai.oauth import OAuthTokenStorage
    from kollabor_ai.oauth.openai_oauth import query_codex_model_details

    storage = OAuthTokenStorage()
    tokens = await storage.load_tokens("openai", auto_refresh=True)
    if not tokens:
        return []

    details = await query_codex_model_details(tokens.access_token, tokens.account_id)

    result: List[Dict[str, str]] = []
    for model in details:
        slug = str(model.get("slug") or "")
        if not slug:
            continue
        parts = []
        window = model.get("context_window")
        if isinstance(window, int) and window > 0:
            parts.append(f"{window // 1000}K ctx")
        efforts = [
            str(level.get("effort"))
            for level in model.get("supported_reasoning_levels") or []
            if isinstance(level, dict) and level.get("effort")
        ]
        if efforts:
            parts.append(f"effort: {'/'.join(efforts)}")
        result.append({"id": slug, "note": " • ".join(parts) or "codex"})
    return result


# A provider's /models listing includes everything the account can call, not
# just chat models. These families are never chat completions, and they sort to
# the top of a reverse-alphabetical list, so they are filtered out.
NON_CHAT_MODEL_MARKERS = (
    "embedding",
    "embed-",
    "whisper",
    "tts-",
    "-tts",
    "dall-e",
    "moderation",
    "stable-diffusion",
    "rerank",
    "clip-",
    "-image-",
    "image-generation",
    "transcribe",
    "speech",
    "guard",
)


def _is_non_chat_model(model_id: str) -> bool:
    """Whether an id from a /models listing is not a chat model."""
    low = model_id.lower()
    return any(marker in low for marker in NON_CHAT_MODEL_MARKERS)


def _models_endpoint(base_url: str) -> str:
    """Turn a chat base URL into its ``/models`` sibling.

    Custom/local profiles store the full chat endpoint (e.g.
    ``http://localhost:1234/v1/chat/completions``), so trim that suffix before
    appending ``/models``. A pasted URL may also carry a query string (Azure's
    ``?api-version=``), which has to come off or the path lands after the ``?``.
    """
    base = (base_url or OPENAI_DEFAULT_BASE_URL).split("#")[0].split("?")[0]
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return f"{base}/models"


async def _openai_compatible_models(profile: Any) -> List[Dict[str, str]]:
    """Catalog from any OpenAI-compatible ``GET {base_url}/models`` endpoint.

    Covers plain OpenAI API keys, custom vendors that speak the OpenAI wire
    format (xAI, Z.AI, Kimi), and local servers. Local servers usually accept
    the request without a key, so a missing key is not fatal here.
    """
    try:
        base_url = profile.get_endpoint() or ""
    except Exception:
        base_url = str(getattr(profile, "base_url", "") or "")

    try:
        api_key = (profile.get_api_key() or "").strip()
    except Exception:
        api_key = ""

    url = _models_endpoint(base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "OpenAI-compatible model catalog fetch failed (HTTP %s) at %s",
            e.response.status_code,
            url,
        )
        return []
    except Exception as e:  # noqa: BLE001 - never break the picker
        logger.warning("OpenAI-compatible model catalog fetch failed at %s: %s", url, e)
        return []

    entries = data.get("data") if isinstance(data, dict) else data
    result: List[Dict[str, str]] = []
    skipped = 0
    for model in entries or []:
        if isinstance(model, str):
            model_id, owner = model, ""
        elif isinstance(model, dict):
            model_id = str(model.get("id") or "")
            owner = str(model.get("owned_by") or "")
        else:
            continue
        if not model_id:
            continue
        if _is_non_chat_model(model_id):
            skipped += 1
            continue
        result.append({"id": model_id, "note": owner or "available"})

    # Newest first among same-family ids: gpt-5.x above gpt-4.x. Only safe
    # because the non-chat ids are filtered out above -- otherwise "whisper-1"
    # and "tts-1" sort to the top of the picker.
    result.sort(key=lambda m: m["id"], reverse=True)
    if skipped:
        logger.debug("model catalog: filtered %d non-chat models at %s", skipped, url)
    return result


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
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_API_VERSION = "2023-06-01"


async def _anthropic_models(profile: Any) -> List[Dict[str, str]]:
    """Live Anthropic catalog with context length + tool support notes.

    Reads the API key and endpoint from the profile (keyring / env / config).
    The endpoint matters: an Anthropic-compatible gateway (Z.AI, a proxy) must
    be queried at its own host, not api.anthropic.com, or its key 401s. A
    missing key yields an empty list so the picker still opens.
    """
    api_key = ""
    try:
        api_key = (profile.get_api_key() or "").strip()
    except Exception:
        api_key = ""

    if not api_key:
        logger.warning("model catalog: no Anthropic API key, skipping live fetch")
        return []

    try:
        base_url = profile.get_endpoint() or ""
    except Exception:
        base_url = str(getattr(profile, "base_url", "") or "")
    base = (base_url or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/v1/models"

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Anthropic model catalog fetch failed (HTTP %s) at %s",
            e.response.status_code,
            url,
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
        result.append({"id": model_id, "note": f"anthropic • {display_name}"})
    return result
