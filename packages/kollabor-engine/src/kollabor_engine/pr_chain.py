"""Read-only view of an external PR-chain runner's state folder (#1008).

The runner keeps ``prs/<n>/STATE.json``, ``issues/<n>/STATE.json`` and an append-only
``ledger.jsonl`` under one root. This module only reads them; it never writes.
"""

import heapq
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_ROOT = "~/backups/pr-chain"
KINDS = {"pr": "prs", "issue": "issues"}
TEXT_FIELDS = ("status", "started", "updated", "head", "last_verdict", "why", "branch")
EVENT_TEXT_FIELDS = ("ts", "stage", "status", "verdict", "why")
INT_FIELDS = ("round", "pr")
EVENT_INT_FIELDS = ("pr", "issue", "round", "n")
MAX_STATE_BYTES = 256 * 1024
MAX_TEXT = 1000
MAX_DIGITS = 12
MAX_PID = 2**31 - 1
# The newest ledger lines are all a screen shows. The file grows without bound.
LEDGER_TAIL_BYTES = 256 * 1024
MAX_EVENTS = 200
# A poll looks at no more than MAX_SCAN entries of a folder and keeps the MAX_ITEMS highest numbers
# (the newest PRs and issues) of each kind, so a huge or hostile folder cannot grow the work or the response.
MAX_SCAN = 10_000
MAX_ITEMS = 100


def root() -> Path:
    return Path(os.environ.get("KOLLAB_PR_CHAIN_ROOT") or DEFAULT_ROOT).expanduser()


def _text(value: Any) -> Optional[str]:
    """Any JSON value as short text; None stays absent. The runner's files are not trusted to have types."""
    if value is None:
        return None
    try:
        text = str(value)
    except RecursionError:
        return None
    return text[:MAX_TEXT].encode("utf-8", "replace").decode("utf-8")


def _number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < 10**MAX_DIGITS else None
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= MAX_DIGITS:
        return int(value)
    return None


def _clean(data: Dict[str, Any], text_fields: Any, int_fields: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in text_fields:
        if (text := _text(data.get(key))) is not None:
            out[key] = text
    for key in int_fields:
        if (number := _number(data.get(key))) is not None:
            out[key] = number
    return out


_DIR_FD = os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")


def _open_dir(base: Path, parts: Tuple[str, ...]) -> int:
    """A descriptor for the folder ``base/<parts>``, refusing a symlink at any step below ``base``.

    Each step is opened relative to the descriptor of the one before it with ``O_NOFOLLOW``, so a
    folder swapped for a symlink after a check cannot redirect the read. ``base`` itself may be a link.
    Without ``dir_fd`` support (Windows) the steps are checked with ``lstat`` instead.
    """
    if not _DIR_FD:
        path = base
        for part in parts:
            path = path / part
            if stat.S_ISLNK(os.lstat(path).st_mode):
                raise OSError(f"{part} is a symlink")
        return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    for part in parts:
        try:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        finally:
            os.close(fd)
        fd = nxt
    return fd


def _read_file(base: Path, parts: Tuple[str, ...], limit: int, tail: bool = False) -> bytes:
    """Up to ``limit`` + 1 bytes of the regular file ``base/<parts>`` (the last ones with ``tail``).

    The runner's folder is not trusted: a symlink at any step below ``base`` could point at
    another file the engine can read, and a named pipe or device would block a reader forever.
    Both raise ``OSError`` without the file being read.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    folder = _open_dir(base, parts[:-1])
    try:
        if _DIR_FD:
            fd = os.open(parts[-1], flags, dir_fd=folder)
        else:
            if stat.S_ISLNK(os.lstat(base.joinpath(*parts)).st_mode):
                raise OSError(f"{parts[-1]} is a symlink")
            fd = os.open(base.joinpath(*parts), flags)
    finally:
        os.close(folder)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"{parts[-1]} is not a regular file")
        if tail and info.st_size > limit:
            os.lseek(fd, info.st_size - limit, os.SEEK_SET)
        chunks: List[bytes] = []
        total = 0
        while total <= limit and (chunk := os.read(fd, limit + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _runner_alive(base: Path, claim: str) -> bool:
    """A claim counts only while the process that wrote it is still running."""
    try:
        pid = int(_read_file(base, ("claims", claim, "pid"), 64).decode("ascii", "replace").strip())
    except (OSError, ValueError, OverflowError):
        return False
    try:
        if not 1 < pid <= MAX_PID:
            return False
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return False
    return True


def read_item(base: Path, kind: str, number: int) -> Optional[Dict[str, Any]]:
    """One item's STATE.json as a row, or None when it is missing or unreadable."""
    try:
        raw = _read_file(base, (KINDS[kind], str(number), "STATE.json"), MAX_STATE_BYTES)
        if len(raw) > MAX_STATE_BYTES:
            return None
        data = json.loads(raw.decode("utf-8", "replace"))
    except OSError, ValueError, RecursionError:
        return None
    if not isinstance(data, dict):
        return None
    row: Dict[str, Any] = {"kind": kind, "number": number}
    row.update(_clean(data, TEXT_FIELDS, INT_FIELDS))
    row["claimed"] = _runner_alive(base, str(number) if kind == "pr" else f"i{number}")
    return row


def _is_number_folder(name: str) -> bool:
    """Only the canonical spelling of a number names an item, so ``007`` is not a second ``7``."""
    return name.isascii() and name.isdigit() and len(name) <= MAX_DIGITS and name == str(int(name))


def _newest_numbers(base: Path, sub: str) -> Tuple[List[int], bool]:
    """The highest item numbers in ``base/sub`` and whether others were left out.

    At most ``MAX_SCAN`` entries are looked at and ``MAX_ITEMS`` numbers kept. A symlinked folder or
    entry is skipped; the runner's folder is not trusted.
    """
    best: List[int] = []
    cut = False
    fd = _open_dir(base, (sub,))
    try:
        with os.scandir(fd if _DIR_FD else base / sub) as entries:
            for scanned, entry in enumerate(entries):
                if scanned >= MAX_SCAN:
                    cut = True
                    break
                if not _is_number_folder(entry.name) or entry.is_symlink():
                    continue
                if len(best) < MAX_ITEMS:
                    heapq.heappush(best, int(entry.name))
                else:
                    cut = True
                    heapq.heappushpop(best, int(entry.name))
    finally:
        os.close(fd)
    return best, cut


def list_items(base: Path) -> Tuple[List[Dict[str, Any]], bool]:
    """The newest PRs and issues with a readable state file, most recently updated first.

    The second value is True when more folders existed than were listed.
    """
    rows: List[Dict[str, Any]] = []
    truncated = False
    for kind, sub in KINDS.items():
        try:
            numbers, cut = _newest_numbers(base, sub)
        except OSError:
            continue
        truncated = truncated or cut
        for number in numbers:
            row = read_item(base, kind, number)
            if row:
                rows.append(row)
    rows.sort(key=lambda r: str(r.get("updated") or ""), reverse=True)
    return rows, truncated


def read_events(base: Path, kind: Optional[str] = None, number: Optional[int] = None) -> List[Dict[str, Any]]:
    """The newest ledger events, oldest first. A half-written or damaged line is skipped.

    Issue work is recorded under ``issue`` and PR work under ``pr``; with ``kind`` and
    ``number`` only that item's events return.
    """
    try:
        raw = _read_file(base, ("ledger.jsonl",), LEDGER_TAIL_BYTES, tail=True).decode("utf-8", "replace")
    except OSError:
        return []
    events: List[Dict[str, Any]] = []
    for line in raw.split("\n"):
        try:
            event = json.loads(line)
        except ValueError, RecursionError:
            continue
        if not isinstance(event, dict):
            continue
        tag = event.get("pr")
        if isinstance(tag, str) and tag[:1] == "i" and _number(tag[1:]) is not None:
            # The runner tags an issue's agent runs ``i<number>`` under ``pr``.
            event = {**event, "issue": tag[1:], "pr": None}
        event = _clean(event, EVENT_TEXT_FIELDS, EVENT_INT_FIELDS)
        if number is not None and event.get(kind or "pr") != number:
            continue
        events.append(event)
    return events[-MAX_EVENTS:]
