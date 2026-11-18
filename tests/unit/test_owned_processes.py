"""Regression tests for the test suite's own subprocess cleanup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from tests.unit import _owned_processes as owned

pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")


def _wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for subprocess state")


def _alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_wait_for_exit_accepts_an_unreaped_orphan_zombie():
    class Zombie:
        pid = 123

        def is_running(self):
            return True

        def status(self):
            return psutil.STATUS_ZOMBIE

    owned.wait_for_exit(Zombie(), timeout=0.5)


def test_pidfile_cleanup_stops_its_process(tmp_path):
    child = subprocess.Popen(owned.bounded_child_command())
    try:
        process = psutil.Process(child.pid)
        identity = tmp_path / "child.json"
        identity.write_text(json.dumps({"pid": child.pid, "birth": process.create_time()}))
        owned.cleanup_pidfile(identity, process=child)
        child.wait(timeout=5)
        assert not _alive(process)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_pidfile_cleanup_refuses_a_reused_pid(tmp_path, monkeypatch):
    identity = tmp_path / "other.json"
    identity.write_text(json.dumps({"pid": 123456, "birth": 100.0}))
    lookups = []

    def process_lookup(pid):
        lookups.append(pid)
        raise AssertionError("PID-only cleanup must not resolve or signal a process")

    monkeypatch.setattr(owned.psutil, "Process", process_lookup)
    owned.cleanup_pidfile(identity)
    assert lookups == []


def test_pidfile_cleanup_uses_only_a_matching_direct_child_handle(tmp_path):
    identity = tmp_path / "child.json"
    identity.write_text(json.dumps({"pid": 123456, "birth": 100.0}))
    calls = []
    process = SimpleNamespace(
        pid=123456,
        poll=lambda: None,
        kill=lambda: calls.append("kill"),
        wait=lambda timeout: calls.append(("wait", timeout)),
    )

    owned.cleanup_pidfile(identity, process=process)

    assert calls == ["kill", ("wait", 5)]


def test_pidfile_cleanup_rejects_invalid_data(tmp_path):
    identity = tmp_path / "bad.json"
    identity.write_text('{"pid": 1}')
    with pytest.raises(ValueError):
        owned.cleanup_pidfile(identity)


def test_watchdog_startup_failure_reports_where_to_find_details(tmp_path, monkeypatch):
    ready = tmp_path / "guard.json"
    monkeypatch.setattr(owned, "_WATCHDOG", "raise SystemExit('intentional guard failure')")
    result = subprocess.run(
        ["/bin/sh", "-c", owned.watchdog_command(ready)],
        capture_output=True,
        text=True,
        timeout=10,
        start_new_session=True,
    )
    assert result.returncode != 0
    assert "test watchdog failed" in result.stderr
    assert str(ready) + ".stderr" in result.stderr
    assert "intentional guard failure" in (tmp_path / "guard.json.stderr").read_text()


def test_bounded_child_exits_when_its_owner_is_killed(tmp_path):
    identity_file = tmp_path / "child.json"
    driver_code = r"""
import json, os, signal, subprocess, sys, time
import psutil
from tests.unit._owned_processes import bounded_child_command
signal.alarm(45)
owner = psutil.Process(int(sys.argv[2]))
birth = float(sys.argv[3])
child = subprocess.Popen(bounded_child_command(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with open(sys.argv[1] + '.tmp', 'w') as stream:
    json.dump({'pid': child.pid, 'birth': psutil.Process(child.pid).create_time()}, stream)
os.replace(sys.argv[1] + '.tmp', sys.argv[1])
try:
    while owner.is_running() and owner.status() != psutil.STATUS_ZOMBIE and owner.create_time() == birth:
        time.sleep(0.1)
except psutil.Error:
    pass
"""
    driver = subprocess.Popen(
        [sys.executable, "-c", driver_code, str(identity_file), str(os.getpid()), str(psutil.Process().create_time())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    child = None
    try:
        _wait_for(lambda: identity_file.exists() or driver.poll() is not None)
        if driver.poll() is not None:
            raise AssertionError(f"driver exited: {driver.stderr.read().decode(errors='replace')}")
        identity = json.loads(identity_file.read_text())
        child = psutil.Process(identity["pid"])
        assert child.create_time() == identity["birth"] and _alive(child)
        driver.kill()
        _wait_for(lambda: not _alive(child))
    finally:
        if driver.poll() is None:
            driver.kill()
        driver.wait(timeout=5)
        if driver.stderr:
            driver.stderr.close()


def test_group_cleanup_refuses_an_unrelated_watchdog(tmp_path, monkeypatch):
    process = psutil.Process()
    ready = tmp_path / "guard.json"
    ready.write_text(json.dumps({"pid": process.pid, "birth": process.create_time() - 100, "pgid": os.getpgrp()}))
    signals = []
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))
    owned.cleanup_group(os.getpgrp(), ready)
    assert not signals


@pytest.mark.parametrize("exit_mode", ["kill_owner", "term_owner", "deadline"])
def test_watchdog_stops_its_group_after_owner_death_or_deadline(tmp_path, exit_mode):
    """An interrupted pytest or a hard deadline stops the private group."""
    ready = tmp_path / "guard.json"
    sleeper_file = tmp_path / "sleeper.pid"
    driver_code = r"""
import shlex, signal, subprocess, sys, time
from pathlib import Path
import psutil
from tests.unit import _owned_processes as owned
signal.alarm(60)
ready, sleeper = map(Path, sys.argv[1:3])
owner = psutil.Process(int(sys.argv[4]))
birth = float(sys.argv[5])
if sys.argv[3] == 'deadline':
    owned.WATCHDOG_SECONDS = 1
command = owned.watchdog_command(ready) + "sleep 60 & printf '%s' $! > " + shlex.quote(str(sleeper)) + "; wait"
subprocess.Popen(['/bin/sh', '-c', command], start_new_session=True,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    while owner.is_running() and owner.status() != psutil.STATUS_ZOMBIE and owner.create_time() == birth:
        time.sleep(0.1)
except psutil.Error:
    pass
"""
    driver = subprocess.Popen(
        [
            sys.executable,
            "-c",
            driver_code,
            str(ready),
            str(sleeper_file),
            exit_mode,
            str(os.getpid()),
            str(psutil.Process().create_time()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    sleeper = guard = None
    pgid = None
    try:
        _wait_for(lambda: ready.exists() and sleeper_file.exists() or driver.poll() is not None)
        if driver.poll() is not None:
            raise AssertionError(f"driver exited: {driver.stderr.read().decode(errors='replace')}")
        data = json.loads(ready.read_text())
        pgid = data["pgid"]
        assert pgid != os.getpgrp()
        guard = psutil.Process(data["pid"])
        sleeper = psutil.Process(int(sleeper_file.read_text()))
        assert _alive(guard) and _alive(sleeper)
        if exit_mode == "kill_owner":
            driver.kill()
        elif exit_mode == "term_owner":
            driver.terminate()
        # Do not reap yet: the owner can remain a zombie in real runs.
        _wait_for(lambda: not _alive(guard) and not _alive(sleeper))
        if exit_mode == "deadline":
            assert driver.poll() is None
    finally:
        if driver.poll() is None:
            driver.kill()
        driver.wait(timeout=5)
        if pgid is not None:
            owned.cleanup_group(pgid, ready)
        if driver.stderr:
            driver.stderr.close()


@pytest.mark.parametrize("exit_mode", ["kill_owner", "term_owner"])
def test_detached_acp_child_exits_after_test_owner_is_killed(tmp_path, exit_mode):
    """The fast-fork fixture has its own parent-death and time bound."""
    child_file = tmp_path / "orphan.json"
    driver_code = r"""
import os, signal, subprocess, sys, time
import psutil
from tests.unit.test_acp_bridge import FAST_ORPHAN_ADAPTER
signal.alarm(45)
owner = psutil.Process(int(sys.argv[2]))
birth = float(sys.argv[3])
env = dict(os.environ, ACP_OWNED_CHILD_FILE=sys.argv[1], ACP_OWNED_CHILD_MODE='startup',
           ACP_TEST_OWNER_PID=str(os.getpid()), ACP_TEST_OWNER_BIRTH=str(psutil.Process().create_time()))
subprocess.run([sys.executable, '-c', FAST_ORPHAN_ADAPTER], env=env, timeout=10, check=True,
               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    while owner.is_running() and owner.status() != psutil.STATUS_ZOMBIE and owner.create_time() == birth:
        time.sleep(0.1)
except psutil.Error:
    pass
"""
    driver = subprocess.Popen(
        [sys.executable, "-c", driver_code, str(child_file), str(os.getpid()), str(psutil.Process().create_time())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    child = None
    try:
        _wait_for(lambda: child_file.exists() or driver.poll() is not None)
        if driver.poll() is not None:
            raise AssertionError(f"driver exited: {driver.stderr.read().decode(errors='replace')}")
        identity = json.loads(child_file.read_text())
        child = psutil.Process(identity["pid"])
        assert child.create_time() == identity["birth"] and _alive(child)
        if exit_mode == "kill_owner":
            driver.kill()
        else:
            driver.terminate()
        # The child must notice death even before the owner is reaped.
        _wait_for(lambda: not _alive(child))
    finally:
        if driver.poll() is None:
            driver.kill()
        driver.wait(timeout=5)
        if driver.stderr:
            driver.stderr.close()
