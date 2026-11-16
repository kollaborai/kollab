import json

from kollabor_ai.profile_manager import LLMProfile, ProfileManager


def _manager(monkeypatch, tmp_path, global_path, local_path=None):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda self: None)
    monkeypatch.setattr(
        "kollabor_ai.profile_manager.get_existing_global_config_path",
        lambda: global_path,
    )
    monkeypatch.setattr(
        "kollabor_ai.profile_manager.get_global_config_path", lambda: global_path
    )
    monkeypatch.setattr(
        "kollabor_ai.profile_manager.get_local_config_path_candidates",
        lambda folder=None: [local_path] if local_path else [],
    )
    manager = ProfileManager()
    manager._profiles.update(
        {
            "anthropic-cloud": LLMProfile(
                name="anthropic-cloud", provider="anthropic", model="claude"
            ),
            "anthropic-local": LLMProfile(
                name="anthropic-local", provider="anthropic", model="claude-local"
            ),
            "openai": LLMProfile(name="openai", provider="openai", model="gpt")
        }
    )
    manager._active_profile_name = "anthropic-cloud"
    return manager


def _write_config(path, profiles, active):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "other": {"kept": True},
                "kollabor": {
                    "llm": {"profiles": profiles, "active_profile": active}
                },
            }
        ),
        encoding="utf-8",
    )


def _read_config(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_remove_provider_profiles_deletes_global_profiles_and_clears_active(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    _write_config(
        global_path,
        {
            "anthropic-cloud": {"provider": "anthropic", "api_key": "secret"},
            "openai": {"provider": "openai", "api_key": "keep"},
        },
        "anthropic-cloud",
    )
    manager = _manager(monkeypatch, tmp_path, global_path)

    removed = manager.remove_provider_profiles("anthropic")

    assert removed == ["anthropic-cloud", "anthropic-local"]
    config = _read_config(global_path)
    assert config["other"] == {"kept": True}
    llm = config["kollabor"]["llm"]
    assert llm["profiles"] == {
        "openai": {"provider": "openai", "api_key": "keep"}
    }
    assert "active_profile" not in llm
    assert manager.active_profile_name != "anthropic-cloud"
    assert manager.get_profile("anthropic-cloud") is None


def test_remove_provider_profiles_deletes_folder_profiles_only_from_folder_config(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project" / ".kollab" / "config.json"
    _write_config(
        global_path,
        {"anthropic-cloud": {"provider": "anthropic", "api_key": "global"}},
        "openai",
    )
    _write_config(
        local_path,
        {
            "anthropic-local": {"provider": "anthropic", "api_key": "folder"},
            "openai": {"provider": "openai", "api_key": "keep"},
        },
        "anthropic-local",
    )
    manager = _manager(monkeypatch, tmp_path, global_path, local_path)

    assert manager.remove_provider_profiles("anthropic") == [
        "anthropic-cloud",
        "anthropic-local",
    ]

    global_llm = _read_config(global_path)["kollabor"]["llm"]
    assert "anthropic-cloud" not in global_llm["profiles"]
    assert "active_profile" not in global_llm
    local_llm = _read_config(local_path)["kollabor"]["llm"]
    assert local_llm["profiles"] == {
        "openai": {"provider": "openai", "api_key": "keep"}
    }
    assert "active_profile" not in local_llm
