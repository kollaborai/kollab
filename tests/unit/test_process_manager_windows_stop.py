"""Stop path of SubprocessStrategy: proc_ctl.kill_tree on Windows, killpg on POSIX."""

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(
    0, str(Path(__file__).parents[2] / "packages" / "kollabor-agent" / "src")
)

from kollabor_agent import process_manager
from kollabor_agent.process_manager import SpawnRequest, SubprocessStrategy
from kollabor_config import proc_ctl

SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(60)"]


class FakeProc:
    """Stands in for the Popen handle: no pipes, waits succeed unless told to time out."""

    pid = 4321
    stdin = stdout = stderr = None

    def __init__(self, first_wait_times_out=False):
        self.first_wait_times_out = first_wait_times_out
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        if self.first_wait_times_out:
            self.first_wait_times_out = False
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_windows_branch_stops_the_tree_with_kill_tree(monkeypatch):
    calls = []
    monkeypatch.setattr(
        proc_ctl, "kill_tree", lambda pid, sig, **kwargs: calls.append((pid, sig))
    )
    monkeypatch.setattr(process_manager, "os", SimpleNamespace(name="nt"))
    proc = FakeProc()

    assert asyncio.run(SubprocessStrategy().kill(proc)) is True
    assert calls == [(proc.pid, proc_ctl.SOFT)]
    assert not proc.terminated


def test_windows_timeout_escalates_with_hard_kill_tree(monkeypatch):
    calls = []
    monkeypatch.setattr(
        proc_ctl, "kill_tree", lambda pid, sig, **kwargs: calls.append((pid, sig))
    )
    monkeypatch.setattr(process_manager, "os", SimpleNamespace(name="nt"))
    proc = FakeProc(first_wait_times_out=True)

    assert asyncio.run(SubprocessStrategy().kill(proc)) is True
    assert calls == [(proc.pid, proc_ctl.SOFT), (proc.pid, proc_ctl.HARD)]
    assert not proc.killed


def test_windows_gone_process_falls_back_to_terminate(monkeypatch):
    def kill_tree_gone(pid, sig, **kwargs):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(proc_ctl, "kill_tree", kill_tree_gone)
    monkeypatch.setattr(process_manager, "os", SimpleNamespace(name="nt"))
    proc = FakeProc()

    assert asyncio.run(SubprocessStrategy().kill(proc)) is True
    assert proc.terminated


@pytest.mark.skipif(os.name == "nt", reason="POSIX killpg path")
def test_posix_kill_still_signals_the_process_group(monkeypatch):
    sent = []

    def record_killpg(pgid, sig):
        sent.append((pgid, sig))
        os.killpg(pgid, sig)

    async def run():
        strategy = SubprocessStrategy()
        spawned = await strategy.spawn(SpawnRequest(name="sleeper", cmd=SLEEP_CMD))
        monkeypatch.setattr(
            process_manager,
            "os",
            SimpleNamespace(name="posix", getpgid=os.getpgid, killpg=record_killpg),
        )
        ok = await strategy.kill(spawned.handle)
        return ok, spawned.pid

    ok, pid = asyncio.run(run())
    assert ok is True
    assert sent == [(pid, signal.SIGTERM)]  # start_new_session makes the pid its group


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree stop")
def test_windows_kill_ends_a_real_process():
    async def run():
        strategy = SubprocessStrategy()
        spawned = await strategy.spawn(SpawnRequest(name="sleeper", cmd=SLEEP_CMD))
        ok = await strategy.kill(spawned.handle)
        return ok, spawned.handle

    ok, proc = asyncio.run(run())
    try:
        assert ok is True
        proc.wait(timeout=10)  # raises TimeoutExpired if the process is still alive
    finally:
        if proc.poll() is None:
            proc.kill()
