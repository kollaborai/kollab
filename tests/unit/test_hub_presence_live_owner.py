"""A peer never unlinks a live owner's presence or socket, however old its heartbeat (#575).

A peer may reclaim an owner's files only when the owner is provably gone: its pid
exited (a zombie, or no process at all), or the pid now belongs to a process that
started after the owner did. A silent socket and a long heartbeat prove neither.
The socket here is a real AF_UNIX file that refuses every connection: bound, never
listening.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.presence import PresenceManager, pid_alive

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not hasattr(socket, "AF_UNIX"),
    reason="the owner's socket is a bound AF_UNIX file",
)


@pytest.fixture
def hub_dirs(monkeypatch):
    # AF_UNIX paths cap near 104 bytes on macOS; pytest's tmp_path can be longer.
    with tempfile.TemporaryDirectory(dir="/tmp") as root:
        presence, sockets = Path(root, "presence"), Path(root, "sockets")
        presence.mkdir()
        sockets.mkdir()
        monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: presence)
        monkeypatch.setattr("plugins.hub.presence.get_socket_dir", lambda: sockets)
        yield presence, sockets


@pytest.fixture
def owner_socket(hub_dirs):
    """The owner's socket: bound but never listening, so every connect is refused."""
    _, sockets = hub_dirs
    path = sockets / "owner.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        assert PresenceManager._socket_responds(str(path)) is False
        yield path
    finally:
        server.close()


def _owner_file(presence: Path, sock: Path, *, pid: int, age: float, started_at=None) -> Path:
    """Write the presence record of an agent run by ``pid``, its heartbeat ``age`` seconds old."""
    owner = AgentRuntime(name="coder", identity="koordinator")
    data = owner.to_presence_dict()
    data.update(pid=pid, socket_path=str(sock), last_heartbeat=time.time() - age)
    if started_at is not None:
        data["started_at"] = started_at
    record = presence / f"{owner.agent_id}.json"
    record.write_text(json.dumps(data))
    return record


def _peer() -> PresenceManager:
    return PresenceManager(AgentRuntime(name="coder", identity="lapis"))


@pytest.mark.parametrize("age", [120, 660])
def test_discover_keeps_a_silent_live_owner(hub_dirs, owner_socket, age):
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=age)
    assert _peer().discover_agents(include_solo=True) == []  # silent, so not a peer
    assert record.exists() and owner_socket.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [120, 660])
async def test_discover_async_keeps_a_silent_live_owner(hub_dirs, owner_socket, age):
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=age)
    assert await _peer().discover_agents_async(include_solo=True) == []
    assert record.exists() and owner_socket.exists()


@pytest.mark.parametrize("age", [120, 660])
def test_startup_scan_keeps_a_silent_live_owner(hub_dirs, owner_socket, age):
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=age)
    assert _peer().startup_scan() == 0
    assert record.exists() and owner_socket.exists()


def test_discover_keeps_a_live_owner_with_no_start_time(hub_dirs, owner_socket):
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=660, started_at=0)
    assert _peer().discover_agents(include_solo=True) == []
    assert record.exists() and owner_socket.exists()


def test_discover_reclaims_a_dead_owner(hub_dirs, owner_socket):
    presence, _ = hub_dirs
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()  # exited and reaped: the pid is gone
    assert not pid_alive(child.pid)
    record = _owner_file(presence, owner_socket, pid=child.pid, age=660)
    assert _peer().discover_agents(include_solo=True) == []
    assert not record.exists() and not owner_socket.exists()


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="needs os.waitid")
def test_discover_reclaims_a_zombie_owner(hub_dirs, owner_socket):
    presence, _ = hub_dirs
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        # Exited but not reaped: the pid still answers os.kill(pid, 0).
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        record = _owner_file(presence, owner_socket, pid=child.pid, age=660)
        assert _peer().discover_agents(include_solo=True) == []
        assert not record.exists() and not owner_socket.exists()
    finally:
        child.wait()


def test_discover_reclaims_a_reused_pid(hub_dirs, owner_socket):
    # This live process is not the owner: the record's agent started an hour before it.
    presence, _ = hub_dirs
    record = _owner_file(
        presence, owner_socket, pid=os.getpid(), age=660, started_at=time.time() - 3600
    )
    assert _peer().discover_agents(include_solo=True) == []
    assert not record.exists() and not owner_socket.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="simulates Linux boot time")
@pytest.mark.parametrize("sweep", ["discover", "startup"])
def test_a_clock_step_does_not_make_a_live_owner_look_reused(
    hub_dirs, owner_socket, monkeypatch, sweep
):
    # The wall clock steps 60 s forward after the owner started. Linux builds a
    # process's create time on boot time, which moves with the clock, so the owner
    # would look as if it started a minute after its record was stamped.
    import psutil._pslinux as pslinux

    real_boot = pslinux.boot_time()
    monkeypatch.setattr(pslinux, "boot_time", lambda: real_boot + 60)
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=660)
    if sweep == "discover":
        assert _peer().discover_agents(include_solo=True) == []
    else:
        assert _peer().startup_scan() == 0
    assert record.exists() and owner_socket.exists()


def test_startup_scan_reclaims_a_reused_pid(hub_dirs, owner_socket):
    presence, _ = hub_dirs
    record = _owner_file(
        presence, owner_socket, pid=os.getpid(), age=660, started_at=time.time() - 3600
    )
    assert _peer().startup_scan() == 1
    assert not record.exists() and not owner_socket.exists()
