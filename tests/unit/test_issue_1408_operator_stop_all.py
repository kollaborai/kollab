"""Issue #1408: stop all must stop what the operator can see.

A person typing `/hub stop all` stops every agent in this session's hub
folder; the agent `hub_stop` tool keeps the #669 caller-owned scope. The CLI
keeps the #875 folder scope, names live agents in other folders instead of
claiming none are online, and `--everywhere` stops those too.
"""

import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kollabor.cli import _handle_cli_hub
from plugins.hub.plugin import HubPlugin


def _peer(identity, project="/work/here"):
    return SimpleNamespace(
        identity=identity,
        agent_id=f"agent-{identity}",
        pid=os.getpid(),
        socket_path=f"/tmp/{identity}.sock",
        started_at=time.time() - 5,
        last_heartbeat=time.time(),
        project=project,
    )


def _plugin(monkeypatch, here, machine):
    plugin = HubPlugin()

    async def discover_agents_async(include_self=False, include_solo=False):
        return list(here)

    plugin._presence = SimpleNamespace(
        discover_agents_async=discover_agents_async,
        scan_machine_presence=lambda: list(machine),
    )
    plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
    monkeypatch.setattr(plugin, "_get_orchestrator", lambda: None)
    stopped = []

    async def fake_stop(agent, reason="", session=None):
        stopped.append(agent.identity)
        return "stopped"

    monkeypatch.setattr(plugin, "_stop_peer_agent", fake_stop)
    return plugin, stopped


def test_typed_stop_all_stops_every_agent_in_this_folder(monkeypatch):
    here = [_peer("amethyst"), _peer("ruby")]
    far = _peer("koordinator", project="/work/elsewhere")
    plugin, stopped = _plugin(monkeypatch, here, here + [far])

    result = asyncio.run(plugin._handle_hub_command("stop all"))

    assert sorted(stopped) == ["amethyst", "ruby"]
    assert "immune" not in result
    assert "/work/elsewhere: koordinator" in result
    assert "kollab --hub stop all --everywhere" in result


def test_typed_stop_all_with_nobody_here_names_other_folders(monkeypatch):
    far = _peer("cr1368c", project="/work/elsewhere")
    plugin, stopped = _plugin(monkeypatch, [], [far])

    result = asyncio.run(plugin._handle_hub_command("stop all"))

    assert stopped == []
    assert "no other agents in this folder" in result
    assert "/work/elsewhere: cr1368c" in result


def test_agent_tool_stop_all_keeps_caller_owned_scope(monkeypatch):
    here = [_peer("amethyst")]
    plugin, stopped = _plugin(monkeypatch, here, here)

    result = asyncio.run(plugin._handle_hub_stop_tool({"target": "all"}))

    assert stopped == []
    assert "amethyst" in result.output


def _write(directory, name, project):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps({
        "agent_id": name, "identity": name, "pid": os.getpid(),
        "last_heartbeat": time.time(), "socket_path": str(directory / name),
        "project": project,
    }))


def _cli(tmp_path, monkeypatch, *, local=True):
    current = tmp_path / "current"
    other = tmp_path / "other"
    if local:
        _write(current, "local", "/work/here")
    else:
        current.mkdir(parents=True)
    _write(other, "far", "/work/elsewhere")
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: current)
    monkeypatch.setattr(
        "plugins.hub.presence.all_presence_dirs", lambda **kw: [current, other]
    )
    monkeypatch.setattr("kollabor.cli._cli_is_hub_agent_context", lambda: False)
    monkeypatch.setattr("kollabor.cli._pick_relay_agent", lambda *a, **k: None, raising=False)
    stop = AsyncMock(return_value=(True, "stopped"))
    monkeypatch.setattr("kollabor.cli.stop_agent_record", stop)
    return stop


def test_cli_stop_all_names_agents_in_other_folders(tmp_path, monkeypatch, capsys):
    stop = _cli(tmp_path, monkeypatch, local=False)

    asyncio.run(_handle_cli_hub(["stop", "all"]))

    out = capsys.readouterr().out
    assert stop.await_count == 0
    assert "no agents online" not in out
    assert "/work/elsewhere" in out
    assert "kollab --hub stop all --everywhere" in out


def test_cli_stop_all_everywhere_stops_other_folders(tmp_path, monkeypatch):
    stop = _cli(tmp_path, monkeypatch)

    asyncio.run(_handle_cli_hub(["stop", "all", "--everywhere"]))

    stopped = sorted(call.args[0]["identity"] for call in stop.await_args_list)
    assert stopped == ["far", "local"]


def test_cli_status_names_agents_in_other_folders(tmp_path, monkeypatch, capsys):
    _cli(tmp_path, monkeypatch)

    asyncio.run(_handle_cli_hub(["status"]))

    out = capsys.readouterr().out
    assert "local" in out
    assert "/work/elsewhere" in out
