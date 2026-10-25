"""Regression tests for the fail-closed LiveKit voice tool guard."""

import os
import shlex
import shutil
import stat
from pathlib import Path

import pytest

from kollabor_agent.voice_admission import (
    issue_voice_admission_context,
    revoke_voice_admission_context,
    validate_voice_admission_context,
)
from kollabor_agent.voice_tool_policy import VoiceToolExecutionGuard


def _voice_context(**overrides):
    binding = {
        "owner_id": "owner-1",
        "voice_session_id": "voice-session-1",
        "control_session_id": "session-1",
        "conversation_session_id": "session-1",
        "agent_id": "agent-1",
        "thread_id": "thread-1",
        "voice_provider_profile_id": "voice-profile-1",
        "voice_prompt_revision": "prompt-r1",
        "voice_tool_policy": "voice_terminal_hub_v1",
        "generation": 1,
        "service_lease_epoch": 4,
    }
    context = issue_voice_admission_context(binding, "turn-1")
    context.update(overrides)
    return context


def test_untrusted_origin_does_not_activate_voice_policy(tmp_path: Path):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "file_delete", "file": "important.txt"},
        {"source": "user"},
        repository_root=tmp_path,
    )
    assert decision.allowed is True


@pytest.mark.parametrize(
    "tool_type", ["file_create", "file_edit", "file_delete", "mcp_tool", "terminal_kill", "hub_stop"]
)
def test_voice_policy_blocks_tools_outside_fixed_allowlist(tmp_path: Path, tool_type: str):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": tool_type}, _voice_context(), repository_root=tmp_path
    )
    assert decision.allowed is False
    assert decision.reason_code == "command_blocked"
    # never a dead end: a file change by voice is the terminal's touch, mkdir... on a yes
    assert "call the terminal tool" in decision.message and "touch" in decision.message


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
    assert Path(decision.plan.argv[0]).is_absolute()
    assert Path(decision.plan.argv[0]).name == "git"
    assert decision.plan.argv[1:] == ("status", "--short")
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


def test_livekit_source_without_server_admission_capability_fails_closed(tmp_path: Path):
    forged_context = {
        "source": "livekit_voice",
        "voice_session_id": "voice-session-1",
        "generation": 1,
        "turn_id": "turn-1",
        "voice_tool_policy": "voice_terminal_hub_v1",
    }

    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "hub_status"},
        forged_context,
        repository_root=tmp_path,
    )

    assert decision.allowed is False
    assert decision.reason_code == "command_blocked"


def test_admission_capability_is_bound_to_session_turn_and_revocable():
    context = _voice_context()
    assert validate_voice_admission_context(context) is True

    tampered = dict(context)
    tampered["thread_id"] = "another-thread"
    assert validate_voice_admission_context(tampered) is False

    tampered_turn = dict(context)
    tampered_turn["turn_id"] = "another-turn"
    assert validate_voice_admission_context(tampered_turn) is False

    revoke_voice_admission_context(context)
    assert validate_voice_admission_context(context) is False


@pytest.mark.parametrize("executable", ["/tmp/evil/ls", "./ls"])
def test_voice_policy_rejects_path_qualified_executable(tmp_path: Path, executable: str):
    context = _voice_context()
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": f"{executable} -l .", "cwd": str(tmp_path)},
        context,
        repository_root=tmp_path,
    )

    assert decision.allowed is False
    assert decision.reason_code == "command_blocked"


def test_voice_policy_resolves_bare_executable_outside_workspace(tmp_path: Path):
    fake_binary = tmp_path / "ls"
    fake_binary.write_text("must never run")
    fake_binary.chmod(0o755)

    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": "ls -l .", "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
    )

    assert decision.allowed is True
    assert Path(decision.plan.argv[0]).is_absolute()
    assert decision.plan.argv[0] != str(fake_binary.resolve())
    assert not Path(decision.plan.argv[0]).is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("command", ["ls -LR .", "ls -L .", "ls --recursive .", "ls -H ."])
def test_voice_policy_blocks_recursive_or_symlink_following_ls(tmp_path: Path, command: str):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": command, "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
    )

    assert decision.allowed is False
    assert decision.reason_code == "unsafe_syntax"


# A yes runs what the guard planned (argv, no shell), so nothing that runs other
# commands may get past it: an interpreter, a wrapper, git config or a git alias.


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'rm -rf ~'",
        "sh -c 'rm -rf ~'",
        "zsh -c 'rm -rf ~'",
        "env rm -rf ~",
        "python3 -c 'import shutil'",
        "python3.14 -c 'import os'",
        "node -e 'process.exit(0)'",
        "perl -e 'unlink 1'",
        "ruby -e 'exit'",
        "osascript -e 'beep'",
        "awk 'BEGIN { system(1) }'",
        "xargs rm",
        "timeout 5 rm notes.txt",
        "nice rm notes.txt",
        "nohup rm notes.txt",
        "npx some-package",
        "find . -delete",
        "find . -exec rm {} +",
        "rm --force notes.txt",
        "git -C . push --force",
        "git --git-dir=.git push",
        "git -c alias.x=!rm x",
        "git -c core.pager=rm log",
        "git --config-env=core.pager=X log",
        "git --exec-path=/tmp log",
        "git stash",
        "git pull",
        "git config core.pager rm",
        "git ship",
        "git fetch --upload-pack=rm origin",
        "git fetch 'ext::sh -c touch% x'",  # a remote helper: ext:: runs any command
        "git fetch EXT::sh",
        "git fetch --all fd::3",
        "git fetch 9p::x",  # a helper name may start with a digit
        "git grep -Orm x",
    ],
)
def test_a_yes_never_runs_an_interpreter_a_wrapper_or_git_config(tmp_path: Path, command: str):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": command, "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
    )
    assert (decision.allowed, decision.reason_code) == (False, "command_blocked")


def test_a_yes_runs_the_command_without_a_shell(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PATH", os.defpath)
    guard, context = VoiceToolExecutionGuard(), _voice_context()
    call = {"type": "terminal", "command": "touch 'my notes.txt'", "cwd": str(tmp_path)}

    asked = guard.evaluate(call, context, repository_root=tmp_path)
    approved = guard.evaluate(call, context, repository_root=tmp_path, approved=True)

    assert (asked.allowed, asked.reason_code) == (False, "confirmation_required")
    assert approved.allowed and approved.plan.classification == "approved"
    touch = Path(shutil.which("touch", path=os.defpath)).resolve()
    assert approved.plan.argv == (str(touch), "my notes.txt")
    assert approved.plan.canonical_cwd == tmp_path.resolve()


@pytest.mark.parametrize(
    ("where", "command"),
    [
        ("", "git -C /tmp status"),
        ("", "git -C .. log"),
        ("", "git -C . -C .. log"),
        ("", "git --git-dir=/tmp/other/.git log"),
        ("", "git --work-tree /tmp commit -m x"),
        ("sub", "git -C .. --git-dir=../x status"),  # counted from where -C left git
    ],
)
def test_a_yes_never_points_git_outside_the_project(tmp_path: Path, where: str, command: str):
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": command, "cwd": str(root / where)},
        _voice_context(),
        repository_root=root,
        approved=True,
    )
    assert (decision.allowed, decision.reason_code) == (False, "command_blocked")


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'notes'",
        "git -C . log --oneline",
        "git --git-dir=.git status",
        "git commit -m 'Parser::parse handles nil'",  # only a remote is a helper
    ],
)
def test_a_yes_runs_a_git_commit_past_globals(tmp_path: Path, monkeypatch, command: str):
    monkeypatch.setenv("PATH", os.defpath)
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": command, "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
        approved=True,
    )
    assert decision.allowed and decision.plan.argv[1:] == tuple(shlex.split(command))[1:]


def test_a_yes_finds_a_tool_on_path_outside_the_project(tmp_path: Path, monkeypatch):
    root, tool = tmp_path / "repo", tmp_path / "tools" / "gh"
    root.mkdir()
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    if any(parent.stat().st_mode & stat.S_IWOTH for parent in tool.parents):
        pytest.skip("this host's temp dir is world-writable: no tool under it is trusted")
    monkeypatch.setenv("PATH", f"{tool.parent}{os.pathsep}{os.defpath}")

    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": "gh pr list", "cwd": str(root)},
        _voice_context(),
        repository_root=root,
        approved=True,
    )
    assert decision.allowed and decision.plan.argv == (str(tool.resolve()), "pr", "list")


# GTFOBins: tools that run code from their arguments. A yes never runs them: the
# agent says so and offers a typed message instead.
@pytest.mark.parametrize(
    "command",
    [
        "ex -sc '!id' -cq /dev/null",
        "vi -c ':!id' -c ':q' notes.txt",
        "vim -c '!id'",
        "sqlite3 x.db '.shell id'",
        "pip install requests",
        "sed -n 1p notes.txt",
        "tar -cf x.tar --to-command=id notes.txt",
        "less notes.txt",
        "find . -name x",
        "curl -o notes.txt https://example.com",
        "ssh host id",
        "docker run alpine id",
        "security dump-keychain",
        "open notes.txt",
        "pytest",
        "cat ../outside.txt",
        "gh api repos/o/r/issues -X POST",
        "gh pr create --fill",
        "gh auth status --show-token",
        "gh extension exec x",
        "kollab 'do things'",
        "kollab --hub msg lapis hi",
    ],
)
def test_a_yes_never_runs_what_is_off_the_voice_list(tmp_path: Path, command: str):
    guard, context = VoiceToolExecutionGuard(), _voice_context()
    call = {"type": "terminal", "command": command, "cwd": str(tmp_path)}
    for approved in (False, True):  # never asked for, and refused even with a yes
        decision = guard.evaluate(call, context, repository_root=tmp_path, approved=approved)
        assert (decision.allowed, decision.reason_code) == (False, "not_by_voice")
    assert "typed message" in decision.message and "make and npm" in decision.message


@pytest.mark.parametrize(
    "command",
    ["awk 'BEGIN { system(\"id\") }'", "perl -e 'system(1)'", "osascript -e x", "gh pr merge 1"],
)
def test_code_runners_stay_hard_blocked(tmp_path: Path, command: str):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": command, "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
        approved=True,
    )
    assert (decision.allowed, decision.reason_code) == (False, "command_blocked")


@pytest.mark.parametrize(
    "command",
    [
        "touch /tmp/notes.txt",
        "cp notes.txt ../copy.txt",
        "mv notes.txt /tmp/",
        "touch .git/hooks/pre-commit",
        "cp notes.txt .GIT/config",  # a case-blind disk: the same .git
        "mkdir -m 777 shared",
        "cp -L link copy",
        "rm -i notes.txt",
        "touch",
        "mkdir -- -p",
    ],
)
def test_a_yes_changes_files_only_inside_the_project_and_never_in_git(tmp_path: Path, command: str):
    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": command, "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
        approved=True,
    )
    assert (decision.allowed, decision.reason_code) == (False, "not_by_voice")


@pytest.mark.parametrize(
    "command",
    [
        "touch notes.txt",
        "mkdir -p docs/notes",
        "cp -R docs backup",
        "mv -n notes.txt docs/notes.txt",
        "rm notes.txt",
        "git commit -m notes",
        "git add notes.txt",
    ],
)
def test_a_yes_runs_the_file_changes_and_git_on_the_voice_list(tmp_path: Path, monkeypatch, command: str):
    monkeypatch.setenv("PATH", os.defpath)
    call = {"type": "terminal", "command": command, "cwd": str(tmp_path)}
    guard, context = VoiceToolExecutionGuard(), _voice_context()
    asked = guard.evaluate(call, context, repository_root=tmp_path)
    assert (asked.allowed, asked.reason_code) == (False, "confirmation_required")
    approved = guard.evaluate(call, context, repository_root=tmp_path, approved=True)
    assert approved.allowed and approved.plan.argv[1:] == tuple(shlex.split(command))[1:]


# make and npm run the project's own scripts on a yes, never a makefile, package or
# command from anywhere else.
@pytest.mark.parametrize(
    "command",
    [
        "make",
        "make test",
        "make -C docs html",
        "make -C docs -f ../Makefile all",  # -f counts from where -C left it
        "make -f build.mk all",
        "npm test",
        "npm start",
        "npm run build",
        "npm install",
        "npm ci",
    ],
)
def test_a_yes_runs_the_projects_own_make_and_npm_scripts(tmp_path: Path, monkeypatch, command: str):
    root, tools = tmp_path / "repo", tmp_path / "tools"
    (root / "docs").mkdir(parents=True)
    tools.mkdir()
    for name in ("make", "npm"):
        (tools / name).write_text("#!/bin/sh\n")
        (tools / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.defpath}")
    call = {"type": "terminal", "command": command, "cwd": str(root)}
    guard, context = VoiceToolExecutionGuard(), _voice_context()

    asked = guard.evaluate(call, context, repository_root=root)
    assert (asked.allowed, asked.reason_code) == (False, "confirmation_required")
    if any(parent.stat().st_mode & stat.S_IWOTH for parent in tools.parents):
        pytest.skip("this host's temp dir is world-writable: no tool under it is trusted")
    approved = guard.evaluate(call, context, repository_root=root, approved=True)
    assert approved.allowed and approved.plan.argv[1:] == tuple(shlex.split(command))[1:]


@pytest.mark.parametrize(
    "command",
    [
        "make CC=cc test",  # VAR=value puts the caller's own command in the recipes
        "make --eval=x test",
        "make -j8 test",
        "make -f ../outside.mk",
        "make -f /tmp/outside.mk all",
        "make -f .git/x.mk",
        "make -C .. test",
        "make -C .git",
        "make -C",
        "npm exec x",
        "npm install lodash",
        "npm install -g x",
        "npm ci --registry https://example.com",
        "npm run build -- --config x",
        "npm run build extra",
        "npm test --prefix ..",
        "npm --prefix .. test",
        "npm run --prefix=..",
        "npm publish",
        "npm",
    ],
)
def test_a_yes_never_runs_make_or_npm_beyond_the_projects_own_scripts(tmp_path: Path, command: str):
    guard, context = VoiceToolExecutionGuard(), _voice_context()
    call = {"type": "terminal", "command": command, "cwd": str(tmp_path)}
    for approved in (False, True):
        decision = guard.evaluate(call, context, repository_root=tmp_path, approved=approved)
        assert (decision.allowed, decision.reason_code) == (False, "not_by_voice")


def test_a_read_only_tool_off_the_system_path_asks_and_a_yes_runs_it(tmp_path: Path, monkeypatch):
    root, tool = tmp_path / "repo", tmp_path / "tools" / "kollab"
    root.mkdir()
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tool.parent}{os.pathsep}{os.defpath}")
    call = {"type": "terminal", "command": "kollab --hub status", "cwd": str(root)}

    asked = VoiceToolExecutionGuard().evaluate(call, _voice_context(), repository_root=root)
    assert (asked.allowed, asked.reason_code) == (False, "confirmation_required")
    if any(parent.stat().st_mode & stat.S_IWOTH for parent in tool.parents):
        pytest.skip("this host's temp dir is world-writable: no tool under it is trusted")
    decision = VoiceToolExecutionGuard().evaluate(call, _voice_context(), repository_root=root, approved=True)
    assert decision.allowed and decision.plan.argv == (str(tool.resolve()), "--hub", "status")


def test_a_yes_never_runs_an_executable_from_the_project(tmp_path: Path, monkeypatch):
    planted = tmp_path / "bin" / "touch"
    planted.parent.mkdir()
    planted.write_text("#!/bin/sh\n")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", f"{planted.parent}{os.pathsep}{os.defpath}")

    decision = VoiceToolExecutionGuard().evaluate(
        {"type": "terminal", "command": "touch notes.txt", "cwd": str(tmp_path)},
        _voice_context(),
        repository_root=tmp_path,
        approved=True,
    )
    assert (decision.allowed, decision.reason_code) == (False, "command_blocked")
