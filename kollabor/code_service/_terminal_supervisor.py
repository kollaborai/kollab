"""Linux terminal subreaper, isolated to one terminal's process tree."""

import ctypes
import os
import signal
import sys
import time
from pathlib import Path

_GRACE = 1.0
_REAP_LIMIT = 2.0
_STOP = False


def _stop(_signum, _frame):
    global _STOP
    _STOP = True


def _stat(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rfind(")") + 2 :].split()
        return int(fields[1]), int(fields[19])  # parent PID and start time
    except (OSError, ValueError, IndexError):
        return None


def _children(pid):
    result = set()
    try:
        tasks = list(Path(f"/proc/{pid}/task").iterdir())
    except OSError:
        return result
    for task in tasks:
        try:
            result.update(int(value) for value in (task / "children").read_text().split())
        except (OSError, ValueError):
            continue
    return result


def _descendants():
    root = os.getpid()
    pending = [root]
    found = {}
    while pending:
        parent = pending.pop()
        for pid in _children(parent):
            stat = _stat(pid)
            if stat is not None and stat[0] == parent and pid not in found:
                found[pid] = stat[1]
                pending.append(pid)
    return found


def _owned(pid, start):
    root = os.getpid()
    seen = set()
    current = pid
    while current != root and current not in seen:
        seen.add(current)
        stat = _stat(current)
        if stat is None or (current == pid and stat[1] != start):
            return False
        current = stat[0]
    return current == root


def _signal(pid, start, sig):
    fd = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                fd = os.pidfd_open(pid)
            except OSError:
                # Recheck identity and ancestry before the portable signal path.
                fd = None
        if not _owned(pid, start):
            return
        if fd is not None:
            signal.pidfd_send_signal(fd, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    finally:
        if fd is not None:
            os.close(fd)


def main():
    ready_fd = int(sys.argv[1])
    shell = sys.argv[2]
    args = sys.argv[2:]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        os.write(ready_fd, b"error\n")
        os._exit(1)
    signal.signal(signal.SIGHUP, _stop)
    signal.signal(signal.SIGTERM, _stop)
    child = os.fork()
    if child == 0:
        os.close(ready_fd)
        os.login_tty(0)
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGPIPE, signal.SIGXFSZ):
            signal.signal(sig, signal.SIG_DFL)
        os.execv(shell, args)
    os.write(ready_fd, f"{child}\n".encode())
    os.close(ready_fd)
    for fd in (0, 1, 2):
        os.close(fd)
    shell_code = 0
    deadline = None
    signalled = set()
    while True:
        no_children = False
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                no_children = True
                break
            if not pid:
                break
            if pid == child:
                shell_code = os.waitstatus_to_exitcode(status)
                deadline = deadline or time.monotonic() + _GRACE
        if _STOP and deadline is None:
            deadline = time.monotonic() + _GRACE
        if deadline is not None:
            for pid, start in _descendants().items():
                identity = (pid, start)
                if identity not in signalled:
                    _signal(pid, start, signal.SIGHUP)
                    _signal(pid, start, signal.SIGTERM)
                    signalled.add(identity)
                if time.monotonic() >= deadline:
                    _signal(pid, start, signal.SIGKILL)
        if no_children:
            break
        if deadline is not None and time.monotonic() >= deadline + _REAP_LIMIT:
            # The daemon must not wait forever on an unsignalable or uninterruptible child.
            os._exit(125)
        time.sleep(0.02)
    os._exit((shell_code if shell_code >= 0 else 128 - shell_code) & 0xff)


if __name__ == "__main__":
    main()
