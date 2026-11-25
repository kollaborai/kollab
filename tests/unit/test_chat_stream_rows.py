"""Daemon paging over chat stream row log and transcript data (#1451)."""

from __future__ import annotations

import pytest
from kollabor_rpc import RpcRequest, RpcServer

from kollabor.state.chat_stream_rows import (
    ChatStreamRowPager,
    ConversationChanged,
    StaleRowCursor,
    register_chat_stream_rows_handler,
)


def _row(row_id: str, seq: int) -> dict:
    return {
        "id": row_id,
        "seq": seq,
        "kind": "input",
        "role": "user",
        "turn_id": None,
        "status": "ended",
        "parts": [{"type": "text", "text": row_id}],
        "created_at": "2026-11-25T12:00:00Z",
    }


class _Sources:
    def __init__(self):
        self.conversation_uid = "conv-1"
        self.compaction_seq = 0
        self.transcript = [_row(f"row-{index}", index) for index in range(1, 6)]
        self.row_log = [_row("row-5", 5), _row("row-6", 6)]
        self.snapshot_rows = [_row("row-5", 5), _row("row-6", 6)]

    def pager(self):
        return ChatStreamRowPager(
            conversation_uid_provider=lambda: self.conversation_uid,
            snapshot_rows_provider=lambda: self.snapshot_rows,
            transcript_rows_provider=lambda: self.transcript,
            row_log_provider=lambda: self.row_log,
            compaction_seq_provider=lambda: self.compaction_seq,
        )


@pytest.mark.asyncio
async def test_daemon_pages_row_log_and_transcript_back_to_the_start():
    source = _Sources()
    pager = source.pager()

    first = pager.read(before=None, limit=2)
    second = pager.read(before=first["older"], limit=2)

    assert [row["id"] for row in first["rows"]] == ["row-3", "row-4"]
    assert [row["id"] for row in second["rows"]] == ["row-1", "row-2"]
    assert second["older"] is None


def test_daemon_returns_an_empty_page_at_the_start_of_the_transcript():
    source = _Sources()
    source.snapshot_rows = source.transcript
    source.row_log = []
    pager = source.pager()

    assert pager.read(before=None, limit=2) == {"rows": [], "older": None}


def test_daemon_marks_a_cursor_stale_after_compaction():
    source = _Sources()
    pager = source.pager()
    cursor = pager.read(before=None, limit=2)["older"]
    source.compaction_seq = 9

    with pytest.raises(StaleRowCursor):
        pager.read(before=cursor, limit=2)


def test_daemon_rejects_a_cursor_from_another_conversation():
    source = _Sources()
    cursor = source.pager().read(before=None, limit=2)["older"]
    source.conversation_uid = "conv-2"

    with pytest.raises(ConversationChanged):
        source.pager().read(before=cursor, limit=2)


async def _rpc_call(rpc: RpcServer, method: str, params: dict) -> dict:
    response = await rpc.handle_request(
        RpcRequest(request_id=f"{method}-{len(params)}", method=method, params=params)
    )
    assert response.error is None
    return response.result


@pytest.mark.asyncio
async def test_rows_rpc_maps_compaction_and_conversation_cursors_to_conflict_codes():
    source = _Sources()
    rpc = RpcServer()
    register_chat_stream_rows_handler(rpc, source.pager())
    cursor = (await _rpc_call(rpc, "state.chat_stream.rows", {"before": None, "limit": 2}))[
        "older"
    ]

    source.compaction_seq = 9
    stale = await _rpc_call(
        rpc, "state.chat_stream.rows", {"before": cursor, "limit": 2}
    )
    assert stale == {"error": "stale_cursor"}

    source.conversation_uid = "conv-2"
    changed = await _rpc_call(
        rpc, "state.chat_stream.rows", {"before": cursor, "limit": 2}
    )
    assert changed == {"error": "conversation_changed"}
