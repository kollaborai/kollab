from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService

_WIRE_BUDGET = 4 * 1024 * 1024


class _Bus:
    def get_service(self, _name):
        return None

    def register_service(self, _name, _service):
        pass


class _State:
    def __init__(self, history):
        self._event_bus = _Bus()
        self._llm_service = SimpleNamespace(
            conversation_manager=None,
            conversation_history=history,
        )

    def get_conversation_uid(self):
        return "conversation-byte-budget"

    def chat_stream_snapshot(self):
        return {"rows": [], "turns": [], "inputs": []}


def _tool_history(turns: int, output: str, arguments: str = "{}"):
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    messages = [
        SimpleNamespace(
            role="system",
            content="prompt",
            timestamp=start,
            metadata={"system_initialization": True},
            uuid="system",
        )
    ]
    for index in range(turns):
        turn_id = f"turn-{index}"
        tool_id = f"call-{index}"
        entries = (
            (
                "user",
                f"question-{index}",
                {"request_id": f"input-{index}", "turn_id": turn_id},
            ),
            (
                "assistant",
                "",
                {
                    "turn_id": turn_id,
                    "tool_calls": [
                        {
                            "id": tool_id,
                            "function": {"name": "read", "arguments": arguments},
                        }
                    ],
                },
            ),
            ("tool", output, {"tool_call_id": tool_id, "turn_id": turn_id}),
            ("assistant", f"answer-{index}", {"turn_id": turn_id}),
        )
        for role, content, metadata in entries:
            messages.append(
                SimpleNamespace(
                    role=role,
                    content=content,
                    timestamp=start + timedelta(seconds=len(messages)),
                    metadata=metadata,
                    uuid=f"message-{len(messages)}",
                )
            )
    return messages


def _wire_bytes(value) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def test_tool_heavy_snapshot_replace_and_pages_are_bounded_without_losing_rows():
    arguments = json.dumps({"payload": "a" * 150_000})
    state = _State(_tool_history(220, "x" * 75_000, arguments))
    stream = ChatStreamService(state)
    expected_ids = [row["id"] for row in stream._transcript_rows(state.get_conversation_uid())]

    response = stream.subscribe({"after": None})
    snapshot_event = response["events"][0]
    assert snapshot_event["type"] == "snapshot"
    assert _wire_bytes(response) <= _WIRE_BUDGET
    snapshot_rows = snapshot_event["data"]["rows"]
    assert snapshot_rows
    assert snapshot_event["data"]["older"] is not None
    assert len(snapshot_rows) < len(expected_ids)

    replacement = stream.publish_conversation_reset()
    assert replacement["type"] == "replace"
    assert _wire_bytes(replacement) <= _WIRE_BUDGET
    assert len(replacement["data"]["rows"]) < len(expected_ids)

    snapshot_event = stream.subscribe({"after": None})["events"][0]
    assert snapshot_event["type"] == "snapshot"
    assert _wire_bytes(snapshot_event) <= _WIRE_BUDGET
    snapshot_rows = snapshot_event["data"]["rows"]

    snapshot_ids = [row["id"] for row in snapshot_rows]
    older_pages = []
    cursor = snapshot_event["data"]["older"]
    pager = stream.row_pager()
    while cursor is not None:
        page = pager.read(before=cursor, limit=100)
        assert page["rows"]
        assert _wire_bytes(page) <= _WIRE_BUDGET
        assert len(page["rows"]) < 100
        older_pages.append([row["id"] for row in page["rows"]])
        cursor = page["older"]

    seen_ids = [row_id for page in reversed(older_pages) for row_id in page]
    seen_ids.extend(snapshot_ids)
    assert len(seen_ids) == len(set(seen_ids))
    assert seen_ids == expected_ids


def test_individually_oversized_tool_result_has_a_bounded_preview_and_fetchable_chunks():
    output = "large tool output 🌵\n" + "z" * (5 * 1024 * 1024)
    state = _State(_tool_history(1, output))
    stream = ChatStreamService(state)
    event = stream.subscribe({"after": None})["events"][0]
    assert _wire_bytes(event) <= _WIRE_BUDGET

    tool_row = next(row for row in event["data"]["rows"] if row["kind"] == "tool")
    tool_part = tool_row["parts"][0]
    preview = tool_part["chat_stream_result_preview"]
    assert preview["kind"] == "chat-stream-tool-output-preview"
    assert preview["row_id"] == tool_row["id"]
    assert preview["tool_call_id"] == tool_part["tool_call_id"]
    assert preview["output_bytes"] == len(output.encode("utf-8"))
    assert len(preview["preview"].encode("utf-8")) < 16 * 1024

    chunks = []
    offset = 0
    while True:
        chunk = stream.get_tool_output(
            {
                "row_id": tool_row["id"],
                "tool_call_id": tool_part["tool_call_id"],
                "output_revision": preview["output_revision"],
                "offset": offset,
                "limit": 32 * 1024,
            }
        )
        assert chunk["offset"] == offset
        assert len(chunk["content"].encode("utf-8")) <= 32 * 1024
        chunks.append(chunk["content"])
        if chunk["done"]:
            break
        offset = chunk["next_offset"]

    assert "".join(chunks) == output

    live_row = {
        "id": "live-tool-row",
        "seq": 0,
        "kind": "tool",
        "role": "assistant",
        "turn_id": "live-turn",
        "status": "completed",
        "parts": [
            {
                "type": "tool-call",
                "tool_call_id": "live-call",
                "tool_name": "read",
                "args": {},
                "result": output,
            }
        ],
        "created_at": "2026-11-01T00:00:00Z",
    }
    stream._upsert(state.get_conversation_uid(), live_row)
    live_event = stream._log(state.get_conversation_uid())._events[-1]
    assert _wire_bytes({"events": [live_event]}) <= _WIRE_BUDGET
    assert stream._rows[state.get_conversation_uid()]["live-tool-row"]["parts"][0]["result"] == output
    assert live_event["data"]["parts"][0]["chat_stream_result_preview"]["kind"] == (
        "chat-stream-tool-output-preview"
    )
    live_chunk = stream.get_tool_output(
        {
            "row_id": "live-tool-row",
            "tool_call_id": "live-call",
            "output_revision": live_event["data"]["parts"][0]["chat_stream_result_preview"]["output_revision"],
            "offset": 0,
            "limit": 1024,
        }
    )
    assert live_chunk["content"] == output[:1024]

    changed_row = {**live_row, "parts": [dict(live_row["parts"][0], result="changed")]}
    stream._upsert(state.get_conversation_uid(), changed_row)
    stale_chunk = stream.get_tool_output(
        {
            "row_id": "live-tool-row",
            "tool_call_id": "live-call",
            "output_revision": live_event["data"]["parts"][0]["chat_stream_result_preview"]["output_revision"],
            "offset": 0,
            "limit": 1024,
        }
    )
    assert stale_chunk == {"error": "output_changed"}
