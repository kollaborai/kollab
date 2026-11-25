"""Native and ACP display events become the shared chat stream (#1451)."""

from __future__ import annotations

import pytest

from kollabor_events.chat_stream import sequence_problems
from kollabor_tui.display_tap import DisplayTap
from kollabor.state.chat_stream import ChatStreamService


class _QueueRuntime:
    async def add_input(self, params):
        return {"accepted": True, "id": params["id"], "state": "queued"}


class _Bus:
    def __init__(self):
        self.tap = DisplayTap()
        self.tap.conversation_source = lambda: "conversation-a"
        self.services = {
            "display_tap": self.tap,
            "web_message_queue_runtime": _QueueRuntime(),
        }

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service


class _State:
    def __init__(self):
        self._event_bus = _Bus()
        self._llm_service = type("LLM", (), {"conversation_manager": None})()
        self.snapshot_rows = []

    def get_conversation_uid(self):
        return "conversation-a"

    def chat_stream_snapshot(self):
        return {"rows": list(self.snapshot_rows), "turns": [], "inputs": []}


def _events(stream, first):
    cursor = first["data"]["next_seq"] - 1
    replay = stream.subscribe({"after": cursor, "conversation_uid": "conversation-a"})[
        "events"
    ]
    return [first, *replay]


async def _add(stream, input_id, text):
    return await stream.add_input({"id": input_id, "text": text, "attachments": []})


def _publish(state, event_type, **fields):
    state._event_bus.tap.publish({"type": event_type, **fields})


@pytest.mark.asyncio
async def test_native_queued_input_streams_one_reply_and_ends_after_its_turn():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    await _add(stream, "web-native-1", "hello")

    _publish(
        state,
        "turn_start",
        request_id="web-native-1",
        turn_id="native-1",
        input_source="web",
    )
    _publish(
        state,
        "token",
        request_id="web-native-1",
        turn_id="native-1",
        text="hello back",
    )
    _publish(
        state,
        "turn_complete",
        request_id="web-native-1",
        turn_id="native-1",
        stop_reason="end_turn",
    )

    events = _events(stream, initial)
    assert sequence_problems(events) == []
    input_rows = [
        event["data"]
        for event in events
        if event["type"] == "row.upsert" and event["data"]["kind"] == "input"
    ]
    assert {row["id"] for row in input_rows} == {"web-native-1"}
    assert any(event["type"] == "row.append" for event in events)


@pytest.mark.asyncio
async def test_acp_steered_input_joins_the_current_turn_and_both_inputs_end_once():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    await _add(stream, "web-acp-1", "start")
    await _add(stream, "web-acp-2", "steer")

    _publish(
        state,
        "turn_start",
        request_id="web-acp-1",
        turn_id=4,
        input_source="acp",
    )
    _publish(
        state,
        "hub_message",
        message_id="web-acp-2",
        input_source="steering",
    )
    _publish(
        state,
        "token",
        request_id="web-acp-1",
        turn_id=4,
        text="combined reply",
    )
    _publish(
        state,
        "turn_complete",
        request_id="web-acp-1",
        turn_id=4,
        stop_reason="end_turn",
    )

    events = _events(stream, initial)
    assert sequence_problems(events) == []
    joined = [
        event["data"]
        for event in events
        if event["type"] == "input.state"
        and event["data"]["id"] == "web-acp-2"
        and event["data"]["state"] == "joined"
    ]
    assert len(joined) == 1
    assert joined[0]["turn_id"] == "4"


@pytest.mark.asyncio
async def test_reload_mid_turn_replays_terminal_events_from_the_snapshot_cursor():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    await _add(stream, "web-reload-1", "hello")
    _publish(
        state,
        "turn_start",
        request_id="web-reload-1",
        turn_id="native-2",
    )
    _publish(
        state,
        "token",
        request_id="web-reload-1",
        turn_id="native-2",
        text="partial",
    )

    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    cursor = snapshot["data"]["next_seq"] - 1
    _publish(
        state,
        "turn_complete",
        request_id="web-reload-1",
        turn_id="native-2",
        stop_reason="end_turn",
    )
    missed = stream.subscribe({"after": cursor, "conversation_uid": "conversation-a"})[
        "events"
    ]

    assert sequence_problems([snapshot, *missed]) == []


@pytest.mark.asyncio
async def test_compaction_replaces_rows_with_a_marker_and_kept_transcript_ids():
    state = _State()
    stream = ChatStreamService(state)
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    await _add(stream, "web-compact-1", "kept")
    _publish(
        state,
        "turn_start",
        request_id="web-compact-1",
        turn_id="compact-1",
    )
    _publish(
        state,
        "turn_complete",
        request_id="web-compact-1",
        turn_id="compact-1",
        stop_reason="end_turn",
    )
    state.snapshot_rows = [
        {
            "id": "web-compact-1",
            "seq": 1,
            "kind": "input",
            "role": "user",
            "turn_id": "compact-1",
            "status": "done",
            "parts": [{"type": "text", "text": "kept"}],
            "created_at": "2026-11-25T12:00:00Z",
        }
    ]

    stream.replace_transcript()

    events = _events(stream, initial)
    assert sequence_problems(events) == []
    replacement = next(event for event in events if event["type"] == "replace")
    assert replacement["data"]["rows"][0]["kind"] == "marker"
    assert replacement["data"]["rows"][1]["id"] == "web-compact-1"


@pytest.mark.asyncio
async def test_input_image_parts_keep_the_clients_row_id_when_the_turn_starts():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    input_id = "web-image-1"
    await stream.add_input(
        {
            "id": input_id,
            "text": "look",
            "attachments": [
                {
                    "name": "image.png",
                    "content_type": "image/png",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        }
    )

    _publish(
        state,
        "user_message",
        request_id=input_id,
        turn_id="image-turn",
        text="look",
    )

    events = _events(stream, initial)
    assert sequence_problems(events, complete=False) == []
    input_rows = [
        event["data"]
        for event in events
        if event["type"] == "row.upsert"
        and event["data"]["kind"] == "input"
        and event["data"]["id"] == input_id
    ]
    assert input_rows[-1]["parts"] == [
        {"type": "text", "text": "look"},
        {"type": "image", "url": "data:image/png;base64,AAAA"},
    ]


@pytest.mark.asyncio
async def test_turn_error_is_a_marker_row_and_fails_its_input():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    await _add(stream, "web-error-1", "hello")
    _publish(
        state,
        "turn_start",
        request_id="web-error-1",
        turn_id="error-turn",
    )
    _publish(
        state,
        "error",
        request_id="web-error-1",
        turn_id="error-turn",
        message="provider failed",
    )
    _publish(
        state,
        "turn_complete",
        request_id="web-error-1",
        turn_id="error-turn",
        stop_reason="error",
    )

    events = _events(stream, initial)

    assert sequence_problems(events) == []
    markers = [
        event["data"]
        for event in events
        if event["type"] == "row.upsert" and event["data"]["kind"] == "marker"
    ]
    assert len(markers) == 1
    assert markers[0]["role"] == "system"
    assert markers[0]["status"] == "failed"
    assert markers[0]["parts"] == [{"type": "text", "text": "provider failed"}]
