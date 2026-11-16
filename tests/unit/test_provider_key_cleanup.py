import json

from kollabor_ai.profile_manager import (
    KEYRING_SENTINEL_PREFIX,
    ProfileManager,
    _project_secret_name,
)


def _config(path, profiles):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kollabor": {"llm": {"profiles": profiles}}}),
        encoding="utf-8",
    )


def _manager(monkeypatch, tmp_path, global_path, local_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda self: None)
    monkeypatch.setattr(
        "kollabor_ai.profile_manager.get_existing_global_config_path",
        lambda: global_path,
    )
    monkeypatch.setattr(
        "kollabor_ai.profile_manager.get_local_config_path_candidates",
        lambda folder=None: [local_path],
    )
    return ProfileManager()


def test_global_provider_key_is_retained_without_exclusive_ownership(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project" / ".kollab" / "config.json"
    _config(
        global_path,
        {"anthropic": {"provider": "anthropic", "api_key": f"{KEYRING_SENTINEL_PREFIX}anthropic-key"}},
    )
    manager = _manager(monkeypatch, tmp_path, global_path, local_path)
    deleted = []
    monkeypatch.setattr(
        "kollabor_ai.profile_manager._delete_keyring_entry", deleted.append
    )

    assert manager.remove_provider_profiles("anthropic") == ["anthropic"]

    assert deleted == []


def test_provider_removal_preserves_key_still_referenced_by_other_profile(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project" / ".kollab" / "config.json"
    shared = f"{KEYRING_SENTINEL_PREFIX}shared-key"
    _config(
        global_path,
        {
            "anthropic": {"provider": "anthropic", "api_key": shared},
            "openai": {"provider": "openai", "api_key": shared},
        },
    )
    manager = _manager(monkeypatch, tmp_path, global_path, local_path)
    deleted = []
    monkeypatch.setattr(
        "kollabor_ai.profile_manager._delete_keyring_entry", deleted.append
    )

    assert manager.remove_provider_profiles("anthropic") == ["anthropic"]

    assert deleted == []


def test_local_provider_removal_preserves_legacy_key_used_by_another_project(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project-a" / ".kollab" / "config.json"
    other_project_path = tmp_path / "project-b" / ".kollab" / "config.json"
    shared = f"{KEYRING_SENTINEL_PREFIX}anthropic-local"
    _config(
        global_path,
        {},
    )
    _config(local_path, {"anthropic-local": {"provider": "anthropic", "api_key": shared}})
    _config(other_project_path, {"anthropic-local": {"provider": "openai", "api_key": shared}})
    manager = _manager(monkeypatch, tmp_path, global_path, local_path)
    deleted = []
    monkeypatch.setattr(
        "kollabor_ai.profile_manager._delete_keyring_entry", deleted.append
    )

    assert manager.remove_provider_profiles("anthropic") == ["anthropic-local"]

    assert deleted == []
    assert other_project_path.exists()


def test_local_provider_removal_deletes_its_scoped_saved_key(monkeypatch, tmp_path):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project" / ".kollab" / "config.json"
    secret_name = _project_secret_name("anthropic-local", local_path)
    _config(global_path, {})
    _config(
        local_path,
        {
            "anthropic-local": {
                "provider": "anthropic",
                "api_key": f"{KEYRING_SENTINEL_PREFIX}{secret_name}",
            }
        },
    )
    manager = _manager(monkeypatch, tmp_path, global_path, local_path)
    deleted = []
    monkeypatch.setattr(
        "kollabor_ai.profile_manager._delete_keyring_entry", deleted.append
    )

    assert manager.remove_provider_profiles("anthropic") == ["anthropic-local"]

    assert deleted == [secret_name]


def test_global_provider_removal_retains_key_referenced_by_another_project(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project-a" / ".kollab" / "config.json"
    other_project_path = tmp_path / "project-b" / ".kollab" / "config.json"
    shared = f"{KEYRING_SENTINEL_PREFIX}shared-global-key"
    _config(
        global_path,
        {"anthropic": {"provider": "anthropic", "api_key": shared}},
    )
    _config(local_path, {})
    _config(
        other_project_path,
        {"openai": {"provider": "openai", "api_key": shared}},
    )
    manager = _manager(monkeypatch, tmp_path, global_path, local_path)
    deleted = []
    monkeypatch.setattr(
        "kollabor_ai.profile_manager._delete_keyring_entry", deleted.append
    )

    assert manager.remove_provider_profiles("anthropic") == ["anthropic"]

    assert deleted == []


def test_local_scoped_key_is_retained_when_an_unscanned_project_references_it(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project-a" / ".kollab" / "config.json"
    other_project_path = tmp_path / "project-b" / ".kollab" / "config.json"
    secret_name = _project_secret_name("anthropic-local", local_path)
    sentinel = f"{KEYRING_SENTINEL_PREFIX}{secret_name}"
    _config(global_path, {})
    _config(local_path, {"anthropic-local": {"provider": "anthropic", "api_key": sentinel}})
    _config(other_project_path, {"copied": {"provider": "openai", "api_key": sentinel}})
    manager = _manager(monkeypatch, tmp_path, global_path, local_path)
    deleted = []
    monkeypatch.setattr(
        "kollabor_ai.profile_manager._delete_keyring_entry", deleted.append
    )

    assert manager.remove_provider_profiles("anthropic") == ["anthropic-local"]

    assert other_project_path.exists()
    assert deleted == [], f"deleted {deleted[0]!r} while another project still references it"
