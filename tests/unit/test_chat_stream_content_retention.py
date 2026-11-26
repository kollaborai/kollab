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
