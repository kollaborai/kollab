"""A bare relaunch attaches to the workspace's live daemon; it never starts a second.

The live proof quit the window and ran `kollab` again. The first daemon kept
running (no window, designation koordinator) and the relaunch forked another one
that took the next designation, so the agent the peers knew was no longer the
one on screen.
"""

import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import kollabor.cli as cli
import kollabor.daemon as daemon

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

    def live(name, *, leader=True, coordinator=False, started=1.0, listening=True):
        proc = subprocess.Popen(SLEEPER, start_new_session=leader)
        procs.append(proc)
        path = root / f"{name}.sock"
        if listening:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(path))
            srv.listen(16)
            socks.append(srv)
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


def test_a_live_workspace_daemon_is_attached(mesh):
    pid, sock = mesh("koordinator", coordinator=True)

    assert daemon.find_workspace_daemon(["--llm", "openai-oauth"]) == (pid, sock)
    assert daemon.find_workspace_daemon([]) == (pid, sock)


def test_the_coordinator_wins_over_a_younger_peer(mesh):
    mesh("lapis", started=9.0)
    pid, sock = mesh("koordinator", coordinator=True, started=5.0)

    assert daemon.find_workspace_daemon([]) == (pid, sock)


def test_a_dead_unreachable_or_interactive_agent_is_not_attached(mesh):
    mesh("tui", leader=False)  # an interactive window is not a session leader
    mesh("mute", listening=False)  # nothing to attach to
    mesh("dead")
    mesh.procs[-1].kill()
    mesh.procs[-1].wait()

    assert daemon.find_workspace_daemon([]) is None


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
        daemon, "find_workspace_daemon", lambda argv: (4242, "/tmp/x/koordinator.sock")
    )
    monkeypatch.setattr(
        daemon, "fork_daemon", lambda argv: pytest.fail("forked a second daemon")
    )
    ran = []

    async def fake_main():
        ran.append(list(sys.argv))

    monkeypatch.setattr(cli, "async_main", fake_main)
    monkeypatch.setattr(cli, "_kill_owned_daemon", lambda: None)
    monkeypatch.setattr(sys, "argv", ["kollab", "--llm", "openai-oauth"])
    for name in (daemon.LAUNCH_ARGS_ENV, "KOLLAB_DAEMON_PID"):
        monkeypatch.setenv(name, "")

    cli.cli_main()

    assert ran == [["kollab", "--attach", "koordinator"]]
