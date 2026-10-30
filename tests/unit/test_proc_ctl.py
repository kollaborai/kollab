"""Tests for kollabor_config.proc_ctl (kollab #268)."""

import asyncio
import contextlib
import importlib
import os
import signal
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

_SRC = (
    Path(__file__).resolve().parents[2] / "packages/kollabor-config/src/kollabor_config"
)
_PKG = "kollabor_config_proc_ctl_under_test"
_pkg = types.ModuleType(_PKG)
_pkg.__path__ = [str(_SRC)]
sys.modules[_PKG] = _pkg
proc_ctl = importlib.import_module(f"{_PKG}.proc_ctl")
HARD = proc_ctl.HARD
ChildJob = proc_ctl.ChildJob
kill = proc_ctl.kill
kill_tree = proc_ctl.kill_tree
on_stop_signal = proc_ctl.on_stop_signal
pid_alive = proc_ctl.pid_alive
process_created = proc_ctl.process_created
process_table = proc_ctl.process_table
start_child = proc_ctl.start_child
terminate = proc_ctl.terminate

posix_only = pytest.mark.skipif(
    os.name == "nt", reason="POSIX process groups and signals"
)
windows_only = pytest.mark.skipif(
    os.name != "nt", reason="Windows jobs, Toolhelp and console events"
)

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]
TREE = textwrap.dedent("""
    import subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    print(grandchild.pid, flush=True)
    time.sleep(30)
    """)
STOP_CHILD = textwrap.dedent("""
    import asyncio, importlib.util, os
    spec = importlib.util.spec_from_file_location("proc_ctl_child", os.environ["PROC_CTL_FILE"])
    proc_ctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proc_ctl)
    on_stop_signal = proc_ctl.on_stop_signal

    async def main():
        stop = asyncio.Event()
        on_stop_signal(asyncio.get_running_loop(), stop.set)
        print("ready", flush=True)
        await stop.wait()
        print("stopped", flush=True)

    asyncio.run(main())
    """)
SRC = os.path.dirname(os.path.dirname(os.path.abspath(proc_ctl.__file__)))


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def _spawn_tree() -> tuple[subprocess.Popen, int]:
    child = start_child([sys.executable, "-c", TREE], stdout=subprocess.PIPE, text=True)
    return child, int(child.stdout.readline())


def _cleanup(child: subprocess.Popen, grandchild: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(grandchild, HARD)
    if child.poll() is None:
        child.kill()
    child.wait(timeout=10)
    child.stdout.close()


def test_pid_alive_self() -> None:
    assert pid_alive(os.getpid()) is True


@posix_only
def test_start_child_is_session_leader_and_kill_ends_it() -> None:
    child = start_child(SLEEP)
    try:
        assert pid_alive(child.pid)
        assert os.getsid(child.pid) == child.pid
        kill(child.pid)
        child.wait(timeout=10)
        assert pid_alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@posix_only
def test_kill_tree_hard_ends_child_and_grandchild() -> None:
    child, grandchild = _spawn_tree()
    try:
        kill_tree(child.pid, HARD)
        child.wait(timeout=10)
        assert _wait_dead(grandchild)
    finally:
        _cleanup(child, grandchild)


@posix_only
def test_kill_tree_protect_spares_the_group() -> None:
    child, grandchild = _spawn_tree()
    try:
        kill_tree(child.pid, HARD, protect={grandchild})
        child.wait(timeout=10)
        assert pid_alive(grandchild)
    finally:
        _cleanup(child, grandchild)


@posix_only
def test_terminate_dead_pid_raises() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    with pytest.raises(ProcessLookupError):
        terminate(child.pid)


@posix_only
def test_on_stop_signal_runs_callback_and_undo_restores_handler() -> None:
    before = signal.getsignal(signal.SIGTERM)

    async def run() -> None:
        hit = asyncio.Event()
        undo = on_stop_signal(asyncio.get_running_loop(), hit.set)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(hit.wait(), 5)
        finally:
            undo()

    asyncio.run(run())
    assert signal.getsignal(signal.SIGTERM) == before


@posix_only
def test_process_created_is_none_and_process_table_unsupported() -> None:
    assert process_created(os.getpid()) is None
    with pytest.raises(NotImplementedError):
        process_table()


@windows_only
def test_pid_alive_access_denied_means_alive() -> None:
    assert pid_alive(4) is True


@windows_only
def test_pid_alive_never_calls_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_kill(*args: object, **kwargs: object) -> None:
        raise AssertionError("pid_alive must not call os.kill")

    monkeypatch.setattr(os, "kill", no_kill)
    child = start_child(SLEEP)
    try:
        assert pid_alive(child.pid) is True
        kill(child.pid)
        child.wait(timeout=10)
        assert pid_alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@windows_only
def test_hard_kill_tree_ends_grandchild() -> None:
    child, grandchild = _spawn_tree()
    try:
        assert process_table()[grandchild][0] == child.pid
        kill_tree(child.pid, HARD)
        assert _wait_dead(grandchild)
    finally:
        _cleanup(child, grandchild)


@windows_only
def test_child_job_close_ends_adopted_child() -> None:
    job = ChildJob()
    child = subprocess.Popen(SLEEP)
    if not job.adopt(child):
        child.kill()
        child.wait(timeout=10)
        pytest.skip("runner forbids nested job objects")
    try:
        job.close()
        child.wait(timeout=10)
        assert pid_alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@windows_only
def test_on_stop_signal_ctrl_break_runs_callback() -> None:
    env = {
        **os.environ,
        "PROC_CTL_FILE": proc_ctl.__file__,
    }
    child = start_child(
        [sys.executable, "-c", STOP_CHILD], stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        try:
            os.kill(child.pid, signal.CTRL_BREAK_EVENT)
        except OSError:
            pytest.skip("CTRL_BREAK_EVENT needs a console")
        out, _ = child.communicate(timeout=10)
        assert "stopped" in out
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
