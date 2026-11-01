"""Test-only ownership safeguards for deliberately detached POSIX processes.

These helpers never scan for or signal unrelated processes. Identity files contain
both PID and creation time; a group is signalled only while its own watchdog or
another identity-checked test member is still alive in that private group. The
watchdog also bounds leaks if pytest dies before a test's ``finally`` block.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import signal
import sys
import time
from pathlib import Path

import psutil

WATCHDOG_SECONDS = 60

# A non-detached test child still needs to exit if pytest itself is killed.
_BOUNDED_CHILD = r'''
import signal, sys, time
import psutil

signal.alarm(60)
try:
    owner = psutil.Process(int(sys.argv[1]))
    birth = float(sys.argv[2])
    while (owner.is_running() and owner.status() != psutil.STATUS_ZOMBIE
           and owner.create_time() == birth):
        time.sleep(0.1)
except psutil.Error:
    pass
'''


def bounded_child_command() -> list[str]:
    """A test child that exits on owner death, with a 60-second hard limit."""
    return [sys.executable, "-c", _BOUNDED_CHILD, str(os.getpid()), str(psutil.Process().create_time())]


# The watchdog is launched inside each private ShellExecutor session *before*
# any test workload. Its handshake is atomic; the shell waits for it before
# starting a workload. If pytest is killed, the watchdog sees its owner's
# death; if that check fails for any reason, its hard deadline still expires.
_WATCHDOG = r'''
import json, os, signal, sys, time
from pathlib import Path
import psutil

owner_pid, owner_birth, owner_group, ready, limit = sys.argv[1:]
owner_pid, owner_group, limit = int(owner_pid), int(owner_group), float(limit)
owner_birth = float(owner_birth)
group = os.getpgrp()
if group <= 1 or group == owner_group or group != os.getsid(0):
    raise SystemExit("watchdog requires a private session")
owner = psutil.Process(owner_pid)
if owner.create_time() != owner_birth:
    raise SystemExit("test owner changed")
ready = Path(ready)
tmp = ready.with_suffix(ready.suffix + ".tmp")
tmp.write_text(json.dumps({"pid": os.getpid(), "birth": psutil.Process().create_time(), "pgid": group}))
os.replace(tmp, ready)
deadline = time.monotonic() + limit
while time.monotonic() < deadline:
    try:
        if not owner.is_running() or owner.status() == psutil.STATUS_ZOMBIE or owner.create_time() != owner_birth:
            break
    except psutil.Error:
        break
    time.sleep(0.1)
try:
    os.killpg(group, signal.SIGKILL)
except ProcessLookupError:
    pass
'''


def watchdog_command(ready: Path) -> str:
    """Shell prefix that starts a private-group watchdog and waits for readiness."""
    owner = psutil.Process()
    args = [sys.executable, "-c", _WATCHDOG, str(os.getpid()), str(owner.create_time()),
            str(os.getpgrp()), str(ready), str(WATCHDOG_SECONDS)]
    guard = " ".join(shlex.quote(arg) for arg in args)
    path = shlex.quote(str(ready))
    error_file = shlex.quote(str(ready) + ".stderr")
    return (
        f"{guard} </dev/null >/dev/null 2>{error_file} &\n"
        "_test_guard_pid=$!; _test_guard_tries=0\n"
        f"while [ ! -s {path} ] && kill -0 \"$_test_guard_pid\" 2>/dev/null "
        '&& [ "$_test_guard_tries" -lt 250 ]; do '
        '_test_guard_tries=$((_test_guard_tries + 1)); sleep 0.02; done\n'
        f"if [ ! -s {path} ]; then printf 'test watchdog failed; see %s\\n' {error_file} >&2; exit 1; fi\n"
    )


def _identity(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid test process identity: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"invalid test process identity: {path}")
    pid, birth = data.get("pid"), data.get("birth")
    if type(pid) is not int or pid <= 1 or type(birth) not in (int, float) or not math.isfinite(birth):
        raise ValueError(f"invalid test process identity: {path}")
    return data


def wait_for_exit(process: psutil.Process, timeout: float = 5) -> None:
    """Wait for death, including an orphan zombie that only init can reap."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.02)
    raise psutil.TimeoutExpired(timeout, pid=process.pid)


def cleanup_pidfile(path: Path) -> None:
    """Kill only the still-matching process in a test-owned identity file."""
    data = _identity(path)
    if not data or data["pid"] == os.getpid():
        return
    try:
        process = psutil.Process(data["pid"])
        if process.create_time() != data["birth"]:
            return  # PID was reused; it is not the process the test created.
        process.kill()
        wait_for_exit(process)
    except psutil.NoSuchProcess:
        return


def cleanup_group(pgid: int, ready: Path, *, anchors=()) -> None:
    """Kill a private group only with a live, identity-checked test member.

    Anchors must be captured while the group's verified watchdog is alive. They
    preserve group ownership if the code under test kills the watchdog first.
    """
    if type(pgid) is not int or pgid <= 1 or pgid == os.getpgrp():
        return
    data = _identity(ready)
    if not data or data.get("pgid") != pgid or data["pid"] == os.getpid():
        return
    try:
        guard = psutil.Process(data["pid"])
        if (guard.create_time() == data["birth"] and str(ready) in guard.cmdline()
                and os.getpgid(guard.pid) == pgid):
            os.killpg(pgid, signal.SIGKILL)
            return
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass
    for process, birth in anchors:
        try:
            if (process.pid != os.getpid() and process.is_running() and process.create_time() == birth
                    and process.status() != psutil.STATUS_ZOMBIE and os.getpgid(process.pid) == pgid):
                os.killpg(pgid, signal.SIGKILL)
                return
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass
