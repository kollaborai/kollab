from __future__ import annotations

from types import SimpleNamespace

import kollabor.state.chat_stream as chat_stream_module
from kollabor.state.chat_stream import ChatStreamService


def test_append_refreshes_a_preview_whose_original_content_exceeds_cache_limit(
    monkeypatch,
):
    # Keep this regression small while exercising the same uncached revision
    # path used when a real streamed value exceeds the 32 MiB cache ceiling.
    monkeypatch.setattr(chat_stream_module, "_CHAT_STREAM_TOOL_OUTPUT_CACHE_BYTES", 4)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(
            conversation_manager=None,
            conversation_history=[],
        ),
        get_conversation_uid=lambda: "uncached-stream-content-test",
    )
    service = ChatStreamService(state)
    uid = state.get_conversation_uid()
    row = {
        "id": "reply-uncached-stream-content",
        "seq": 0,
        "kind": "reply",
        "role": "assistant",
        "turn_id": "turn-uncached-stream-content",
        "status": "streaming",
        "parts": [
            {"type": "text", "text": "initial streamed content " * 6_000},
            {"type": "text", "text": "second streamed content " * 6_000},
        ],
        "created_at": "2026-11-01T00:00:00Z",
    }

    service._upsert(uid, row)
    snapshot = service._snapshot(uid)
    marker = snapshot["rows"][0]["parts"][0]["chat_stream_content_preview"]

    append = service.record_event(
        "row.append",
        {"id": row["id"], "part": 0, "text": "latest tail"},
    )

    assert append["type"] == "row.upsert"
    refreshed = append["data"]["parts"][0]["chat_stream_content_preview"]
    assert refreshed["output_revision"] != marker["output_revision"]
    chunk = service.get_content(
        {
            "row_id": row["id"],
            "part_index": 0,
            "field": "text",
            "output_revision": refreshed["output_revision"],
            "offset": 0,
            "limit": 32,
        }
    )
    expected = row["parts"][0]["text"] + "latest tail"
    assert chunk["content"] == expected[:32]


def test_append_refreshes_a_preview_evicted_by_another_rows_lru_entry(monkeypatch):
    # Both values fit individually, but together exceed the shared cache. The
    # second row evicts the first row's published revision from the LRU.
    monkeypatch.setattr(chat_stream_module, "_CHAT_STREAM_TOOL_OUTPUT_CACHE_BYTES", 500_000)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(
            conversation_manager=None,
            conversation_history=[],
        ),
        get_conversation_uid=lambda: "evicted-stream-content-test",
    )
    service = ChatStreamService(state)
    uid = state.get_conversation_uid()
    first = {
        "id": "reply-first-stream-content",
        "seq": 0,
        "kind": "reply",
        "role": "assistant",
        "turn_id": "turn-first-stream-content",
        "status": "streaming",
        "parts": [{"type": "text", "text": "A" * 300_000}],
        "created_at": "2026-11-01T00:00:00Z",
    }
    second = {
        **first,
        "id": "reply-second-stream-content",
        "turn_id": "turn-second-stream-content",
        "parts": [{"type": "text", "text": "B" * 300_000}],
    }

    service._upsert(uid, first)
    first_snapshot = service._snapshot(uid)
    first_marker = next(
        row for row in first_snapshot["rows"] if row["id"] == first["id"]
    )["parts"][0]["chat_stream_content_preview"]
    service._upsert(uid, second)

    assert not any(key[1] == first["id"] for key in service._content_cache)
    append = service.record_event(
        "row.append",
        {"id": first["id"], "part": 0, "text": "latest tail"},
    )

    assert append["type"] == "row.upsert"
    stale = service.get_content(
        {
            "row_id": first["id"],
            "part_index": 0,
            "field": "text",
            "output_revision": first_marker["output_revision"],
            "offset": 0,
            "limit": 32,
        }
    )
    assert stale == {"error": "output_changed"}
    refreshed = append["data"]["parts"][0]["chat_stream_content_preview"]
    assert refreshed["output_revision"] != first_marker["output_revision"]
    chunk = service.get_content(
        {
            "row_id": first["id"],
            "part_index": 0,
            "field": "text",
            "output_revision": refreshed["output_revision"],
            "offset": 0,
            "limit": 32,
        }
    )
    assert chunk["content"] == ("A" * 300_000 + "latest tail")[:32]
