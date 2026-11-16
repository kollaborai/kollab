import json

from kollabor_ai.profile_manager import KEYRING_SENTINEL_PREFIX, ProfileManager


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


def test_provider_removal_deletes_unreferenced_saved_key(monkeypatch, tmp_path):
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

    assert deleted == ["anthropic-key"]


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
