"""Daemon paging over chat stream row log and transcript data (#1451)."""

from __future__ import annotations

import pytest

from kollabor.state.chat_stream_rows import (
    ChatStreamRowPager,
    ConversationChanged,
    StaleRowCursor,
    register_chat_stream_rows_handler,
)
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor_events.chat_stream import REPLAY_WINDOW
from kollabor_rpc import RpcRequest, RpcServer


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
        live_row = _row("row-3", 3)
        live_row["parts"] = [{"type": "text", "text": "row-3-live"}]
        self.row_log = [live_row, _row("row-5", 5), _row("row-6", 6)]
        self.snapshot_rows = [_row("row-5", 5), _row("row-6", 6)]

    def pager(self):
        return ChatStreamRowPager(
            conversation_uid_provider=lambda: self.conversation_uid,
            snapshot_rows_provider=lambda: self.snapshot_rows,
            transcript_rows_provider=lambda: self.transcript,
            row_log_provider=lambda: self.row_log,
            compaction_seq_provider=lambda: self.compaction_seq,
        )


def test_daemon_pages_row_log_and_transcript_back_to_the_start():
    source = _Sources()
    pager = source.pager()

    first = pager.read(before=None, limit=2)
    second = pager.read(before=first["older"], limit=2)

    assert [row["id"] for row in first["rows"]] == ["row-3", "row-4"]
    assert first["rows"][0]["parts"][0]["text"] == "row-3-live"
    assert [row["id"] for row in second["rows"]] == ["row-1", "row-2"]
    assert second["older"] is None


def test_daemon_pages_live_only_note_at_its_transcript_position():
    source = _Sources()
    before = _row("before-note", 1)
    before["created_at"] = "2026-11-25T12:00:00Z"
    boundary = _row("snapshot-boundary", 2)
    boundary["created_at"] = "2026-11-25T12:00:02Z"
    note = _row("live-note", 3)
    note.update(
        {
            "kind": "note",
            "role": "system",
            "created_at": "2026-11-25T12:00:01Z",
            "parts": [{"type": "text", "text": "between transcript rows"}],
        }
    )
    source.transcript = [before, boundary]
    source.snapshot_rows = [boundary]
    source.row_log = [note]

    page = source.pager().read(before=None, limit=10)

    assert [row["id"] for row in page["rows"]] == ["before-note", "live-note"]


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
    cursor = (
        await _rpc_call(rpc, "state.chat_stream.rows", {"before": None, "limit": 2})
    )["older"]

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


@pytest.mark.asyncio
async def test_state_startup_registers_the_rows_handler_for_local_chat_stream():
    llm = type("LLM", (), {})()
    llm.conversation_history = [
        {
            "role": "user",
            "content": f"row {index}",
            "metadata": {"request_id": f"input-{index}"},
            "timestamp": "2026-11-25T12:00:00Z",
            "uuid": f"input-{index}",
        }
        for index in range(REPLAY_WINDOW + 3)
    ]
    llm.conversation_manager = type(
        "ConversationManager", (), {"conversation_uid": lambda self: "conv-1"}
    )()
    state = LocalStateService(llm, profile_manager=object(), event_bus=None)
    rpc = RpcServer()

    register_state_handlers(rpc, state)
    snapshot_response = await rpc.handle_request(
        RpcRequest(
            request_id="snapshot-at-startup",
            method="state.chat_stream.subscribe",
            params={"after": None, "conversation_uid": None},
        )
    )
    assert snapshot_response.error is None
    snapshot = snapshot_response.result["events"][0]["data"]
    assert len(snapshot["rows"]) == REPLAY_WINDOW
    assert isinstance(snapshot["older"], str)

    response = await rpc.handle_request(
        RpcRequest(
            request_id="rows-at-startup",
            method="state.chat_stream.rows",
            params={"before": snapshot["older"], "limit": 2},
        )
    )

    assert response.error is None
    assert [row["id"] for row in response.result["rows"]] == [
        "input-1",
        "input-2",
    ]
    assert isinstance(response.result["older"], str)
