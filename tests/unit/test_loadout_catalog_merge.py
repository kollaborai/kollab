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


class _Profile:
    """A configured provider profile with no bundled registry models."""

    def __init__(self, name="openrouter", provider="openrouter"):
        self.name = name
        self._provider = provider
        self.auth_type = ""

    def get_provider(self):
        return self._provider

    def get_api_key(self):
        return "sk-test"

    def get_endpoint(self):
        return ""

    def get_model(self):
        return ""


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


def test_registry_models_still_listed_before_catalog_models():
    """The catalog augments the registry; it does not replace it."""
    openai = _Profile(name="openai-oauth", provider="openai_responses")
    manager = LoadoutManager(_ProfileManager([openai]))
    registry_names = [lo.name for lo in manager.list_loadouts()]
    assert registry_names, "openai should have bundled registry models"

    _seed(openai, ["gpt-brand-new"])
    merged = [lo.name for lo in manager.list_loadouts()]
    assert merged[: len(registry_names)] == registry_names
    assert merged[-1] == "gpt-brand-new"


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
