from __future__ import annotations

import json
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor.state.chat_stream_rows import (
    CHAT_STREAM_MAX_ROW_BYTES,
    chat_stream_wire_bytes,
)
from kollabor_events.chat_stream import event_problems


def _row(result):
    return {
        "id": "row-result-preview",
        "seq": 1,
        "kind": "tool",
        "role": "assistant",
        "turn_id": "turn-result-preview",
        "status": "completed",
        "parts": [
            {
                "type": "tool-call",
                "tool_call_id": "call-result-preview",
                "tool_name": "read",
                "args": {},
                "result": result,
            }
        ],
        "created_at": "2026-11-01T00:00:00Z",
    }


def _event(row):
    return {
        "seq": 1,
        "conversation_uid": "result-preview-test",
        "generation": "a" * 32,
        "type": "row.upsert",
        "data": row,
    }


def test_marker_shaped_large_result_is_opaque_bounded_and_fetchable():
    result = {
        "kind": "chat-stream-tool-output-preview",
        "row_id": "user-data-row",
        "tool_call_id": "user-data-call",
        "payload": "ordinary tool result " * 10_000,
    }
    row = _row(result)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(
            conversation_manager=None, conversation_history=[]
        ),
        get_conversation_uid=lambda: "result-preview-test",
    )
    service = ChatStreamService(state)
    service._upsert("result-preview-test", row)
    projected = service._log("result-preview-test")._events[-1]["data"]
    part = projected["parts"][0]
    marker = part["chat_stream_result_preview"]
    expected = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    )

    assert event_problems(_event(projected)) == []
    assert "result" not in part
    assert marker["kind"] == "chat-stream-tool-output-preview"
    assert marker["tool_call_id"] == "call-result-preview"
    assert marker["output_type"] == "json"
    assert marker["output_revision"] == service._cached_tool_output(
        "result-preview-test", row["id"], "call-result-preview", result
    )[2]
    assert len(expected.encode("utf-8")) == marker["output_bytes"]
    assert len(expected) == marker["output_chars"]
    assert len(str(marker["preview"])) < len(expected)
    assert chat_stream_wire_bytes(projected) < CHAT_STREAM_MAX_ROW_BYTES
    assert row["parts"][0]["result"] == result

    chunk = service.get_tool_output(
        {
            "row_id": row["id"],
            "tool_call_id": "call-result-preview",
            "output_revision": marker["output_revision"],
            "offset": 0,
            "limit": 128,
        }
    )
    assert chunk["output_type"] == "json"
    assert chunk["output_revision"] == marker["output_revision"]
    assert chunk["content"] == expected[:128]

    service._clear_tool_output_cache("result-preview-test")
    uncached_chunk = service.get_tool_output(
        {
            "row_id": row["id"],
            "tool_call_id": "call-result-preview",
            "output_revision": marker["output_revision"],
            "offset": 0,
            "limit": 128,
        }
    )
    assert uncached_chunk["content"] == expected[:128]


def test_marker_shaped_result_data_is_not_counted_as_preview_reference():
    result = {
        "kind": "chat-stream-tool-output-preview",
        "row_id": "row-result-preview",
        "tool_call_id": "call-result-preview",
        "output_bytes": 1,
        "output_chars": 1,
        "output_type": "text",
        "output_revision": "d" * 64,
        "preview": "ordinary user-owned result data",
    }
    row = _row(result)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(
            conversation_manager=None, conversation_history=[]
        ),
        get_conversation_uid=lambda: "result-preview-test",
    )
    service = ChatStreamService(state)

    service._upsert("result-preview-test", row)

    projected = service._log("result-preview-test")._events[-1]["data"]
    assert event_problems(_event(projected)) == []
    assert projected["parts"][0]["result"] == result
    assert ("result-preview-test", row["id"]) not in service._row_content_unretained
    assert ("result-preview-test", row["id"]) not in service._row_content_cache_refs


def test_event_contract_rejects_invalid_top_level_tool_result_preview():
    row = _row("result")
    row["parts"][0]["chat_stream_result_preview"] = {
        "kind": "chat-stream-tool-output-preview",
        "row_id": row["id"],
        "tool_call_id": "a-different-call",
        "output_bytes": 1,
        "output_chars": 1,
        "output_type": "text",
        "output_revision": "b" * 64,
        "preview": "x",
    }

    assert any(
        "tool_call_id" in problem for problem in event_problems(_event(row))
    )


def test_event_contract_validates_part_previews_on_prompt_parts():
    row = _row("result")
    row["parts"] = [
        {
            "type": "prompt",
            "prompt_id": "prompt-1",
            "data": {},
            "chat_stream_part_preview": {
                "kind": "chat-stream-part-preview",
                "row_id": "different-row",
                "part_index": 0,
                "output_bytes": 100,
                "output_chars": 100,
                "output_type": "json",
                "output_revision": "c" * 64,
                "preview": "{}",
            },
        }
    ]

    assert any("row_id" in problem for problem in event_problems(_event(row)))
