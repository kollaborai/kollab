"""Normalization and pagination for browser/API hub capture consumers."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WRAPPER = re.compile(r"^\[(?:hub_capture|capture(?::[^\]]*)?)\]\s*")


def normalize_text(value: Any) -> str:
    """Strip terminal controls and resolve carriage-return redraws."""
    text = str(value or "").replace("\r\n", "\n")
    # A bare CR redraws the current terminal line; retain the final redraw.
    lines: list[str] = []
    for physical in text.split("\n"):
        current = ""
        for part in physical.split("\r"):
            if part == "":
                current = ""
            else:
                current = part
        lines.append(current)
    text = "\n".join(lines)
    text = _OSC.sub("", _CSI.sub("", text))
    text = _CTRL.sub("", text)
    return "\n".join(_WRAPPER.sub("", line, count=1) for line in text.split("\n"))


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def _record(raw: Any, seq: int) -> dict[str, Any]:
    data: dict[str, Any]
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        data = parsed if isinstance(parsed, dict) else {"type": "text", "text": raw}
    else:
        data = {"type": "text", "text": str(raw)}

    source_type = str(data.get("type") or data.get("kind") or "text")
    kind_map = {
        "tool_start": "tool-call", "tool_call": "tool-call", "tool": "tool-call",
        "tool_end": "tool-result", "tool_result": "tool-result", "tool-response": "tool-result",
        "error": "error", "status": "status", "message": "message",
        "response": "message", "token": "text", "output": "text", "text": "text",
    }
    kind = kind_map.get(source_type, source_type if source_type in {"text", "status", "error", "message"} else "text")
    text = data.get("text", data.get("rendered", data.get("message", data.get("content", ""))))
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False)
    timestamp = _timestamp(data.get("timestamp", data.get("ts")))
    normalized = normalize_text(text)
    item: dict[str, Any] = {
        "seq": int(data.get("seq", seq)),
        "timestamp": timestamp,
        "ts": timestamp,
        "kind": kind,
        "text": normalized,
        "payload": normalized,
    }
    tool = data.get("tool", data.get("tool_name", data.get("name")))
    if tool and kind in {"tool-call", "tool-result"}:
        item["tool"] = str(tool)
    status = data.get("status")
    if status is None and kind == "error":
        status = "error"
    if status is not None:
        item["status"] = str(status)
    return item


def normalize_records(raw_events: Iterable[Any]) -> list[dict[str, Any]]:
    events = list(raw_events)
    records = [_record(event, index + 1) for index, event in enumerate(events)]
    return [record for record in records if record["text"] or record["kind"] != "text"]


def paginate(records: list[dict[str, Any]], cursor: Any = None, max_chars: int | None = None) -> tuple[list[dict[str, Any]], bool, str | None]:
    try:
        start = max(0, int(cursor or 0))
    except (TypeError, ValueError):
        start = 0
    page = records[start:]
    truncated = False
    used = 0
    if max_chars is not None:
        try:
            budget = max(1, int(max_chars))
        except (TypeError, ValueError):
            budget = None
        if budget is not None:
            kept: list[dict[str, Any]] = []
            for record in page:
                cost = len(record.get("text", ""))
                if kept and used + cost > budget:
                    truncated = True
                    break
                kept.append(record)
                used += cost
            page = kept
            truncated = truncated or start + len(page) < len(records)
    next_cursor = str(start + len(page)) if start + len(page) < len(records) else None
    return page, truncated, next_cursor


def plain_text(records: Iterable[dict[str, Any]]) -> str:
    return "\n".join(str(record.get("text", "")) for record in records)
