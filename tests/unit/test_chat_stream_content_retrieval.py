from __future__ import annotations

import json
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor.state.chat_stream_rows import (
    CHAT_STREAM_MAX_ROW_BYTES,
    bounded_chat_stream_row,
    chat_stream_wire_bytes,
)
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
                "args": {"payload": "a" * 300_000},
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
        assert len(part["text"]) < 16_384

    args_marker = tool_part["args"]
    assert args_marker["kind"] == "chat-stream-content-preview"
    assert args_marker["row_id"] == row["id"]
    assert args_marker["field"] == "args"
    assert "args" in tool_part


def test_chat_content_chunks_round_trip_and_reject_stale_revision():
    text = "reply 🌵\n" * 8_000
    reasoning = "private reasoning " * 8_000
    args = {"payload": "a" * 300_000, "unicode": "🌵"}
    row = _row(
        [
            {"type": "text", "text": text},
            {"type": "reasoning", "text": reasoning},
            {
                "type": "tool-call",
                "tool_call_id": "call-large-args",
                "tool_name": "read",
                "args": args,
                "result": "done",
            },
        ]
    )
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_manager=None, conversation_history=[]),
        get_conversation_uid=lambda: "content-preview-test",
    )
    service = ChatStreamService(state)
    service._upsert("content-preview-test", row)
    event = service._log("content-preview-test")._events[-1]
    projected = event["data"]
    markers = [
        projected["parts"][0]["chat_stream_content_preview"],
        projected["parts"][1]["chat_stream_content_preview"],
        projected["parts"][2]["args"],
    ]

    for marker, field, expected in (
        (markers[0], "text", text),
        (markers[1], "reasoning", reasoning),
        (markers[2], "args", args),
    ):
        chunks = []
        offset = 0
        while True:
            chunk = service.get_content(
                {
                    "row_id": row["id"],
                    "part_index": marker["part_index"],
                    "field": field,
                    "output_revision": marker["output_revision"],
                    "offset": offset,
                    "limit": 32_768,
                }
            )
            assert chunk["offset"] == offset
            assert len(chunk["content"].encode("utf-8")) <= 32_768
            chunks.append(chunk["content"])
            if chunk["done"]:
                break
            offset = chunk["next_offset"]
        combined = "".join(chunks)
        if field == "args":
            assert json.loads(combined) == expected
        else:
            assert combined == expected

    replacement = {
        **row,
        "parts": [
            {"type": "text", "text": "changed " + text},
            *row["parts"][1:],
        ],
    }
    service._upsert("content-preview-test", replacement)
    stale = service.get_content(
        {
            "row_id": row["id"],
            "part_index": 0,
            "field": "text",
            "output_revision": markers[0]["output_revision"],
            "offset": 0,
            "limit": 128,
        }
    )
    assert stale == {"error": "output_changed"}


def test_pathological_valid_parts_use_bounded_row_placeholder_and_page_back():
    original_parts = [
        {"type": "image", "url": "data:image/png;base64," + "A" * 300_000},
        {"type": "prompt", "prompt_id": "prompt-large", "data": {"payload": "B" * 300_000}},
        {
            "type": "hub-note",
            "from": "agent-large",
            "to": "",
            "text": "C" * 300_000,
            "observed": False,
        },
    ]
    row = _row(original_parts)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_manager=None, conversation_history=[]),
        get_conversation_uid=lambda: "content-preview-test",
    )
    service = ChatStreamService(state)
    service._upsert("content-preview-test", row)

    projected = service._log("content-preview-test")._events[-1]["data"]
    marker = projected.get("chat_stream_parts_preview")
    assert isinstance(marker, dict)
    assert marker["kind"] == "chat-stream-parts-preview"
    assert marker["row_id"] == row["id"]
    assert marker["part_count"] == len(original_parts)
    assert chat_stream_wire_bytes(projected) <= CHAT_STREAM_MAX_ROW_BYTES
    assert event_problems(_event(projected)) == []

    page = service.get_parts(
        {
            "row_id": row["id"],
            "output_revision": marker["output_revision"],
            "offset": 0,
            "limit": len(original_parts),
        }
    )
    assert page["done"] is True
    assert page["total_parts"] == len(original_parts)
    assert len(page["parts"]) == len(original_parts)
    for part_index, (page_part, original_part) in enumerate(
        zip(page["parts"], original_parts, strict=True)
    ):
        part_marker = page_part.get("chat_stream_part_preview")
        assert isinstance(part_marker, dict)
        assert part_marker["part_index"] == part_index
        chunks = []
        offset = 0
        while True:
            chunk = service.get_content(
                {
                    "row_id": row["id"],
                    "part_index": part_index,
                    "field": "part",
                    "output_revision": part_marker["output_revision"],
                    "offset": offset,
                    "limit": 32_768,
                }
            )
            assert len(chunk["content"].encode("utf-8")) <= 32_768
            chunks.append(chunk["content"])
            if chunk["done"]:
                break
            offset = chunk["next_offset"]
        assert json.loads("".join(chunks)) == original_part


def test_pathological_part_count_is_pageable_without_truncation():
    original_parts = [
        {"type": "text", "text": f"part {index:05d} " + "x" * 96}
        for index in range(5_000)
    ]
    row = _row(original_parts)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_manager=None, conversation_history=[]),
        get_conversation_uid=lambda: "content-preview-test",
    )
    service = ChatStreamService(state)
    service._upsert("content-preview-test", row)
    projected = service._log("content-preview-test")._events[-1]["data"]
    marker = projected.get("chat_stream_parts_preview")
    assert isinstance(marker, dict)
    assert marker["part_count"] == len(original_parts)
    assert chat_stream_wire_bytes(projected) <= CHAT_STREAM_MAX_ROW_BYTES

    page = service.get_parts(
        {
            "row_id": row["id"],
            "output_revision": marker["output_revision"],
            "offset": 4_990,
            "limit": 10,
        }
    )
    assert page["done"] is True
    assert page["parts"] == original_parts[4_990:]
