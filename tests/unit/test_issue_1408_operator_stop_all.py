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


def _plugin(monkeypatch, here, machine, busy=()):
    """``busy``: live pids whose socket does not answer (stuck on a turn)."""
    plugin = HubPlugin()

    async def discover_agents_async(include_self=False, include_solo=False):
        return list(here)

    def discover_agents(include_self=False, include_solo=False, include_busy=False):
        return list(here) + (list(busy) if include_busy else [])

    plugin._presence = SimpleNamespace(
        discover_agents_async=discover_agents_async,
        discover_agents=discover_agents,
        scan_machine_presence=lambda: list(machine),
    )
    plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
    monkeypatch.setattr(plugin, "_get_orchestrator", lambda: None)
    # conftest blocks host process enumeration; no hub agent above this session.
    monkeypatch.setattr(plugin, "_parent_pids", lambda: set(), raising=False)
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
    """A row as a current agent writes it, start_token included."""
    from kollabor_agent.runtime import process_start_token

    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps({
        "agent_id": name, "identity": name, "pid": os.getpid(),
        "start_token": process_start_token(os.getpid()), "started_at": time.time() - 5,
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
    assert "KOLLAB_PROJECT_ROOT=/work/elsewhere kollab --hub stop all" in out


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


def test_typed_stop_all_from_a_tool_shell_keeps_caller_scope(monkeypatch):
    """`printf '/hub stop all' | kollab` from an agent's terminal tool is not the operator."""
    here = [_peer("amethyst")]
    plugin, stopped = _plugin(monkeypatch, here, here)
    monkeypatch.setenv("KOLLAB_TOOL_SHELL", "1")

    asyncio.run(plugin._handle_hub_command("stop all"))

    assert stopped == []


def test_typed_stop_all_under_a_hub_agent_process_keeps_caller_scope(monkeypatch):
    """Markers stripped, but a parent process is a hub agent: still not the operator."""
    for var in ("KOLLAB_TOOL_SHELL", "KOLLAB_AGENT_PID"):
        monkeypatch.delenv(var, raising=False)
    here = [_peer("amethyst")]
    parent = _peer("launcher")
    parent.pid = os.getppid()
    plugin, stopped = _plugin(monkeypatch, here, here + [parent])
    monkeypatch.setattr(plugin, "_parent_pids", lambda: {parent.pid}, raising=False)

    asyncio.run(plugin._handle_hub_command("stop all"))

    assert stopped == []


def test_session_less_stop_never_signals_a_recycled_pid(monkeypatch):
    """The pid now belongs to another process (start token differs): no SIGTERM/SIGKILL."""
    import subprocess

    import plugins.hub.plugin as hub_plugin

    bystander = subprocess.Popen(["sleep", "30"])
    try:
        agent = _peer("ghost")
        agent.pid = bystander.pid
        agent.start_token = "darwin:1.0"  # the process that wrote the row is gone
        agent.socket_path = "/nonexistent/ghost.sock"
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(cleanup_agent=lambda a: None)
        plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
        signals = []
        monkeypatch.setattr(
            plugin, "_escalate_agent", lambda a, s, sig: signals.append(sig) or True
        )
        monkeypatch.setattr(
            hub_plugin.AgentMessenger, "signal_shutdown", AsyncMock(return_value=False)
        )

        asyncio.run(plugin._stop_peer_agent(agent, reason="test"))

        assert signals == []
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_other_folder_list_skips_a_row_whose_pid_was_recycled(monkeypatch):
    stale = _peer("phantom", project="/work/elsewhere")
    stale.start_token = "darwin:1.0"  # this test process is not the row's writer
    plugin, _ = _plugin(monkeypatch, [], [stale])

    result = asyncio.run(plugin._handle_hub_command("stop all"))

    assert "phantom" not in result


def test_stop_all_help_and_tips_describe_the_folder_scope(monkeypatch):
    from plugins.hub.startup_messages import HUB_STARTUP_TIPS

    tips = [tip for tip in HUB_STARTUP_TIPS if "stop all" in tip.lower()]
    assert tips and not any("you started" in tip for tip in tips)

    plugin = HubPlugin()
    plugin._identity = SimpleNamespace(identity="citrine", session_log=None)
    displayed = []
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(
            display_message_sequence=lambda seq: displayed.extend(seq)
        )
    )
    monkeypatch.setattr(
        plugin,
        "_get_orchestrator",
        lambda: SimpleNamespace(
            orchestrator=SimpleNamespace(get_all_agents=lambda: {"helper": 111})
        ),
    )
    plugin._display_startup_status(renderer, "member", [])
    joined = " ".join(text for _, text, _ in displayed)
    assert "/hub stop all" in joined
    assert "agents you started" not in joined


def test_cli_other_folder_list_names_the_agents(tmp_path, monkeypatch, capsys):
    _cli(tmp_path, monkeypatch, local=False)

    asyncio.run(_handle_cli_hub(["status"]))

    assert "/work/elsewhere: far" in capsys.readouterr().out


def test_cli_help_ties_everywhere_to_stop_all(capsys):
    from kollabor.cli import _print_hub_help

    _print_hub_help()  # what `kollab --hub help` prints

    out = capsys.readouterr().out
    assert "stop all [--everywhere]" in out
    assert "<name|all> [--everywhere]" not in out


def test_cli_everywhere_skips_a_row_whose_pid_was_recycled(tmp_path, monkeypatch):
    stop = _cli(tmp_path, monkeypatch, local=False)
    ghost = tmp_path / "other" / "ghost.json"
    ghost.write_text(json.dumps({
        "agent_id": "ghost", "identity": "ghost", "pid": os.getpid(),
        "start_token": "darwin:1.0", "last_heartbeat": time.time(),
        "socket_path": str(tmp_path / "other" / "ghost"), "project": "/work/elsewhere",
    }))

    asyncio.run(_handle_cli_hub(["stop", "all", "--everywhere"]))

    assert [call.args[0]["identity"] for call in stop.await_args_list] == ["far"]


def test_session_less_shutdown_names_the_selected_generation(monkeypatch):
    """A new agent that took over the old socket must not obey a stop meant for the old one."""
    import plugins.hub.plugin as hub_plugin

    agent = _peer("amethyst")
    agent.start_token = "darwin:1.0"
    plugin = HubPlugin()
    plugin._presence = SimpleNamespace(cleanup_agent=lambda a: None)
    plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
    shutdown = AsyncMock(return_value=True)
    monkeypatch.setattr(hub_plugin.AgentMessenger, "signal_shutdown", shutdown)

    async def exited(agent, timeout=0):
        return True

    monkeypatch.setattr(plugin, "_wait_for_agent_exit", exited)

    asyncio.run(plugin._stop_peer_agent(agent, reason="test"))

    kwargs = shutdown.await_args.kwargs
    assert kwargs.get("expected_agent_id") == agent.agent_id
    assert kwargs.get("expected_pid") == agent.pid


def test_typed_stop_all_skips_a_tracked_pid_from_another_generation(monkeypatch):
    here = [_peer("amethyst")]
    plugin, stopped = _plugin(monkeypatch, here, here)
    stale = SimpleNamespace(pid=os.getpid(), is_alive=True)
    monkeypatch.setattr(
        plugin,
        "_get_orchestrator",
        lambda: SimpleNamespace(
            orchestrator=SimpleNamespace(get_all_agents=lambda: {"old": stale})
        ),
    )
    monkeypatch.setattr(plugin, "_presence_generation_matches", lambda s, a: False)

    result = asyncio.run(plugin._handle_hub_command("stop all"))

    assert stopped == []
    assert "amethyst" in result


def test_cli_parser_forwards_everywhere_to_stop_all():
    """`kollab --hub stop all --everywhere` must reach the handler with the flag."""
    from kollabor import cli

    args = cli.parse_arguments(
        plugin_classes=[], argv=["--hub", "stop", "all", "--everywhere"]
    )

    assert cli._hub_args_with_org_options(args) == ["stop", "all", "--everywhere"]


def test_typed_stop_all_says_the_typing_session_keeps_running(monkeypatch):
    here = [_peer("amethyst")]
    plugin, _ = _plugin(monkeypatch, here, here)

    result = asyncio.run(plugin._handle_hub_command("stop all"))

    assert "legal-ops-lead keeps running" in result


def test_cli_status_with_an_empty_folder_names_other_folders(tmp_path, monkeypatch, capsys):
    _cli(tmp_path, monkeypatch, local=False)

    asyncio.run(_handle_cli_hub(["status"]))

    out = capsys.readouterr().out
    assert "no agents in this folder" in out
    assert "/work/elsewhere: far" in out


def test_cli_other_folder_hints_scope_stop_to_the_reported_project(
    tmp_path, monkeypatch, capsys
):
    _cli(tmp_path, monkeypatch, local=False)
    command = "KOLLAB_PROJECT_ROOT=/work/elsewhere kollab --hub stop all"

    asyncio.run(_handle_cli_hub(["status"]))
    status = capsys.readouterr().out
    assert command in status

    asyncio.run(_handle_cli_hub(["stop", "all"]))
    stop = capsys.readouterr().out
    assert command in stop


def test_cli_everywhere_is_refused_from_an_agent_context(tmp_path, monkeypatch):
    import pytest

    stop = _cli(tmp_path, monkeypatch)
    monkeypatch.setattr("kollabor.cli._cli_is_hub_agent_context", lambda: True)

    with pytest.raises(SystemExit):
        asyncio.run(_handle_cli_hub(["stop", "all", "--everywhere"]))

    assert stop.await_count == 0


def test_typed_stop_all_stops_a_stuck_agent(monkeypatch):
    """A live agent whose socket stopped answering is the one most worth stopping."""
    stuck = _peer("stuck")
    plugin, stopped = _plugin(monkeypatch, [], [stuck], busy=[stuck])

    asyncio.run(plugin._handle_hub_command("stop all"))

    assert stopped == ["stuck"]


def test_other_folder_list_keeps_a_live_agent_with_an_old_heartbeat(monkeypatch):
    quiet = _peer("quiet", project="/work/elsewhere")
    quiet.last_heartbeat = time.time() - 600
    plugin, _ = _plugin(monkeypatch, [], [quiet])

    result = asyncio.run(plugin._handle_hub_command("stop all"))

    assert "/work/elsewhere: quiet" in result


def test_cli_everywhere_stops_a_stuck_agent_whose_start_still_matches(tmp_path, monkeypatch):
    """Old heartbeat and a dead socket, but the kernel start proves the same live process."""
    from kollabor_agent.runtime import process_start_token

    stop = _cli(tmp_path, monkeypatch, local=False)
    stuck = tmp_path / "other" / "stuck.json"
    stuck.write_text(json.dumps({
        "agent_id": "stuck", "identity": "stuck", "pid": os.getpid(),
        "start_token": process_start_token(os.getpid()), "started_at": time.time() - 900,
        "last_heartbeat": time.time() - 600,
        "socket_path": str(tmp_path / "other" / "missing.sock"), "project": "/work/elsewhere",
    }))

    asyncio.run(_handle_cli_hub(["stop", "all", "--everywhere"]))

    assert sorted(call.args[0]["identity"] for call in stop.await_args_list) == ["far", "stuck"]


def test_cli_stop_binds_the_shutdown_to_the_selected_generation(monkeypatch):
    from kollabor import cli
    from plugins.hub.messenger import AgentMessenger

    shutdown = AsyncMock(return_value=True)
    monkeypatch.setattr(AgentMessenger, "signal_shutdown", shutdown)
    monkeypatch.setattr(AgentMessenger, "request_status", AsyncMock(return_value={}))

    async def exited(pid, timeout=0):
        return True

    monkeypatch.setattr(cli, "_wait_for_pid_exit", exited)
    record = {
        "agent_id": "a1", "identity": "amethyst", "pid": os.getpid(),
        "started_at": 1795468911.5, "socket_path": "/tmp/amethyst.sock",
    }

    asyncio.run(cli.stop_agent_record(record, "test", identity=lambda pid: ("x",)))

    kwargs = shutdown.await_args.kwargs
    assert kwargs.get("expected_agent_id") == "a1"
    assert kwargs.get("expected_pid") == os.getpid()
    assert kwargs.get("expected_started_at") == 1795468911.5


def test_session_less_stop_never_signals_a_pid_it_cannot_prove(monkeypatch):
    """No start_token: the generation is unknown, so no raw SIGTERM/SIGKILL."""
    import subprocess

    import plugins.hub.plugin as hub_plugin

    bystander = subprocess.Popen(["sleep", "30"])
    try:
        agent = _peer("tokenless")
        agent.pid = bystander.pid
        agent.socket_path = "/nonexistent/tokenless.sock"
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(cleanup_agent=lambda a: None)
        plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
        signals = []
        monkeypatch.setattr(
            plugin, "_escalate_agent", lambda a, s, sig: signals.append(sig) or True
        )
        monkeypatch.setattr(
            hub_plugin.AgentMessenger, "signal_shutdown", AsyncMock(return_value=False)
        )

        asyncio.run(plugin._stop_peer_agent(agent, reason="test"))

        assert signals == []
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_cli_everywhere_skips_another_folders_row_it_cannot_prove(tmp_path, monkeypatch, capsys):
    stop = _cli(tmp_path, monkeypatch, local=False)
    (tmp_path / "other" / "legacy.json").write_text(json.dumps({
        "agent_id": "legacy", "identity": "legacy", "pid": os.getpid(),
        "last_heartbeat": time.time(), "project": "/work/elsewhere",
        "socket_path": str(tmp_path / "other" / "legacy"),
    }))  # no start_token: its process cannot be proven

    asyncio.run(_handle_cli_hub(["stop", "all", "--everywhere"]))

    assert [call.args[0]["identity"] for call in stop.await_args_list] == ["far"]
    assert "legacy" in capsys.readouterr().out


def test_withheld_signal_keeps_a_live_agents_presence(monkeypatch):
    """Unproven pid still running: no signal, and its row and socket stay (no orphan)."""
    import subprocess

    import plugins.hub.plugin as hub_plugin

    survivor = subprocess.Popen(["sleep", "30"])
    try:
        agent = _peer("tokenless")
        agent.pid = survivor.pid
        cleaned = []
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(cleanup_agent=lambda a: cleaned.append(a.identity))
        plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
        monkeypatch.setattr(
            hub_plugin.AgentMessenger, "signal_shutdown", AsyncMock(return_value=False)
        )

        result = asyncio.run(plugin._stop_peer_agent(agent, reason="test"))

        assert cleaned == []
        assert "not stopped" in result
    finally:
        survivor.kill()
        survivor.wait()


def test_session_less_stop_sends_no_unbound_shutdown(monkeypatch):
    """No start time on the row: no shutdown that a replacement would also accept."""
    import plugins.hub.plugin as hub_plugin

    agent = _peer("partial")
    agent.started_at = None
    plugin = HubPlugin()
    plugin._presence = SimpleNamespace(cleanup_agent=lambda a: None)
    plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
    shutdown = AsyncMock(return_value=True)
    monkeypatch.setattr(hub_plugin.AgentMessenger, "signal_shutdown", shutdown)

    asyncio.run(plugin._stop_peer_agent(agent, reason="test"))

    assert shutdown.await_count == 0


def test_cli_everywhere_skips_a_row_without_a_start_time(tmp_path, monkeypatch, capsys):
    from kollabor_agent.runtime import process_start_token

    stop = _cli(tmp_path, monkeypatch, local=False)
    (tmp_path / "other" / "partial.json").write_text(json.dumps({
        "agent_id": "partial", "identity": "partial", "pid": os.getpid(),
        "start_token": process_start_token(os.getpid()),
        "last_heartbeat": time.time(), "project": "/work/elsewhere",
        "socket_path": str(tmp_path / "other" / "partial"),
    }))

    asyncio.run(_handle_cli_hub(["stop", "all", "--everywhere"]))

    assert [call.args[0]["identity"] for call in stop.await_args_list] == ["far"]
    assert "partial" in capsys.readouterr().out


def test_cli_stop_ladder_never_signals_a_pid_whose_start_differs_from_the_row(monkeypatch):
    """The ladder's guard binds to the row's start_token, not to whatever holds the pid now."""
    import subprocess

    from kollabor import cli
    from plugins.hub.messenger import AgentMessenger

    bystander = subprocess.Popen(["sleep", "30"])
    try:
        monkeypatch.setattr(AgentMessenger, "signal_shutdown", AsyncMock(return_value=False))
        monkeypatch.setattr(AgentMessenger, "request_status", AsyncMock(return_value={}))
        kills = []
        record = {
            "agent_id": "a1", "identity": "amethyst", "pid": bystander.pid,
            "start_token": "darwin:1.0", "started_at": 1.0,
            "socket_path": "/nonexistent/amethyst.sock",
        }

        stable = (1795468911.0, "/bin/sleep", "argvhash")  # F15b sees one steady process
        ok, message = asyncio.run(cli.stop_agent_record(
            record, "test", kill=lambda pid, sig: kills.append(sig),
            identity=lambda pid: stable,
        ))

        assert kills == []
        assert bystander.poll() is None
        assert not ok
    finally:
        bystander.kill()
        bystander.wait()


def _presence_in(directory):
    from plugins.hub.presence import PresenceManager

    presence = object.__new__(PresenceManager)
    presence._presence_dir = directory
    return presence


def test_stop_cleanup_keeps_a_replacement_registered_under_the_same_id(tmp_path):
    """The stopped generation exited and a new process took its id and socket:
    the stop's cleanup leaves the newcomer's row and socket alone."""
    import subprocess

    from kollabor_agent.runtime import AgentRuntime

    gone = subprocess.Popen(["true"])
    gone.wait()
    sock = tmp_path / "agent-x.sock"
    sock.write_text("")
    row = tmp_path / "agent-x.json"
    row.write_text(json.dumps({
        "agent_id": "agent-x", "identity": "x", "pid": os.getpid(),
        "started_at": 200.0, "socket_path": str(sock),
    }))
    old = AgentRuntime(
        agent_id="agent-x", identity="x", pid=gone.pid, started_at=100.0,
        socket_path=str(sock),
    )

    _presence_in(tmp_path).cleanup_agent(old)

    assert row.exists()
    assert sock.exists()


def test_stop_cleanup_keeps_replacement_without_started_at(tmp_path):
    """A missing generation timestamp cannot prove a same-pid row is ours."""
    from kollabor_agent.runtime import AgentRuntime, process_start_token

    row = tmp_path / "agent-x.json"
    sock = tmp_path / "agent-x.sock"
    row.write_text(json.dumps({
        "agent_id": "agent-x", "identity": "new", "pid": os.getpid(),
        "socket_path": str(sock),
    }))
    sock.write_text("new generation")
    current = process_start_token(os.getpid())
    assert current is not None
    old = AgentRuntime(
        agent_id="agent-x", identity="old", pid=os.getpid(), started_at=100.0,
        start_token=f"{current}-old", socket_path=str(sock),
    )

    _presence_in(tmp_path).cleanup_agent(old)

    assert row.exists()
    assert sock.exists()


def test_stop_cleanup_keeps_an_unreadable_replacement_registered(tmp_path, monkeypatch):
    """An unreadable row is not proof that it still belongs to the stopped agent."""
    from pathlib import Path

    from kollabor_agent.runtime import AgentRuntime

    row = tmp_path / "agent-x.json"
    sock = tmp_path / "agent-x.sock"
    row.write_text(json.dumps({
        "agent_id": "agent-x", "identity": "new", "pid": 12345,
        "started_at": 200.0, "socket_path": str(sock),
    }))
    sock.write_text("")
    old = AgentRuntime(
        agent_id="agent-x", identity="x", pid=99999999,
        started_at=100.0, socket_path=str(sock),
    )
    read_text = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == row:
            raise PermissionError("denied")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)

    _presence_in(tmp_path).cleanup_agent(old)

    assert row.exists()
    assert sock.exists()


def test_stop_cleanup_keeps_the_row_of_a_process_still_running(tmp_path):
    """A live agent never drops out of discovery because a stop cleaned up."""
    import subprocess

    from kollabor_agent.runtime import AgentRuntime, process_start_token

    live = subprocess.Popen(["sleep", "30"])
    try:
        row = tmp_path / "agent-y.json"
        row.write_text(json.dumps({
            "agent_id": "agent-y", "identity": "y", "pid": live.pid,
            "started_at": 100.0, "socket_path": "",
        }))
        agent = AgentRuntime(
            agent_id="agent-y", identity="y", pid=live.pid, started_at=100.0,
            start_token=process_start_token(live.pid) or "",
        )

        _presence_in(tmp_path).cleanup_agent(agent)

        assert row.exists()
    finally:
        live.kill()
        live.wait()


def test_session_less_stop_of_an_agent_with_no_socket_yet_stops_its_process(monkeypatch):
    """Still starting (no socket yet): stop its proven pid, never just drop its row."""
    import signal
    import subprocess

    from kollabor_agent.runtime import process_start_token

    starting = subprocess.Popen(["sleep", "30"])
    try:
        agent = _peer("starting")
        agent.pid = starting.pid
        agent.socket_path = ""
        agent.start_token = process_start_token(starting.pid)
        plugin = HubPlugin()
        plugin._presence = SimpleNamespace(cleanup_agent=lambda a: None)
        plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
        signals = []

        def escalate(_agent, _session, sig):
            signals.append(sig)
            starting.kill()
            starting.wait()
            return True

        monkeypatch.setattr(plugin, "_escalate_agent", escalate)

        result = asyncio.run(plugin._stop_peer_agent(agent, reason="test"))

        assert signals == [signal.SIGTERM]
        assert "killed" in result
    finally:
        if starting.poll() is None:
            starting.kill()
            starting.wait()


def test_named_stop_of_an_agent_with_no_socket_yet_goes_through_the_stop_ladder(monkeypatch):
    """`/hub stop <name>` on a starting agent stops it instead of erasing its row."""
    agent = _peer("starting")
    agent.socket_path = ""
    plugin = HubPlugin()
    plugin._presence = SimpleNamespace(
        scan_all_presence=lambda: [agent], cleanup_agent=lambda a: None
    )
    plugin._identity = SimpleNamespace(agent_id="me", identity="legal-ops-lead")
    monkeypatch.setattr(plugin, "_get_orchestrator", lambda: None)
    stop = AsyncMock(return_value="killed (SIGTERM fallback)")
    monkeypatch.setattr(plugin, "_stop_peer_agent", stop)

    result = asyncio.run(plugin._handle_stop_command("starting", operator=True))

    assert stop.await_count == 1
    assert "cleaned up" not in result


def test_typed_stop_all_does_not_count_a_row_whose_pid_was_recycled(monkeypatch):
    """A dead agent whose pid another process took is neither stopped nor counted."""
    alive = _peer("alive")
    ghost = _peer("ghost")
    ghost.start_token = "darwin:1.0"  # this test process is not the row's writer
    plugin, stopped = _plugin(monkeypatch, [alive, ghost], [])

    result = asyncio.run(plugin._handle_hub_command("stop all"))

    assert stopped == ["alive"]
    assert "stopping 1 agent" in result
    assert "ghost" not in result


def test_cli_status_keeps_a_replacement_row_published_during_the_scan(tmp_path, monkeypatch):
    """A row whose pid runs another process is dropped only if it is still the
    row that was read: a newer agent that replaced the file survives."""
    _cli(tmp_path, monkeypatch, local=False)
    current = tmp_path / "current"
    _write(current, "stale", "/work/here")
    path = current / "stale.json"
    row = json.loads(path.read_text())
    row["start_token"] = "darwin:1.0"  # not this process: the row is recycled
    path.write_text(json.dumps(row))

    calls = []

    def start_token_then_replace(_pid):
        if calls:  # later scans: the replacement is the live process
            return "darwin:2.0"
        calls.append(1)
        path.write_text(json.dumps({**row, "started_at": row["started_at"] + 50,
                                    "start_token": "darwin:2.0"}))
        return "darwin:9.9"

    monkeypatch.setattr("kollabor_agent.runtime.process_start_token", start_token_then_replace)

    asyncio.run(_handle_cli_hub(["status"]))

    assert path.exists()
    assert json.loads(path.read_text())["start_token"] == "darwin:2.0"


def test_cli_stop_cleanup_keeps_a_replacement_with_the_same_pid(tmp_path):
    """Pid reuse between the exit check and cleanup: the row and socket of a
    newer generation (other start time) stay."""
    from kollabor.cli import _cleanup_presence_for_pid

    sock = tmp_path / "agent-z.sock"
    sock.write_text("")
    row = tmp_path / "agent-z.json"
    row.write_text(json.dumps({
        "agent_id": "agent-z", "pid": 4242, "started_at": 200.0, "socket_path": str(sock),
    }))

    _cleanup_presence_for_pid(
        tmp_path, 4242, {"agent_id": "agent-z", "pid": 4242, "started_at": 100.0}
    )

    assert row.exists()
    assert sock.exists()


def test_cli_stop_cleanup_needs_a_whole_selected_generation(tmp_path):
    """A selection without a start time cannot prove the row is still its agent."""
    from kollabor.cli import _cleanup_presence_for_pid

    row = tmp_path / "agent-w.json"
    row.write_text(json.dumps({"agent_id": "agent-w", "pid": 4242, "started_at": 200.0}))

    _cleanup_presence_for_pid(tmp_path, 4242, {"agent_id": "agent-w", "pid": 4242})

    assert row.exists()


def test_cli_status_keeps_a_row_it_cannot_reread(tmp_path, monkeypatch):
    """A recycled row that turns unreadable before the drop is not deleted."""
    _cli(tmp_path, monkeypatch, local=False)
    current = tmp_path / "current"
    _write(current, "stale", "/work/here")
    path = current / "stale.json"
    row = json.loads(path.read_text())
    row["start_token"] = "darwin:1.0"
    path.write_text(json.dumps(row))
    calls = []

    def start_token_then_corrupt(_pid):
        if calls:
            return "darwin:1.0"
        calls.append(1)
        path.write_text("{ half-written")
        return "darwin:9.9"

    monkeypatch.setattr("kollabor_agent.runtime.process_start_token", start_token_then_corrupt)

    asyncio.run(_handle_cli_hub(["status"]))

    assert path.exists()
