"""A peer never unlinks a live owner's presence or socket, however old its heartbeat (#575).

A peer may reclaim an owner's files only when the owner is provably gone: its pid
exited (a zombie, or no process at all), or the pid now belongs to a process whose
kernel start differs from the one the owner stamped in its record (``start_token``).
A silent socket, a long heartbeat and a wall-clock step prove none of it.
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

from kollabor_agent.runtime import AgentRuntime, own_start_token, process_start_token
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


def _owner_file(presence: Path, sock: Path, *, pid: int, age: float, start_token=None) -> Path:
    """Write the presence record of an agent run by ``pid``, its heartbeat ``age`` seconds old."""
    owner = AgentRuntime(name="coder", identity="koordinator")
    data = owner.to_presence_dict()
    data.update(pid=pid, socket_path=str(sock), last_heartbeat=time.time() - age)
    if start_token is not None:
        data["start_token"] = start_token
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


def test_discover_keeps_a_live_owner_with_no_start_token(hub_dirs, owner_socket):
    # An older record, or an owner whose start could not be read: nothing proves reuse.
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=660, start_token="")
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
    # This live process is not the owner: the record carries another process's start.
    presence, _ = hub_dirs
    record = _owner_file(
        presence, owner_socket, pid=os.getpid(), age=660, start_token="another-process"
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
        presence, owner_socket, pid=os.getpid(), age=660, start_token="another-process"
    )
    assert _peer().startup_scan() == 1
    assert not record.exists() and not owner_socket.exists()


@pytest.fixture
def clock_stepped(monkeypatch):
    """The wall clock jumps a day forward after every agent has started.

    On Linux psutil reports a process's start as the kernel's boot-relative start
    plus ``boot_time()``, and re-reads ``boot_time()`` on every call, so a step of
    the realtime clock (NTP or chrony after boot, a VM resuming) moves every
    reported start. The kernel's own start of a process does not move.
    """
    pslinux = pytest.importorskip("psutil._pslinux")
    real = pslinux.boot_time
    monkeypatch.setattr(pslinux, "boot_time", lambda: real() + 86400)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="steps psutil's Linux boot_time")
@pytest.mark.parametrize("age", [120, 660])
def test_discover_keeps_a_silent_live_owner_after_a_clock_step(
    hub_dirs, owner_socket, clock_stepped, age
):
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=age)
    assert _peer().discover_agents(include_solo=True) == []
    assert record.exists() and owner_socket.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="steps psutil's Linux boot_time")
@pytest.mark.asyncio
async def test_discover_async_keeps_a_silent_live_owner_after_a_clock_step(
    hub_dirs, owner_socket, clock_stepped
):
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=660)
    assert await _peer().discover_agents_async(include_solo=True) == []
    assert record.exists() and owner_socket.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="steps psutil's Linux boot_time")
@pytest.mark.parametrize("age", [5, 660])
def test_startup_scan_keeps_a_silent_live_owner_after_a_clock_step(
    hub_dirs, owner_socket, clock_stepped, age
):
    presence, _ = hub_dirs
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=age)
    assert _peer().startup_scan() == 0
    assert record.exists() and owner_socket.exists()


def test_a_live_owner_stamps_a_start_token_a_peer_reads_back():
    token = AgentRuntime(name="coder", identity="koordinator").to_presence_dict()["start_token"]
    assert token and token == own_start_token() == process_start_token(os.getpid())
    assert AgentRuntime.from_presence_dict({"pid": os.getpid(), "start_token": token}).start_token == token


def test_another_process_start_token_differs_from_this_one():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process_start_token(child.pid) not in (None, process_start_token(os.getpid()))
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="steps psutil's Linux boot_time")
def test_start_token_does_not_move_with_the_wall_clock(monkeypatch):
    before = process_start_token(os.getpid())
    pslinux = pytest.importorskip("psutil._pslinux")
    real = pslinux.boot_time
    monkeypatch.setattr(pslinux, "boot_time", lambda: real() + 86400)
    import psutil

    assert psutil.Process(os.getpid()).create_time() > time.time() + 80000  # psutil moved
    assert process_start_token(os.getpid()) == before  # the token did not


def test_a_peer_reads_the_token_another_process_stamped_for_itself():
    code = (
        "import sys, time; from kollabor_agent.runtime import own_start_token;"
        "print(own_start_token(), flush=True); time.sleep(30)"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        stamped = child.stdout.readline().strip()
        assert stamped and stamped == process_start_token(child.pid)
    finally:
        child.kill()
        child.wait()


# macOS and NetBSD: psutil's public ``Process.create_time()`` takes no ``monotonic``
# argument, so the token must come from the kernel's own record another way. Linux
# psutil serves the same private call, so a darwin ``sys.platform`` drives that branch.


@pytest.fixture
def macos(monkeypatch):
    pytest.importorskip("psutil")
    monkeypatch.setattr(sys, "platform", "darwin")


def test_a_macos_process_has_a_start_token_that_names_it(macos):
    mine = process_start_token(os.getpid())
    assert mine and mine.startswith("darwin:")
    assert process_start_token(os.getpid()) == mine
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process_start_token(child.pid) not in (None, mine)
    finally:
        child.kill()
        child.wait()


def test_discover_reclaims_a_reused_pid_on_macos(hub_dirs, owner_socket, macos):
    # The record holds the start of an earlier owner; an unrelated process has the pid.
    presence, _ = hub_dirs
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        earlier_owner = process_start_token(os.getpid())
        assert earlier_owner
        record = _owner_file(
            presence, owner_socket, pid=child.pid, age=660, start_token=earlier_owner
        )
        assert _peer().discover_agents(include_solo=True) == []
        assert not record.exists() and not owner_socket.exists()
    finally:
        child.kill()
        child.wait()


def test_startup_scan_keeps_a_live_owner_on_macos(hub_dirs, owner_socket, macos):
    presence, _ = hub_dirs
    token = process_start_token(os.getpid())
    assert token
    record = _owner_file(presence, owner_socket, pid=os.getpid(), age=660, start_token=token)
    assert _peer().startup_scan() == 0
    assert record.exists() and owner_socket.exists()


@pytest.mark.parametrize("platform", ["freebsd14", "openbsd7"])
def test_a_bsd_whose_start_follows_the_clock_gives_no_token(monkeypatch, platform):
    # psutil documents the FreeBSD and OpenBSD start as subject to clock updates:
    # no token, so a peer keeps the record rather than guess.
    monkeypatch.setattr(sys, "platform", platform)
    assert process_start_token(os.getpid()) is None


def test_a_macos_psutil_without_the_monotonic_flag_gives_no_token(monkeypatch, macos):
    import psutil

    class _Proc:
        def create_time(self):  # no ``monotonic`` argument
            return 1.0

    class _Process:
        _proc = _Proc()

        def __init__(self, pid):
            pass

    monkeypatch.setattr(psutil, "Process", _Process)
    assert process_start_token(os.getpid()) is None
