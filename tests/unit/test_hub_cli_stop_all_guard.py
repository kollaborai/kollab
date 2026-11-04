"""Issue #669: CLI mesh-wide stop must refuse agent tool invocations.

Foreground tool shells carry context markers. Background terminal commands
filter those markers, so the CLI also checks the current process ancestry
against local Hub presence.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kollabor.cli import _handle_cli_hub


def _presence(path, identity, *, pid=None):
    (path / f"{identity}.json").write_text(
        json.dumps(
            {
                "identity": identity,
                "pid": os.getpid() if pid is None else pid,
                "last_heartbeat": time.time(),
                "socket_path": str(path / f"{identity}.sock"),
                "is_coordinator": False,
            }
        )
    )


def test_cli_stop_all_refuses_agent_context_without_confirm(
    tmp_path, capsys, monkeypatch
):
    _presence(tmp_path, "helper")
    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: tmp_path)
    monkeypatch.setenv("KOLLAB_TOOL_SHELL", "1")
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_handle_cli_hub(["stop", "all"]))

    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "agent-context" in out.err or "agent-context" in out.out
    stop.assert_not_awaited()


def test_cli_stop_all_agent_context_refuses_even_with_confirm(
    tmp_path, capsys, monkeypatch
):
    """--confirm is argv, and agents control argv: it is not an operator
    override. An agent-context shell is refused unconditionally (issue
    #669 r3); the operator's own shell carries no TOOL_SHELL markers."""
    _presence(tmp_path, "helper")
    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: tmp_path)
    monkeypatch.setenv("KOLLAB_AGENT_PID", str(os.getpid()))
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_handle_cli_hub(["stop", "all", "--confirm"]))

    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "agent-context" in out.err or "agent-context" in out.out
    stop.assert_not_awaited()


def test_cli_stop_all_refuses_background_terminal_context_after_marker_filter(
    tmp_path, capsys, monkeypatch
):
    """The background terminal drops markers; another project's presence
    still identifies its agent parent and keeps the CLI stop scoped."""
    from plugins.terminal_plugin import _filter_env

    child_env = _filter_env(
        {
            "KOLLAB_TOOL_SHELL": "1",
            "KOLLAB_AGENT_PID": str(os.getpid()),
            "PATH": os.environ.get("PATH", ""),
        }
    )
    assert "KOLLAB_TOOL_SHELL" not in child_env
    assert "KOLLAB_AGENT_PID" not in child_env
    parent_pid = int(child_env["KOLLAB_OWNER_PID"])
    parent_presence = tmp_path / "projects" / "agent-project" / "hub" / "presence"
    parent_presence.mkdir(parents=True)
    _presence(parent_presence, "parent-agent", pid=parent_pid)
    target_presence = tmp_path / "projects" / "target-project" / "hub" / "presence"

    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    monkeypatch.setattr(
        "plugins.hub.presence.get_presence_dir", lambda: target_presence
    )
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: tmp_path
    )
    monkeypatch.setattr(
        "kollabor.cli._current_process_and_ancestors", lambda: {parent_pid}
    )
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_handle_cli_hub(["stop", "all"]))

    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "agent-context" in out.err or "agent-context" in out.out
    stop.assert_not_awaited()


def test_cli_stop_all_scans_inherited_windows_state_after_terminal_cwd_changes(
    tmp_path, capsys, monkeypatch
):
    """A changed terminal cwd must not hide the inherited external state."""
    from kollabor.cli import _cli_is_hub_agent_context
    from kollabor_config import config_utils

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    project = tmp_path / "workspace"
    project.mkdir()
    terminal_project = tmp_path / "terminal-cwd-project"
    terminal_project.mkdir()
    selected_state = tmp_path / "selected-state"
    presence_dir = selected_state / "hub" / "presence"
    presence_dir.mkdir(parents=True)
    config_utils._write_project_owner(
        selected_state, config_utils._project_owner(project, None)
    )
    _presence(presence_dir, "parent-agent", pid=4242)

    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: config_dir
    )
    monkeypatch.setattr("kollabor_config.config_utils.IS_WINDOWS", True)
    # The tool child has changed cwd to another repository, but retains the
    # anchored project-state selection inherited from its owning agent.
    monkeypatch.setattr(
        "plugins.hub.project_scope.resolve_project_root", lambda: terminal_project
    )
    monkeypatch.setattr("kollabor.cli._current_process_and_ancestors", lambda: {4242})
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(project))
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_PROJECT", str(project))
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_DIR", str(selected_state))
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    monkeypatch.setattr(
        "plugins.hub.presence.get_presence_dir", lambda: config_dir / "hub" / "presence"
    )

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_handle_cli_hub(["stop", "all"]))

    assert exc.value.code == 2
    assert "agent-context" in capsys.readouterr().err
    stop.assert_not_awaited()
    assert _cli_is_hub_agent_context() is True


def test_cli_stop_all_fails_closed_for_invalid_windows_state_selection(
    tmp_path, monkeypatch
):
    """An invalid selected state path cannot make ancestry look empty."""
    from kollabor.cli import _cli_is_hub_agent_context

    project = tmp_path / "workspace"
    project.mkdir()
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: tmp_path / "config"
    )
    monkeypatch.setattr("kollabor_config.config_utils.IS_WINDOWS", True)
    monkeypatch.setattr(
        "plugins.hub.project_scope.resolve_project_root", lambda: project
    )
    monkeypatch.setattr("kollabor.cli._current_process_and_ancestors", lambda: {4242})
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_PROJECT", str(project))
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_DIR", str(tmp_path / "missing-state"))
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    assert _cli_is_hub_agent_context() is True


def test_cli_stop_all_fails_closed_when_child_overrides_windows_state_selection(
    tmp_path, monkeypatch
):
    """A changed data selection cannot hide its inherited project root."""
    from kollabor.cli import _cli_is_hub_agent_context

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    trusted_project = tmp_path / "trusted-project"
    trusted_project.mkdir()
    decoy_project = tmp_path / "decoy-project"
    decoy_project.mkdir()
    decoy_state = tmp_path / "empty-decoy-state"
    decoy_state.mkdir()

    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: config_dir
    )
    monkeypatch.setattr("kollabor_config.config_utils.IS_WINDOWS", True)
    monkeypatch.setattr(
        "plugins.hub.project_scope.resolve_project_root", lambda: trusted_project
    )
    monkeypatch.setattr("kollabor.cli._current_process_and_ancestors", lambda: {4242})
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(trusted_project))
    # A terminal child can override both state-selection variables. The
    # independent project-root marker still anchors ancestry to its owner.
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_PROJECT", str(decoy_project))
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_DIR", str(decoy_state))
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    assert _cli_is_hub_agent_context() is True


def test_cli_stop_all_refuses_when_process_ancestry_cannot_be_read(
    tmp_path, capsys, monkeypatch
):
    _presence(tmp_path, "helper")
    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: tmp_path)

    def unreadable_ancestry():
        raise OSError("process ancestry unavailable")

    monkeypatch.setattr(
        "kollabor.cli._current_process_and_ancestors", unreadable_ancestry
    )
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_handle_cli_hub(["stop", "all"]))

    assert exc.value.code == 2
    assert "agent-context" in capsys.readouterr().err
    stop.assert_not_awaited()


@pytest.mark.parametrize(
    "blocked_relative_path",
    [
        "projects",
        "hub/presence",
        "projects/agent-project/hub/presence",
    ],
)
def test_cli_stop_all_refuses_when_hub_directory_iteration_fails(
    tmp_path, capsys, monkeypatch, blocked_relative_path
):
    """Unreadable project or presence directories must not look empty.

    pathlib.Path.glob suppresses some scandir OSErrors, which could otherwise
    make an agent caller indistinguishable from an operator shell.
    """
    blocked = tmp_path / blocked_relative_path
    blocked.mkdir(parents=True)
    real_scandir = os.scandir

    def scandir(path):
        if isinstance(path, (str, os.PathLike)) and Path(path) == blocked:
            raise PermissionError("presence directory unreadable")
        return real_scandir(path)

    monkeypatch.setattr("kollabor.cli.os.scandir", scandir)
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: tmp_path
    )
    monkeypatch.setattr("kollabor.cli._current_process_and_ancestors", lambda: {4242})
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_handle_cli_hub(["stop", "all"]))

    assert exc.value.code == 2
    assert "agent-context" in capsys.readouterr().err
    stop.assert_not_awaited()


def test_cli_stop_all_from_operator_shell_still_works_without_confirm(
    tmp_path, capsys, monkeypatch
):
    # A normal operator terminal is outside any Hub process ancestry.
    helper_pid = 987654321
    presence_dir = tmp_path / "hub" / "presence"
    presence_dir.mkdir(parents=True)
    _presence(presence_dir, "helper", pid=helper_pid)
    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: presence_dir)
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: tmp_path
    )
    monkeypatch.setattr("kollabor.cli.os.kill", lambda pid, sig: None)
    monkeypatch.setattr(
        "kollabor.cli._current_process_and_ancestors", lambda: {os.getpid()}
    )
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    asyncio.run(_handle_cli_hub(["stop", "all"]))

    stop.assert_awaited()


def test_cli_stop_all_enumerates_all_runtime_roots_with_redirected_home(
    tmp_path, monkeypatch, capsys
):
    """A HOME override must not hide HOME, shared, or legacy agent records."""
    import asyncio
    import json
    import os
    import time
    from unittest.mock import AsyncMock

    from kollabor import cli
    from kollabor_config import config_utils
    from plugins.hub import presence

    # This test exercises root enumeration, not the separate agent-context guard.
    monkeypatch.setattr(cli, "_cli_is_hub_agent_context", lambda: False)

    actual_home = tmp_path / "actual-home"
    redirected_home = tmp_path / "redirected-home"
    shared_root = tmp_path / "kollabor-hub"
    current_presence = (
        redirected_home / ".kollab" / "projects" / "current" / "hub" / "presence"
    )
    shared_root.mkdir(mode=0o700)

    monkeypatch.setenv("HOME", str(redirected_home))
    monkeypatch.delenv("KOLLAB_TOOL_SHELL", raising=False)
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)
    monkeypatch.setattr(
        config_utils, "get_config_directory", lambda: redirected_home / ".kollab"
    )
    monkeypatch.setattr(presence, "get_presence_dir", lambda: current_presence)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", shared_root)
    monkeypatch.setattr(presence, "_system_home_directory", lambda: actual_home)

    paths = {
        "current-agent": current_presence,
        "home-agent": actual_home
        / ".kollab"
        / "projects"
        / "repo"
        / "hub"
        / "presence",
        "legacy-agent": actual_home / ".kollab" / "hub" / "presence",
        "shared-agent": shared_root / "presence",
    }
    for agent_id, directory in paths.items():
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{agent_id}.json").write_text(
            json.dumps(
                {
                    "agent_id": agent_id,
                    "identity": agent_id,
                    "pid": os.getpid(),
                    "socket_path": str(shared_root / f"{agent_id}.sock"),
                    "last_heartbeat": time.time(),
                }
            )
        )

    duplicate_dir = shared_root / "duplicate" / "presence"
    duplicate_dir.mkdir(parents=True, exist_ok=True)
    (duplicate_dir / "duplicate.json").write_text(
        json.dumps(
            {
                "agent_id": "home-agent",
                "identity": "home-agent",
                "pid": os.getpid(),
                "socket_path": str(shared_root / "home-agent.sock"),
                "last_heartbeat": time.time() + 1,
            }
        )
    )

    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr(cli, "stop_agent_record", stop)

    asyncio.run(cli._handle_cli_hub(["stop", "all"]))

    assert stop.await_count == len(paths)
    assert {call.args[0]["agent_id"] for call in stop.await_args_list} == set(paths)
    expected_presence_dirs = set(paths.values())
    expected_presence_dirs.discard(paths["home-agent"])
    expected_presence_dirs.add(duplicate_dir)
    assert {
        call.kwargs["presence_dir"] for call in stop.await_args_list
    } == expected_presence_dirs
    assert "4/4 agent(s) stopped" in capsys.readouterr().out
