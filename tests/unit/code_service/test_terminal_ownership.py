"""Process ownership helpers: reject unrelated and reused process identities."""

import ctypes
import os
import struct
from types import SimpleNamespace

from kollabor.code_service import _terminal_darwin, _terminal_supervisor


def test_supervisor_rejects_unrelated_and_reused_pids(monkeypatch):
    entries = {12: (10, 100), 13: (12, 200), 99: (1, 300)}
    monkeypatch.setattr(_terminal_supervisor.os, "getpid", lambda: 10)
    monkeypatch.setattr(_terminal_supervisor, "_stat", entries.get)
    assert _terminal_supervisor._owned(13, 200)
    assert not _terminal_supervisor._owned(13, 201)
    assert not _terminal_supervisor._owned(99, 300)


def test_supervisor_does_not_signal_unverified_pid(monkeypatch):
    sent = []
    monkeypatch.delattr(_terminal_supervisor.os, "pidfd_open", raising=False)
    monkeypatch.setattr(_terminal_supervisor, "_owned", lambda pid, start: (pid, start) == (12, 100))
    monkeypatch.setattr(_terminal_supervisor.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    _terminal_supervisor._signal(13, 200, 9)
    _terminal_supervisor._signal(12, 101, 9)
    _terminal_supervisor._signal(12, 100, 9)
    assert sent == [(12, 9)]


def test_darwin_pty_holder_requires_matching_device_inode_and_start(monkeypatch):
    class Process:
        def __init__(self, pid, started):
            self.pid = pid
            self.started = started

        def uids(self):
            return SimpleNamespace(real=os.getuid())

        def create_time(self):
            return self.started

        def is_running(self):
            return True

    class Function:
        def __init__(self, fn):
            self.fn = fn
            self.argtypes = None

        def __call__(self, *args):
            return self.fn(*args)

    def pidinfo(_pid, _kind, _arg, buf, _size):
        if buf is None:
            return 8
        ctypes.memmove(buf, struct.pack("=iI", 4, 1), 8)
        return 8

    def fdinfo(pid, _fd, _kind, buf, _size):
        raw = bytearray(1200)
        struct.pack_into("=I", raw, 24, 12)
        struct.pack_into("=Q", raw, 32, 44 if pid == 10001 else 45)
        raw[176:185] = b"/dev/ttyX"
        ctypes.memmove(buf, bytes(raw), len(raw))
        return len(raw)

    lib = SimpleNamespace(proc_pidinfo=Function(pidinfo), proc_pidfdinfo=Function(fdinfo))
    monkeypatch.setattr(_terminal_darwin.ctypes, "CDLL", lambda *_args, **_kw: lib)
    monkeypatch.setattr(_terminal_darwin.psutil, "process_iter", lambda: [
        Process(10001, 20), Process(10002, 20), Process(10003, 1),
    ])
    holders = list(_terminal_darwin.pty_holders("/dev/ttyX", (12, 44), 10))
    assert [p.pid for p in holders] == [10001]


def test_darwin_libproc_finds_own_child_pty_descriptor(monkeypatch):
    import subprocess
    import sys

    import psutil
    import pytest

    if sys.platform != "darwin":
        pytest.skip("Darwin libproc is not available")
    master, slave = os.openpty()
    child = None
    try:
        info = os.fstat(slave)
        path = os.ttyname(slave)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 stdin=slave, stdout=slave, stderr=slave)
        process = psutil.Process(child.pid)
        monkeypatch.setattr(_terminal_darwin.psutil, "process_iter", lambda: [process])
        holders = list(_terminal_darwin.pty_holders(path, (info.st_dev & 0xFFFFFFFF, info.st_ino), 0))
        assert [holder.pid for holder in holders] == [child.pid]
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=5)
        os.close(slave)
        os.close(master)
