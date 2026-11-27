from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import kollabor.state.chat_stream as chat_stream_module
from kollabor.state.chat_stream import ChatStreamService
from kollabor.state.chat_stream_rows import CHAT_STREAM_MAX_ROW_BYTES


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
        return "conversation-performance"

    def chat_stream_snapshot(self):
        return {"rows": [], "turns": [], "inputs": []}


def _native_history(turns: int):
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
        turn_id = f"native-{index}"
        entries = (
            (
                "user",
                f"question-{index}",
                {"request_id": f"web-{index}", "turn_id": turn_id},
            ),
            (
                "assistant",
                f"step-{index}",
                {
                    "turn_id": turn_id,
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ],
                },
            ),
            ("tool", f"result-{index}", {"tool_call_id": f"call-{index}"}),
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


def test_snapshot_turn_history_scans_are_linear_and_transcript_is_projected_once(
    monkeypatch,
):
    history = _native_history(80)
    stream = ChatStreamService(_State(history))

    turn_id_calls = 0
    original_turn_id = ChatStreamService._event_turn_id

    def count_turn_id_calls(event):
        nonlocal turn_id_calls
        turn_id_calls += 1
        return original_turn_id(event)

    monkeypatch.setattr(
        ChatStreamService, "_event_turn_id", staticmethod(count_turn_id_calls)
    )
    transcript_passes = 0
    original_transcript_rows = stream._transcript_rows

    def count_transcript_passes(uid):
        nonlocal transcript_passes
        transcript_passes += 1
        return original_transcript_rows(uid)

    monkeypatch.setattr(stream, "_transcript_rows", count_transcript_passes)

    events = stream.subscribe({"after": None, "conversation_uid": None})["events"]

    assert events[0]["type"] == "snapshot"
    assert turn_id_calls <= 3 * len(
        history
    ), f"turn-history lookup count was not linear: {turn_id_calls} for {len(history)} messages"
    assert (
        transcript_passes == 1
    ), f"snapshot projected transcript {transcript_passes} times"


def test_large_streaming_row_appends_are_coalesced_and_final_upsert_is_current(
    monkeypatch,
):
    clock = [100.0]
    monkeypatch.setattr(chat_stream_module.time, "monotonic", lambda: clock[0])
    state = _State([])
    stream = ChatStreamService(state)
    uid = state.get_conversation_uid()
    original_text = "x" * (CHAT_STREAM_MAX_ROW_BYTES + 1)
    stream._upsert(
        uid,
        {
            "id": "large-streaming-reply",
            "seq": 0,
            "kind": "reply",
            "role": "assistant",
            "turn_id": "turn-large",
            "status": "streaming",
            "parts": [{"type": "text", "text": original_text}],
            "created_at": "2026-11-01T00:00:00Z",
        },
    )
    first_upsert = stream._log(uid)._events[-1]
    first_preview = first_upsert["data"]["parts"][0]["chat_stream_content_preview"]

    original_wire_bytes = chat_stream_module.chat_stream_wire_bytes
    full_row_scans = []

    def count_full_row_scans(value):
        if (
            isinstance(value, dict)
            and value.get("id") == "large-streaming-reply"
            and isinstance(value.get("parts"), list)
        ):
            full_row_scans.append(value)
        return original_wire_bytes(value)

    monkeypatch.setattr(chat_stream_module, "chat_stream_wire_bytes", count_full_row_scans)
    clock[0] += 0.05
    append = stream.record_event(
        "row.append",
        {"id": "large-streaming-reply", "part": 0, "text": "tail-one"},
    )

    assert append["type"] == "row.append"
    assert full_row_scans == []

    retained_snapshot = stream.get_content(
        {
            "row_id": "large-streaming-reply",
            "part_index": 0,
            "field": "text",
            "output_revision": first_preview["output_revision"],
            "offset": 0,
            "limit": 32,
        }
    )
    assert retained_snapshot["content"] == original_text[:32]
    assert retained_snapshot["total_chars"] == len(original_text)

    clock[0] += 1.0
    refresh = stream.record_event(
        "row.append",
        {"id": "large-streaming-reply", "part": 0, "text": "tail-two"},
    )
    assert refresh["type"] == "row.append"
    assert full_row_scans == []

    final_text = original_text + "tail-onetail-two-final"
    stream._reply_rows[(uid, "turn-large")] = "large-streaming-reply"
    stream._reply_text[(uid, "turn-large")] = final_text
    stream._upsert_reply(uid, "turn-large", final_text, status="completed")
    final_event = stream._log(uid)._events[-1]
    final_preview = final_event["data"]["parts"][0]["chat_stream_content_preview"]

    assert final_event["type"] == "row.upsert"
    assert final_event["data"]["status"] == "completed"
    assert final_preview["output_chars"] == len(final_text)
    final_chunk = stream.get_content(
        {
            "row_id": "large-streaming-reply",
            "part_index": 0,
            "field": "text",
            "output_revision": final_preview["output_revision"],
            "offset": 0,
            "limit": 32,
        }
    )
    assert final_chunk["content"] == final_text[:32]


def test_snapshot_preserves_live_inputs_when_their_rows_are_trimmed():
    state = _State([])
    stream = ChatStreamService(state)
    uid = state.get_conversation_uid()
    stream._inputs[uid] = {
        "queued-without-row": {"id": "queued-without-row", "state": "queued"},
        "running-without-row": {
            "id": "running-without-row",
            "state": "started",
            "turn_id": "turn-running",
        },
    }
    stream._turns[uid] = {
        "turn-running": {
            "turn_id": "turn-running",
            "state": "running",
            "input_ids": ["running-without-row"],
        }
    }

    rows, turns, inputs, _state = stream._snapshot_components(
        uid, transcript_rows=[], row_byte_budget=0
    )

    assert rows == []
    assert {item["id"] for item in inputs} == {
        "queued-without-row",
        "running-without-row",
    }
    assert turns == [
        {
            "turn_id": "turn-running",
            "state": "running",
            "input_ids": ["running-without-row"],
        }
    ]


def test_snapshot_drops_unreferenced_turn_metadata():
    state = _State([])
    stream = ChatStreamService(state)
    uid = state.get_conversation_uid()
    stream._turns[uid] = {
        f"old-{index}": {
            "turn_id": f"old-{index}",
            "state": "ended",
            "input_ids": [],
            "origin": {
                "kind": "goal",
                "goal_id": f"goal-{index}",
                "attempt_id": f"attempt-{index}",
                "unused_payload": "x" * 25_000,
            },
        }
        for index in range(180)
    }
    stream._turns[uid]["active-turn"] = {
        "turn_id": "active-turn",
        "state": "running",
        "input_ids": ["active-input"],
    }
    stream._inputs[uid] = {
        "active-input": {
            "id": "active-input",
            "state": "started",
            "turn_id": "active-turn",
        }
    }

    snapshot = stream._snapshot(uid)

    assert snapshot["rows"] == []
    assert snapshot["turns"] == [stream._turns[uid]["active-turn"]]
    assert snapshot["inputs"] == [stream._inputs[uid]["active-input"]]


def test_snapshot_keeps_a_running_goal_turn_without_rows_or_inputs():
    state = _State([])
    stream = ChatStreamService(state)
    uid = state.get_conversation_uid()
    goal_turn = {
        "turn_id": "rowless-goal-turn",
        "state": "running",
        "input_ids": [],
        "origin": {
            "kind": "goal",
            "goal_id": "goal-1",
            "attempt_id": "attempt-1",
        },
    }
    stream._turns[uid] = {goal_turn["turn_id"]: goal_turn}

    rows, turns, inputs, _state = stream._snapshot_components(
        uid, transcript_rows=[], row_byte_budget=0
    )

    assert rows == []
    assert inputs == []
    assert turns == [goal_turn]


def test_snapshot_keeps_a_rowless_running_non_goal_turn():
    state = _State([])
    stream = ChatStreamService(state)
    uid = state.get_conversation_uid()
    running_turn = {
        "turn_id": "rowless-native-turn",
        "state": "running",
        "input_ids": [],
    }
    stream._turns[uid] = {running_turn["turn_id"]: running_turn}

    rows, turns, inputs, _state = stream._snapshot_components(
        uid, transcript_rows=[], row_byte_budget=0
    )

    assert rows == []
    assert inputs == []
    assert turns == [running_turn]


def test_snapshot_pages_the_window_when_its_row_budget_trims_every_row(monkeypatch):
    state = _State([])
    stream = ChatStreamService(state)
    uid = state.get_conversation_uid()
    monkeypatch.setattr(chat_stream_module, "CHAT_STREAM_MAX_RESPONSE_BYTES", 512)
    row = {
        "id": "row-windowed",
        "seq": 1,
        "kind": "reply",
        "role": "assistant",
        "turn_id": None,
        "status": "completed",
        "parts": [{"type": "text", "text": "large row " * 1000}],
        "created_at": "2026-11-26T10:00:00Z",
    }
    stream._upsert(uid, row)

    empty_snapshot = stream._snapshot(uid)

    assert empty_snapshot["rows"] == []
    assert empty_snapshot["older"] is not None
    pager = stream._row_pager(snapshot_rows=[], transcript_rows=[])
    page = pager.read(before=empty_snapshot["older"], limit=10)
    assert [item["id"] for item in page["rows"]] == [row["id"]]
