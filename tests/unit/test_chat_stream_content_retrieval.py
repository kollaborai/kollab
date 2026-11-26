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
    assert chat_stream_wire_bytes(page) <= 256 * 1024
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

    changed_row = {**row, "parts": [{"type": "text", "text": "replacement"}]}
    service._upsert("content-preview-test", changed_row)
    assert service.get_parts(
        {
            "row_id": row["id"],
            "output_revision": marker["output_revision"],
            "offset": 0,
            "limit": len(original_parts),
        }
    ) == {"error": "output_changed"}


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

    actual_parts = []
    offset = 0
    while True:
        page = service.get_parts(
            {
                "row_id": row["id"],
                "output_revision": marker["output_revision"],
                "offset": offset,
                "limit": 32,
            }
        )
        assert page["offset"] == offset
        assert page["next_offset"] > offset
        assert chat_stream_wire_bytes(page) <= 256 * 1024
        actual_parts.extend(page["parts"])
        if page["done"]:
            break
        offset = page["next_offset"]
    assert actual_parts == original_parts


def test_unpageable_required_row_metadata_fails_explicitly():
    row = _row([{"type": "text", "text": "small"}])
    row["status"] = "x" * CHAT_STREAM_MAX_ROW_BYTES

    try:
        bounded_chat_stream_row(row)
    except ValueError as exc:
        assert str(exc) == "chat stream row metadata exceeds the row byte budget"
    else:
        raise AssertionError("unpageable row metadata must not produce an oversized row")

    long_id_row = _row([{"type": "text", "text": "x" * 300_000}])
    long_id_row["id"] = "row-" + "x" * 600
    try:
        bounded_chat_stream_row(long_id_row)
    except ValueError as exc:
        assert str(exc) == "chat stream row id exceeds the content retrieval limit"
    else:
        raise AssertionError("an inaccessible content marker must not be returned")


def test_persisted_hub_note_parts_are_recoverable_after_service_restart():
    original_part = {
        "type": "hub-note",
        "from": "agent-large",
        "to": "",
        "text": "persistent note " * 20_000,
        "observed": False,
    }
    persisted_row = {
        **_row([original_part]),
        "id": "persistent-large-note",
        "kind": "note",
        "role": "system",
    }
    manager = SimpleNamespace(chat_stream_rows=[persisted_row])
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(
            conversation_manager=manager,
            conversation_history=[],
        ),
        get_conversation_uid=lambda: "content-preview-test",
    )
    service = ChatStreamService(state)

    projected = service._project_chat_stream_row(
        "content-preview-test", service._transcript_rows("content-preview-test")[0]
    )
    marker = projected["chat_stream_parts_preview"]
    page = service.get_parts(
        {
            "row_id": persisted_row["id"],
            "output_revision": marker["output_revision"],
            "offset": 0,
            "limit": 1,
        }
    )
    part_marker = page["parts"][0]["chat_stream_part_preview"]
    chunks = []
    offset = 0
    while True:
        chunk = service.get_content(
            {
                "row_id": persisted_row["id"],
                "part_index": 0,
                "field": "part",
                "output_revision": part_marker["output_revision"],
                "offset": offset,
                "limit": 32_768,
            }
        )
        chunks.append(chunk["content"])
        if chunk["done"]:
            break
        offset = chunk["next_offset"]

    assert page["done"] is True
    assert json.loads("".join(chunks)) == original_part
