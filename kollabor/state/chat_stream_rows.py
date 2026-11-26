"""Daemon paging for older rows in the ordered chat stream (#1451)."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

_MAX_CURSOR_LENGTH = 8192
_MAX_PAGE_SIZE = 100
CHAT_STREAM_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
CHAT_STREAM_MAX_ROW_BYTES = 256 * 1024
CHAT_STREAM_INLINE_TOOL_OUTPUT_BYTES = 8 * 1024
CHAT_STREAM_TOOL_OUTPUT_PREVIEW_CHARS = 1024
CHAT_STREAM_TOOL_OUTPUT_CHUNK_CHARS = 32 * 1024
CHAT_STREAM_CONTENT_PREVIEW_CHARS = 1024
CHAT_STREAM_PART_PAGE_SIZE = 32
CHAT_STREAM_PART_PAGE_BYTES = 256 * 1024
CHAT_STREAM_PART_INLINE_BYTES = 16 * 1024
CHAT_STREAM_ROW_PLACEHOLDER = "[Large chat row. Expand to load all content.]"


def chat_stream_wire_bytes(value: Any) -> int:
    """Measure a conservative compact-JSON RPC representation."""
    return len(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _tool_output_text(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return value, "text"
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
            separators=(",", ":"),
        ),
        "json",
    )


def chat_stream_tool_output_revision(content: str, output_type: str) -> str:
    """Fingerprint the exact full-output representation returned by the API."""
    payload = output_type.encode("ascii") + b"\0" + content.encode(
        "utf-8", errors="replace"
    )
    return hashlib.sha256(payload).hexdigest()


def chat_stream_parts_revision(parts: list[Any]) -> tuple[str, str, str]:
    """Serialize and fingerprint one row's exact canonical part list."""
    content = json.dumps(
        parts,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
        separators=(",", ":"),
    )
    return (
        content,
        "json",
        chat_stream_tool_output_revision(content, "json"),
    )


def bounded_chat_stream_row(
    row: dict[str, Any],
    *,
    output_resolver: Callable[
        [str | None, str | None, Any], tuple[str, str, str]
    ]
    | None = None,
    content_resolver: Callable[
        [str | None, int, str, Any], tuple[str, str, str]
    ]
    | None = None,
) -> dict[str, Any]:
    """Make a transport copy with large tool results replaced by previews.

    The daemon keeps the original row for paging and on-demand output reads.
    This projection is only for RPC, HTTP and SSE payloads.
    """
    copied = copy.deepcopy(row)
    parts = copied.get("parts")
    if not isinstance(parts, list):
        if chat_stream_wire_bytes(copied) > CHAT_STREAM_MAX_ROW_BYTES:
            raise ValueError("chat stream row metadata exceeds the row byte budget")
        return copied
    original_parts = copy.deepcopy(parts)
    row_id = copied.get("id")

    def ensure_retrievable_row_id() -> None:
        if not isinstance(row_id, str) or not row_id or len(row_id) > 512:
            raise ValueError("chat stream row id exceeds the content retrieval limit")

    def tool_output_is_retrievable(part: dict[str, Any]) -> bool:
        tool_call_id = part.get("tool_call_id")
        return (
            isinstance(tool_call_id, str)
            and bool(tool_call_id)
            and len(tool_call_id) <= 512
        )

    def output_preview(part: dict[str, Any], part_index: int, value: Any) -> dict[str, Any]:
        tool_call_id = part.get("tool_call_id")
        try:
            if output_resolver is None:
                content, output_type = _tool_output_text(value)
                revision = chat_stream_tool_output_revision(content, output_type)
            else:
                content, output_type, revision = output_resolver(
                    row_id, tool_call_id, value
                )
        except (TypeError, ValueError):
            content, output_type = str(value), "text"
            revision = chat_stream_tool_output_revision(content, output_type)
        return {
            "kind": "chat-stream-tool-output-preview",
            "row_id": row_id,
            "tool_call_id": tool_call_id,
            "output_bytes": len(content.encode("utf-8", errors="replace")),
            "output_chars": len(content),
            "output_type": output_type,
            "output_revision": revision,
            "preview": content[:CHAT_STREAM_TOOL_OUTPUT_PREVIEW_CHARS],
        }

    def content_preview(
        part_index: int, field: str, value: Any
    ) -> tuple[dict[str, Any], str]:
        ensure_retrievable_row_id()
        try:
            if content_resolver is None:
                content, output_type = _tool_output_text(value)
                revision = chat_stream_tool_output_revision(content, output_type)
            else:
                content, output_type, revision = content_resolver(
                    row_id, part_index, field, value
                )
        except (TypeError, ValueError):
            content, output_type = str(value), "text"
            revision = chat_stream_tool_output_revision(content, output_type)
        return (
            {
                "kind": "chat-stream-content-preview",
                "row_id": row_id,
                "part_index": part_index,
                "field": field,
                "output_bytes": len(content.encode("utf-8", errors="replace")),
                "output_chars": len(content),
                "output_type": output_type,
                "output_revision": revision,
                "preview": content[:CHAT_STREAM_CONTENT_PREVIEW_CHARS],
            },
            content,
        )

    def preview_args(part: dict[str, Any], part_index: int) -> None:
        args = part.get("args")
        if args is None or (
            isinstance(args, dict)
            and args.get("kind") == "chat-stream-content-preview"
        ):
            return
        marker, _content = content_preview(part_index, "args", args)
        part["args"] = marker

    for part_index, part in enumerate(parts):
        if not isinstance(part, dict) or part.get("type") != "tool-call":
            continue
        result = part.get("result")
        if result is None or (
            isinstance(result, dict)
            and result.get("kind") == "chat-stream-tool-output-preview"
        ):
            continue
        marker = output_preview(part, part_index, result)
        output_bytes = marker["output_bytes"]
        if output_bytes <= CHAT_STREAM_INLINE_TOOL_OUTPUT_BYTES:
            continue
        if not tool_output_is_retrievable(part):
            continue
        ensure_retrievable_row_id()
        part["result"] = marker

    # First replace individually large text, reasoning and argument fields.
    # Any remaining oversized or unusually wide part list is exposed through
    # the row-level page marker below.
    if chat_stream_wire_bytes(copied) > CHAT_STREAM_MAX_ROW_BYTES:
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"text", "reasoning"}:
                value = part.get("text")
                field = part["type"]
                if (
                    isinstance(value, str)
                    and len(value.encode("utf-8", errors="replace"))
                    > CHAT_STREAM_PART_INLINE_BYTES
                ):
                    marker, content = content_preview(part_index, field, value)
                    part["text"] = content[:CHAT_STREAM_CONTENT_PREVIEW_CHARS]
                    part["chat_stream_content_preview"] = marker
            args = part.get("args")
            if args is not None and chat_stream_wire_bytes(args) > CHAT_STREAM_PART_INLINE_BYTES:
                preview_args(part, part_index)
    if chat_stream_wire_bytes(copied) > CHAT_STREAM_MAX_ROW_BYTES:
        compact_parts = []
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict):
                compact_parts.append(copy.deepcopy(part))
                continue
            if part.get("type") == "tool-call":
                compact: dict[str, Any] = {
                    "type": "tool-call",
                    "tool_call_id": part.get("tool_call_id"),
                    "tool_name": part.get("tool_name", "Tool"),
                }
                if "args" in part:
                    compact["args"] = copy.deepcopy(part["args"])
                    if chat_stream_wire_bytes(compact["args"]) > CHAT_STREAM_PART_INLINE_BYTES:
                        compact["args"] = content_preview(
                            part_index, "args", part["args"]
                        )[0]
                result = part.get("result")
                if result is not None:
                    if (
                        isinstance(result, dict)
                        and result.get("kind") == "chat-stream-tool-output-preview"
                    ):
                        compact["result"] = result
                    elif tool_output_is_retrievable(part):
                        ensure_retrievable_row_id()
                        compact["result"] = output_preview(part, part_index, result)
                    else:
                        compact["result"] = copy.deepcopy(result)
                if "is_error" in part:
                    compact["is_error"] = part["is_error"]
                compact_parts.append(compact)
            elif part.get("type") in {"text", "reasoning"}:
                field = part["type"]
                value = part.get("text")
                if isinstance(value, str):
                    compact: dict[str, Any] = {
                        "type": part["type"],
                        "text": value[:CHAT_STREAM_CONTENT_PREVIEW_CHARS],
                    }
                    marker = part.get("chat_stream_content_preview")
                    if isinstance(marker, dict):
                        compact["chat_stream_content_preview"] = marker
                    elif (
                        value != compact["text"]
                    ):
                        compact["chat_stream_content_preview"] = content_preview(
                            part_index, field, value
                        )[0]
                    compact_parts.append(compact)
            else:
                compact_parts.append(copy.deepcopy(part))
        copied["parts"] = compact_parts
    if chat_stream_wire_bytes(copied) > CHAT_STREAM_MAX_ROW_BYTES:
        ensure_retrievable_row_id()
        if content_resolver is None:
            content, output_type, revision = chat_stream_parts_revision(
                original_parts
            )
        else:
            content, output_type, revision = content_resolver(
                row_id, -1, "parts", original_parts
            )
        copied["parts"] = [{"type": "text", "text": CHAT_STREAM_ROW_PLACEHOLDER}]
        copied["chat_stream_parts_preview"] = {
            "kind": "chat-stream-parts-preview",
            "row_id": row_id,
            "part_count": len(original_parts),
            "output_bytes": len(content.encode("utf-8", errors="replace")),
            "output_chars": len(content),
            "output_type": output_type,
            "output_revision": revision,
        }
    if chat_stream_wire_bytes(copied) > CHAT_STREAM_MAX_ROW_BYTES:
        # Required row metadata cannot itself be paged because the row id is the
        # paging key. Fail explicitly instead of returning an oversized payload.
        raise ValueError("chat stream row metadata exceeds the row byte budget")
    return copied


def bounded_chat_stream_part(
    row_id: str,
    part_index: int,
    part: dict[str, Any],
    *,
    content_resolver: Callable[
        [str | None, int, str, Any], tuple[str, str, str]
    ]
    | None = None,
) -> dict[str, Any]:
    """Keep a page item small; expose the exact oversized part by revision."""
    copied = copy.deepcopy(part)
    if chat_stream_wire_bytes(copied) <= CHAT_STREAM_PART_INLINE_BYTES:
        return copied
    if content_resolver is None:
        content = json.dumps(
            part,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
            separators=(",", ":"),
        )
        output_type = "json"
        revision = chat_stream_tool_output_revision(content, "json")
    else:
        content, output_type, revision = content_resolver(
            row_id, part_index, "part", part
        )
    return {
        "type": "text",
        "text": "[Large part. Expand to load full content.]",
        "chat_stream_part_preview": {
            "kind": "chat-stream-part-preview",
            "row_id": row_id,
            "part_index": part_index,
            "output_bytes": len(content.encode("utf-8", errors="replace")),
            "output_chars": len(content),
            "output_type": output_type,
            "output_revision": revision,
            "preview": content[:CHAT_STREAM_CONTENT_PREVIEW_CHARS],
        },
    }


def bounded_chat_stream_rows(
    rows: list[dict[str, Any]],
    *,
    max_bytes: int,
    max_rows: int | None = None,
    row_projector: Callable[[dict[str, Any]], dict[str, Any]] = bounded_chat_stream_row,
) -> list[dict[str, Any]]:
    """Return the newest contiguous row suffix that fits a byte budget."""
    source = rows[-max_rows:] if max_rows is not None else rows
    projected = [row_projector(row) for row in source]
    sizes = [chat_stream_wire_bytes(row) for row in projected]
    total = 2 + sum(sizes) + max(0, len(projected) - 1)
    start = 0
    while start < len(projected) and total > max_bytes:
        if start == len(projected) - 1:
            total = 0
        else:
            total -= sizes[start] + 1
        start += 1
    return projected[start:]


class InvalidRowCursor(ValueError):
    """The supplied row cursor is malformed."""


class StaleRowCursor(ValueError):
    """The transcript was compacted or the cursor row is no longer present."""


class ConversationChanged(ValueError):
    """The supplied cursor belongs to another conversation."""


def _row_time(row: dict[str, Any]) -> datetime | None:
    value = row.get("created_at")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _is_compaction_marker(row: dict[str, Any]) -> bool:
    parts = row.get("parts")
    return row.get("kind") == "marker" and isinstance(parts, list) and any(
        isinstance(part, dict)
        and part.get("type") == "text"
        and part.get("text") == "Earlier messages compacted"
        for part in parts
    )


def merge_chat_stream_rows(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge rows, preserving transcript order and inserting live rows by time."""
    ordered_ids: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    for source_index, source in enumerate(sources):
        for row in source:
            if not isinstance(row, dict):
                raise ValueError("chat stream rows must be objects")
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                raise ValueError("chat stream row id must be a non-empty string")
            copied = copy.deepcopy(row)
            if row_id in rows:
                rows[row_id] = copied
                continue
            insert_at = len(ordered_ids)
            if source_index and _is_compaction_marker(copied):
                insert_at = 0
            elif source_index:
                row_time = _row_time(copied)
                if row_time is not None:
                    for index, current_id in enumerate(ordered_ids):
                        current = rows[current_id]
                        current_time = _row_time(current)
                        if (
                            not _is_compaction_marker(current)
                            and current_time is not None
                            and current_time > row_time
                        ):
                            insert_at = index
                            break
            ordered_ids.insert(insert_at, row_id)
            rows[row_id] = copied
    return [rows[row_id] for row_id in ordered_ids]


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error) as error:
        raise InvalidRowCursor("Invalid row cursor.") from error


class ChatStreamRowPager:
    """Page rows from the daemon's live row log and saved transcript.

    ``snapshot_rows_provider`` supplies the current stream window. The other
    providers supply the daemon's in-memory row log, persisted transcript, and
    latest compaction sequence. Rows from both sources are kept in their
    transcript order, with the live copy replacing the row with the same id.
    """

    def __init__(
        self,
        *,
        conversation_uid_provider: Callable[[], str],
        snapshot_rows_provider: Callable[[], list[dict[str, Any]]],
        transcript_rows_provider: Callable[[], list[dict[str, Any]]],
        row_log_provider: Callable[[], list[dict[str, Any]]],
        compaction_seq_provider: Callable[[], int],
        row_projector: Callable[[dict[str, Any]], dict[str, Any]] = bounded_chat_stream_row,
        max_response_bytes: int = CHAT_STREAM_MAX_RESPONSE_BYTES,
    ) -> None:
        self._conversation_uid_provider = conversation_uid_provider
        self._snapshot_rows_provider = snapshot_rows_provider
        self._transcript_rows_provider = transcript_rows_provider
        self._row_log_provider = row_log_provider
        self._compaction_seq_provider = compaction_seq_provider
        self._row_projector = row_projector
        self._max_response_bytes = max_response_bytes

    def read(self, *, before: str | None, limit: int) -> dict[str, Any]:
        """Return the oldest-first page before ``before`` or the current window."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_PAGE_SIZE
        ):
            raise ValueError("row page limit is outside the allowed range")

        conversation_uid = self._conversation_uid_provider()
        if not isinstance(conversation_uid, str) or not conversation_uid:
            raise ValueError("conversation uid is unavailable")
        compaction_seq = self._compaction_seq_provider()
        if (
            isinstance(compaction_seq, bool)
            or not isinstance(compaction_seq, int)
            or compaction_seq < 0
        ):
            raise ValueError("chat stream compaction sequence is invalid")

        snapshot_rows = self._snapshot_rows_provider()
        transcript_rows = self._transcript_rows_provider()
        row_log_rows = self._row_log_provider()
        if not all(
            isinstance(rows, list)
            for rows in (snapshot_rows, transcript_rows, row_log_rows)
        ):
            raise ValueError("chat stream row sources must be lists")

        all_rows = merge_chat_stream_rows(transcript_rows, snapshot_rows, row_log_rows)
        if before is None:
            if not snapshot_rows:
                return {"rows": [], "older": None}
            boundary = snapshot_rows[0]
            boundary_id = boundary.get("id")
            boundary_index = next(
                (
                    index
                    for index, row in enumerate(all_rows)
                    if row.get("id") == boundary_id
                ),
                None,
            )
            if boundary_index is None:
                raise StaleRowCursor("The snapshot boundary is no longer present.")
            boundary_seq = all_rows[boundary_index].get("seq")
        else:
            if (
                not isinstance(before, str)
                or not before
                or len(before) > _MAX_CURSOR_LENGTH
            ):
                raise InvalidRowCursor("Invalid row cursor.")
            boundary_id, boundary_seq, cursor_uid, cursor_compaction_seq = (
                self._decode_cursor(before)
            )
            if cursor_uid != conversation_uid:
                raise ConversationChanged("Row cursor belongs to another conversation.")
            if cursor_compaction_seq != compaction_seq:
                raise StaleRowCursor("The transcript was compacted after this cursor.")

        if (
            not isinstance(boundary_id, str)
            or isinstance(boundary_seq, bool)
            or not isinstance(boundary_seq, int)
        ):
            raise StaleRowCursor("The row cursor no longer has a valid boundary.")
        if before is not None:
            boundary_index = next(
                (
                    index
                    for index, row in enumerate(all_rows)
                    if row.get("id") == boundary_id and row.get("seq") == boundary_seq
                ),
                None,
            )
            if boundary_index is None:
                raise StaleRowCursor("The row cursor boundary is no longer present.")

        older_rows = all_rows[:boundary_index]
        page_rows = older_rows[-limit:]
        projected_rows = [self._row_projector(row) for row in page_rows]
        row_sizes = [chat_stream_wire_bytes(row) for row in projected_rows]
        remaining_size = sum(row_sizes) + max(0, len(projected_rows) - 1)
        start = 0
        while start < len(projected_rows):
            selected = projected_rows[start:]
            older = None
            if len(older_rows) > len(selected):
                oldest = selected[0]
                older = self._encode_cursor(
                    conversation_uid,
                    oldest["id"],
                    oldest["seq"],
                    compaction_seq,
                )
            response = {"rows": selected, "older": older}
            empty_response_bytes = chat_stream_wire_bytes(
                {"rows": [], "older": older}
            )
            if empty_response_bytes + remaining_size <= self._max_response_bytes:
                return response
            if start == len(projected_rows) - 1:
                remaining_size = 0
            else:
                remaining_size -= row_sizes[start] + 1
            start += 1
        if not page_rows:
            return {"rows": [], "older": None}
        raise ValueError("chat stream row page exceeds the response byte budget")

    def snapshot_older_cursor(self) -> str | None:
        """Return the cursor immediately before the current snapshot window."""
        conversation_uid = self._conversation_uid_provider()
        if not isinstance(conversation_uid, str) or not conversation_uid:
            raise ValueError("conversation uid is unavailable")
        compaction_seq = self._compaction_seq_provider()
        if (
            isinstance(compaction_seq, bool)
            or not isinstance(compaction_seq, int)
            or compaction_seq < 0
        ):
            raise ValueError("chat stream compaction sequence is invalid")

        snapshot_rows = self._snapshot_rows_provider()
        transcript_rows = self._transcript_rows_provider()
        row_log_rows = self._row_log_provider()
        if not all(
            isinstance(rows, list)
            for rows in (snapshot_rows, transcript_rows, row_log_rows)
        ):
            raise ValueError("chat stream row sources must be lists")
        if not snapshot_rows:
            return None

        all_rows = merge_chat_stream_rows(transcript_rows, snapshot_rows, row_log_rows)
        boundary = snapshot_rows[0]
        boundary_index = next(
            (
                index
                for index, row in enumerate(all_rows)
                if row.get("id") == boundary.get("id")
            ),
            None,
        )
        if boundary_index is None:
            raise StaleRowCursor("The snapshot boundary is no longer present.")
        if boundary_index == 0:
            return None

        row = all_rows[boundary_index]
        boundary_id = row.get("id")
        boundary_seq = row.get("seq")
        if (
            not isinstance(boundary_id, str)
            or isinstance(boundary_seq, bool)
            or not isinstance(boundary_seq, int)
        ):
            raise StaleRowCursor("The snapshot boundary no longer has a valid cursor.")
        return self._encode_cursor(
            conversation_uid,
            boundary_id,
            boundary_seq,
            compaction_seq,
        )

    @staticmethod
    def _encode_cursor(
        conversation_uid: str,
        boundary_id: str,
        boundary_seq: int,
        compaction_seq: int,
    ) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "conversation_uid": conversation_uid,
                "boundary_id": boundary_id,
                "boundary_seq": boundary_seq,
                "compaction_seq": compaction_seq,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _b64encode(payload)

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, int, str, int]:
        try:
            payload = json.loads(_b64decode(cursor))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidRowCursor("Invalid row cursor.") from error
        if (
            not isinstance(payload, dict)
            or isinstance(payload.get("v"), bool)
            or payload.get("v") != 1
        ):
            raise InvalidRowCursor("Invalid row cursor.")
        conversation_uid = payload.get("conversation_uid")
        boundary_id = payload.get("boundary_id")
        boundary_seq = payload.get("boundary_seq")
        compaction_seq = payload.get("compaction_seq")
        if (
            not isinstance(conversation_uid, str)
            or not conversation_uid
            or not isinstance(boundary_id, str)
            or not boundary_id
            or isinstance(boundary_seq, bool)
            or not isinstance(boundary_seq, int)
            or isinstance(compaction_seq, bool)
            or not isinstance(compaction_seq, int)
            or compaction_seq < 0
        ):
            raise InvalidRowCursor("Invalid row cursor.")
        return boundary_id, boundary_seq, conversation_uid, compaction_seq


def register_chat_stream_rows_handler(
    rpc_server: Any, pager: ChatStreamRowPager
) -> None:
    """Register the daemon read action consumed by the engine rows route."""

    async def _read_rows(params: dict[str, Any]) -> dict[str, Any]:
        limit = params.get("limit", 50)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_PAGE_SIZE
        ):
            return {"error": "invalid_request"}
        try:
            return pager.read(before=params.get("before"), limit=limit)
        except ConversationChanged:
            return {"error": "conversation_changed"}
        except StaleRowCursor:
            return {"error": "stale_cursor"}
        except InvalidRowCursor:
            return {"error": "invalid_cursor"}

    rpc_server.register("state.chat_stream.rows", _read_rows)
