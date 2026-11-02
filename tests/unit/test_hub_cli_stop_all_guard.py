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
