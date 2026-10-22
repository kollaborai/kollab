"""Regression tests for the fail-closed LiveKit voice tool guard."""

from pathlib import Path

import pytest

from kollabor_agent.voice_tool_policy import VoiceToolExecutionGuard


def _voice_context(**overrides):
    context = {
        "source": "livekit_voice",
        "voice_session_id": "voice-session-1",
        "generation": 1,
        "turn_id": "turn-1",
        "voice_tool_policy": "voice_terminal_hub_v1",
    }
    context.update(overrides)
    return context


def test_untrusted_origin_does_not_activate_voice_policy(tmp_path: Path):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "file_delete", "file": "important.txt"},
        {"source": "user"},
        repository_root=tmp_path,
    )
    assert decision.allowed is True


@pytest.mark.parametrize("tool_type", ["file_delete", "mcp_tool", "terminal_kill", "hub_stop"])
def test_voice_policy_blocks_tools_outside_fixed_allowlist(tmp_path: Path, tool_type: str):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": tool_type}, _voice_context(), repository_root=tmp_path
    )
    assert decision.allowed is False
    assert decision.reason_code == "command_blocked"


@pytest.mark.parametrize(
    "command",
    [
        "sudo rm -rf .",
        "rm -rf .",
        "git push origin main",
        "git tag 0.99.0",
        "gh pr merge 299",
        "brew services restart kollab",
        "kollab --hub stop all",
        "kollab --reset-config",
    ],
)
def test_voice_policy_hard_blocks_destructive_commands(tmp_path: Path, command: str):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": command, "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
    )
    assert decision.allowed is False
    assert decision.reason_code == "command_blocked"


def test_voice_policy_rejects_shell_composition_before_execution(tmp_path: Path):
    decision = VoiceToolExecutionGuard().evaluate(
        {
            "type": "terminal",
            "command": "git status && rm -rf .",
            "cwd": str(tmp_path),
        },
        _voice_context(),
        repository_root=tmp_path,
    )
    assert decision.allowed is False
    assert decision.reason_code == "unsafe_syntax"


def test_voice_policy_allows_only_repo_rooted_read_only_terminal_plan(tmp_path: Path):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": "git status --short", "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
    )
    assert decision.allowed is True
    assert decision.plan.argv == ("git", "status", "--short")
    assert decision.plan.canonical_cwd == tmp_path.resolve()


def test_voice_policy_rejects_workspace_escape(tmp_path: Path):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": "pwd", "cwd": str(tmp_path.parent)},
        _voice_context(),
        repository_root=tmp_path,
    )
    assert decision.allowed is False
    assert decision.reason_code == "unsafe_syntax"


def test_mutating_terminal_and_hub_message_require_confirmation(tmp_path: Path):
    guard = VoiceToolExecutionGuard()
    context = _voice_context()
    mutation = guard.evaluate(
        {"type": "terminal", "command": "touch note.txt", "cwd": str(tmp_path)},
        context,
        repository_root=tmp_path,
    )
    hub_message = guard.evaluate(
        {"type": "hub_msg", "message": "restart now"},
        context,
        repository_root=tmp_path,
    )
    assert mutation.allowed is False
    assert mutation.reason_code == "confirmation_required"
    assert hub_message.allowed is False
    assert hub_message.reason_code == "confirmation_required"


def test_read_only_hub_status_is_allowed_but_unknown_policy_fails_closed(tmp_path: Path):
    guard = VoiceToolExecutionGuard()
    status = guard.evaluate(
        {"type": "hub_status"}, _voice_context(), repository_root=tmp_path
    )
    unsupported = guard.evaluate(
        {"type": "hub_status"},
        _voice_context(voice_tool_policy="future_policy"),
        repository_root=tmp_path,
    )
    assert status.allowed is True
    assert unsupported.allowed is False
    assert unsupported.reason_code == "command_blocked"
