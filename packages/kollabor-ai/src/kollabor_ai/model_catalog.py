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
import hashlib
import json
import logging
import os
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0

# Disk cache. Same shape as kollabor_agent.agent_manager's agent metadata
# cache: versioned JSON, discarded whole on version mismatch or TTL expiry.
# Bump CACHE_VERSION when the cached entry shape changes. Entries also carry
# the provider and endpoint they were fetched from (#558); that is additive, so
# an older entry without them still loads and is taken as a match.
CACHE_VERSION = 2
CACHE_TTL_SECONDS = 3600  # 1h -- provider catalogs move slowly
_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}


def _cache_file() -> Optional[Path]:
    """Path to the on-disk catalog cache, or None when it must not be touched.

    Disabled under pytest for the same reason ``keyring_enabled()`` is: the
    cache is keyed by profile name, so a developer's real ``~/.kollab`` entry
    would leak into any test whose fake profile happens to share a name --
    making the suite pass on a clean machine and fail on a working one. Tests
    that exercise the cache patch this function with a tmp_path.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    try:
        from kollabor_config import get_config_directory

        return get_config_directory() / "model_catalog.cache"
    except Exception as e:  # noqa: BLE001 -- cache is best-effort
        logger.debug("model catalog cache path unavailable: %s", e)
        return None


def _profile_key(profile: Any) -> str:
    """Cache key for a profile: its name, falling back to its provider."""
    if profile is None:
        return ""
    if isinstance(profile, dict):
        name = str(profile.get("name") or "")
        provider = str(profile.get("provider") or "")
    else:
        name = str(getattr(profile, "name", "") or "")
        provider = str(getattr(profile, "provider", "") or "")
    if name:
        return name
    try:
        get_provider = profile.get_provider
        return str(get_provider() or "").lower()
    except Exception:
        return provider.lower()


def _fingerprint(endpoint: str) -> str:
    """SHA-256 of a normalized endpoint; "" for none.

    An endpoint can carry a password, a query key or a path token, and may come
    only from an env var that is kept off config.json. The cache file records
    this digest, never the endpoint itself.
    """
    endpoint = endpoint.rstrip("/")
    if not endpoint:
        return ""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _profile_source(profile: Any) -> Dict[str, str]:
    """Which provider and endpoint a profile's catalog comes from.

    The cache is keyed by profile name, and an edit can change a profile's
    provider or endpoint in place. An entry fetched from the old source is
    not this profile's catalog (#558). The endpoint is kept as a fingerprint.
    """
    if isinstance(profile, dict):
        provider = str(profile.get("provider") or "")
        endpoint = str(profile.get("base_url") or profile.get("endpoint") or "")
    else:
        provider = ""
        endpoint = ""
        try:
            provider = str(profile.get_provider() or "")
        except Exception:  # noqa: BLE001 -- identity is best-effort
            provider = str(getattr(profile, "provider", "") or "")
        try:
            endpoint = str(profile.get_endpoint() or "")
        except Exception:  # noqa: BLE001
            endpoint = str(getattr(profile, "base_url", "") or "")
    return {"provider": provider.lower(), "endpoint_id": _fingerprint(endpoint)}


def _same_source(entry: Dict[str, Any], profile: Any) -> bool:
    """Whether the complete recorded source matches this profile.

    Legacy entries without both provider and endpoint metadata are cold misses:
    their models cannot be attributed to the profile's current source.
    """
    source = _profile_source(profile)
    for field, expected in source.items():
        if field not in entry or str(entry[field] or "") != expected:
            return False
    return True


def _load_disk_cache() -> Dict[str, Dict[str, Any]]:
    """Load the whole cache file. Returns {} on any problem."""
    path = _cache_file()
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION:
            logger.debug("model catalog cache version mismatch, ignoring")
            return {}
        entries = data.get("entries", {})
        return entries if isinstance(entries, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load model catalog cache: %s", e)
        return {}


def _save_disk_cache(entries: Dict[str, Dict[str, Any]]) -> None:
    """Write the whole cache file. Never raises."""
    path = _cache_file()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Drop the raw "endpoint" an earlier build wrote beside the models.
        clean = {
            key: (
                {k: v for k, v in entry.items() if k != "endpoint"}
                if isinstance(entry, dict)
                else entry
            )
            for key, entry in entries.items()
        }
        payload = {"version": CACHE_VERSION, "entries": clean}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to save model catalog cache: %s", e)


def _fresh(entry: Any, max_age: float) -> bool:
    """Whether a cache entry is a usable, unexpired record."""
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("models"), list):
        return False
    return (time.time() - float(entry.get("timestamp", 0) or 0)) <= max_age


def _login_of(profile: Any) -> Optional[str]:
    """Who a ChatGPT login profile is signed in as (token_storage.login_mark):
    two logins can serve different models, so a cached catalog is used only
    while the login it was fetched with is still the one signed in (#553). ""
    when none is: nothing is kept for it. None for other profiles."""
    if profile is None:
        return None
    get = profile.get if isinstance(profile, dict) else (lambda key, default=None: getattr(profile, key, default))
    try:
        provider = str(profile.get_provider() or "").lower()
    except Exception:
        provider = str(get("provider", "") or "").lower()
    if str(get("auth_type", "") or "").lower() != "oauth" and provider != "openai_responses":
        return None
    from kollabor_ai.oauth.token_storage import stored_login_mark

    name = str(get("name", "") or "")
    return stored_login_mark("openai", None if name in ("", "openai-oauth") else name)


# The login a ChatGPT login fetch asked as, which its tokens name: the stored
# login can change between reading it and loading them (#553).
_ASKED_AS: ContextVar[Optional[List[str]]] = ContextVar("model_catalog_asked_as", default=None)


def cached_provider_models(
    profile: Any, max_age: float = CACHE_TTL_SECONDS
) -> List[Dict[str, Any]]:
    """Cached catalog for a profile. Never fetches, never blocks, never raises.

    This is what synchronous callers (loadout synthesis, name resolution) read.
    Returns an empty list when nothing has been cached yet.
    """
    key = _profile_key(profile)
    if not key:
        return []

    login = _login_of(profile)
    if login == "":
        return []  # no login: no list is known to be its

    def usable(entry: Any) -> bool:
        return _fresh(entry, max_age) and entry.get("login") == login and _same_source(entry, profile)

    entry = _MEMORY_CACHE.get(key)
    if not usable(entry):
        entry = _load_disk_cache().get(key)
        if usable(entry):
            _MEMORY_CACHE[key] = entry  # type: ignore[assignment]
        else:
            return []
    return list(entry.get("models", []))  # type: ignore[union-attr]


def _store(profile: Any, models: List[Dict[str, Any]], login: Optional[str] = None) -> None:
    """Record a catalog fetched as ``login`` (_login_of) in both the memory and
    disk caches."""
    key = _profile_key(profile)
    if not key or login == "":
        return
    entry = {"timestamp": time.time(), "models": models, "login": login, **_profile_source(profile)}
    _MEMORY_CACHE[key] = entry
    if (login or "").startswith("token:"):
        return  # a digest of a token stays in this process: the cache file never holds one
    entries = _load_disk_cache()
    entries[key] = entry
    _save_disk_cache(entries)


async def get_provider_models(
    profile: Any,
    *,
    max_age: float = CACHE_TTL_SECONDS,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """Cached-then-live catalog for a profile.

    Returns the cached list when it is younger than ``max_age``; otherwise
    fetches, stores, and returns the fresh one. A failed fetch falls back to
    whatever is cached (even if stale) so a flaky network never empties a
    picker that had content a moment ago.
    """
    cached = cached_provider_models(profile, max_age=max_age)
    if cached:
        return cached

    asked: List[str] = []
    reset = _ASKED_AS.set(asked)
    try:
        models = await list_provider_models(profile, timeout=timeout)
    finally:
        _ASKED_AS.reset(reset)
    login = _login_of(profile)  # the account signed in now
    if login is not None and asked != [login]:
        # It asked as another login, or with no tokens: the list may be another login's.
        return cached_provider_models(profile, max_age=float("inf"))
    if models:
        _store(profile, models, login)
        return models

    # Fetch failed or returned nothing -- serve a stale entry if we have one.
    return cached_provider_models(profile, max_age=float("inf"))


# Providers that speak the OpenAI wire protocol, so GET {base_url}/models
# returns {"data": [{"id": ...}]}. "custom" covers xAI, Z.AI, Kimi, and local
# servers (Ollama, LM Studio, vLLM, llama.cpp).
OPENAI_COMPATIBLE_PROVIDERS = {"openai", "custom", "local"}
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


async def list_provider_models(
    profile: Any, timeout: float = DEFAULT_TIMEOUT
) -> List[Dict[str, Any]]:
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
            return await asyncio.wait_for(_openai_oauth_models(profile), timeout=timeout)
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


async def _openai_oauth_models(profile: Any = None) -> List[Dict[str, Any]]:
    """Codex backend models for an OpenAI OAuth (ChatGPT) profile, with its own login.

    The backend reports context window and supported effort levels per model,
    so the picker can show both instead of a bare "codex" tag. Two logins can
    serve different models, so each profile asks with its own, as the provider
    loads it at start (api_communication_service).
    """
    from kollabor_ai.oauth import OAuthTokenStorage
    from kollabor_ai.oauth.openai_oauth import query_codex_model_details
    from kollabor_ai.oauth.token_storage import login_mark

    storage = OAuthTokenStorage()
    name = str(getattr(profile, "name", "") or "")
    tokens = await storage.load_tokens(
        "openai", auto_refresh=True, profile_name=name if name not in ("", "openai-oauth") else None
    )
    if not tokens:
        return []
    asked = _ASKED_AS.get()
    if asked is not None:  # get_provider_models keeps the list only for this login
        fields = ("account_id", "refresh_token", "access_token")
        asked.append(login_mark({key: getattr(tokens, key, None) for key in fields}))

    details = await query_codex_model_details(tokens.access_token, tokens.account_id)

    result: List[Dict[str, Any]] = []
    for model in details:
        slug = str(model.get("slug") or "")
        if not slug:
            continue
        parts = []
        window = model.get("context_window")
        has_context_window = type(window) is int and window > 0
        if has_context_window:
            parts.append(f"{window // 1000}K ctx")
        efforts = [
            str(level.get("effort"))
            for level in model.get("supported_reasoning_levels") or []
            if isinstance(level, dict) and level.get("effort")
        ]
        if efforts:
            parts.append(f"effort: {'/'.join(efforts)}")
        catalog_entry: Dict[str, Any] = {
            "id": slug,
            "note": " • ".join(parts) or "codex",
        }
        if has_context_window:
            catalog_entry["context_window"] = window
        if efforts:
            catalog_entry["efforts"] = efforts
        result.append(catalog_entry)
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


async def _openai_compatible_models(profile: Any) -> List[Dict[str, Any]]:
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
    result: List[Dict[str, Any]] = []
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


async def _openrouter_models() -> List[Dict[str, Any]]:
    """Live OpenRouter catalog with context length + tool support notes."""
    from kollabor_ai.providers.openrouter_model_info import OpenRouterModelInfo

    models = await OpenRouterModelInfo().list_models()
    result: List[Dict[str, Any]] = []
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
# account can access. Paged with ``limit`` / ``after_id``; every page is read.
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_PAGE_LIMIT = 1000  # the API maximum; the default is 20
ANTHROPIC_MAX_PAGES = 20  # bound on a server that never stops saying has_more


async def _anthropic_models(profile: Any) -> List[Dict[str, Any]]:
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

    # The endpoint pages (20 by default): read every page, since the loadout
    # list treats this catalog as the account's whole list (#558). A listing
    # that cannot be completed is empty, never partial, so the registry still
    # seeds the picker instead of a truncated list hiding models.
    pages: List[Dict[str, Any]] = []
    after_id: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            for _ in range(ANTHROPIC_MAX_PAGES):
                params: Dict[str, Any] = {"limit": ANTHROPIC_PAGE_LIMIT}
                if after_id:
                    params["after_id"] = after_id
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                page = response.json()
                pages.append(page)
                if not page.get("has_more"):
                    break
                next_id = str(page.get("last_id") or "")
                if not next_id or next_id == after_id:
                    logger.warning("Anthropic model catalog paging did not advance")
                    return []
                after_id = next_id
            else:
                logger.warning("Anthropic model catalog has too many pages")
                return []
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

    result: List[Dict[str, Any]] = []
    for model in (m for page in pages for m in page.get("data", [])):
        model_id = str(model.get("id") or "")
        if not model_id:
            continue
        # Anthropic /v1/models does not expose context_length or a tool flag;
        # surface what we have and let the picker fall back to known metadata.
        display_name = str(model.get("display_name") or model_id)
        result.append({"id": model_id, "note": f"anthropic • {display_name}"})
    return result
