"""Exclusive file locks that work on POSIX and Windows (kollab #268)."""

import contextlib
import os
import time
from collections.abc import Iterator
from typing import IO

if os.name == "nt":
    import msvcrt
else:
    import fcntl

# Windows locks are mandatory, so lock a byte past the data: byte 0 would block readers.
_LOCK_OFFSET = 0x7FFFFFFF


def _lock_byte(fd: int, mode: int) -> None:
    pos = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
    try:
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, pos, os.SEEK_SET)


def try_lock(fd: int) -> bool:
    if os.name == "nt":
        try:
            _lock_byte(fd, msvcrt.LK_NBLCK)
        except PermissionError:  # a held Windows byte-range lock raises EACCES
            return False
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def lock(fd: int) -> None:
    if os.name == "nt":
        while not try_lock(fd):
            time.sleep(0.05)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX)


def unlock(fd: int) -> None:
    if os.name == "nt":
        _lock_byte(fd, msvcrt.LK_UNLCK)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def locked_file(path: str | os.PathLike[str], mode: str = "a+") -> Iterator[IO[str]]:
    f = open(path, mode)
    try:
        lock(f.fileno())
        try:
            yield f
        finally:
            unlock(f.fileno())
    finally:
        f.close()
