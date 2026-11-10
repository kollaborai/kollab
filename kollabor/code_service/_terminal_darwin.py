"""Inspect open PTY slave descriptors with Darwin's libproc APIs."""

import ctypes
import os
import struct

import psutil


class _FD(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_int32), ("kind", ctypes.c_uint32)]


def pty_holders(path: str, identity: tuple[int, int], earliest: float):
    """Return identity-checked same-user processes holding the allocated slave vnode."""
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidfdinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    for process in psutil.process_iter():
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
