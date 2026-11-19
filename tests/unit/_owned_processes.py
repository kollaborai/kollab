"""Test-only ownership safeguards for deliberately detached POSIX processes.

These helpers never scan for or signal unrelated processes. A private process
group is signalled only by its own live watchdog, while that watchdog keeps the
group ID from being recycled. PID files are never sufficient authority to
signal a process; callers need a retained direct-child handle. If the watchdog
is missing or dies, callers wait a bounded time for recorded identities, then
report that cleanup is deferred to the ephemeral CI runner; they do not signal
a numeric PID or PGID as a fallback.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shlex
import sys
import time
from collections.abc import Iterable
from importlib import import_module
from pathlib import Path

import psutil

WATCHDOG_SECONDS = 60


def asyncio_child_signal_is_reap_serialized() -> bool:
    """Whether the active stdlib watcher serializes reaping on its event loop.

    POSIX ``Process.terminate/kill`` still signal numeric PIDs. Enable them
    only for the known CPython selector loop's pidfd watcher, or its threaded
    watcher when waitid(WNOWAIT) prevents a background reap before the event
    loop callback. Unknown loops/watchers fail closed. Windows uses the
    subprocess process handle for signaling.
    """
    if sys.implementation.name != "cpython":
        return False
    if os.name == "nt":
        return True
    if os.name != "posix":
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    try:
        unix_events = import_module("asyncio.unix_events")
    except ImportError:
        return False
    loop_type = getattr(unix_events, "_UnixSelectorEventLoop", None)
    if loop_type is None or type(loop) is not loop_type:
        return False
    watcher = getattr(loop, "_watcher", None)
    pidfd_watcher_type = getattr(unix_events, "_PidfdChildWatcher", None)
    if pidfd_watcher_type is not None and type(watcher) is pidfd_watcher_type:
        return hasattr(os, "pidfd_open")
    threaded_watcher_type = getattr(unix_events, "_ThreadedChildWatcher", None)
    if threaded_watcher_type is not None and type(watcher) is threaded_watcher_type:
        return hasattr(os, "waitid") and hasattr(os, "WNOWAIT")
    return False


# A non-detached test child still needs to exit if pytest itself is killed.
_BOUNDED_CHILD = r"""
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
"""


def bounded_child_command() -> list[str]:
    """A test child that exits on owner death, with a 60-second hard limit."""
    return [sys.executable, "-c", _BOUNDED_CHILD, str(os.getpid()), str(psutil.Process().create_time())]


# The watchdog is launched inside each private ShellExecutor session *before*
# any test workload. Its handshake is atomic; the shell waits for it before
# starting a workload. If pytest is killed, the watchdog sees its owner's
# death; if that check fails for any reason, its hard deadline still expires.
# Cleanup requests also go through the watchdog, which verifies and signals
# its own group while it is alive. The live member prevents the PGID from being
# recycled before the group signal. This bound applies only while the watchdog
# remains alive; otherwise callers report survivors and defer to runner teardown.
_WATCHDOG = r"""
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
stop_request = ready.with_suffix(ready.suffix + ".stop")
deadline = time.monotonic() + limit
while time.monotonic() < deadline:
    try:
        if stop_request.exists():
            break
        if not owner.is_running() or owner.status() == psutil.STATUS_ZOMBIE or owner.create_time() != owner_birth:
            break
    except psutil.Error:
        break
    time.sleep(0.1)
try:
    if os.getpgrp() != group or os.getsid(0) != group:
        raise SystemExit("watchdog left its private process group")
    os.killpg(group, signal.SIGKILL)
except ProcessLookupError:
    pass
"""


def watchdog_command(ready: Path) -> str:
    """Shell prefix that starts a private-group watchdog and waits for readiness."""
    owner = psutil.Process()
    args = [
        sys.executable,
        "-c",
        _WATCHDOG,
        str(os.getpid()),
        str(owner.create_time()),
        str(os.getpgrp()),
        str(ready),
        str(WATCHDOG_SECONDS),
    ]
    guard = " ".join(shlex.quote(arg) for arg in args)
    path = shlex.quote(str(ready))
    error_file = shlex.quote(str(ready) + ".stderr")
    return (
        f"{guard} </dev/null >/dev/null 2>{error_file} &\n"
        "_test_guard_pid=$!; _test_guard_tries=0\n"
        f'while [ ! -s {path} ] && kill -0 "$_test_guard_pid" 2>/dev/null '
        '&& [ "$_test_guard_tries" -lt 250 ]; do '
        "_test_guard_tries=$((_test_guard_tries + 1)); sleep 0.02; done\n"
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


def wait_for_recorded_identities(identities: Iterable[tuple[int, float]], timeout: float = 5) -> None:
    """Observe exact recorded PIDs for a bounded time without signaling them.

    A PID whose creation time changed is treated as the old identity's exit.
    If a same-birth process remains or cannot be verified by the deadline, the
    caller must report deferred cleanup; this helper never falls back to kill.
    """
    checked = tuple(identities)
    for pid, birth in checked:
        if type(pid) is not int or pid <= 1 or type(birth) not in (int, float) or not math.isfinite(birth):
            raise ValueError("invalid recorded process identity; no signal attempted")
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout must be finite and nonnegative")

    deadline = time.monotonic() + timeout
    while True:
        pending = []
        for pid, birth in checked:
            try:
                process = psutil.Process(pid)
                if process.create_time() != birth:
                    continue
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    pending.append(pid)
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as exc:
                raise RuntimeError(f"could not verify recorded process {pid}; no signal attempted") from exc

        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"recorded processes remain after {timeout:g}s: {pending}; "
                "no PID/PGID fallback; defer cleanup to the ephemeral CI runner"
            )
        time.sleep(min(0.05, remaining))


def cleanup_pidfile(path: Path, *, process=None) -> None:
    """Clean up only through a retained direct-child handle, never a bare PID."""
    data = _identity(path)
    if not data or process is None or data["pid"] == os.getpid():
        return
    # The retained Popen handle is the authority: poll() either reaps an exited
    # direct child or returns None while this process is still its sole reaper.
    # The caller must serialize this synchronous call with every poll/wait on
    # that handle; this helper adds no lock. Popen's kill() still sends a numeric
    # PID signal; the unreaped-child/sole-reaper condition prevents reuse here,
    # but this is not an atomic OS process handle.
    if process.pid != data["pid"] or process.poll() is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        return
    process.wait(timeout=5)


def _request_watchdog_stop(ready: Path) -> None:
    request = ready.with_suffix(ready.suffix + ".stop")
    try:
        fd = os.open(request, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    os.close(fd)


def cleanup_group(pgid: int, ready: Path) -> None:
    """Ask the group's live watchdog to stop its own group, else fail closed.

    The parent never resolves or signals a numeric PID. If startup has not
    published its identity yet, or the watchdog has already exited, a stop
    request is harmless: only this test's watchdog reads this private path.
    There is intentionally no anchor fallback: after a watchdog dies, the
    caller observes exact recorded identities for a bounded time and reports
    survivors for ephemeral-runner cleanup.
    """
    if type(pgid) is not int or pgid <= 1 or pgid == os.getpgrp():
        return
    data = _identity(ready)
    if data is not None and data.get("pgid") != pgid:
        return
    _request_watchdog_stop(ready)
