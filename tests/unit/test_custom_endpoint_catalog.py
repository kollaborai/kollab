"""A custom endpoint's /llm section lists only what that endpoint serves.

Regression tests for the bundled "custom" registry placeholders (grok-4.7 &
co.) appearing under an OpenAI-compatible endpoint profile whose live
/models catalog was cached on disk (#447). The disk cache is pointed at
tmp_path and the HTTP fetch is stubbed, so nothing here touches the
network or ~/.kollab.
"""

import asyncio
import json
import time

import pytest

from kollabor_ai import model_catalog
from kollabor_ai.loadout_manager import LoadoutManager
from kollabor_ai.model_registry import list_models_for_provider

LIVE_MODELS = [
    {"id": "glm-5.3", "note": "available"},
    {"id": "glm-5.3-air", "note": "available"},
    {"id": "glm-4.7-flash", "note": "available"},
]
LIVE_IDS = [m["id"] for m in LIVE_MODELS]
BUNDLED_CUSTOM_IDS = {"grok-4.7", "llama-4-scout", "deepseek-v4-pro"}


class _Profile:
    """Minimal LLMProfile stand-in: just the getters the managers call."""

    def __init__(self, name, provider, api_key="", endpoint="", auth_type=""):
        self.name = name
        self.provider = provider
        self.api_key = api_key
        self.endpoint = endpoint
        self.auth_type = auth_type

    def get_provider(self):
        return self.provider

    def get_api_key(self):
        return self.api_key

    def get_endpoint(self):
        return self.endpoint


class _ProfileManager:
    def __init__(self, profiles):
        self._profiles = list(profiles)

    def list_profiles(self):
        return list(self._profiles)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Disk cache in tmp_path, empty memory cache, per test."""
    monkeypatch.setattr(
        model_catalog, "_cache_file", lambda: tmp_path / "model_catalog.cache"
    )
    monkeypatch.setattr(model_catalog, "_MEMORY_CACHE", {})
    yield


def _seed_disk_cache(profile_name, models, age_seconds):
    """Write a cache entry as if a previous process had fetched it."""
    path = model_catalog._cache_file()
    entry = {
        "timestamp": time.time() - age_seconds,
        "models": models,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": model_catalog.CACHE_VERSION, "entries": {profile_name: entry}}
        ),
        encoding="utf-8",
    )


def _manager(*profiles):
    return LoadoutManager(config={}, profile_manager=_ProfileManager(profiles))


def _custom_manager():
    profile = _Profile(
        name="local",
        provider="custom",
        api_key="sk-dummy",
        endpoint="http://127.0.0.1:9/api/coding/paas/v4/chat/completions",
    )
    return _manager(profile)


def _implicit_ids(manager, profile_name):
    return [
        lo.name for lo in manager.list_loadouts() if lo.provider_profile == profile_name
    ]


def test_cold_custom_profile_lists_no_registry_placeholders():
    """(a) Cold cache, no fetch yet: no bundled 'custom' models sneak in."""
    ids = _implicit_ids(_custom_manager(), "local")
    assert ids == []


def test_after_successful_fetch_lists_exactly_live_models(monkeypatch):
    """(b) A successful refresh: exactly the endpoint's live models."""

    async def fake_fetch(profile, timeout=model_catalog.DEFAULT_TIMEOUT):
        return list(LIVE_MODELS)

    monkeypatch.setattr(model_catalog, "list_provider_models", fake_fetch)
    manager = _custom_manager()
    counts = asyncio.run(manager.refresh_catalogs())
    assert counts == {"local": 3}
    ids = _implicit_ids(manager, "local")
    assert ids == LIVE_IDS
    assert not (set(ids) & BUNDLED_CUSTOM_IDS)


def test_stale_cache_then_failed_refresh_lists_no_placeholders(monkeypatch):
    """(c) Cache older than CACHE_TTL_SECONDS + a failed refresh: nothing
    from the bundled registry appears."""
    _seed_disk_cache("local", LIVE_MODELS, model_catalog.CACHE_TTL_SECONDS + 60)

    async def failed_fetch(profile, timeout=model_catalog.DEFAULT_TIMEOUT):
        return []

    monkeypatch.setattr(model_catalog, "list_provider_models", failed_fetch)
    manager = _custom_manager()
    counts = asyncio.run(manager.refresh_catalogs())
    # get_provider_models serves a stale entry to a failed fetch by design
    # (model_catalog.py: "a flaky network never empties a picker"), so the
    # refresh counts 3 -- but list_loadouts re-reads with the default TTL,
    # so the stale models do not become loadouts.
    assert counts == {"local": 3}
    ids = _implicit_ids(manager, "local")
    assert not (set(ids) & BUNDLED_CUSTOM_IDS)
    assert "grok-4.7" not in ids


def test_fresh_disk_cache_at_process_start_lists_live_models():
    """(d) A fresh cached entry from a previous process is used as-is."""
    _seed_disk_cache("local", LIVE_MODELS, 30)
    ids = _implicit_ids(_custom_manager(), "local")
    assert ids == LIVE_IDS
    assert not (set(ids) & BUNDLED_CUSTOM_IDS)


def test_grok_never_appears_for_custom_anywhere():
    """grok-4.7 must never show for a custom profile in any state."""
    manager = _custom_manager()
    all_ids = [lo.name for lo in manager.list_loadouts()]
    assert "grok-4.7" not in all_ids


def test_other_providers_keep_registry_fallback():
    """Non-custom providers still list their bundled registry models."""
    profile = _Profile(name="api", provider="openai", api_key="sk-real")
    manager = _manager(profile)
    ids = _implicit_ids(manager, "api")
    registry_ids = [name for name, _info in list_models_for_provider("openai")]
    assert registry_ids
    assert ids == registry_ids


def test_model_picker_skips_registry_placeholders_for_custom_endpoints():
    from types import SimpleNamespace

    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    fake = SimpleNamespace(profile_manager=None, _registry_note=lambda info: "")
    custom = ModelCommandHandler._build_known_models(fake, "custom", "glm-5.3")
    assert [m["id"] for m in custom] == ["glm-5.3"]
    anthropic = ModelCommandHandler._build_known_models(
        fake, "anthropic", "claude-opus-5-5"
    )
    assert len(anthropic) > 1
