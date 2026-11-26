from __future__ import annotations

from kollabor.state.chat_stream_rows import bounded_chat_stream_row
from kollabor_events.chat_stream import event_problems


def _event(row):
    return {
        "seq": 1,
        "conversation_uid": "content-preview-test",
        "generation": "a" * 32,
        "type": "row.upsert",
        "data": row,
    }


def _row(parts):
    return {
        "id": "row-content-preview",
        "seq": 1,
        "kind": "reply",
        "role": "assistant",
        "turn_id": "turn-content-preview",
        "status": "completed",
        "parts": parts,
        "created_at": "2026-11-01T00:00:00Z",
    }


def test_oversized_row_projection_keeps_tool_args_and_validates():
    parts = [
        {
            "type": "tool-call",
            "tool_call_id": f"call-{index}",
            "tool_name": "read",
            "args": {"path": "/tmp/file"},
            # Below the inline-output threshold, but the combined row exceeds
            # the row limit and forces the last-resort compact projection.
            "result": "x" * 8_000,
        }
        for index in range(34)
    ]

    projected = bounded_chat_stream_row(_row(parts))

    assert event_problems(_event(projected)) == []
    assert len(projected["parts"]) == len(parts)
    assert all("args" in part for part in projected["parts"])


def test_large_text_reasoning_and_tool_args_use_fetchable_previews():
    row = _row(
        [
            {"type": "text", "text": "reply 🌵" * 50_000},
            {"type": "reasoning", "text": "reasoning " * 50_000},
            {
                "type": "tool-call",
                "tool_call_id": "call-large-args",
                "tool_name": "read",
                "args": {"payload": "a" * 80_000},
                "result": "done",
            },
        ]
    )

    projected = bounded_chat_stream_row(row)
    text_part, reasoning_part, tool_part = projected["parts"]

    for part, field in ((text_part, "text"), (reasoning_part, "reasoning")):
        marker = part.get("chat_stream_content_preview")
        assert isinstance(marker, dict)
        assert marker["kind"] == "chat-stream-content-preview"
        assert marker["row_id"] == row["id"]
        assert marker["field"] == field
        assert len(part[field]) < 16_384

    args_marker = tool_part["args"]
    assert args_marker["kind"] == "chat-stream-content-preview"
    assert args_marker["row_id"] == row["id"]
    assert args_marker["field"] == "args"
    assert "args" in tool_part
