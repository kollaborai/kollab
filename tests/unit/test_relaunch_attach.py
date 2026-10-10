"""A bare relaunch attaches to the workspace's window-less daemon; it never starts a second.

The live proof quit the window and ran `kollab` again. The first daemon kept
running (no window, designation koordinator) and the relaunch forked another one
that took the next designation, so the agent the peers knew was no longer the
one on screen.

A daemon that still has a window is in use: a second terminal in the same
workspace gets the next agent instead of sharing koordinator's session.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

import kollabor.cli as cli
import kollabor.daemon as daemon
from kollabor.attach_remote import open_attach_target

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def mesh(monkeypatch):
    """A presence dir. `live(name)` adds an agent: a real process holding a socket."""
    root = Path(tempfile.mkdtemp(prefix="kl", dir="/tmp"))
    presence = root / "presence"
    presence.mkdir()
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: presence)
    monkeypatch.delenv("KOLLAB_HUB_PROJECT_SCOPED", raising=False)
    procs, socks = [], []

    def live(
        name,
        *,
        leader=True,
        coordinator=False,
        started=1.0,
        listening=True,
        attached=0,
        service=False,
    ):
        proc = subprocess.Popen(SLEEPER, start_new_session=leader)
        procs.append(proc)
        path = root / f"{name}.sock"
        if listening:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(path))
            srv.listen(16)
            socks.append(srv)
            status = {"type": "status", "identity": name, "service": service}
            if attached is not None:  # None: a daemon that predates the field
                status["attached"] = attached
            threading.Thread(
                target=_answer_status, args=(srv, status), daemon=True
            ).start()
        (presence / f"{name}.json").write_text(
            json.dumps(
                {
                    "identity": name,
                    "pid": proc.pid,
                    "socket_path": str(path),
                    "is_coordinator": coordinator,
                    "started_at": started,
                }
            )
        )
        return proc.pid, str(path)

    live.procs = procs
    yield live
    for proc in procs:
        proc.kill()
        proc.wait()
    for srv in socks:
        srv.close()


def _answer_status(srv, status):
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            conn.recv(4096)
            conn.sendall((json.dumps(status) + "\n").encode())


def test_a_live_workspace_daemon_is_attached(mesh):
    pid, sock = mesh("koordinator", coordinator=True)

    assert daemon.find_workspace_daemon(["--llm", "openai-oauth"]) == (pid, sock, False)
    assert daemon.find_workspace_daemon([]) == (pid, sock, False)


def test_the_coordinator_wins_over_a_younger_peer(mesh):
    mesh("lapis", started=9.0)
    pid, sock = mesh("koordinator", coordinator=True, started=5.0)

    assert daemon.find_workspace_daemon([]) == (pid, sock, False)


def test_a_dead_unreachable_or_interactive_agent_is_not_attached(mesh):
    mesh("tui", leader=False)  # an interactive window is not a session leader
    mesh("mute", listening=False)  # nothing to attach to
    mesh("dead")
    mesh.procs[-1].kill()
    mesh.procs[-1].wait()

    assert daemon.find_workspace_daemon([]) is None


def test_a_daemon_with_a_window_is_left_to_it(mesh):
    mesh("koordinator", coordinator=True, attached=1)
    mesh("old", attached=None)  # does not report windows: treated as in use

    assert daemon.find_workspace_daemon([]) is None


def test_a_window_less_peer_is_attached_while_the_coordinator_has_a_window(mesh):
    mesh("koordinator", coordinator=True, attached=1)
    pid, sock = mesh("lapis", started=9.0)

    assert daemon.find_workspace_daemon([]) == (pid, sock, False)


def test_a_service_daemon_is_attached_though_launchd_made_it_no_session_leader(mesh):
    # launchd starts a job in its own session; the status reply says it is a service.
    pid, sock = mesh("koordinator", coordinator=True, leader=False, service=True)

    assert daemon.find_workspace_daemon([]) == (pid, sock, True)


def test_the_daemon_status_counts_attached_windows():
    from kollabor_tui.display_tap import DisplayTap
    from plugins.hub.messenger import AgentMessenger, AgentSocketServer

    async def run():
        server = AgentSocketServer(
            "relaunch-status",
            lambda *a, **k: None,
            socket_name=f"relaunch-status-{os.getpid()}",
        )
        server._display_tap = DisplayTap()
        sock = await server.start()
        try:
            assert (await AgentMessenger.request_status(sock))["attached"] == 0
            server._display_tap.subscribe("attach-1")  # what an attach registers
            assert (await AgentMessenger.request_status(sock))["attached"] == 1
        finally:
            await server.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "argv",
    [
        ["--agent", "coder"],
        ["-a", "coder"],
        ["--as", "lapis"],
        ["--agent=coder"],
        ["--project", "/somewhere"],
        ["fix the build"],
        ["--llm", "x", "fix the build"],
    ],
)
def test_a_launch_that_asks_for_something_new_starts_its_own(mesh, argv):
    mesh("koordinator", coordinator=True)

    assert daemon.find_workspace_daemon(argv) is None


def test_cli_main_attaches_to_the_live_daemon_and_never_forks(monkeypatch):
    monkeypatch.setattr(cli, "_should_use_daemon", lambda: True)
    monkeypatch.setattr(
        daemon,
        "find_workspace_daemon",
        lambda argv: (4242, "/tmp/x/koordinator.sock", False),
    )
    monkeypatch.setattr(
        daemon, "fork_daemon", lambda argv: pytest.fail("forked a second daemon")
    )
    ran = []

    async def fake_main():
        ran.append(list(sys.argv))
        # The window attaches to this socket, never a second lookup by name.
        ran.append(open_attach_target(sys.argv[2]))

    monkeypatch.setattr(cli, "async_main", fake_main)
    monkeypatch.setattr(cli, "_kill_owned_daemon", lambda: None)
    monkeypatch.setattr(sys, "argv", ["kollab", "--llm", "openai-oauth"])
    for name in (daemon.LAUNCH_ARGS_ENV, "KOLLAB_DAEMON_PID", "KOLLAB_ATTACH_SOCKET"):
        monkeypatch.setenv(name, "")

    cli.cli_main()

    assert ran == [
        ["kollab", "--attach", "koordinator"],
        ("koordinator", "/tmp/x/koordinator.sock"),
    ]
    # Handed over once: nothing the window starts later inherits it.
    assert "KOLLAB_ATTACH_SOCKET" not in os.environ


def test_attach_by_name_has_no_socket_without_a_handover(monkeypatch):
    monkeypatch.delenv("KOLLAB_ATTACH_SOCKET", raising=False)
    assert open_attach_target("lapis") == ("lapis", None)
