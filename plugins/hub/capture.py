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

    event_type = str(
        data.get("event_type") or data.get("type") or data.get("kind") or "text"
    )
    source_type = event_type
    kind_map = {
        "tool_start": "tool-call",
        "tool_call": "tool-call",
        "tool": "tool-call",
        "tool_end": "tool-result",
        "tool_result": "tool-result",
        "tool-response": "tool-result",
        "error": "error",
        "status": "status",
        "message": "message",
        "user_message": "message",
        "assistant_message": "message",
        "response": "message",
        "token": "text",
        "output": "text",
        "text": "text",
    }
    kind = kind_map.get(
        source_type,
        source_type if source_type in {"text", "status", "error", "message"} else "text",
    )
    text = data.get(
        "text", data.get("rendered", data.get("message", data.get("content", "")))
    )
    if isinstance(text, (dict, list)):
        text = _string_value(text)
    try:
        event_seq = int(data.get("seq", seq))
    except (TypeError, ValueError):
        event_seq = seq
    timestamp = _timestamp(data.get("timestamp", data.get("ts")))
    normalized = normalize_text(_string_value(text))
    role_value = data.get("role")
    role = role_value.lower() if isinstance(role_value, str) else None
    if role not in {"user", "assistant"}:
        role = {
            "user_message": "user",
            "assistant_message": "assistant",
            "response": "assistant",
            "token": "assistant",
        }.get(event_type)
    excluded = {
        "type", "kind", "event_type", "role", "seq", "timestamp", "ts",
        "text", "rendered", "message", "content",
    }
    payload = _normalize_payload(
        {key: value for key, value in data.items() if key not in excluded}
    )
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
    if event_type in {"thinking", "reasoning", "analysis"}:
        item["event_type"] = event_type
    if role in {"user", "assistant"}:
        item["role"] = role
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


_DEFAULT_TRANSCRIPT_LINES = 100
_DEFAULT_TRANSCRIPT_CHARS = 10_000
_MAX_TRANSCRIPT_LINES = 1_000
_MAX_TRANSCRIPT_CHARS = 100_000


def _string_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _labelled_lines(label: str, value: Any) -> list[str]:
    text = normalize_text(_string_value(value))
    if not text:
        return []
    lines = text.split("\n")
    return [f"{label} {lines[0]}", *(f"  {line}" for line in lines[1:])]


def _record_sequence(record: dict[str, Any]) -> int:
    try:
        return int(record.get("seq", 0))
    except (TypeError, ValueError):
        return 0


def _transcript_turn_key(record: dict[str, Any]) -> tuple[str, str] | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    request_id = record.get("request_id")
    if request_id is None:
        request_id = payload.get("request_id")
    turn_id = record.get("turn_id")
    if turn_id is None:
        turn_id = payload.get("turn_id")
    if request_id is None and turn_id is None:
        return None
    return (
        _string_value(request_id) if request_id is not None else "",
        _string_value(turn_id) if turn_id is not None else "",
    )


def _transcript_role(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return str(record.get("role") or payload.get("role") or "").lower()


def group_transcript_records(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group assistant stream chunks and prefer final responses for the same turn."""
    ordered = sorted(
        (record for record in records if isinstance(record, dict)),
        key=lambda record: _record_sequence(record),
    )

    def is_response(record: dict[str, Any]) -> bool:
        return (
            record.get("kind") == "message"
            and _transcript_role(record) == "assistant"
        )

    def is_token(record: dict[str, Any]) -> bool:
        if record.get("kind") != "text" or _transcript_role(record) != "assistant":
            return False
        return record.get("event_type") not in {"thinking", "reasoning", "analysis"}

    final_turns = set()
    for record in ordered:
        if not is_response(record):
            continue
        if not normalize_text(_string_value(record.get("text", ""))):
            continue
        turn_key = _transcript_turn_key(record)
        if turn_key is not None:
            final_turns.add(turn_key)

    grouped: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    pending_key: tuple[str, str] | None = None

    def flush_pending() -> None:
        nonlocal pending, pending_key
        if not pending:
            return
        turn_key = pending_key
        if turn_key is None or turn_key not in final_turns:
            text = "".join(
                normalize_text(_string_value(record.get("text", "")))
                for record in pending
            )
            combined = dict(pending[-1])
            combined["seq"] = max(_record_sequence(record) for record in pending)
            combined["kind"] = "text"
            combined["role"] = "assistant"
            combined["text"] = text
            payload: dict[str, Any] = {}
            payload_text_present = False
            for record in pending:
                source_payload = record.get("payload")
                if not isinstance(source_payload, dict):
                    continue
                payload_text_present = payload_text_present or "text" in source_payload
                for key, value in source_payload.items():
                    if key != "text":
                        payload.setdefault(key, value)
            if payload_text_present:
                payload["text"] = text
            combined["payload"] = payload
            grouped.append(combined)
        pending = []
        pending_key = None

    for record in ordered:
        if is_token(record):
            turn_key = _transcript_turn_key(record)
            if pending and turn_key != pending_key:
                flush_pending()
            if turn_key is not None and turn_key in final_turns:
                continue
            pending.append(record)
            pending_key = turn_key
            continue

        if is_response(record) and _transcript_turn_key(record) is None:
            if pending and pending_key is None:
                pending = []
                pending_key = None
        flush_pending()
        grouped.append(record)

    flush_pending()
    return grouped


def _transcript_record_lines(record: dict[str, Any]) -> list[str]:
    event_type = str(record.get("event_type") or "")
    if event_type in {"thinking", "reasoning", "analysis"}:
        return []

    kind = str(record.get("kind") or "text")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    role = str(record.get("role") or payload.get("role") or "").lower()
    text = normalize_text(_string_value(record.get("text", "")))
    tool = record.get("tool") or payload.get("tool_name") or payload.get("tool")
    tool_name = normalize_text(_string_value(tool)) if tool else ""

    if kind == "tool-call":
        return [f"Tool call: {tool_name}" if tool_name else "Tool call"]
    if kind == "tool-result":
        status = record.get("status") or payload.get("status")
        if status is None:
            success = payload.get("success")
            if success is True:
                status = "ok"
            elif success is False or payload.get("error"):
                status = "error"
            else:
                status = "unknown"
        label = f"Tool result [{normalize_text(_string_value(status))}]"
        if tool_name:
            label += f" {tool_name}"
        result = (
            text
            or payload.get("output")
            or payload.get("error")
            or payload.get("result")
        )
        if result is None or result == "":
            return [label]
        return _labelled_lines(f"{label}:", result)
    if kind == "status":
        status = text or payload.get("status") or payload.get("state")
        return _labelled_lines("Status:", status)
    if kind == "error":
        error = text or payload.get("error") or payload.get("message")
        return _labelled_lines("Error:", error)
    if role == "user" or event_type == "user_message":
        return _labelled_lines("User:", text)
    if role == "assistant" or event_type == "assistant_message":
        return _labelled_lines("Assistant:", text)
    if kind == "message":
        return _labelled_lines("Message:", text)
    if kind == "text":
        return _labelled_lines("Output:", text)
    return _labelled_lines("Status:", text)


def _bounded_transcript(
    lines: list[str],
    *,
    max_lines: int,
    max_chars: int,
    truncated: bool,
    next_cursor: Any,
    empty_message: str,
) -> str:
    try:
        line_limit = min(_MAX_TRANSCRIPT_LINES, max(1, int(max_lines)))
    except (TypeError, ValueError):
        line_limit = _DEFAULT_TRANSCRIPT_LINES
    try:
        char_limit = min(_MAX_TRANSCRIPT_CHARS, max(1, int(max_chars)))
    except (TypeError, ValueError):
        char_limit = _DEFAULT_TRANSCRIPT_CHARS

    visible: list[str] = []
    used_chars = 0
    for line in lines:
        line = normalize_text(_string_value(line))
        added_chars = len(line) + (1 if visible else 0)
        if len(visible) >= line_limit or used_chars + added_chars > char_limit:
            truncated = True
            break
        visible.append(line)
        used_chars += added_chars

    if truncated:
        cursor = normalize_text(_string_value(next_cursor))[:120]
        marker = (
            f"... (truncated, next_cursor={cursor})" if cursor else "... (truncated)"
        )
        marker = marker[:char_limit]
        if line_limit == 1 and visible:
            content_budget = max(0, char_limit - len(marker) - 1)
            content = visible[0][:content_budget].rstrip()
            visible = [f"{content} {marker}" if content else marker]
        else:
            while visible and (
                len(visible) >= line_limit
                or len("\n".join(visible)) + 1 + len(marker) > char_limit
            ):
                visible.pop()
            if len(visible) < line_limit:
                visible.append(marker)
            elif not visible:
                visible = [marker]
    if not visible:
        return empty_message
    return "\n".join(visible)


def render_transcript(
    capture: Any,
    *,
    identity: str | None = None,
    max_lines: int = _DEFAULT_TRANSCRIPT_LINES,
    max_chars: int = _DEFAULT_TRANSCRIPT_CHARS,
) -> str:
    """Render structured hub captures as a bounded, readable transcript.

    The input is the JSON capture envelope returned by ``request_capture``.
    Old peers that return plain text or a list of terminal lines keep their
    sanitized fallback behavior. Tool arguments and thinking/reasoning events
    are deliberately omitted from the readable transcript.
    """
    empty_message = (
        f"no recent output from '{normalize_text(_string_value(identity))}'"
        if identity
        else "no recent output"
    )
    truncated = False
    next_cursor = None
    raw_records: Any = None

    if isinstance(capture, dict):
        truncated = bool(capture.get("truncated"))
        next_cursor = capture.get("next_cursor")
        raw_records = capture.get("lines", capture.get("records"))
        if raw_records is None:
            text = normalize_text(_string_value(capture.get("text", "")))
            lines = text.split("\n") if text else []
            return _bounded_transcript(
                lines,
                max_lines=max_lines,
                max_chars=max_chars,
                truncated=truncated,
                next_cursor=next_cursor,
                empty_message=empty_message,
            )
    elif isinstance(capture, list):
        raw_records = capture
    elif capture is not None:
        text = normalize_text(_string_value(capture))
        return _bounded_transcript(
            text.split("\n") if text else [],
            max_lines=max_lines,
            max_chars=max_chars,
            truncated=False,
            next_cursor=None,
            empty_message=empty_message,
        )

    if not isinstance(raw_records, list) or not raw_records:
        return _bounded_transcript(
            [],
            max_lines=max_lines,
            max_chars=max_chars,
            truncated=truncated,
            next_cursor=next_cursor,
            empty_message=empty_message,
        )

    if all(isinstance(record, dict) for record in raw_records):
        records = []
        for record in raw_records:
            if {"kind", "text", "payload"}.issubset(record):
                records.append(record)
            else:
                records.extend(normalize_records([record]))
    else:
        legacy_lines = [normalize_text(_string_value(record)) for record in raw_records]
        return _bounded_transcript(
            legacy_lines,
            max_lines=max_lines,
            max_chars=max_chars,
            truncated=truncated,
            next_cursor=next_cursor,
            empty_message=empty_message,
        )

    records = group_transcript_records(records)

    lines: list[str] = []
    for record in records:
        if isinstance(record, dict):
            lines.extend(_transcript_record_lines(record))

    return _bounded_transcript(
        lines,
        max_lines=max_lines,
        max_chars=max_chars,
        truncated=truncated,
        next_cursor=next_cursor,
        empty_message=empty_message,
    )
