"""Read-only view of an external PR-chain runner's state folder (#1008).

The runner keeps ``prs/<n>/STATE.json``, ``issues/<n>/STATE.json`` and an append-only
``ledger.jsonl`` under one root. This module only reads them; it never writes.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_ROOT = "~/backups/pr-chain"
KINDS = {"pr": "prs", "issue": "issues"}
TEXT_FIELDS = ("status", "started", "updated", "head", "last_verdict", "why", "branch")
EVENT_TEXT_FIELDS = ("ts", "stage", "status", "verdict", "why")
INT_FIELDS = ("round", "pr")
EVENT_INT_FIELDS = ("pr", "issue", "round", "n")
MAX_STATE_BYTES = 256 * 1024
MAX_TEXT = 1000
# The newest ledger lines are all a screen shows. The file grows without bound.
LEDGER_TAIL_BYTES = 256 * 1024
MAX_EVENTS = 200


def root() -> Path:
    return Path(os.environ.get("KOLLAB_PR_CHAIN_ROOT") or DEFAULT_ROOT).expanduser()


def _text(value: Any) -> Optional[str]:
    """Any JSON value as short text; None stays absent. The runner's files are not trusted to have types."""
    return None if value is None else str(value)[:MAX_TEXT]


def _number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
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


def _runner_alive(claim: Path) -> bool:
    """A claim counts only while the process that wrote it is still running."""
    try:
        pid = int((claim / "pid").read_text().strip())
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def read_item(base: Path, kind: str, number: int) -> Optional[Dict[str, Any]]:
    """One item's STATE.json as a row, or None when it is missing or unreadable."""
    path = base / KINDS[kind] / str(number) / "STATE.json"
    try:
        if path.stat().st_size > MAX_STATE_BYTES:
            return None
        data = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if not isinstance(data, dict):
        return None
    row: Dict[str, Any] = {"kind": kind, "number": number}
    row.update(_clean(data, TEXT_FIELDS, INT_FIELDS))
    row["claimed"] = _runner_alive(base / "claims" / (str(number) if kind == "pr" else f"i{number}"))
    return row


def list_items(base: Path) -> List[Dict[str, Any]]:
    """Every PR and issue with a readable state file, most recently updated first."""
    rows: List[Dict[str, Any]] = []
    for kind, sub in KINDS.items():
        try:
            names = [p.name for p in (base / sub).iterdir() if p.name.isascii() and p.name.isdigit()]
        except OSError:
            continue
        for name in names:
            row = read_item(base, kind, int(name))
            if row:
                rows.append(row)
    rows.sort(key=lambda r: str(r.get("updated") or ""), reverse=True)
    return rows


def read_events(base: Path, kind: Optional[str] = None, number: Optional[int] = None) -> List[Dict[str, Any]]:
    """The newest ledger events, oldest first. A half-written or damaged line is skipped.

    Issue work is recorded under ``issue`` and PR work under ``pr``; with ``kind`` and
    ``number`` only that item's events return.
    """
    try:
        with open(base / "ledger.jsonl", "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LEDGER_TAIL_BYTES))
            raw = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    events: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        event = _clean(event, EVENT_TEXT_FIELDS, EVENT_INT_FIELDS)
        if number is not None and event.get(kind or "pr") != number:
            continue
        events.append(event)
    return events[-MAX_EVENTS:]
