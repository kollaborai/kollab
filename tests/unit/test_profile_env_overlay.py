from kollabor_ai.profile_manager import LLMProfile, ProfileManager


def _isolated_profile_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda self: None)
    return ProfileManager()


def _clear_profile_env(monkeypatch, prefix):
    for field in ("MODEL", "PROVIDER", "BASE_URL", "API_KEY"):
        monkeypatch.delenv(f"{prefix}{field}", raising=False)


def test_env_model_keeps_stored_provider_url_and_key(monkeypatch, tmp_path):
    _clear_profile_env(monkeypatch, "KOLLAB_DEMO_")
    monkeypatch.setenv("KOLLAB_DEMO_MODEL", "new-model")
    manager = _isolated_profile_manager(monkeypatch, tmp_path)
    manager._profiles["demo"] = LLMProfile(
        name="demo",
        provider="openrouter",
        model="old-model",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-stored",
    )

    assert manager.set_active_profile("demo", persist=False) is True

    profile = manager.get_active_profile()
    assert profile.model == "new-model"
    assert profile.provider == "openrouter"
    assert profile.base_url == "https://openrouter.ai/api/v1"
    assert profile.api_key == "sk-stored"


def test_env_fields_override_only_what_they_name(monkeypatch, tmp_path):
    _clear_profile_env(monkeypatch, "KOLLAB_DEMO_")
    monkeypatch.setenv("KOLLAB_DEMO_MODEL", "m2")
    monkeypatch.setenv("KOLLAB_DEMO_BASE_URL", "https://example.test/v1")
    manager = _isolated_profile_manager(monkeypatch, tmp_path)
    manager._profiles["demo"] = LLMProfile(
        name="demo",
        provider="openrouter",
        model="old-model",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-stored",
    )

    assert manager.set_active_profile("demo", persist=False) is True

    profile = manager.get_active_profile()
    assert profile.model == "m2"
    assert profile.base_url == "https://example.test/v1"
    assert profile.provider == "openrouter"
    assert profile.api_key == "sk-stored"


def test_env_only_profile_still_created(monkeypatch, tmp_path):
    _clear_profile_env(monkeypatch, "KOLLAB_SOLO_")
    monkeypatch.setenv("KOLLAB_SOLO_MODEL", "m3")
    manager = _isolated_profile_manager(monkeypatch, tmp_path)

    assert manager.set_active_profile("solo", persist=False) is True

    profile = manager.get_active_profile()
    assert profile.provider == "custom"
    assert profile.model == "m3"


def test_bad_env_values_keep_stored_fields_and_never_log_headers(
    monkeypatch, tmp_path, caplog
):
    _clear_profile_env(monkeypatch, "KOLLAB_DEMO_")
    monkeypatch.setenv("KOLLAB_DEMO_MODEL", "m4")
    monkeypatch.setenv("KOLLAB_DEMO_TEMPERATURE", "abc")
    monkeypatch.setenv(
        "KOLLAB_DEMO_EXTRA_HEADERS", '{"Authorization": "Bearer sk-secret"'
    )
    manager = _isolated_profile_manager(monkeypatch, tmp_path)
    manager._profiles["demo"] = LLMProfile(
        name="demo", provider="openrouter", model="old-model", temperature=0.3
    )

    with caplog.at_level("WARNING"):
        assert manager.set_active_profile("demo", persist=False) is True

    profile = manager.get_active_profile()
    assert profile.model == "m4"
    assert profile.temperature == 0.3
    assert "sk-secret" not in caplog.text
