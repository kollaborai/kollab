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


def _tool_history(turns: int, output: str):
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
                            "function": {"name": "read", "arguments": "{}"},
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
    state = _State(_tool_history(220, "x" * 75_000))
    stream = ChatStreamService(state)
    expected_ids = [row["id"] for row in stream._transcript_rows(state.get_conversation_uid())]

    response = stream.subscribe({"after": None})
    snapshot_event = response["events"][0]
    assert snapshot_event["type"] == "snapshot"
    assert _wire_bytes(response) <= _WIRE_BUDGET
    snapshot_rows = snapshot_event["data"]["rows"]
    assert snapshot_rows
    assert snapshot_event["data"]["older"] is not None

    replacement = stream.publish_conversation_reset()
    assert replacement["type"] == "replace"
    assert _wire_bytes(replacement) <= _WIRE_BUDGET

    seen_ids = [row["id"] for row in snapshot_rows]
    cursor = snapshot_event["data"]["older"]
    pager = stream.row_pager()
    while cursor is not None:
        page = pager.read(before=cursor, limit=100)
        assert page["rows"]
        assert _wire_bytes(page) <= _WIRE_BUDGET
        seen_ids.extend(row["id"] for row in page["rows"])
        cursor = page["older"]

    assert len(seen_ids) == len(set(seen_ids))
    assert set(seen_ids) == set(expected_ids)


def test_individually_oversized_tool_result_has_a_bounded_preview_and_fetchable_chunks():
    output = "large tool output 🌵\n" + "z" * (5 * 1024 * 1024)
    state = _State(_tool_history(1, output))
    stream = ChatStreamService(state)
    event = stream.subscribe({"after": None})["events"][0]
    assert _wire_bytes(event) <= _WIRE_BUDGET

    tool_row = next(row for row in event["data"]["rows"] if row["kind"] == "tool")
    tool_part = tool_row["parts"][0]
    preview = tool_part["result"]
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
