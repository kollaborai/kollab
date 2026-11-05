"""Live-catalog merge for /llm loadouts.

Regression cover for the defect where a provider with zero bundled
``models.json`` entries (OpenRouter is a proxy; its catalog is live-only)
contributed zero implicit loadouts and was then *skipped silently* by the
picker -- rendering identically to a provider that was never configured.

See docs/specs/llm-loadout-live-catalog.md and llm-loadouts-contract.md C3.5/C3.6.
"""

import asyncio
import json
import time
from unittest.mock import patch

import pytest

from kollabor_ai import model_catalog as mc
from kollabor_ai.loadout_manager import LoadoutManager
from kollabor_ai.model_registry import list_models_for_provider


class _Profile:
    """A configured provider profile with no bundled registry models."""

    def __init__(self, name="openrouter", provider="openrouter", model=""):
        self.name = name
        self._provider = provider
        self._model = model
        self.auth_type = ""

    def get_provider(self):
        return self._provider

    def get_api_key(self):
        return "sk-test"

    def get_endpoint(self):
        return ""

    def get_model(self):
        return self._model


class _ProfileManager:
    config = None

    def __init__(self, profiles):
        self._profiles = profiles

    def list_profiles(self):
        return list(self._profiles)

    def get_profile(self, name):
        return next((p for p in self._profiles if p.name == name), None)

    def get_active_profile(self):
        return self._profiles[0] if self._profiles else None


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path):
    """Keep every test off the real ~/.kollab cache."""
    mc._MEMORY_CACHE.clear()
    with patch.object(mc, "_cache_file", lambda: tmp_path / "model_catalog.cache"):
        yield
    mc._MEMORY_CACHE.clear()


def _seed(profile, models):
    mc._store(profile, [{"id": m, "note": ""} for m in models])


# -- the original bug -------------------------------------------------------


def test_provider_with_empty_registry_yields_no_loadouts_when_cold():
    """Baseline: this is the shipped behavior that made OpenRouter invisible."""
    manager = LoadoutManager(_ProfileManager([_Profile()]))
    assert manager.list_loadouts() == []


def test_cached_catalog_models_become_implicit_loadouts():
    profile = _Profile()
    manager = LoadoutManager(_ProfileManager([profile]))
    _seed(profile, ["anthropic/claude-opus-4.5", "x-ai/grok-4.5"])

    names = [lo.name for lo in manager.list_loadouts()]
    assert names == ["anthropic/claude-opus-4.5", "x-ai/grok-4.5"]


def test_catalog_loadout_carries_its_provider_profile():
    """activate() switches provider via this field -- it must be wired."""
    profile = _Profile()
    manager = LoadoutManager(_ProfileManager([profile]))
    _seed(profile, ["anthropic/claude-opus-4.5"])

    loadout = manager.list_loadouts()[0]
    assert loadout.provider_profile == "openrouter"
    assert loadout.implicit is True


def test_catalog_model_is_resolvable_by_name():
    """`/llm <name>` and `kollab --llm <name>` both route through resolve()."""
    profile = _Profile()
    manager = LoadoutManager(_ProfileManager([profile]))
    _seed(profile, ["anthropic/claude-opus-4.5", "x-ai/grok-4.5"])

    exact, _ = manager.resolve("anthropic/claude-opus-4.5")
    assert exact is not None and exact.provider_profile == "openrouter"

    substring, _ = manager.resolve("grok")
    assert substring is not None and substring.name == "x-ai/grok-4.5"


# -- merge semantics --------------------------------------------------------


def test_live_catalog_with_entries_replaces_the_registry():
    """Cold, openai lists its registry; once the catalog has entries it is the
    whole list (#558). The registry is a cold-cache seed, not a supplement."""
    openai = _Profile(name="openai-oauth", provider="openai_responses")
    manager = LoadoutManager(_ProfileManager([openai]))
    assert manager.list_loadouts(), "openai should have bundled registry models"

    _seed(openai, ["gpt-brand-new"])
    assert [lo.name for lo in manager.list_loadouts()] == ["gpt-brand-new"]


def test_catalog_duplicate_of_registry_model_is_not_listed_twice():
    openai = _Profile(name="openai-oauth", provider="openai_responses")
    manager = LoadoutManager(_ProfileManager([openai]))
    existing = manager.list_loadouts()[0].name

    _seed(openai, [existing])
    names = [lo.name for lo in manager.list_loadouts()]
    assert names.count(existing) == 1


def test_explicit_loadout_still_shadows_a_catalog_model():
    profile = _Profile()

    class _Config:
        def get(self, key, default=None):
            return {
                "mine": {"provider_profile": "openrouter", "model": "x-ai/grok-4.5"}
            }

    manager = LoadoutManager(_ProfileManager([profile]), config=_Config())
    _seed(profile, ["mine", "x-ai/grok-4.5"])

    loadouts = manager.list_loadouts()
    mine = [lo for lo in loadouts if lo.name == "mine"]
    assert len(mine) == 1 and mine[0].implicit is False


def test_unconfigured_provider_contributes_nothing():
    """provider_profiles() gates on credentials; a cached catalog can't bypass it."""

    class _NoCreds(_Profile):
        def get_api_key(self):
            return ""

    profile = _NoCreds()
    manager = LoadoutManager(_ProfileManager([profile]))
    _seed(profile, ["anthropic/claude-opus-4.5"])
    assert manager.list_loadouts() == []


# -- #558: a live catalog with entries is the list ---------------------------


def test_live_catalog_hides_registry_models_it_omits():
    """An account whose catalog lists only gpt-5.6-luna must not be offered
    gpt-6-luna: picking it fails at the provider (#558)."""
    profile = _Profile(name="openai-api", provider="openai", model="gpt-5.6-luna")
    manager = LoadoutManager(_ProfileManager([profile]))
    registry = [name for name, _info in list_models_for_provider("openai")]
    assert "gpt-6-luna" in registry, "precondition: the registry offers gpt-6-luna"
    _seed(profile, ["gpt-5.6-luna"])

    assert [lo.name for lo in manager.list_loadouts()] == ["gpt-5.6-luna"]
    assert manager.resolve("gpt-6-luna")[0] is None


def test_profile_own_model_stays_listed_when_catalog_omits_it():
    profile = _Profile(
        name="openai-api", provider="openai", model="gpt-private-preview"
    )
    manager = LoadoutManager(_ProfileManager([profile]))
    _seed(profile, ["gpt-5.6-luna"])

    names = [lo.name for lo in manager.list_loadouts()]
    assert names == ["gpt-5.6-luna", "gpt-private-preview"]


def test_stale_catalog_is_still_the_provider_list():
    """A catalog cached more than the 1 h TTL ago is still the provider's list.

    Launch paths (``kollab --llm``, the startup default, ``/llm <name>``) never
    refresh it, so reading it with the TTL brought the registry back and
    offered gpt-6-luna again (#558)."""
    profile = _Profile(name="openai-api", provider="openai", model="gpt-5.6-luna")
    manager = LoadoutManager(_ProfileManager([profile]))
    with patch.object(mc.time, "time", return_value=time.time() - 86400):
        _seed(profile, ["gpt-5.6-luna"])
    mc._MEMORY_CACHE.clear()  # a fresh process reads only the disk cache

    assert [lo.name for lo in manager.list_loadouts()] == ["gpt-5.6-luna"]
    assert manager.resolve("gpt-6-luna")[0] is None


def test_catalog_cached_under_another_provider_is_not_this_profiles_list():
    """A profile edited to a new provider keeps its name, and the cache is keyed
    by name. The catalog it had under the old provider must not become its
    list under the new one: a Claude id would be sent to Gemini (#558)."""
    profile = _Profile(name="work", provider="anthropic", model="claude-opus-4-1")
    manager = LoadoutManager(_ProfileManager([profile]))
    with patch.object(mc.time, "time", return_value=time.time() - 7200):
        _seed(profile, ["claude-sonnet-4-5", "claude-opus-4-1"])
    mc._MEMORY_CACHE.clear()  # a fresh process reads only the disk cache

    profile._provider = "gemini"  # edited in place: same name, new provider
    profile._model = "gemini-2.5-pro"

    names = [lo.name for lo in manager.list_loadouts()]
    assert not any(n.startswith("claude-") for n in names)
    assert set(names) >= {m for m, _ in list_models_for_provider("gemini")}
    assert manager.resolve("claude-opus-4-1")[0] is None
    assert manager.resolve("gemini-2.5-flash")[0] is not None

    # Gemini has no listing API, so a refresh cannot repair it either.
    assert asyncio.run(manager.refresh_catalogs()) == {"work": 0}
    assert manager.resolve("claude-opus-4-1")[0] is None


def test_catalog_cached_for_another_endpoint_is_not_this_profiles_list():
    """Same name and provider, different endpoint: the old server's catalog is
    not the new server's list."""

    class _Endpoint(_Profile):
        endpoint = "http://localhost:1234/v1"

        def get_endpoint(self):
            return self.endpoint

    profile = _Endpoint(name="local", provider="custom")
    manager = LoadoutManager(_ProfileManager([profile]))
    _seed(profile, ["old-server-model"])
    mc._MEMORY_CACHE.clear()
    assert [lo.name for lo in manager.list_loadouts()] == ["old-server-model"]

    profile.endpoint = "http://localhost:8080/v1"  # edited in place
    assert manager.list_loadouts() == []

    profile.endpoint = "http://localhost:1234/v1/"  # trailing slash: same server
    assert [lo.name for lo in manager.list_loadouts()] == ["old-server-model"]


def _anthropic_pages(monkeypatch, pages):
    """Serve ``pages`` from a mocked GET /v1/models, keyed by ``after_id``."""

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            return _Resp(pages[(params or {}).get("after_id")])

    monkeypatch.setattr(mc.httpx, "AsyncClient", _Client)


def test_truncated_anthropic_page_does_not_hide_registry_models(monkeypatch):
    """The first /v1/models page is not the whole list (#558 review).

    claude-haiku-4-5 sits on page 2; with only page 1 read, the catalog would
    be the whole list and the model would drop out of /llm."""
    profile = _Profile(name="anthropic-api", provider="anthropic")
    manager = LoadoutManager(_ProfileManager([profile]))
    registry = [name for name, _info in list_models_for_provider("anthropic")]
    assert "claude-haiku-4-5" in registry, "precondition: registry has the model"
    _anthropic_pages(
        monkeypatch,
        {
            None: {
                "data": [{"id": f"claude-page1-{i}"} for i in range(20)],
                "has_more": True,
                "last_id": "claude-page1-19",
            },
            "claude-page1-19": {
                "data": [{"id": "claude-haiku-4-5"}],
                "has_more": False,
                "last_id": "claude-haiku-4-5",
            },
        },
    )

    asyncio.run(mc.get_provider_models(profile))

    assert "claude-haiku-4-5" in [lo.name for lo in manager.list_loadouts()]
    assert manager.resolve("claude-haiku-4-5")[0] is not None


# -- failure modes ----------------------------------------------------------


def test_catalog_lookup_failure_degrades_to_registry(monkeypatch):
    """Invariant I6: a broken catalog never empties the list."""
    openai = _Profile(name="openai-oauth", provider="openai_responses")
    manager = LoadoutManager(_ProfileManager([openai]))

    def _boom(*_a, **_k):
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr(mc, "cached_provider_models", _boom)
    assert manager.list_loadouts(), "registry models must survive a catalog failure"


def test_refresh_catalogs_reports_zero_for_a_failing_provider():
    profile = _Profile()
    manager = LoadoutManager(_ProfileManager([profile]))

    async def _fail(*_a, **_k):
        raise RuntimeError("network down")

    with patch.object(mc, "get_provider_models", _fail):
        counts = asyncio.run(manager.refresh_catalogs())
    assert counts == {"openrouter": 0}


def test_refresh_catalogs_counts_each_provider():
    a = _Profile(name="openrouter")
    b = _Profile(name="openai-oauth", provider="openai_responses")
    manager = LoadoutManager(_ProfileManager([a, b]))

    async def _models(profile, **_k):
        return [{"id": f"{profile.name}/m1", "note": ""}]

    with patch.object(mc, "get_provider_models", _models):
        counts = asyncio.run(manager.refresh_catalogs())
    assert counts == {"openrouter": 1, "openai-oauth": 1}


# -- disk cache -------------------------------------------------------------


def test_cache_survives_process_restart(tmp_path):
    profile = _Profile()
    _seed(profile, ["a/b"])
    mc._MEMORY_CACHE.clear()  # simulate a fresh process
    assert [m["id"] for m in mc.cached_provider_models(profile)] == ["a/b"]


def test_expired_cache_is_ignored():
    profile = _Profile()
    _seed(profile, ["a/b"])
    assert mc.cached_provider_models(profile, max_age=-1) == []


def test_version_mismatch_discards_cache(tmp_path):
    profile = _Profile()
    _seed(profile, ["a/b"])

    path = tmp_path / "model_catalog.cache"
    data = json.loads(path.read_text())
    data["version"] = mc.CACHE_VERSION + 1
    path.write_text(json.dumps(data))

    mc._MEMORY_CACHE.clear()
    assert mc.cached_provider_models(profile) == []


def test_corrupt_cache_file_is_survivable(tmp_path):
    (tmp_path / "model_catalog.cache").write_text("{not json")
    assert mc.cached_provider_models(_Profile()) == []


def test_get_provider_models_serves_stale_entry_when_fetch_fails():
    """A flaky network must not empty a picker that had content."""
    profile = _Profile()
    _seed(profile, ["a/b"])

    async def _empty(*_a, **_k):
        return []

    with patch.object(mc, "list_provider_models", _empty):
        # max_age=-1 forces a refetch; the fetch yields nothing.
        got = asyncio.run(mc.get_provider_models(profile, max_age=-1))
    assert [m["id"] for m in got] == ["a/b"]


def test_get_provider_models_caches_a_successful_fetch():
    profile = _Profile()
    calls = []

    async def _once(*_a, **_k):
        calls.append(1)
        return [{"id": "fresh/model", "note": ""}]

    with patch.object(mc, "list_provider_models", _once):
        first = asyncio.run(mc.get_provider_models(profile))
        second = asyncio.run(mc.get_provider_models(profile))

    assert len(calls) == 1, "second call must be served from cache"
    assert [m["id"] for m in second] == ["fresh/model"]
    assert first == second


def test_profiles_are_cached_independently():
    a = _Profile(name="openrouter")
    b = _Profile(name="openai-oauth", provider="openai_responses")
    _seed(a, ["a/one"])
    _seed(b, ["b/one"])

    assert [m["id"] for m in mc.cached_provider_models(a)] == ["a/one"]
    assert [m["id"] for m in mc.cached_provider_models(b)] == ["b/one"]


def test_stale_memory_entry_falls_back_to_disk():
    profile = _Profile()
    _seed(profile, ["a/b"])
    mc._MEMORY_CACHE["openrouter"] = {"timestamp": 0, "models": []}
    assert [m["id"] for m in mc.cached_provider_models(profile)] == ["a/b"]


def test_timestamp_is_recorded_on_store():
    profile = _Profile()
    before = time.time()
    _seed(profile, ["a/b"])
    assert mc._MEMORY_CACHE["openrouter"]["timestamp"] >= before
