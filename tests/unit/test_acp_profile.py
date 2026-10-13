import pytest

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
