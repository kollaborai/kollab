from __future__ import annotations

from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor.state.chat_stream_rows import (
    chat_stream_parts_revision,
    chat_stream_tool_output_revision,
    chat_stream_wire_bytes,
)


class _Bus:
    def get_service(self, _name):
        return None

    def register_service(self, _name, _service):
        pass


class _State:
    def __init__(self):
        self._event_bus = _Bus()
        self._llm_service = SimpleNamespace(
            conversation_manager=None,
            conversation_history=[],
        )

    def get_conversation_uid(self):
        return "retrieval-lookup"

    def chat_stream_snapshot(self):
        return {"rows": [], "turns": [], "inputs": []}


def _row():
    return {
        "id": "row-live",
        "seq": 1,
        "kind": "reply",
        "role": "assistant",
        "turn_id": "turn-live",
        "status": "completed",
        "parts": [
            {"type": "text", "text": "live text"},
            {
                "type": "tool-call",
                "tool_call_id": "call-live",
                "tool_name": "inspect",
                "args": {},
                "result": "live result",
            },
        ],
        "created_at": "2026-11-26T10:00:00Z",
    }


def _observe_transcript_lookups(stream):
    uid = stream._conversation_uid()
    lock = stream._log(uid)._lock
    calls = []
    original = stream._transcript_rows

    def observed(requested_uid):
        calls.append(lock._is_owned())
        return original(requested_uid)

    stream._transcript_rows = observed
    return calls


def test_live_content_parts_and_tool_output_skip_transcript_rebuilds():
    stream = ChatStreamService(_State())
    uid = stream._conversation_uid()
    row = _row()
    stream._rows[uid] = {row["id"]: row}
    calls = _observe_transcript_lookups(stream)
    parts = row["parts"]
    parts_content, parts_type, parts_revision = chat_stream_parts_revision(parts)
    assert parts_content
    assert parts_type == "json"
    text_revision = chat_stream_tool_output_revision("live text", "text")
    result_revision = chat_stream_tool_output_revision("live result", "text")

    content = stream.get_content(
        {
            "row_id": row["id"],
            "part_index": 0,
            "field": "text",
            "output_revision": text_revision,
            "offset": 0,
            "limit": 32,
        }
    )
    page = stream.get_parts(
        {
            "row_id": row["id"],
            "output_revision": parts_revision,
            "offset": 0,
            "limit": 8,
        }
    )
    result = stream.get_tool_output(
        {
            "row_id": row["id"],
            "tool_call_id": "call-live",
            "output_revision": result_revision,
            "offset": 0,
            "limit": 32,
        }
    )

    assert content["content"] == "live text"
    assert page["parts"] == parts
    assert result["content"] == "live result"
    assert chat_stream_wire_bytes(page) < 256 * 1024
    assert calls == [], "live lookups should not rebuild the persisted transcript"


def test_transcript_fallback_runs_outside_the_log_lock_for_all_retrievals():
    for method in ("get_content", "get_parts", "get_tool_output"):
        stream = ChatStreamService(_State())
        uid = stream._conversation_uid()
        row = _row()
        lock = stream._log(uid)._lock
        calls = []

        def transcript_rows(requested_uid):
            calls.append(lock._is_owned())
            assert requested_uid == uid
            return [row]

        stream._transcript_rows = transcript_rows
        if method == "get_content":
            params = {
                "row_id": row["id"],
                "part_index": 0,
                "field": "text",
                "output_revision": chat_stream_tool_output_revision(
                    "live text", "text"
                ),
                "offset": 0,
                "limit": 32,
            }
        elif method == "get_parts":
            _content, _output_type, revision = chat_stream_parts_revision(row["parts"])
            params = {
                "row_id": row["id"],
                "output_revision": revision,
                "offset": 0,
                "limit": 8,
            }
        else:
            params = {
                "row_id": row["id"],
                "tool_call_id": "call-live",
                "output_revision": chat_stream_tool_output_revision(
                    "live result", "text"
                ),
                "offset": 0,
                "limit": 32,
            }

        response = getattr(stream, method)(params)

        assert "error" not in response
        assert calls == [False], f"{method} rebuilt transcript under the log lock"
