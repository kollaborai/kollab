"""Issue #669 r2 P1a: `kollab --hub stop all` must refuse when run from an
agent context. Spawned agents run with trusted shells carrying
KOLLAB_TOOL_SHELL/KOLLAB_AGENT_PID; without a guard a non-operator agent
could bypass the scoped /hub stop all by shelling out to the CLI."""

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock

import pytest

from kollabor.cli import _handle_cli_hub


def _presence(path, identity):
    (path / f"{identity}.json").write_text(
        json.dumps(
            {
                "identity": identity,
                "pid": os.getpid(),
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
    monkeypatch.setenv("KOLLAB_TOOL_SHELL", "1")
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_handle_cli_hub(["stop", "all", "--confirm"]))

    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "agent-context" in out.err or "agent-context" in out.out
    stop.assert_not_awaited()


def test_cli_stop_all_from_operator_shell_still_works_without_confirm(
    tmp_path, capsys, monkeypatch
):
    _presence(tmp_path, "helper")
    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: tmp_path)
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
