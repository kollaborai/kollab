"""Process liveness, termination and stop signals that work on POSIX and Windows (kollab #268).

The POSIX branches keep today's behavior. The Windows branches call Win32 through ctypes.
"""

import asyncio
import errno
import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

SOFT = signal.SIGTERM
HARD = getattr(signal, "SIGKILL", 9)

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    ERROR_ACCESS_DENIED = 5
    ERROR_INVALID_PARAMETER = 87

    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
    WAIT_OBJECT_0 = 0x0
    WAIT_TIMEOUT = 0x102
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9  # JobObjectExtendedLimitInformation
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    P = ctypes.POINTER
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [P(_FILETIME)] * 4
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, P(_PROCESSENTRY32W)]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, P(_PROCESSENTRY32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

    def _open_process(pid: int, access: int) -> int | None:
        """Handle to ``pid``; None on failure, with the error in ``ctypes.get_last_error()``."""
        return _kernel32.OpenProcess(access, False, pid) or None

    def _win_terminate(pid: int) -> None:
        h = _open_process(pid, PROCESS_TERMINATE)
        if h is None:
            err = ctypes.get_last_error()
            if err == ERROR_INVALID_PARAMETER:
                raise ProcessLookupError(errno.ESRCH, f"no process {pid}")
            if err == ERROR_ACCESS_DENIED:
                raise PermissionError(errno.EACCES, f"no access to process {pid}")
            raise OSError(err, f"OpenProcess({pid}) failed")
        try:
            if not _kernel32.TerminateProcess(h, 1):
                raise OSError(
                    ctypes.get_last_error(), f"TerminateProcess({pid}) failed"
                )
        finally:
            _kernel32.CloseHandle(h)

    def _win_tree_members(
        pid: int, protect: set[int], root_created: int | None
    ) -> list[tuple[int, int | None]]:
        """``pid`` and its descendants as (pid, created): leaves first, root last."""
        if pid == os.getpid() or pid in protect:
            return []
        table = process_table()
        kids: dict[int, list[int]] = {}
        for child, (ppid, _, _) in table.items():
            kids.setdefault(ppid, []).append(child)
        created_of: dict[int, int | None] = {pid: root_created}
        order = [pid]
        for parent in order:  # breadth-first; the list grows while we walk it
            for child in kids.get(parent, []):
                if child in created_of or child in protect or child == os.getpid():
                    continue
                child_created = table[child][1]
                if not child_created:
                    logger.warning(
                        "kill_tree: pid %s has an unreadable creation time; its subtree is left alone",
                        child,
                    )
                    continue
                # a child created before its parent means the parent pid was reused
                if child_created < (created_of[parent] or 0):
                    continue
                created_of[child] = child_created
                order.append(child)
        return [(member, created_of[member]) for member in reversed(order)]

    def _win_stop_signal(
        loop: asyncio.AbstractEventLoop, callback: Callable[[], None]
    ) -> Callable[[], None]:
        def handler(signum: int, frame: object) -> None:
            loop.call_soon_threadsafe(callback)

        signals = (signal.SIGINT, signal.SIGBREAK)
        previous = {}
        for sig in signals:
            previous[sig] = signal.signal(sig, handler)

        def undo() -> None:
            for sig, old in previous.items():
                if old is not None:
                    signal.signal(sig, old)

        return undo


def _posix_stop_signal(
    loop: asyncio.AbstractEventLoop, callback: Callable[[], None]
) -> Callable[[], None]:
    signals = (signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.getsignal(sig) for sig in signals}
    for sig in signals:
        loop.add_signal_handler(sig, callback)

    def undo() -> None:
        for sig in signals:
            loop.remove_signal_handler(sig)
            if previous[sig] is not None:
                signal.signal(sig, previous[sig])

    return undo


def pid_alive(pid: int) -> bool:
    """True while ``pid`` runs. Access denied still means it runs. Never sends a signal on Windows."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True
    h = _open_process(pid, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION)
    if h is None:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        return _kernel32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(h)


def process_created(pid: int) -> int | None:
    """Creation time of ``pid`` as a FILETIME integer. None when unknown, and always None on POSIX."""
    if os.name != "nt":
        return None
    h = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if h is None:
        return None
    try:
        created, exited, kernel, user = (
            _FILETIME(),
            _FILETIME(),
            _FILETIME(),
            _FILETIME(),
        )
        ok = _kernel32.GetProcessTimes(
            h,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return created.dwHighDateTime << 32 | created.dwLowDateTime
    finally:
        _kernel32.CloseHandle(h)


class ChildJob:
    """One Job Object per process, so Windows ends our children when this process ends. A no-op on POSIX."""

    _instance: "ChildJob | None" = None

    def __init__(self) -> None:
        self._handle: int | None = None
        if os.name != "nt":
            return
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            err = ctypes.get_last_error()
            _kernel32.CloseHandle(handle)
            raise OSError(err, "SetInformationJobObject failed")
        self._handle = handle

    @classmethod
    def instance(cls) -> "ChildJob":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def adopt(self, popen: subprocess.Popen) -> bool:
        """Put ``popen`` in this job. False on POSIX, or when the runner's job forbids nesting."""
        if self._handle is None:
            return False
        if not _kernel32.AssignProcessToJobObject(self._handle, int(popen._handle)):
            err = ctypes.get_last_error()
            logger.warning(
                "could not put child %s in a job object (winerror %s)", popen.pid, err
            )
            return False
        return True

    def close(self) -> None:
        """Close the job. On Windows that ends every child still in it."""
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
        if ChildJob._instance is self:
            ChildJob._instance = None


def start_child(
    argv: list[str], *, detached: bool = False, **popen_kwargs: Any
) -> subprocess.Popen:
    """Start ``argv`` in its own session (POSIX) or process group (Windows).

    On Windows a non-detached child is adopted into ``ChildJob.instance()``. A detached child breaks away
    from the job and outlives us, as a setsid'd daemon does on POSIX.
    """
    if os.name != "nt":
        return subprocess.Popen(argv, start_new_session=True, **popen_kwargs)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if detached:
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_BREAKAWAY_FROM_JOB
    try:
        child = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
    except OSError as exc:
        # A job that forbids breakaway refuses it with ERROR_ACCESS_DENIED: retry once without it.
        if not detached or getattr(exc, "winerror", None) != ERROR_ACCESS_DENIED:
            raise
        flags &= ~subprocess.CREATE_BREAKAWAY_FROM_JOB
        child = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
    if not detached:
        try:
            ChildJob.instance().adopt(child)
        except OSError as exc:
            logger.warning("could not put child %s in the job: %s", child.pid, exc)
    return child


def terminate(pid: int) -> None:
    """Ask ``pid`` to stop with SIGTERM. Windows has no signal, so this ends the process."""
    if os.name != "nt":
        os.kill(pid, SOFT)
    else:
        _win_terminate(pid)


def kill(pid: int) -> None:
    """Stop ``pid`` at once with SIGKILL. On Windows this is TerminateProcess."""
    if os.name != "nt":
        os.kill(pid, HARD)
    else:
        _win_terminate(pid)


def kill_tree(
    pid: int,
    sig: int,
    *,
    protect: Iterable[int] = frozenset(),
    created: int | None = None,
) -> None:
    """Signal ``pid`` and its group (POSIX), or end its descendants too (Windows).

    POSIX is ``group_kill`` from plugins/hub/halt_runtime.py: ``killpg`` only when ``pid`` leads its group,
    the group is not ours and holds no protected pid. Otherwise ``kill``.

    Windows: when ``created`` differs from the process's creation time, the pid was reused and nothing is
    killed. A signal other than HARD waits up to 5 s for the process to exit, then kills the tree.
    """
    protect = set(protect)
    if os.name != "nt":
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = 0
        mine = os.getpgid(0)
        guarded = set()
        for p in protect:
            try:
                guarded.add(os.getpgid(p))
            except OSError:  # it died since the check
                continue
        if pgid == pid and pgid > 1 and pgid != mine and pgid not in guarded:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
        return
    born = process_created(pid)
    if created is not None and born != created:
        return  # the pid was reused
    members = _win_tree_members(pid, protect, born)  # snapshot before any waiting
    if sig != HARD:
        deadline = time.monotonic() + 5
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
    for member, member_created in members:
        # A readable creation time must still match. With none, only the root is ended (the caller passed no
        # ``created``, so this is the old behavior); a child needs a readable time to prove it is ours.
        if member_created and process_created(member) != member_created:
            logger.warning("kill_tree: skipping pid %s (exited or reused)", member)
            continue
        if not member_created and member != pid:
            logger.warning(
                "kill_tree: skipping pid %s (creation time unreadable)", member
            )
            continue
        # access denied or already gone: keep going with the rest of the tree
        try:
            _win_terminate(member)
        except ProcessLookupError:
            logger.debug("kill_tree: pid %s already exited", member)
        except OSError as exc:
            logger.warning("kill_tree: could not end pid %s: %s", member, exc)


def on_stop_signal(
    loop: asyncio.AbstractEventLoop, callback: Callable[[], None]
) -> Callable[[], None]:
    """Run ``callback`` on a stop signal. Returns ``undo``, which restores the previous handlers.

    POSIX: SIGTERM and SIGINT. Windows: SIGINT and SIGBREAK.
    """
    if os.name != "nt":
        return _posix_stop_signal(loop, callback)
    return _win_stop_signal(loop, callback)


def process_table() -> dict[int, tuple[int, int, str]]:
    """Map pid to (parent pid, creation time, exe name) from a Toolhelp snapshot. Windows only.

    A creation time that cannot be read (access denied) is 0.
    """
    if os.name != "nt":
        raise NotImplementedError("process_table is Windows only")
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    table: dict[int, tuple[int, int, str]] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            created = process_created(entry.th32ProcessID) or 0
            table[entry.th32ProcessID] = (
                entry.th32ParentProcessID,
                created,
                entry.szExeFile,
            )
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return table
