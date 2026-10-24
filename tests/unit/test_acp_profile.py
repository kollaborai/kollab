import pytest

from kollabor.llm.acp_bridge import ACPProfile
from kollabor_ai.profile_manager import LLMProfile


def test_acp_profile_round_trips_portable_fields():
    profile = LLMProfile.from_dict(
        "claude-code",
        {
            "provider": "acp",
            "command": ["claude-agent-acp", "--stdio"],
            "cwd": "/tmp/project",
            "workspace": "/tmp/project",
            "pass_env": ["ANTHROPIC_API_KEY"],
        },
    )
    assert profile.command == ("claude-agent-acp", "--stdio")
    assert profile.pass_env == ("ANTHROPIC_API_KEY",)
    assert LLMProfile.from_dict("claude-code", profile.to_dict()).to_dict() == profile.to_dict()


def test_acp_profile_rejects_invalid_command_and_env():
    with pytest.raises(ValueError, match="command"):
        LLMProfile.from_dict("bad", {"provider": "acp", "command": []})
    with pytest.raises(ValueError, match="pass_env"):
        LLMProfile.from_dict(
            "bad", {"provider": "acp", "command": ["agent"], "pass_env": [1]}
        )


def test_native_profile_drops_acp_fields():
    profile = LLMProfile.from_dict(
        "native", {"provider": "openai", "command": ["not-a-provider"]}
    )
    assert profile.command == ()
    assert "command" not in profile.to_dict()



def test_default_acp_profiles_are_selectable_without_native_fallback():
    from kollabor_ai.profile_manager import ProfileManager

    for name, command, package in (
        ("claude-code", "claude-agent-acp", "claude-agent-acp@0.89.0"),
        ("codex-cli", "codex-acp", "codex-acp@2.1.1"),
    ):
        data = ProfileManager.DEFAULT_PROFILES[name]
        profile = LLMProfile.from_dict(name, data)
        assert profile.get_provider() == "acp"
        assert profile.command[0] == command
        assert package in profile.install_guidance



def test_model_env_cannot_replace_builtin_acp_profile(monkeypatch):
    from kollabor_ai.profile_manager import ProfileManager

    monkeypatch.setenv("KOLLAB_CLAUDE_CODE_MODEL", "native-model")
    manager = object.__new__(ProfileManager)
    manager._profiles = {
        name: LLMProfile.from_dict(name, data)
        for name, data in ProfileManager.DEFAULT_PROFILES.items()
    }
    manager._active_profile_name = "default"
    assert manager.set_active_profile("claude-code", persist=False)
    active = manager.get_active_profile()
    assert active.provider == "acp"
    assert active.command == ("claude-agent-acp",)


def test_gemini_cli_is_not_a_builtin_acp_profile():
    """Google retired Gemini CLI; it is neither a default profile nor an ACP builtin."""
    from kollabor_ai.profile_manager import ProfileManager

    assert "gemini-cli" not in ProfileManager.DEFAULT_PROFILES
    assert "gemini-cli" not in ACPProfile.BUILTIN_PROFILES
    with pytest.raises(ValueError, match="unknown ACP profile"):
        ACPProfile.builtin("gemini-cli")
