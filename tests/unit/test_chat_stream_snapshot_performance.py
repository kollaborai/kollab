from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService


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
            ("user", f"question-{index}", {"request_id": f"web-{index}", "turn_id": turn_id}),
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


def test_snapshot_turn_history_scans_are_linear_and_transcript_is_projected_once(monkeypatch):
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
    assert turn_id_calls <= 3 * len(history), (
        f"expected linear turn-history lookups, got {turn_id_calls} scans for {len(history)} messages"
    )
    assert transcript_passes == 1, f"snapshot projected transcript {transcript_passes} times"
