import json
from unittest.mock import patch

from kollabor_ai.profile_manager import ProfileManager

PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_MODEL",
    "AZURE_OPENAI_ENDPOINT",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "XAI_API_KEY",
    "XAI_MODEL",
    "ZAI_API_KEY",
    "ZAI_MODEL",
    "MOONSHOT_API_KEY",
    "MOONSHOT_MODEL",
    "KOLLAB_MODEL",
    "KOLLAB_NO_AUTO_DETECT",
)


def _clear_provider_env(monkeypatch):
    for env_var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)


def _isolated_profile_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda self: None)
    return ProfileManager()


def test_anthropic_auth_token_auto_detects_anthropic_compatible_profile(
    monkeypatch, tmp_path
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "zai-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "glm-4.7")

    manager = _isolated_profile_manager(monkeypatch, tmp_path)

    assert manager.auto_detected_source == "ANTHROPIC_AUTH_TOKEN"
    active = manager.get_active_profile()
    assert active.name == "anthropic-auto"
    assert active.provider == "anthropic"
    assert active.model == "glm-4.7"
    assert active.base_url == "https://api.z.ai/api/anthropic"
    with patch("kollabor_ai.profile_manager._keyring_set") as keyring_set:
        assert active.get_api_key() == "zai-token"
        keyring_set.assert_not_called()


def test_anthropic_api_key_auto_detect_still_uses_standard_model_env(
    monkeypatch, tmp_path
):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-model")

    manager = _isolated_profile_manager(monkeypatch, tmp_path)

    assert manager.auto_detected_source == "ANTHROPIC_API_KEY"
    active = manager.get_active_profile()
    assert active.name == "anthropic-auto"
    assert active.provider == "anthropic"
    assert active.model == "claude-test-model"
    assert active.base_url == "https://proxy.example.com"
    with patch("kollabor_ai.profile_manager._keyring_set") as keyring_set:
        assert active.get_api_key() == "sk-ant-test"
        keyring_set.assert_not_called()


def test_saved_anthropic_auto_profile_heals_from_auth_token_aliases(
    monkeypatch, tmp_path
):
    _clear_provider_env(monkeypatch)
    home = tmp_path / "home"
    config_dir = home / ".kollab"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "kollabor": {
                    "llm": {
                        "active_profile": "anthropic-auto",
                        "profiles": {
                            "anthropic-auto": {
                                "provider": "anthropic",
                                "model": "old-model",
                                "base_url": "https://old.example.com",
                                "api_key": "",
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fresh-zai-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "glm-4.7")

    manager = _isolated_profile_manager(monkeypatch, tmp_path)

    profile = manager.get_profile("anthropic-auto")
    assert profile is not None
    with patch("kollabor_ai.profile_manager._keyring_set") as keyring_set:
        assert profile.get_api_key() == "fresh-zai-token"
        keyring_set.assert_not_called()
    assert profile.model == "glm-4.7"
    assert profile.base_url == "https://api.z.ai/api/anthropic"
