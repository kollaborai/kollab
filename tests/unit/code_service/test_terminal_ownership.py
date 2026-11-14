"""Process ownership helpers: reject unrelated and reused process identities."""

import ctypes
import os
import runpy
import struct
import sys
from types import SimpleNamespace

import pytest

from kollabor.code_service import _terminal_darwin, _terminal_supervisor


def test_supervisor_script_calls_main_when_executed(monkeypatch):
    writes = []

    class ExitCalled(Exception):
        pass

    class Lib:
        @staticmethod
        def prctl(*_args):
            return -1

    monkeypatch.setattr(_terminal_supervisor.ctypes, "CDLL", lambda *_args, **_kwargs: Lib())
    monkeypatch.setattr(_terminal_supervisor.os, "write", lambda _fd, data: writes.append(data) or len(data))
    monkeypatch.setattr(
        _terminal_supervisor.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(ExitCalled(code)),
    )
    monkeypatch.setattr(sys, "argv", [_terminal_supervisor.__file__, "9", "/bin/sh"])

    with pytest.raises(ExitCalled) as exited:
        runpy.run_path(_terminal_supervisor.__file__, run_name="__main__")

    assert exited.value.args == (1,)
    assert writes == [b"error\n"]


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
        def __init__(self, pid, started, live=True, uid=None):
            self.pid = pid
            self.started = started
            self.live = live
            self.uid = os.getuid() if uid is None else uid

        def uids(self):
            return SimpleNamespace(real=self.uid)

        def create_time(self):
            return self.started

        def is_running(self):
            return self.live

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
        struct.pack_into("=Q", raw, 32, 44 if pid in (10001, 10004, 10005) else 45)
        raw[176:185] = b"/dev/ttyX"
        ctypes.memmove(buf, bytes(raw), len(raw))
        return len(raw)

    lib = SimpleNamespace(proc_pidinfo=Function(pidinfo), proc_pidfdinfo=Function(fdinfo))
    monkeypatch.setattr(_terminal_darwin.ctypes, "CDLL", lambda *_args, **_kw: lib)
    processes = [
        Process(10001, 20), Process(10002, 20), Process(10003, 1),
        Process(10004, 20, live=False), Process(10005, 20, uid=os.getuid() + 1),
    ]
    holders = list(_terminal_darwin.pty_holders("/dev/ttyX", (12, 44), 10, processes))
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
        holders = list(_terminal_darwin.pty_holders(path, (info.st_dev & 0xFFFFFFFF, info.st_ino), 0, [process]))
        assert [holder.pid for holder in holders] == [child.pid]
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=5)
        os.close(slave)
        os.close(master)


def test_capture_does_not_rescan_process_tree_for_every_known_process(monkeypatch):
    from kollabor.code_service import terminal

    calls = []

    class Process:
        def __init__(self, pid, parent):
            self.pid = pid
            self.parent_pid = parent

        def is_running(self):
            return True

        def ppid(self):
            return self.parent_pid

        def create_time(self):
            return 100.0

        def children(self, recursive=False):
            calls.append(self.pid)
            return []

    shell = Process(1000, 1)
    processes = [shell, *(Process(pid, 1000) for pid in range(1001, 1031))]
    session = object.__new__(terminal._Session)
    session._owned_processes = set(processes)
    session._supervised = False
    session._shell_process = shell
    session._proc = SimpleNamespace(returncode=None)
    session.pid = shell.pid
    session._inspection_warnings = set()
    session._shell_started = 100.0
    session._pty_name = None
    monkeypatch.setattr(terminal.psutil, "process_iter", lambda: iter(processes))
    monkeypatch.setattr(terminal.os, "getsid", lambda _pid: shell.pid)
    session._collect_processes(session._owned_processes)
    assert len(calls) <= 1, f"capture recursively scanned the system {len(calls)} times"


def test_supervisor_stops_waiting_for_an_unreapable_child(monkeypatch):
    class Exit(Exception):
        pass

    class Clock:
        value = 0.0

        def monotonic(self):
            self.value += 0.5
            return self.value

        def sleep(self, _seconds):
            assert self.value < 20, "supervisor did not honor its reap deadline"

    def exit_process(code):
        raise Exit(code)

    fake_os = SimpleNamespace(
        write=lambda *_args: None, close=lambda *_args: None, fork=lambda: 123,
        waitpid=lambda *_args: (0, 0), WNOHANG=1, _exit=exit_process,
    )
    fake_signal = SimpleNamespace(SIGHUP=1, SIGTERM=15, signal=lambda *_args: None)
    fake_ctypes = SimpleNamespace(CDLL=lambda *_args, **_kwargs: SimpleNamespace(prctl=lambda *_args: 0))
    monkeypatch.setattr(_terminal_supervisor, "os", fake_os)
    monkeypatch.setattr(_terminal_supervisor, "signal", fake_signal)
    monkeypatch.setattr(_terminal_supervisor, "ctypes", fake_ctypes)
    monkeypatch.setattr(_terminal_supervisor, "time", Clock())
    monkeypatch.setattr(_terminal_supervisor, "sys", SimpleNamespace(argv=["supervisor", "4", "/bin/sh"]))
    monkeypatch.setattr(_terminal_supervisor, "_descendants", lambda: {})
    monkeypatch.setattr(_terminal_supervisor, "_STOP", True)
    import pytest

    with pytest.raises(Exit) as outcome:
        _terminal_supervisor.main()
    assert outcome.value.args == (125,)


def test_capture_rejects_process_older_than_its_apparent_parent(monkeypatch):
    from kollabor.code_service import terminal

    class Process:
        def __init__(self, pid, parent, started):
            self.pid, self.parent, self.started = pid, parent, started

        def is_running(self):
            return True

        def ppid(self):
            return self.parent

        def create_time(self):
            return self.started

    shell = Process(1000, 1, 100.0)
    unrelated = Process(1001, 1000, 50.0)
    session = object.__new__(terminal._Session)
    session._owned_processes = {shell}
    session._supervised = False
    session._shell_process = shell
    session._proc = SimpleNamespace(returncode=None)
    session.pid = shell.pid
    session._inspection_warnings = set()
    session._shell_started = shell.started
    session._pty_name = None
    monkeypatch.setattr(terminal.psutil, "process_iter", lambda: iter([shell, unrelated]))
    monkeypatch.setattr(terminal.os, "getsid", lambda _pid: shell.pid)
    result = session._collect_processes(session._owned_processes)
    assert result == {shell}


@pytest.mark.parametrize("failure", ["access_denied", "os_error", "partial"])
def test_capture_uses_pty_fallback_when_psutil_enumeration_denied(monkeypatch, failure):
    import psutil

    from kollabor.code_service import terminal

    shell = object()
    holder = object()
    session = object.__new__(terminal._Session)
    session._supervised = False
    session._shell_process = shell
    session._inspection_warnings = set()
    session._pty_name = "/dev/ttys-test"
    session._pty_identity = (12, 44)
    session._pty_anchor = object()
    session._shell_started = 100.0
    calls = []

    def denied():
        if failure == "os_error":
            raise OSError("process listing failed")
        if failure == "partial":
            def interrupted():
                yield object()
                raise psutil.AccessDenied(pid=1)
            return interrupted()
        raise psutil.AccessDenied(pid=1)

    def holders(path, identity, started, processes):
        calls.append((path, identity, started, processes))
        return [holder]

    monkeypatch.setattr(terminal.psutil, "process_iter", denied)
    monkeypatch.setattr(_terminal_darwin, "pty_holders", holders)
    assert session._collect_processes({shell}) == {shell, holder}
    assert calls == [("/dev/ttys-test", (12, 44), 100.0, None)]


def test_darwin_pty_holders_enumerates_with_libproc_without_psutil_list(monkeypatch):
    import psutil

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def uids(self):
            return SimpleNamespace(real=os.getuid())

        def create_time(self):
            return 1 if self.pid == 10003 else 20

        def is_running(self):
            return True

    class Function:
        def __init__(self, fn):
            self.fn = fn
            self.argtypes = None

        def __call__(self, *args):
            return self.fn(*args)

    def listall(buf, size):
        if buf is None:
            return 3
        assert size >= 3 * ctypes.sizeof(ctypes.c_int)
        ctypes.memmove(buf, struct.pack("=iii", 10001, 10002, 10003), 12)
        return 3

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

    def denied():
        raise psutil.AccessDenied(pid=1)

    lib = SimpleNamespace(proc_listallpids=Function(listall), proc_pidinfo=Function(pidinfo),
                          proc_pidfdinfo=Function(fdinfo))
    monkeypatch.setattr(_terminal_darwin.ctypes, "CDLL", lambda *_args, **_kw: lib)
    monkeypatch.setattr(_terminal_darwin.psutil, "process_iter", denied)
    monkeypatch.setattr(_terminal_darwin.psutil, "Process", Process)
    holders = list(_terminal_darwin.pty_holders("/dev/ttyX", (12, 44), 10))
    assert [p.pid for p in holders] == [10001]


def test_libproc_pid_list_retries_a_full_buffer():
    calls = []

    class Function:
        argtypes = None

        def __call__(self, buf, size):
            calls.append(size)
            if buf is None:
                return 3
            if len(calls) == 2:
                return size // ctypes.sizeof(ctypes.c_int)
            ctypes.memmove(buf, struct.pack("=iii", 11, 12, 13), 12)
            return 3

    assert _terminal_darwin._libproc_pids(SimpleNamespace(proc_listallpids=Function())) == [11, 12, 13]
    assert calls == [0, 256 * ctypes.sizeof(ctypes.c_int), 512 * ctypes.sizeof(ctypes.c_int)]


def test_darwin_libproc_fallback_finds_own_child_when_psutil_listing_fails(monkeypatch):
    import subprocess
    import sys

    import psutil

    from kollabor.code_service import terminal

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
        session = object.__new__(terminal._Session)
        session._supervised = False
        session._shell_process = process
        session._inspection_warnings = set()
        session._pty_name = path
        session._pty_identity = (info.st_dev & 0xFFFFFFFF, info.st_ino)
        session._pty_anchor = object()
        session._shell_started = process.create_time()

        def denied():
            raise psutil.AccessDenied(pid=1)

        monkeypatch.setattr(terminal.psutil, "process_iter", denied)
        monkeypatch.setattr(_terminal_darwin, "_libproc_pids", lambda _lib: [child.pid])
        assert session._collect_processes(set()) == {process}
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=5)
        os.close(slave)
        os.close(master)


def test_supervised_collect_never_uses_platform_process_enumeration(monkeypatch):
    from kollabor.code_service import terminal

    session = object.__new__(terminal._Session)
    session._supervised = True
    known = {object()}

    def unexpected():
        raise AssertionError("supervised terminals must not enumerate processes here")

    monkeypatch.setattr(terminal.psutil, "process_iter", unexpected)
    assert session._collect_processes(known) == known


def test_denied_psutil_and_libproc_does_not_invent_owners(monkeypatch):
    import psutil

    from kollabor.code_service import terminal

    shell = object()
    session = object.__new__(terminal._Session)
    session._supervised = False
    session._shell_process = shell
    session._inspection_warnings = set()
    session._pty_name = "/dev/ttys-test"
    session._pty_identity = (12, 44)
    session._pty_anchor = object()
    session._shell_started = 100.0

    def denied():
        raise psutil.AccessDenied(pid=1)

    def unavailable(*_args):
        raise OSError("libproc unavailable")

    monkeypatch.setattr(terminal.psutil, "process_iter", denied)
    monkeypatch.setattr(_terminal_darwin, "pty_holders", unavailable)
    assert session._collect_processes({shell}) == {shell}
    assert session._inspection_warnings == {"tree", "pty"}
