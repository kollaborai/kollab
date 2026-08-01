"""LoadoutManager: implicit/explicit resolution, persistence, and activation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor_ai import loadout_manager as lm
from kollabor_ai.loadout_manager import Loadout, LoadoutManager

# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _FakeProfile:
    """Minimal stand-in for LLMProfile -- just the getters LoadoutManager uses."""

    def __init__(self, name, provider, api_key="", endpoint="", auth_type=""):
        self.name = name
        self.provider = provider
        self.api_key = api_key
        self.endpoint = endpoint
        self.auth_type = auth_type
        # mutated by _FakeProfileManager.update_profile, inspected by tests
        self.model = ""
        self.temperature = None
        self.effort = ""
        self.max_tokens = None

    def get_provider(self):
        return self.provider

    def get_api_key(self):
        return self.api_key

    def get_endpoint(self):
        return self.endpoint


class _FakeProfileManager:
    """Minimal stand-in for ProfileManager. Records calls for assertions."""

    def __init__(self, profiles=None, config=None, fail_update_for=None):
        self._profiles = {p.name: p for p in (profiles or [])}
        self.config = config
        self.update_calls = []
        self.activate_calls = []
        self.active_name = None
        self._fail_update_for = set(fail_update_for or [])

    def list_profiles(self):
        return list(self._profiles.values())

    def get_profile(self, name):
        return self._profiles.get(name)

    def update_profile(self, name, **kwargs):
        self.update_calls.append((name, dict(kwargs)))
        if name in self._fail_update_for or name not in self._profiles:
            return False
        profile = self._profiles[name]
        for key, value in kwargs.items():
            if key != "save_to_config":
                setattr(profile, key, value)
        return True

    def set_active_profile(self, name, persist=True):
        self.activate_calls.append(name)
        if name not in self._profiles:
            return False
        self.active_name = name
        return True

    def get_active_profile(self):
        return self._profiles.get(self.active_name)


class _MemoryConfig:
    """In-memory config fake: mirrors ConfigService's get()/save_key() contract.

    Same shape as _MemoryConfigService in test_mcp_altview_manager.py, extended
    to support nested initial data (loadouts are stored as a nested dict).
    """

    def __init__(self, initial=None):
        self.values = dict(initial or {})
        self.saved = []

    def get(self, key_path, default=None):
        current = self.values
        for part in key_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def save_key(self, key_path, value, save_target=None):
        current = self.values
        parts = key_path.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
        self.saved.append((key_path, value, save_target))
        return True


class _FileBackedConfig:
    """Config fake whose save_key() really persists JSON under tmp_path.

    Used only by the persist/reload round-trip test, which needs a genuine
    file write -- everything else uses the pure in-memory _MemoryConfig.
    Mirrors ConfigService.get()/save_key() (dot-path get, patch-one-key
    read-modify-write) without pulling in the full ConfigService /
    ConfigLoader / plugin-discovery machinery.
    """

    def __init__(self, path):
        self.path = path

    def _read(self):
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def get(self, key_path, default=None):
        current = self._read()
        for part in key_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def save_key(self, key_path, value, save_target=None):
        data = self._read()
        current = data
        parts = key_path.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return True


def _fake_list_models_for_provider(provider):
    catalog = {
        "openai": [
            ("gpt-5.6", {"provider": "openai"}),
            ("gpt-5.6-terra", {"provider": "openai"}),
        ],
        "anthropic": [
            ("claude-sonnet-5", {"provider": "anthropic"}),
        ],
    }
    return catalog.get(provider, [])


@pytest.fixture(autouse=True)
def _patch_model_registry(monkeypatch):
    """Isolate every test from the real bundled models.json."""
    monkeypatch.setattr(lm, "list_models_for_provider", _fake_list_models_for_provider)


# ----------------------------------------------------------------------
# provider_profiles()
# ----------------------------------------------------------------------


def test_provider_profiles_filter_rules():
    profiles = [
        _FakeProfile("has-key", "openai", api_key="sk-123"),
        _FakeProfile("has-oauth", "openai_responses", auth_type="oauth"),
        _FakeProfile(
            "custom-with-endpoint", "custom", endpoint="http://localhost:1234"
        ),
        _FakeProfile("local-with-endpoint", "local", endpoint="http://localhost:5678"),
        _FakeProfile("custom-no-endpoint", "custom"),
        _FakeProfile("configured-no-key", "openai"),
        _FakeProfile("no-provider", ""),
    ]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    names = [p.name for p in manager.provider_profiles()]
    assert names == [
        "has-key",
        "has-oauth",
        "custom-with-endpoint",
        "local-with-endpoint",
    ]


# ----------------------------------------------------------------------
# list_loadouts(): implicit synthesis, dedup, shadowing
# ----------------------------------------------------------------------


def test_implicit_synthesis_aliases_oauth_transport_to_openai_catalog():
    # The ChatGPT OAuth transport stores provider "openai_responses"; the
    # registry tags those models "openai". The alias keeps an OAuth-only
    # setup from synthesizing zero implicit loadouts.
    profiles = [_FakeProfile("openai-oauth", "openai_responses", auth_type="oauth")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    by_name = {lo.name: lo for lo in manager.list_loadouts()}

    assert set(by_name) == {"gpt-5.6", "gpt-5.6-terra"}
    assert by_name["gpt-5.6"].provider_profile == "openai-oauth"
    assert by_name["gpt-5.6"].implicit


def test_implicit_synthesis_dedup_first_profile_wins():
    profiles = [
        _FakeProfile("openai-work", "openai", api_key="key-1"),
        _FakeProfile("openai-personal", "openai", api_key="key-2"),
    ]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    loadouts = manager.list_loadouts()
    by_name = {lo.name: lo for lo in loadouts}

    assert set(by_name) == {"gpt-5.6", "gpt-5.6-terra"}
    assert all(lo.implicit for lo in loadouts)
    # first provider profile (openai-work) wins for every model name
    assert by_name["gpt-5.6"].provider_profile == "openai-work"
    assert by_name["gpt-5.6-terra"].provider_profile == "openai-work"


def test_explicit_loadout_shadows_implicit_of_same_name():
    profiles = [_FakeProfile("openai-work", "openai", api_key="key-1")]
    config = _MemoryConfig(
        {
            "kollabor": {
                "llm": {
                    "loadouts": {
                        "gpt-5.6": {
                            "provider_profile": "openai-work",
                            "model": "gpt-5.6",
                            "temperature": 0.2,
                        }
                    }
                }
            }
        }
    )
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)

    loadouts = manager.list_loadouts()
    matches = [lo for lo in loadouts if lo.name == "gpt-5.6"]

    assert len(matches) == 1
    assert matches[0].implicit is False
    assert matches[0].temperature == 0.2
    # the shadowed implicit no longer appears as a second entry
    assert [lo.name for lo in loadouts].count("gpt-5.6") == 1


def test_list_loadouts_explicit_sorted_before_implicit_in_registry_order():
    profiles = [_FakeProfile("openai-work", "openai", api_key="key-1")]
    config = _MemoryConfig(
        {
            "kollabor": {
                "llm": {
                    "loadouts": {
                        "zeta": {"provider_profile": "openai-work", "model": "gpt-5.6"},
                        "alpha": {
                            "provider_profile": "openai-work",
                            "model": "gpt-5.6",
                        },
                    }
                }
            }
        }
    )
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)

    names = [lo.name for lo in manager.list_loadouts()]
    # explicit: alphabetical ("alpha" before "zeta"); implicit: registry order
    assert names == ["alpha", "zeta", "gpt-5.6", "gpt-5.6-terra"]


def test_malformed_explicit_entry_is_skipped():
    profiles = [_FakeProfile("openai-work", "openai", api_key="key-1")]
    config = _MemoryConfig(
        {
            "kollabor": {
                "llm": {
                    "loadouts": {
                        "broken": {"description": "missing provider_profile/model"},
                    }
                }
            }
        }
    )
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)

    assert manager.get("broken") is None


# ----------------------------------------------------------------------
# get()
# ----------------------------------------------------------------------


def test_get_exact_name_only():
    profiles = [_FakeProfile("openai-work", "openai", api_key="key-1")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    assert manager.get("gpt-5.6").model == "gpt-5.6"
    assert manager.get("gpt") is None
    assert manager.get("GPT-5.6") is None  # get() is exact, not case-insensitive


# ----------------------------------------------------------------------
# resolve()
# ----------------------------------------------------------------------


def _manager_with_mixed_loadouts():
    profiles = [_FakeProfile("openai-work", "openai", api_key="key-1")]
    config = _MemoryConfig(
        {
            "kollabor": {
                "llm": {
                    "loadouts": {
                        "fast": {
                            "provider_profile": "openai-work",
                            "model": "gpt-5.6-terra",
                        },
                    }
                }
            }
        }
    )
    return LoadoutManager(_FakeProfileManager(profiles), config=config)


def test_resolve_empty_query():
    manager = _manager_with_mixed_loadouts()
    assert manager.resolve("") == (None, [])


def test_resolve_exact_explicit_match():
    manager = _manager_with_mixed_loadouts()
    loadout, suggestions = manager.resolve("fast")
    assert loadout.name == "fast"
    assert loadout.implicit is False
    assert suggestions == []


def test_resolve_exact_implicit_match():
    manager = _manager_with_mixed_loadouts()
    loadout, suggestions = manager.resolve("gpt-5.6")
    assert loadout.name == "gpt-5.6"
    assert loadout.implicit is True
    assert suggestions == []


def test_resolve_case_insensitive_exact():
    manager = _manager_with_mixed_loadouts()
    loadout, suggestions = manager.resolve("FAST")
    assert loadout.name == "fast"
    assert suggestions == []


def test_resolve_unique_substring_match():
    manager = _manager_with_mixed_loadouts()
    loadout, suggestions = manager.resolve("terra")
    assert loadout.name == "gpt-5.6-terra"
    assert suggestions == []


def test_resolve_ambiguous_substring_returns_suggestions():
    manager = _manager_with_mixed_loadouts()
    # "gpt-5.6" alone is an exact implicit match (tested separately), so use
    # a substring that hits both gpt-5.6 and gpt-5.6-terra without being an
    # exact name for either.
    loadout, suggestions = manager.resolve("gpt-5")
    assert loadout is None
    assert set(suggestions) == {"gpt-5.6", "gpt-5.6-terra"}
    assert len(suggestions) <= 8


def test_resolve_no_containing_match_falls_back_to_closest_names():
    manager = _manager_with_mixed_loadouts()
    loadout, suggestions = manager.resolve(
        "gpt-5.7"
    )  # close to gpt-5.6, no substring hit
    assert loadout is None
    assert "gpt-5.6" in suggestions
    assert len(suggestions) <= 8


def test_resolve_no_match_anywhere_returns_empty_suggestions():
    manager = _manager_with_mixed_loadouts()
    loadout, suggestions = manager.resolve("zzzzzzzz-not-close-to-anything")
    assert loadout is None
    assert suggestions == []


# ----------------------------------------------------------------------
# create()
# ----------------------------------------------------------------------


def test_create_persists_only_nondefault_fields():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    config = _MemoryConfig()
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)

    assert manager.create("bare", "work", "gpt-5.6") is True
    assert config.get("kollabor.llm.loadouts.bare") == {
        "provider_profile": "work",
        "model": "gpt-5.6",
    }

    assert (
        manager.create(
            "full",
            "work",
            "gpt-5.6",
            temperature=0.3,
            effort="high",
            max_tokens=4096,
            description="my preset",
        )
        is True
    )
    assert config.get("kollabor.llm.loadouts.full") == {
        "provider_profile": "work",
        "model": "gpt-5.6",
        "temperature": 0.3,
        "effort": "high",
        "max_tokens": 4096,
        "description": "my preset",
    }


def test_create_rejects_empty_name():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    assert manager.create("", "work", "gpt-5.6") is False
    assert manager.create("   ", "work", "gpt-5.6") is False


def test_create_rejects_duplicate_explicit_name():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    assert manager.create("fast", "work", "gpt-5.6") is True
    assert manager.create("fast", "work", "gpt-5.6-terra") is False
    # first write wins -- not overwritten by the rejected second create
    assert manager.get("fast").model == "gpt-5.6"


def test_create_rejects_unknown_provider_profile():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    config = _MemoryConfig()
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)

    assert manager.create("fast", "does-not-exist", "gpt-5.6") is False
    assert config.get("kollabor.llm.loadouts.fast") is None


def test_create_dotted_name_does_not_corrupt_sibling_loadouts():
    """A loadout named after a dotted model (e.g. shadowing gpt-5.6) must be
    stored as a literal key, not split into nested dicts by a dotted path."""
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    config = _MemoryConfig()
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)

    assert manager.create("other", "work", "gpt-5.6-terra") is True
    assert manager.create("gpt-5.6", "work", "gpt-5.6", temperature=0.1) is True

    loadouts = config.get("kollabor.llm.loadouts")
    assert set(loadouts) == {"other", "gpt-5.6"}
    assert loadouts["gpt-5.6"]["temperature"] == 0.1
    assert loadouts["other"]["model"] == "gpt-5.6-terra"


# ----------------------------------------------------------------------
# create() / list_loadouts() persist + reload round-trip (real tmp_path file)
# ----------------------------------------------------------------------


def test_create_persist_reload_round_trip_on_disk(tmp_path):
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    config_path = tmp_path / "config.json"
    file_config = _FileBackedConfig(config_path)
    manager = LoadoutManager(_FakeProfileManager(profiles), config=file_config)

    assert (
        manager.create("fast", "work", "gpt-5.6-terra", temperature=0.4, effort="high")
        is True
    )

    # prove it actually hit disk under tmp_path
    on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert on_disk["kollabor"]["llm"]["loadouts"]["fast"] == {
        "provider_profile": "work",
        "model": "gpt-5.6-terra",
        "temperature": 0.4,
        "effort": "high",
    }

    # a fresh manager/config pointed at the same file re-reads it correctly
    reloaded_manager = LoadoutManager(
        _FakeProfileManager(profiles), config=_FileBackedConfig(config_path)
    )
    reloaded = reloaded_manager.get("fast")
    assert reloaded is not None
    assert reloaded.provider_profile == "work"
    assert reloaded.model == "gpt-5.6-terra"
    assert reloaded.temperature == 0.4
    assert reloaded.effort == "high"
    assert reloaded.implicit is False


# ----------------------------------------------------------------------
# update()
# ----------------------------------------------------------------------


def test_update_explicit_loadout():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    config = _MemoryConfig()
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)
    manager.create("fast", "work", "gpt-5.6")

    assert manager.update("fast", temperature=0.9) is True
    updated = manager.get("fast")
    assert updated.temperature == 0.9
    assert updated.provider_profile == "work"  # untouched fields preserved
    assert updated.model == "gpt-5.6"


def test_update_refuses_implicit_loadout():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    assert manager.update("gpt-5.6", temperature=0.9) is False


def test_update_refuses_unknown_loadout():
    manager = LoadoutManager(_FakeProfileManager([]), config=_MemoryConfig())
    assert manager.update("nope", temperature=0.9) is False


def test_update_rejects_unknown_field():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())
    manager.create("fast", "work", "gpt-5.6")

    assert manager.update("fast", bogus=1) is False
    assert manager.get("fast").model == "gpt-5.6"  # unchanged


def test_update_rejects_unknown_provider_profile():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())
    manager.create("fast", "work", "gpt-5.6")

    assert manager.update("fast", provider_profile="ghost") is False
    assert manager.get("fast").provider_profile == "work"  # unchanged


# ----------------------------------------------------------------------
# delete()
# ----------------------------------------------------------------------


def test_delete_refuses_implicit():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    manager = LoadoutManager(_FakeProfileManager(profiles), config=_MemoryConfig())

    assert manager.delete("gpt-5.6") is False
    assert manager.get("gpt-5.6") is not None  # still there


def test_delete_refuses_unknown():
    manager = LoadoutManager(_FakeProfileManager([]), config=_MemoryConfig())
    assert manager.delete("nope") is False


def test_delete_explicit_preserves_siblings():
    profiles = [_FakeProfile("work", "openai", api_key="key-1")]
    config = _MemoryConfig()
    manager = LoadoutManager(_FakeProfileManager(profiles), config=config)
    manager.create("fast", "work", "gpt-5.6")
    manager.create("careful", "work", "gpt-5.6-terra", temperature=0.1)

    assert manager.delete("fast") is True
    assert manager.get("fast") is None
    sibling = manager.get("careful")
    assert sibling is not None
    assert sibling.temperature == 0.1


# ----------------------------------------------------------------------
# activate()
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_unresolvable_name_returns_none_without_touching_profile():
    pm = _FakeProfileManager([_FakeProfile("work", "openai", api_key="key-1")])
    manager = LoadoutManager(pm, config=_MemoryConfig())

    result = await manager.activate("totally-unknown-loadout")

    assert result is None
    assert pm.update_calls == []


@pytest.mark.asyncio
async def test_activate_passes_only_set_fields_to_update_profile():
    pm = _FakeProfileManager([_FakeProfile("work", "openai", api_key="key-1")])
    manager = LoadoutManager(pm, config=_MemoryConfig())

    result = await manager.activate("gpt-5.6")  # implicit -- no overrides set

    assert result is not None
    assert pm.update_calls == [("work", {"model": "gpt-5.6", "save_to_config": True})]


@pytest.mark.asyncio
async def test_activate_passes_all_overrides_when_set():
    pm = _FakeProfileManager([_FakeProfile("work", "openai", api_key="key-1")])
    manager = LoadoutManager(pm, config=_MemoryConfig())
    loadout = Loadout(
        name="custom",
        provider_profile="work",
        model="gpt-5.6-terra",
        temperature=0.5,
        effort="xhigh",
        max_tokens=2048,
    )

    result = await manager.activate(loadout)

    assert result is loadout
    assert pm.update_calls == [
        (
            "work",
            {
                "model": "gpt-5.6-terra",
                "save_to_config": True,
                "temperature": 0.5,
                "effort": "xhigh",
                "max_tokens": 2048,
            },
        )
    ]


@pytest.mark.asyncio
async def test_activate_uses_state_service_when_present_and_skips_fallback():
    pm = _FakeProfileManager([_FakeProfile("work", "openai", api_key="key-1")])
    manager = LoadoutManager(pm, config=_MemoryConfig())

    state_service = SimpleNamespace(set_active_profile=AsyncMock())
    llm_service = SimpleNamespace(
        api_service=SimpleNamespace(reinitialize_provider=AsyncMock()),
        _load_native_tools=AsyncMock(),
    )
    services = {"state_service": state_service, "llm_service": llm_service}
    event_bus = SimpleNamespace(get_service=lambda name: services.get(name))

    result = await manager.activate("gpt-5.6", event_bus=event_bus)

    assert result is not None
    state_service.set_active_profile.assert_awaited_once_with(
        "work", reload_profile=True
    )
    # daemon-owned path -- local profile_manager/llm_service must not also fire
    assert pm.activate_calls == []
    llm_service.api_service.reinitialize_provider.assert_not_awaited()
    llm_service._load_native_tools.assert_not_awaited()


@pytest.mark.asyncio
async def test_activate_falls_back_to_llm_service_when_no_state_service():
    pm = _FakeProfileManager([_FakeProfile("work", "openai", api_key="key-1")])
    manager = LoadoutManager(pm, config=_MemoryConfig())

    llm_service = SimpleNamespace(
        api_service=SimpleNamespace(reinitialize_provider=AsyncMock()),
        _load_native_tools=AsyncMock(),
    )
    services = {"llm_service": llm_service}
    event_bus = SimpleNamespace(get_service=lambda name: services.get(name))

    result = await manager.activate("gpt-5.6", event_bus=event_bus)

    assert result is not None
    assert pm.activate_calls == ["work"]
    assert pm.active_name == "work"
    llm_service.api_service.reinitialize_provider.assert_awaited_once()
    llm_service._load_native_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_activate_with_no_event_bus_still_activates_directly():
    pm = _FakeProfileManager([_FakeProfile("work", "openai", api_key="key-1")])
    manager = LoadoutManager(pm, config=_MemoryConfig())

    result = await manager.activate("gpt-5.6")

    assert result is not None
    assert pm.activate_calls == ["work"]
    assert pm.active_name == "work"


@pytest.mark.asyncio
async def test_activate_returns_none_when_profile_update_fails():
    pm = _FakeProfileManager(
        [_FakeProfile("work", "openai", api_key="key-1")],
        fail_update_for={"work"},
    )
    manager = LoadoutManager(pm, config=_MemoryConfig())

    result = await manager.activate("gpt-5.6")

    assert result is None
    assert pm.activate_calls == []  # never reached activation


@pytest.mark.asyncio
async def test_activate_state_service_exception_falls_back():
    pm = _FakeProfileManager([_FakeProfile("work", "openai", api_key="key-1")])
    manager = LoadoutManager(pm, config=_MemoryConfig())

    async def _boom(*_a, **_k):
        raise RuntimeError("daemon unreachable")

    state_service = SimpleNamespace(set_active_profile=_boom)
    event_bus = SimpleNamespace(
        get_service=lambda name: {"state_service": state_service}.get(name)
    )

    result = await manager.activate("gpt-5.6", event_bus=event_bus)

    assert result is not None
    assert pm.activate_calls == ["work"]  # fell back to direct activation


# ----------------------------------------------------------------------
# __init__ config resolution
# ----------------------------------------------------------------------


def test_init_defaults_config_from_profile_manager():
    config = _MemoryConfig()
    pm = _FakeProfileManager([], config=config)

    manager = LoadoutManager(pm)

    assert manager.config is config


def test_init_explicit_config_overrides_profile_manager_config():
    pm_config = _MemoryConfig()
    explicit_config = _MemoryConfig()
    pm = _FakeProfileManager([], config=pm_config)

    manager = LoadoutManager(pm, config=explicit_config)

    assert manager.config is explicit_config


def test_writes_fail_gracefully_with_no_config():
    class _NoConfigProfileManager:
        def list_profiles(self):
            return []

        def get_profile(self, name):
            return None

    manager = LoadoutManager(_NoConfigProfileManager())

    assert manager.config is None
    assert manager.create("fast", "work", "gpt-5.6") is False
    assert manager.delete("fast") is False
