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
