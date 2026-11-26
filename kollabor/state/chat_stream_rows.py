"""Daemon paging for older rows in the ordered chat stream (#1451)."""

from __future__ import annotations

import base64
import binascii
import copy
import json
from collections.abc import Callable
from typing import Any

_MAX_CURSOR_LENGTH = 8192
_MAX_PAGE_SIZE = 100


class InvalidRowCursor(ValueError):
    """The supplied row cursor is malformed."""


class StaleRowCursor(ValueError):
    """The transcript was compacted or the cursor row is no longer present."""


class ConversationChanged(ValueError):
    """The supplied cursor belongs to another conversation."""


def _rows_by_id(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge transcript and live row-log records, with the live copy winning."""
    ordered_ids: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    for source in sources:
        for row in source:
            if not isinstance(row, dict):
                raise ValueError("chat stream rows must be objects")
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                raise ValueError("chat stream row id must be a non-empty string")
            if row_id not in rows:
                ordered_ids.append(row_id)
            rows[row_id] = copy.deepcopy(row)
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
    ) -> None:
        self._conversation_uid_provider = conversation_uid_provider
        self._snapshot_rows_provider = snapshot_rows_provider
        self._transcript_rows_provider = transcript_rows_provider
        self._row_log_provider = row_log_provider
        self._compaction_seq_provider = compaction_seq_provider

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

        all_rows = _rows_by_id(transcript_rows, snapshot_rows, row_log_rows)
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
        older = None
        if len(older_rows) > len(page_rows):
            oldest = page_rows[0]
            older = self._encode_cursor(
                conversation_uid,
                oldest["id"],
                oldest["seq"],
                compaction_seq,
            )
        return {"rows": page_rows, "older": older}

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

        all_rows = _rows_by_id(transcript_rows, snapshot_rows, row_log_rows)
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
