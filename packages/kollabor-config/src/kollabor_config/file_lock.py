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

# The open() modes locked_file takes. "w" empties the file only once the lock is held.
_FLAGS = {
    "r": os.O_RDONLY,
    "r+": os.O_RDWR,
    "w": os.O_WRONLY | os.O_CREAT,
    "w+": os.O_RDWR | os.O_CREAT,
    "a": os.O_WRONLY | os.O_CREAT | os.O_APPEND,
    "a+": os.O_RDWR | os.O_CREAT | os.O_APPEND,
}


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
def locked_file(
    path: str | os.PathLike[str], mode: str = "a+", *, private: bool = False
) -> Iterator[IO]:
    """Open ``path`` and hold an exclusive lock on it while the block runs.

    ``mode`` is an open() mode: r, r+, w, w+, a or a+, with an optional b. Text
    is UTF-8. A "w" mode empties the file only after the lock is held, and
    writes are flushed before it is released. ``private`` creates the file
    owner-only (0o600) and refuses to follow a symlink.
    """
    flags = _FLAGS.get(mode.replace("b", ""))
    if flags is None:
        raise ValueError(
            f"locked_file mode must be r, r+, w, w+, a or a+, not {mode!r}"
        )
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if private:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600 if private else 0o666)
    try:
        lock(fd)
    except BaseException:
        os.close(fd)
        raise
    try:
        if mode.startswith("w"):
            os.ftruncate(fd, 0)
        # The fd is already open, so "w" here neither truncates nor creates.
        f = os.fdopen(fd, mode, encoding=None if "b" in mode else "utf-8")
    except BaseException:
        unlock(fd)
        os.close(fd)
        raise
    with f:
        try:
            yield f
        finally:
            try:
                f.flush()
            finally:
                unlock(fd)
