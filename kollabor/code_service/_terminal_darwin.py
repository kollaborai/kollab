"""Inspect open PTY slave descriptors with Darwin's libproc APIs."""

import ctypes
import errno
import os
import struct

import psutil


class _FD(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_int32), ("kind", ctypes.c_uint32)]


def _libproc_pids(lib):
    """List PIDs independently of psutil's process-table enumeration."""
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    ctypes.set_errno(0)
    count = lib.proc_listallpids(None, 0)
    if count <= 0:
        raise OSError(ctypes.get_errno() or errno.EIO, "libproc could not size the process list")
    capacity = max(count + 64, 256)
    for _ in range(5):
        if capacity > 1_000_000:
            break
        buf = (ctypes.c_int * capacity)()
        ctypes.set_errno(0)
        used = lib.proc_listallpids(buf, ctypes.sizeof(buf))
        if used <= 0:
            raise OSError(ctypes.get_errno() or errno.EIO, "libproc could not list processes")
        if used < capacity:
            return [pid for pid in buf[:used] if pid > 0]
        capacity *= 2
    raise OSError(errno.EOVERFLOW, "libproc process list remained too large")


def _libproc_processes(lib):
    for pid in _libproc_pids(lib):
        try:
            # Process retains start time and refuses signals if a PID has been reused.
            yield psutil.Process(pid)
        except (psutil.Error, OSError):
            continue


def pty_holders(path: str, identity: tuple[int, int], earliest: float, processes=None):
    """Return identity-checked same-user processes holding the allocated slave vnode."""
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidfdinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    for process in _libproc_processes(lib) if processes is None else processes:
        try:
            if process.pid == os.getpid() or process.uids().real != os.getuid() or process.create_time() < earliest:
                continue
            size = lib.proc_pidinfo(process.pid, 1, 0, None, 0)  # PROC_PIDLISTFDS
            if size <= 0:
                continue
            size += 32 * ctypes.sizeof(_FD)
            buf = ctypes.create_string_buffer(size)
            used = lib.proc_pidinfo(process.pid, 1, 0, buf, size)
            for offset in range(0, max(0, used - ctypes.sizeof(_FD) + 1), ctypes.sizeof(_FD)):
                entry = _FD.from_buffer_copy(buf.raw, offset)
                if entry.kind != 1:  # PROX_FDTYPE_VNODE
                    continue
                info = ctypes.create_string_buffer(1200)  # vnode_fdinfowithpath
                if lib.proc_pidfdinfo(process.pid, entry.fd, 2, info, len(info)) != len(info):
                    continue
                data = info.raw
                dev = struct.unpack_from("=I", data, 24)[0]
                ino = struct.unpack_from("=Q", data, 32)[0]
                name = os.fsdecode(data[176:].split(b"\0", 1)[0])
                if (dev, ino) == identity and name == path and process.is_running():
                    yield process
                    break
        except (psutil.Error, OSError):
            continue
