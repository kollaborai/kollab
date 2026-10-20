"""Engine-owned ordering for sidebar agent rows.

The store at ``~/.kollab/hub/sidebar-order.json`` is shared by every Web UI
client connected to this engine. Revisions prevent stale clients from replacing
newer order, and a sibling file lock protects the check-and-replace transaction
across engine processes.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from kollabor_config.config_utils import get_config_directory

ORDER_FILE = "sidebar-order.json"
MAX_KEYS = 512
MAX_KEY_LEN = 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_THREAD_LOCK = threading.Lock()

try:  # pragma: no cover - platform-specific imports
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform-specific imports
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SidebarOrderState:
    order: list[str]
    revision: int
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"order": list(self.order), "revision": self.revision, "warning": self.warning}


class SidebarOrderConflict(Exception):
    """Raised when a client submits an order based on an old revision."""

    def __init__(self, state: SidebarOrderState) -> None:
        super().__init__("Sidebar order changed in another client.")
        self.state = state


class SidebarOrderUnreadable(Exception):
    """Raised rather than overwriting an unreadable order file."""

    def __init__(self, state: SidebarOrderState) -> None:
        super().__init__(state.warning or "Sidebar order file is unreadable.")
        self.state = state


def order_path() -> Path:
    return get_config_directory() / "hub" / ORDER_FILE


def parse_order(raw: Any) -> list[str] | None:
    """The deduplicated key list, or None when ``raw`` is not bounded text."""
    if not isinstance(raw, list) or len(raw) > MAX_KEYS:
        return None
    keys: list[str] = []
    for key in raw:
        if not isinstance(key, str) or not key or len(key) > MAX_KEY_LEN or _CONTROL.search(key):
            return None
        if key not in keys:
            keys.append(key)
    return keys


def _unreadable(path: Path, reason: object) -> SidebarOrderState:
    return SidebarOrderState(
        order=[],
        revision=0,
        warning=(
            f"Sidebar order file {path} is unreadable and was preserved. "
            f"Repair it before saving a new order. Details: {reason}"
        ),
    )


def _read_unlocked(path: Path) -> SidebarOrderState:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # A missing file is the normal first-run state, not a recovery warning.
        return SidebarOrderState(order=[], revision=0)
    except (OSError, ValueError, TypeError) as exc:
        return _unreadable(path, exc)

    if not isinstance(raw, dict):
        return _unreadable(path, "the document must be an object")
    order = parse_order(raw.get("order"))
    revision = raw.get("revision", 0)  # revision-less files are the pre-version format
    if order is None:
        return _unreadable(path, "the order field must be a list of bounded text keys")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return _unreadable(path, "revision must be a non-negative integer")
    return SidebarOrderState(order=order, revision=revision)


def read_order_state() -> SidebarOrderState:
    """Read the order and expose corrupt/unreadable storage without modifying it."""
    return _read_unlocked(order_path())


def read_order() -> list[str]:
    """Compatibility helper for callers that only need the current key list."""
    return read_order_state().order


@contextmanager
def _locked_order(path: Path) -> Iterator[None]:
    """Serialize the revision check and replace across threads and processes."""
    with _THREAD_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = path.with_name(f"{path.name}.lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(lock_path, flags, 0o600)
        locked = False
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            elif msvcrt is not None:  # pragma: no cover - Windows
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            else:  # pragma: no cover
                raise OSError("No supported sidebar order lock backend")
            yield
        finally:
            try:
                if locked and fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif locked and msvcrt is not None:  # pragma: no cover - Windows
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)


def _write_unlocked(path: Path, state: SidebarOrderState) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sidebar-order-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"order": state.order, "revision": state.revision}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_order(keys: list[str], expected_revision: int) -> SidebarOrderState:
    """Commit against a revision, rejecting stale or unreadable state atomically."""
    parsed = parse_order(keys)
    if parsed is None:
        raise ValueError("order must be a list of bounded text keys")
    path = order_path()
    with _locked_order(path):
        current = _read_unlocked(path)
        if current.warning:
            raise SidebarOrderUnreadable(current)
        if current.revision != expected_revision:
            raise SidebarOrderConflict(current)
        saved = SidebarOrderState(order=parsed, revision=current.revision + 1)
        _write_unlocked(path, saved)
    return saved
