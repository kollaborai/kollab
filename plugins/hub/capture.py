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


def _normalize_payload(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        return {str(key): _normalize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]
    return value


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
    try:
        event_seq = int(data.get("seq", seq))
    except (TypeError, ValueError):
        event_seq = seq
    timestamp = _timestamp(data.get("timestamp", data.get("ts")))
    normalized = normalize_text(text)
    excluded = {"type", "kind", "seq", "timestamp", "ts", "text", "rendered", "message", "content"}
    payload = _normalize_payload({key: value for key, value in data.items() if key not in excluded})
    if not payload:
        payload = {"text": normalized}
    item: dict[str, Any] = {
        "seq": event_seq,
        "timestamp": timestamp,
        "ts": timestamp,
        "kind": kind,
        "text": normalized,
        "payload": payload,
    }
    tool = data.get("tool", data.get("tool_name", data.get("name")))
    if tool and kind in {"tool-call", "tool-result"}:
        item["tool"] = str(tool)
    status = data.get("status")
    if status is None and kind == "tool-result" and "success" in data:
        status = "ok" if data.get("success") else "error"
    if status is None and kind == "error":
        status = "error"
    if status is not None:
        item["status"] = str(status)
    return item


def normalize_records(raw_events: Iterable[Any]) -> list[dict[str, Any]]:
    events = list(raw_events)
    records = [_record(event, index + 1) for index, event in enumerate(events)]
    return [record for record in records if record["text"] or record["kind"] != "text"]


def paginate(
    records: list[dict[str, Any]],
    cursor: Any = None,
    max_chars: int | None = None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    try:
        after_seq = int(cursor) if cursor is not None else 0
    except (TypeError, ValueError):
        after_seq = 0
    page = [record for record in records if int(record["seq"]) > after_seq]
    truncated = False
    used = 0
    try:
        budget = max(1, int(max_chars)) if max_chars is not None else None
    except (TypeError, ValueError):
        budget = None
    if budget is not None:
        kept: list[dict[str, Any]] = []
        for record in page:
            cost = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            if not kept and cost > budget:
                # An indivisible oversized first record is omitted explicitly;
                # advancing its sequence lets a client continue pagination.
                return [], True, str(record["seq"])
            if kept and used + cost > budget:
                truncated = True
                break
            kept.append(record)
            used += cost
        page = kept
        truncated = truncated or len(page) < len([record for record in records if int(record["seq"]) > after_seq])
    next_cursor = str(page[-1]["seq"]) if truncated and page else None
    return page, truncated, next_cursor


def plain_text(records: Iterable[dict[str, Any]]) -> str:
    return "\n".join(str(record.get("text", "")) for record in records)
