"""Native and ACP display events become the shared chat stream (#1451)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm.acp_bridge import ACPAdmission, ACPResult, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.permissions.attach_bridge import AttachPermissionBridge
from kollabor.state.chat_stream import ChatStreamService
from kollabor_agent.queue_processor import QueueProcessor, TaggedInput, batch_request_ids
from kollabor_events.chat_stream import REPLAY_WINDOW, sequence_problems
from kollabor_events.data_models import ConversationMessage
from kollabor_events.permissions_models import ConfirmationResponse
from kollabor_tui.display_tap import DisplayTap, publish_semantic
from plugins.hub.models import HubMessage


class _QueueRuntime:
    async def input_scope(self, conversation_uid):
        return ("scope", conversation_uid, "principal")

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
        self._llm_service = type(
            "LLM", (), {"conversation_manager": None, "conversation_history": []}
        )()
        self._event_bus.services["llm_service"] = self._llm_service
        self.snapshot_rows = []

    def get_conversation_uid(self):
        return "conversation-a"

    def chat_stream_snapshot(self):
        return {"rows": list(self.snapshot_rows), "turns": [], "inputs": []}


def _events(stream, first):
    cursor = first["data"]["next_seq"] - 1
    replay = stream.subscribe(
        {
            "after": cursor,
            "conversation_uid": "conversation-a",
            "generation": first["generation"],
        }
    )["events"]
    return [first, *replay]


async def _add(stream, input_id, text):
    return await stream.add_input({"id": input_id, "text": text, "attachments": []})


def _publish(state, event_type, **fields):
    state._event_bus.tap.publish({"type": event_type, **fields})


def _acp_orchestrator(state, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data"
    )
    return ACPOrchestrator(state._event_bus)


def _snapshot_rows(stream):
    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    return snapshot, snapshot["data"]["rows"]


def test_row_is_visible_in_a_snapshot_taken_during_live_publish():
    state = _State()
    stream = ChatStreamService(state)
    during_publish = {}

    def snapshot_during_publish(event):
        during_publish["event"] = event
        during_publish["snapshot"] = stream.subscribe(
            {"after": None, "conversation_uid": None}
        )["events"][0]

    stream._publish_live = snapshot_during_publish
    stream._upsert(
        "conversation-a",
        {
            "id": "input-racing-snapshot",
            "seq": 0,
            "kind": "input",
            "role": "user",
            "turn_id": None,
            "status": "queued",
            "parts": [{"type": "text", "text": "hello"}],
            "created_at": "2026-11-25T12:00:00Z",
        },
    )

    snapshot = during_publish["snapshot"]
    assert snapshot["data"]["next_seq"] == during_publish["event"]["seq"] + 1
    assert [row["id"] for row in snapshot["data"]["rows"]] == [
        "input-racing-snapshot"
    ]


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
async def test_native_queue_drain_starts_the_admitted_turn_before_reply_events():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    input_id = "web-native-worker-1"
    await _add(stream, input_id, "hello")

    processor = QueueProcessor.__new__(QueueProcessor)
    tap = state._event_bus.tap
    tap_events = tap.subscribe("native-worker")
    processor.renderer = tap
    processor.processing_queue = asyncio.Queue()
    processor.processing_queue.put_nowait(TaggedInput("hello", (input_id,)))
    processor.is_processing = False
    processor.current_processing_tokens = 0
    processor.processing_start_time = 0
    processor.cancel_processing = False
    processor.turn_completed = False
    processor._last_tool_error_sig = None
    processor._open_voice_turn = None
    processor.active_origin_context = None
    processor.active_request_ids = ()
    processor._after_turn = []
    processor._turn_token = None
    processor._queue_handoffs = {}
    processor._drained_batch_messages = ()
    processor._drained_handoff_items = ()
    processor._drained_handoff_started = False
    processor._active_turn_handoff_attempts = ()

    async def process_batch(messages):
        request_ids = batch_request_ids(messages)
        assert request_ids == (input_id,)
        processor._stamp_turn(None, request_ids)
        publish_semantic(tap, "token", text="hello back")
        publish_semantic(tap, "turn_complete", stop_reason="end_turn")
        processor._stamp_turn(None)
        processor.turn_completed = True

    await processor._drain_queue(None, process_batch, AsyncMock())

    events = _events(stream, initial)
    assert sequence_problems(events) == []
    assert any(
        item["type"] == "input.state"
        and item["data"]["id"] == input_id
        and item["data"]["state"] == "started"
        for item in events
    )
    assert any(
        item["type"] == "turn.state" and item["data"]["state"] == "ended"
        for item in events
    )
    raw_events = []
    while not tap_events.empty():
        raw_events.append(tap_events.get_nowait())
    start = next(event for event in raw_events if event.get("type") == "turn_start")
    token = next(event for event in raw_events if event.get("type") == "token")
    assert start["turn_id"]
    assert start["request_ids"] == [input_id]
    assert raw_events.index(start) < raw_events.index(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_hud", [False, True])
@pytest.mark.parametrize("with_batch", [False, True])
async def test_native_batch_history_keeps_live_input_ids_and_original_text(
    with_hud, with_batch
):
    """Drive native queue admission, history writing and chat projection together."""
    from types import MethodType

    from kollabor.llm.agent_hud import AgentHudEntry, merge_agent_hud_with_user_message
    from kollabor.llm.llm_coordinator import LLMService
    from kollabor_ai.message_content import combine_message_contents

    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    inputs = [("web-native-a", "first message")]
    if with_batch:
        inputs.append(("web-native-b", "second message"))
    for input_id, text in inputs:
        await _add(stream, input_id, text)

    llm = state._llm_service
    llm._is_hidden_voice_message = lambda _message: False
    llm.add = MethodType(LLMService._add_conversation_message, llm)
    records = tuple(
        {
            "source": "webui",
            "timestamp": f"2026-11-25T12:00:0{index}Z",
            "text": text,
            "request_id": input_id,
        }
        for index, (input_id, text) in enumerate(inputs)
    )
    messages = [
        TaggedInput(text, (input_id,), operator_inputs=(records[index],))
        for index, (input_id, text) in enumerate(inputs)
    ]
    if with_hud:
        prompt = merge_agent_hud_with_user_message(
            [AgentHudEntry(section="hub", label="peer->me", content="peer online")],
            combine_message_contents([message.content for message in messages]),
        )
        messages = [
            TaggedInput(
                prompt,
                tuple(item[0] for item in inputs),
                operator_inputs=records,
            )
        ]

    processor = QueueProcessor.__new__(QueueProcessor)
    tap = state._event_bus.tap
    processor.renderer = tap
    processor.processing_queue = asyncio.Queue()
    for message in messages:
        processor.processing_queue.put_nowait(message)
    processor.is_processing = False
    processor.current_processing_tokens = 0
    processor.processing_start_time = 0
    processor.cancel_processing = False
    processor.turn_completed = False
    processor._last_tool_error_sig = None
    processor._open_voice_turn = None
    processor.active_origin_context = None
    processor.active_request_ids = ()
    processor._after_turn = []
    processor._turn_token = None
    processor._queue_handoffs = {}
    processor._drained_batch_messages = ()
    processor._drained_handoff_items = ()
    processor._drained_handoff_started = False
    processor._active_turn_handoff_attempts = ()
    processor._add_message_fn = llm.add
    processor.tool_executor = SimpleNamespace(yes_approvals=None)

    async def execute_turn(
        user_message_provided,
        current_parent_uuid,
        execution_context=None,
        request_ids=(),
    ):
        assert user_message_provided is True
        assert request_ids == tuple(item[0] for item in inputs)
        publish_semantic(tap, "token", text="reply")
        llm.add(
            ConversationMessage(role="assistant", content="reply", metadata={}),
            parent_uuid=current_parent_uuid,
        )
        publish_semantic(tap, "turn_complete", stop_reason="end_turn")
        processor.turn_completed = True
        return current_parent_uuid

    processor._execute_llm_turn = execute_turn

    async def process_batch(batch):
        await processor.process_message_batch(
            batch,
            current_parent_uuid="parent-1",
            request_ids=batch_request_ids(batch),
        )

    await processor._drain_queue(None, process_batch, AsyncMock())

    history_user = next(
        message for message in llm.conversation_history if message.role == "user"
    )
    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    input_rows = [row for row in snapshot["data"]["rows"] if row["kind"] == "input"]
    assert [row["id"] for row in input_rows] == [item[0] for item in inputs]
    assert [
        next(part["text"] for part in row["parts"] if part["type"] == "text")
        for row in input_rows
    ] == [item[1] for item in inputs]
    assert all(
        "<agent_hud>" not in part.get("text", "")
        for row in input_rows
        for part in row["parts"]
    )
    assert all(row["status"] == "completed" for row in input_rows)
    assert [item["id"] for item in snapshot["data"]["inputs"]] == [
        item[0] for item in inputs
    ]
    assert history_user.metadata["request_id"] == inputs[0][0]
    assert history_user.metadata["request_ids"] == [item[0] for item in inputs]
    assert history_user.metadata["turn_id"]


def test_resumed_native_transcript_ends_a_completed_input_without_live_state():
    state = _State()
    state._llm_service.conversation_history = [
        ConversationMessage(
            role="user",
            content="saved question",
            metadata={
                "request_id": "web-resumed-native-1",
                "request_ids": ["web-resumed-native-1"],
                "turn_id": "native-resumed-1",
            },
        )
    ]

    snapshot = ChatStreamService(state).subscribe(
        {"after": None, "conversation_uid": None}
    )["events"][0]["data"]

    assert snapshot["inputs"] == [
        {
            "id": "web-resumed-native-1",
            "state": "ended",
            "turn_id": "native-resumed-1",
            "outcome": "completed",
        }
    ]
    input_row = next(row for row in snapshot["rows"] if row["kind"] == "input")
    assert input_row["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop_reason, expected_outcome",
    [
        ("stop", "cancelled"),
        ("end_turn", "completed"),
        ("max_tokens", "failed"),
        ("max_turn_requests", "failed"),
        ("refusal", "failed"),
        ("cancelled", "cancelled"),
    ],
)
async def test_terminal_stop_reason_projects_the_persisted_outcome(
    stop_reason, expected_outcome
):
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    await _add(stream, "web-terminal-1", "question")

    _publish(
        state,
        "turn_start",
        request_id="web-terminal-1",
        request_ids=["web-terminal-1"],
        turn_id="native-terminal-1",
        input_source="native",
    )
    _publish(
        state,
        "turn_complete",
        request_id="web-terminal-1",
        request_ids=["web-terminal-1"],
        turn_id="native-terminal-1",
        stop_reason=stop_reason,
    )

    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0][
        "data"
    ]
    assert snapshot["inputs"] == [
        {
            "id": "web-terminal-1",
            "state": "ended",
            "turn_id": "native-terminal-1",
            "outcome": expected_outcome,
        }
    ]


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
async def test_acp_steering_admission_identity_recovers_a_missed_turn_start():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    await _add(stream, "client-root-input", "start")
    await _add(stream, "client-steer-input", "steer")

    # A reconnect can miss turn_start. Admission still identifies the exact
    # active turn and input that this client-chosen steering ID joined.
    _publish(
        state,
        "hub_message",
        message_id="client-steer-input",
        request_id="client-steer-input",
        input_source="steering",
        joined_request_id="client-root-input",
        joined_turn_id=41,
    )
    _publish(
        state,
        "token",
        request_id="client-root-input",
        turn_id=41,
        text="combined reply",
    )
    _publish(
        state,
        "turn_complete",
        request_id="client-root-input",
        turn_id=41,
        stop_reason="end_turn",
    )

    events = _events(stream, initial)
    assert sequence_problems(events) == []
    input_states = [event["data"] for event in events if event["type"] == "input.state"]
    assert [
        (item["id"], item["state"], item["turn_id"], item.get("outcome"))
        for item in input_states
    ] == [
        ("client-root-input", "queued", None, None),
        ("client-steer-input", "queued", None, None),
        ("client-root-input", "started", "41", None),
        ("client-steer-input", "joined", "41", None),
        ("client-root-input", "ended", "41", "completed"),
        ("client-steer-input", "ended", "41", "completed"),
    ]
    input_rows = {
        event["data"]["id"]
        for event in events
        if event["type"] == "row.upsert" and event["data"]["kind"] == "input"
    }
    assert input_rows == {"client-root-input", "client-steer-input"}


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
    missed = stream.subscribe(
        {
            "after": cursor,
            "conversation_uid": "conversation-a",
            "generation": snapshot["generation"],
        }
    )["events"]

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


def test_snapshot_and_compaction_read_active_transcript_rows():
    state = _State()
    state._llm_service.conversation_history = [
        ConversationMessage(
            role="user",
            content="look",
            metadata={"request_id": "web-transcript-1"},
        ),
        ConversationMessage(
            role="tool",
            content="file saved",
            metadata={"tool_id": "tool-1"},
        ),
        ConversationMessage(
            role="user",
            content="agent note",
            metadata={"hub_message": True, "hub_message_id": "hub-message-1"},
        ),
    ]
    stream = ChatStreamService(state)

    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    rows = snapshot["data"]["rows"]
    input_row = next(row for row in rows if row["id"] == "web-transcript-1")
    tool_row = next(row for row in rows if row["kind"] == "tool")
    note_row = next(row for row in rows if row["id"] == "hub-message-1")

    assert input_row["kind"] == "input"
    assert tool_row["role"] == "assistant"
    assert note_row["kind"] == "note"
    assert note_row["role"] == "system"
    assert note_row["parts"] == [{"type": "text", "text": "agent note"}]

    replacement = stream.replace_transcript()

    assert replacement["type"] == "replace"
    assert any(row["id"] == "hub-message-1" for row in replacement["data"]["rows"])


def test_snapshot_orders_live_hub_note_between_transcript_rows(monkeypatch):
    state = _State()
    state._llm_service.conversation_history = [
        ConversationMessage(
            role="user",
            content="before note",
            timestamp=datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc),
            metadata={"request_id": "transcript-before-note"},
        ),
        ConversationMessage(
            role="assistant",
            content="after note",
            timestamp=datetime(2026, 11, 25, 12, 0, 2, tzinfo=timezone.utc),
            metadata={
                "turn_id": "turn-after-note",
                "reply_id": "transcript-after-note",
            },
        ),
    ]
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    monkeypatch.setattr(stream, "_timestamp", lambda: "2026-11-25T12:00:01Z")

    _publish(
        state,
        "hub_message",
        message_id="live-hub-note",
        hub_admission="note",
        hub_note={
            "type": "hub-note",
            "from": "peer",
            "to": "koordinator",
            "text": "between transcript messages",
            "observed": True,
        },
    )

    _snapshot, rows = _snapshot_rows(stream)

    assert [row["id"] for row in rows] == [
        "transcript-before-note",
        "live-hub-note",
        "transcript-after-note",
    ]
    assert rows[2]["parts"] == [{"type": "text", "text": "after note"}]


@pytest.mark.asyncio
async def test_compaction_keeps_live_hub_note_before_an_active_turn():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    note_id = "live-hub-note-before-turn"
    _publish(
        state,
        "hub_message",
        message_id=note_id,
        hub_admission="note",
        hub_note={
            "type": "hub-note",
            "from": "peer",
            "to": "koordinator",
            "text": "keep this note",
            "observed": True,
        },
    )
    await _add(stream, "active-turn-input", "continue")
    _publish(
        state,
        "turn_start",
        request_id="active-turn-input",
        turn_id="active-turn",
    )

    replacement = stream.replace_transcript()
    replacement_ids = [row["id"] for row in replacement["data"]["rows"]]
    snapshot = stream.subscribe(
        {"after": None, "conversation_uid": "conversation-a"}
    )["events"][0]
    snapshot_ids = [row["id"] for row in snapshot["data"]["rows"]]

    assert note_id in replacement_ids
    assert replacement_ids.index(note_id) < replacement_ids.index("active-turn-input")
    assert note_id in snapshot_ids


def test_snapshot_translates_canonical_transcript_images_to_chat_parts():
    state = _State()
    state._llm_service.conversation_history = [
        ConversationMessage(
            role="user",
            content=[
                {"type": "text", "text": "look"},
                {
                    "type": "image",
                    "source": {"kind": "url", "url": "https://example.test/image.png"},
                },
            ],
            metadata={"request_id": "web-image-snapshot"},
        )
    ]

    event = ChatStreamService(state).subscribe(
        {"after": None, "conversation_uid": None}
    )["events"][0]

    row = event["data"]["rows"][0]
    assert row["id"] == "web-image-snapshot"
    assert row["parts"] == [
        {"type": "text", "text": "look"},
        {"type": "image", "url": "https://example.test/image.png"},
    ]


def test_snapshot_omits_hidden_voice_transcript_instruction():
    state = _State()
    state._llm_service.conversation_history = [
        ConversationMessage(
            role="user",
            content="hidden voice instruction",
            metadata={"hidden": True},
        ),
        ConversationMessage(
            role="user",
            content="visible user message",
            metadata={"request_id": "visible-user-message"},
        ),
    ]

    snapshot, rows = _snapshot_rows(ChatStreamService(state))

    assert [row["id"] for row in rows] == ["visible-user-message"]
    assert "hidden voice instruction" not in str(snapshot["data"]["rows"])


def test_rows_pager_omits_hidden_voice_instruction_from_older_history():
    state = _State()
    state._llm_service.conversation_history = [
        ConversationMessage(
            role="user",
            content="hidden voice instruction",
            metadata={"hidden": True},
        ),
        *[
            ConversationMessage(
                role="user",
                content=f"visible message {index}",
                metadata={"request_id": f"visible-message-{index}"},
            )
            for index in range(REPLAY_WINDOW + 1)
        ],
    ]
    stream = ChatStreamService(state)

    page = stream.row_pager().read(before=None, limit=10)

    assert [row["id"] for row in page["rows"]] == ["visible-message-0"]


def test_retained_transcript_row_keeps_its_id_when_compaction_changes_its_index():
    state = _State()
    retained = ConversationMessage(role="assistant", content="kept reply")
    state._llm_service.conversation_history = [
        ConversationMessage(role="user", content="old input"),
        retained,
    ]
    stream = ChatStreamService(state)

    before = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    before_id = next(
        row["id"]
        for row in before["data"]["rows"]
        if row["parts"] == [{"type": "text", "text": "kept reply"}]
    )
    state._llm_service.conversation_history = [retained]

    replacement = stream.replace_transcript()

    after_id = next(
        row["id"]
        for row in replacement["data"]["rows"]
        if row["parts"] == [{"type": "text", "text": "kept reply"}]
    )
    assert after_id == before_id


def test_snapshot_exposes_a_cursor_for_transcript_rows_older_than_its_window():
    state = _State()
    state._llm_service.conversation_history = [
        ConversationMessage(
            role="user",
            content=f"transcript row {index}",
            metadata={"request_id": f"input-{index}"},
        )
        for index in range(REPLAY_WINDOW + 3)
    ]
    stream = ChatStreamService(state)

    event = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]

    assert len(event["data"]["rows"]) == REPLAY_WINDOW
    assert isinstance(event["data"]["older"], str)


@pytest.mark.asyncio
async def test_acp_transcript_reply_has_distinct_stable_id_and_string_turn_id(
    tmp_path, monkeypatch
):
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    orchestrator = _acp_orchestrator(state, monkeypatch, tmp_path)
    request_id = "acp-request-7"
    history_key = "session-turn-key-7"
    orchestrator._record_user(
        request_id,
        "question",
        "user",
        None,
        turn_id=7,
        history_key=history_key,
        generation=1,
    )
    orchestrator._on_runner_update(
        {
            "kind": "turn_start",
            "request_id": request_id,
            "turn_id": 7,
            "generation": 1,
            "source": "user",
            "acp_turn_key": history_key,
        }
    )
    orchestrator._on_runner_update(
        {
            "kind": "text",
            "text": "answer",
            "request_id": request_id,
            "turn_id": 7,
            "generation": 1,
            "source": "user",
        }
    )
    result = ACPResult(
        generation=1,
        session_id="acp-session",
        text="answer",
        stop_reason="end_turn",
        updates=({"kind": "text", "text": "answer"},),
        end_frame={"type": "turn_end", "outcome": "success"},
        turn_id=7,
        request_id=request_id,
        source="user",
        history_key=history_key,
    )
    future = asyncio.get_running_loop().create_future()
    future.set_result(result)
    admission = ACPAdmission(
        request_id=request_id,
        source="user",
        thread_id=None,
        generation=1,
        turn_id=7,
        text="question",
        future=future,
        history_key=history_key,
    )

    await orchestrator._complete(admission)

    snapshot, rows = _snapshot_rows(stream)
    projected = {row["id"]: row for row in rows}
    assert request_id in projected
    replies = [row for row in rows if row["kind"] == "reply"]
    assert len(replies) == 1
    assert replies[0]["id"] != request_id
    assert replies[0]["turn_id"] == "7"
    assert replies[0]["parts"] == [{"type": "text", "text": "answer"}]
    assert sequence_problems([snapshot]) == []


@pytest.mark.asyncio
async def test_acp_hub_note_projects_structured_hub_part(tmp_path, monkeypatch):
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    orchestrator = _acp_orchestrator(state, monkeypatch, tmp_path)
    observation = HubMessage(
        action="message",
        from_agent="peer-id-1",
        from_identity="peer-one",
        to="*",
        content="observed update",
        thread_id="thread-note",
        reply_to="message-parent",
    )

    assert orchestrator.observe_notification(observation) is True

    snapshot, rows = _snapshot_rows(stream)
    note = next(row for row in rows if row["id"] == observation.id)
    expected_note = state._llm_service.conversation_history[0].metadata["hub_note"]
    assert note["kind"] == "note"
    assert note["parts"] == [expected_note]
    assert note["parts"][0]["type"] == "hub-note"
    assert "<agent_hud>" not in str(note["parts"])
    assert sequence_problems([snapshot]) == []


@pytest.mark.asyncio
async def test_acp_hub_input_admission_keeps_hub_id_and_ends_its_lifecycle(
    tmp_path, monkeypatch
):
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    orchestrator = _acp_orchestrator(state, monkeypatch, tmp_path)
    message = HubMessage(
        action="message",
        from_agent="peer-id-2",
        from_identity="peer-two",
        to="koordinator",
        content="please handle this",
        thread_id="thread-input",
        reply_to="",
    )
    from kollabor.llm.agent_hud import build_hub_note

    history_key = "hub-input-turn-key"
    orchestrator._record_user(
        message.id,
        message.content,
        "hub_message",
        message.thread_id,
        turn_id=13,
        history_key=history_key,
        generation=1,
        hub_note=build_hub_note(message, observed=False),
        hub_admission="input",
    )
    orchestrator._on_runner_update(
        {
            "kind": "turn_start",
            "request_id": message.id,
            "turn_id": 13,
            "generation": 1,
            "source": "hub_message",
            "acp_turn_key": history_key,
        }
    )
    result = ACPResult(
        generation=1,
        session_id="acp-session",
        text="handled",
        stop_reason="end_turn",
        updates=(),
        end_frame={"type": "turn_end", "outcome": "success"},
        turn_id=13,
        request_id=message.id,
        source="hub_message",
        history_key=history_key,
    )
    future = asyncio.get_running_loop().create_future()
    future.set_result(result)
    admission = ACPAdmission(
        request_id=message.id,
        source="hub_message",
        thread_id=message.thread_id,
        generation=1,
        turn_id=13,
        text=message.content,
        future=future,
        history_key=history_key,
    )

    await orchestrator._complete(admission)

    events = _events(stream, initial)
    assert sequence_problems(events) == []
    row_events = [
        event["data"]
        for event in events
        if event["type"] == "row.upsert" and event["data"]["id"] == message.id
    ]
    assert row_events[-1]["kind"] == "input"
    assert row_events[-1]["parts"][0]["type"] == "hub-note"
    states = [
        event["data"]
        for event in events
        if event["type"] == "input.state" and event["data"]["id"] == message.id
    ]
    assert [(item["state"], item["turn_id"]) for item in states] == [
        ("queued", None),
        ("started", "13"),
        ("ended", "13"),
    ]


class _ACPUpdate:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, *, by_alias):
        return self.payload


@pytest.mark.asyncio
async def test_acp_tool_rows_preserve_structured_args_results_and_rebuild_from_history(
    tmp_path, monkeypatch
):
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    orchestrator = _acp_orchestrator(state, monkeypatch, tmp_path)
    request_id = "acp-tool-request"
    history_key = "acp-tool-turn-key"
    orchestrator._record_user(
        request_id,
        "edit a file",
        "user",
        None,
        turn_id=17,
        history_key=history_key,
        generation=1,
    )
    orchestrator._on_runner_update(
        {
            "kind": "turn_start",
            "request_id": request_id,
            "turn_id": 17,
            "generation": 1,
            "source": "user",
            "acp_turn_key": history_key,
        }
    )
    runner = object.__new__(ACPRunner)
    runner._replaying = False
    runner.session_id = "acp-session"
    runner._active_generation = runner.generation = 1
    runner._cancelled_turn_id = 0
    runner._active_turn_id = 17
    runner._active_context = SimpleNamespace(
        request_id=request_id, source="user", thread_id=None
    )
    runner._updates = []
    runner._tool_cards = {}
    runner.update_callback = orchestrator._on_runner_update
    await runner._session_update(
        "acp-session",
        _ACPUpdate(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "tool-card-17",
                "title": "Edit file",
                "kind": "edit",
                "status": "in_progress",
                "rawInput": {"path": "notes.txt"},
            }
        ),
    )
    await runner._session_update(
        "acp-session",
        _ACPUpdate(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tool-card-17",
                "title": "Edit file",
                "kind": "edit",
                "status": "completed",
                "rawInput": None,
                "rawOutput": {"saved": True},
            }
        ),
    )
    result = ACPResult(
        generation=1,
        session_id="acp-session",
        text="file saved",
        stop_reason="end_turn",
        updates=tuple(runner._updates),
        end_frame={"type": "turn_end", "outcome": "success"},
        turn_id=17,
        request_id=request_id,
        source="user",
        history_key=history_key,
    )
    await orchestrator._record_result(result, (1, 17))

    _, live_rows = _snapshot_rows(stream)
    live_tool = next(row for row in live_rows if row["kind"] == "tool")
    expected_part = {
        "type": "tool-call",
        "tool_call_id": "tool-card-17",
        "tool_name": "Edit file",
        "args": {"path": "notes.txt"},
        "result": {"saved": True},
        "is_error": False,
    }
    assert live_tool["parts"] == [expected_part]

    reloaded, restored_rows = _snapshot_rows(ChatStreamService(state))
    restored_tool = next(row for row in restored_rows if row["kind"] == "tool")
    assert restored_tool["id"] == live_tool["id"]
    assert restored_tool["parts"] == [expected_part]
    assert sequence_problems([reloaded]) == []
    assert reloaded["data"]["inputs"] == [
        {"id": request_id, "state": "ended", "turn_id": "17", "outcome": "completed"}
    ]
    assert reloaded["data"]["turns"] == [
        {"turn_id": "17", "state": "ended", "input_ids": [request_id]}
    ]


@pytest.mark.asyncio
async def test_native_tool_result_keeps_args_from_tool_executor_start():
    from unittest.mock import AsyncMock

    from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor

    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    executor = ToolExecutor(None, state._event_bus)
    executor._execute_tool_inner = AsyncMock(
        return_value=ToolExecutionResult(
            "native-search-1",
            "mcp_tool",
            True,
            output="found it",
        )
    )

    await executor.execute_tool(
        {
            "id": "native-search-1",
            "type": "mcp_tool",
            "name": "search",
            "input": {"query": "lunar eclipse"},
        }
    )

    _, rows = _snapshot_rows(stream)
    tool = next(row for row in rows if row["kind"] == "tool")
    assert tool["parts"] == [
        {
            "type": "tool-call",
            "tool_call_id": "native-search-1",
            "tool_name": "search",
            "args": {"query": "lunar eclipse"},
            "result": "found it",
            "is_error": False,
        }
    ]


def test_sparse_tool_result_keeps_name_and_args_from_start():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()

    _publish(
        state,
        "tool_start",
        turn_id="native-sparse-tool-turn",
        tool_id="native-sparse-tool-1",
        tool_name="Search the web",
        input={"query": "eclipse"},
    )
    _publish(
        state,
        "tool_result",
        turn_id="native-sparse-tool-turn",
        tool_id="native-sparse-tool-1",
        success=True,
        output={"count": 1},
    )

    _, rows = _snapshot_rows(stream)
    tool = next(row for row in rows if row["kind"] == "tool")
    assert tool["parts"][0]["tool_name"] == "Search the web"
    assert tool["parts"][0]["args"] == {"query": "eclipse"}
    assert tool["parts"][0]["result"] == {"count": 1}


@pytest.mark.asyncio
async def test_native_hub_hud_context_is_not_a_second_input_and_hub_input_owns_turn():
    import asyncio
    from types import MethodType

    from kollabor.llm.agent_hud import AgentHudEntry
    from kollabor.llm.llm_coordinator import LLMService
    from kollabor.llm.message_handler import MessageHandler

    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    tap = state._event_bus.tap
    input_id = "native-wake-hud-1"
    hub_note = {
        "type": "hub-note",
        "from": "peer",
        "to": "koordinator",
        "text": "Please check the build.",
        "observed": False,
        "is_direct": True,
    }
    _publish(
        state,
        "hub_message",
        message_id=input_id,
        request_id=input_id,
        input_source="hub",
        hub_note=hub_note,
        hub_admission="input",
    )

    qp = SimpleNamespace(
        cancel_processing=False,
        cancel_origin=None,
        is_processing=False,
        turn_completed=False,
        _stop_hook_active=False,
        _stop_hook_continuations=0,
        _last_tool_error_sig=None,
        last_turn_error=None,
        processing_queue=asyncio.Queue(),
        active_request_ids=(),
    )
    background_tasks = []
    coordinator = SimpleNamespace(
        event_bus=state._event_bus,
        renderer=tap,
        conversation_manager=None,
        conversation_history=[],
        current_parent_uuid="parent-1",
        _queue_processor=qp,
        _continue_serial=None,
        _batch_tail_uuid=None,
        is_processing=False,
        message_display_service=SimpleNamespace(
            display_error_message=lambda *_args, **_kwargs: None
        ),
        _is_hidden_voice_message=lambda _message: False,
        _profile_selects_acp=lambda: (False, False),
        _active_acp=lambda: None,
        claim_turn_serial=lambda: 1,
        publish_cursor=lambda _uuid, _serial: True,
        pop_pending_agent_hud=lambda: [
            AgentHudEntry(
                section="hub",
                label="peer->koordinator",
                content="Please check the build.",
                message_id=input_id,
                hub_note=hub_note,
                hub_admission="input",
            )
        ],
    )
    coordinator._add_conversation_message = MethodType(
        LLMService._add_conversation_message, coordinator
    )

    async def continue_conversation():
        tap.publish({"type": "token", "text": "I will check."})
        tap.publish({"type": "turn_complete", "stop_reason": "end_turn"})
        qp.turn_completed = True

    coordinator._continue_conversation = continue_conversation
    coordinator._process_queue = AsyncMock()
    coordinator.create_background_task = lambda fn, name=None: background_tasks.append(
        asyncio.create_task(fn())
    )

    handler = MessageHandler(coordinator)
    result = await handler.handle_llm_continue(
        {
            "source": "hub:peer",
            "request_id": input_id,
            "hub_message_id": input_id,
            "hub_note": hub_note,
        },
        None,
    )
    await asyncio.gather(*background_tasks)

    assert result["status"] == "triggered"
    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    rows = snapshot["data"]["rows"]
    inputs = [row for row in rows if row["kind"] == "input"]
    assert [row["id"] for row in inputs] == [input_id]
    assert inputs[0]["status"] == "completed"
    starts = [
        event
        for event in tap._history
        if event.get("type") == "turn_start"
    ]
    assert len(starts) == 1
    assert starts[0]["request_ids"] == [input_id]
    assert starts[0]["input_source"] == "hub"
    assert inputs[0]["turn_id"] == starts[0]["turn_id"]
    assert snapshot["data"]["turns"] == [
        {"turn_id": starts[0]["turn_id"], "state": "ended", "input_ids": [input_id]}
    ]
    assert sequence_problems([snapshot]) == []

    state._llm_service = coordinator
    reloaded = ChatStreamService(state).subscribe(
        {"after": None, "conversation_uid": None}
    )["events"][0]
    assert reloaded["data"]["rows"] == []


class _RpcServer:
    def __init__(self):
        self.handlers = {}

    def register(self, method, handler):
        self.handlers[method] = handler


@pytest.mark.asyncio
async def test_permission_prompt_row_carries_full_data_and_resolution():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    bridge = AttachPermissionBridge()
    task = asyncio.create_task(
        bridge.request_confirmation(
            display_tap=state._event_bus.tap,
            rpc_server=_RpcServer(),
            details={
                "tool_id": "tool-permission-1",
                "permission_id": "permission-1",
                "tool_type": "terminal",
                "title": "Run command",
                "command": "pytest -q",
                "risk_level": "MEDIUM",
                "request_id": "acp-permission-request",
                "turn_id": 19,
            },
            timeout=1,
        )
    )
    await asyncio.sleep(0)
    assert bridge.try_settle_response(
        "tool-permission-1", "permission-1", ConfirmationResponse.APPROVE_ONCE
    )
    assert await task is ConfirmationResponse.APPROVE_ONCE

    snapshot, rows = _snapshot_rows(stream)
    prompt = next(row for row in rows if row["id"] == "prompt-permission-1")
    part = prompt["parts"][0]
    assert part["type"] == "prompt"
    assert part["prompt_id"] == "permission-1"
    assert part["data"]["command"] == "pytest -q"
    assert part["data"]["tool_type"] == "terminal"
    assert part["data"]["resolution"] == {
        "type": "permission_granted",
        "tool_id": "tool-permission-1",
        "scope": "once",
        "permission_id": "permission-1",
        "request_id": "acp-permission-request",
        "turn_id": 19,
    }
    assert prompt["status"] == "granted"
    assert prompt["turn_id"] == "19"
    assert sequence_problems([snapshot]) == []
def test_goal_origin_survives_inputless_turn_replay_and_snapshot():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    origin = {"kind": "goal", "goal_id": "full-goal-id", "attempt_id": "full-attempt-id"}
    _publish(state, "turn_start", turn_id="goal-turn", origin=origin)
    running = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    assert running["data"]["turns"] == [{"turn_id": "goal-turn", "state": "running", "input_ids": [], "origin": origin}]
    _publish(state, "turn_complete", turn_id="goal-turn", stop_reason="cancelled")
    events = _events(stream, initial)
    turns = [e["data"] for e in events if e["type"] == "turn.state"]
    assert [t["state"] for t in turns] == ["running", "ended"]
    assert all(t["origin"] == origin for t in turns)
    assert stream.subscribe({"after": None, "conversation_uid": None})["events"][0]["data"]["turns"][-1] == turns[-1]


def test_goal_intermediate_completion_does_not_end_attempt():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    origin = {"kind": "goal", "goal_id": "g", "attempt_id": "a"}
    _publish(state, "turn_start", turn_id="t", origin=origin)
    _publish(state, "turn_complete", turn_id="t", origin=origin, stop_reason="end_turn")
    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    assert snapshot["data"]["turns"][0]["state"] == "running"
    _publish(state, "turn_complete", turn_id="t", origin=origin, goal_attempt_terminal=True)
    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    assert snapshot["data"]["turns"][0]["state"] == "ended"


@pytest.mark.asyncio
async def test_goal_origin_survives_joined_human_input():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    origin = {"kind": "goal", "goal_id": "full-goal", "attempt_id": "full-attempt"}
    _publish(state, "turn_start", turn_id="goal-turn", origin=origin)
    await _add(stream, "human", "steer")
    _publish(state, "user_message", request_id="human", turn_id="goal-turn", text="steer")
    turns = [e["data"] for e in _events(stream, initial) if e["type"] == "turn.state"]
    expected = {"turn_id": "goal-turn", "state": "running", "input_ids": ["human"], "origin": origin}
    assert turns[-1] == expected
    assert stream.subscribe({"after": None, "conversation_uid": None})["events"][0]["data"]["turns"] == [expected]
    _publish(state, "turn_complete", turn_id="goal-turn", goal_attempt_terminal=True)
    turns = [e["data"] for e in _events(stream, initial) if e["type"] == "turn.state"]
    assert any(t["input_ids"] == ["human"] for t in turns)
    assert all(t["origin"] == origin for t in turns)
    assert turns[-1]["state"] == "ended"
    assert stream.subscribe({"after": None, "conversation_uid": None})["events"][0]["data"]["turns"][-1] == turns[-1]


@pytest.mark.parametrize("transition", ["start", "end"])
def test_snapshot_cursor_is_atomic_with_goal_turn_transition(transition):
    import threading

    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    origin = {"kind": "goal", "goal_id": "g", "attempt_id": "a"}
    if transition == "end":
        _publish(state, "turn_start", turn_id="t", origin=origin)
    log = stream._log("conversation-a")
    publish = log.publish
    entered = threading.Event()
    release = threading.Event()
    snapshot_done = threading.Event()
    snapshots = []
    def paused_publish(kind, data, *, on_commit=None):
        if kind == "turn.state":
            entered.set()
            assert release.wait(2)
        return publish(kind, data, on_commit=on_commit)
    log.publish = paused_publish
    def produce():
        if transition == "start":
            _publish(state, "turn_start", turn_id="t", origin=origin)
        else:
            _publish(state, "turn_complete", turn_id="t", goal_attempt_terminal=True)
    def snapshot():
        snapshots.append(stream.subscribe({"after": None, "conversation_uid": None})["events"][0])
        snapshot_done.set()
    producer = threading.Thread(target=produce)
    reader = threading.Thread(target=snapshot)
    producer.start()
    try:
        assert entered.wait(2)
        reader.start()
        assert not snapshot_done.wait(0.1)
    finally:
        release.set()
        producer.join(2)
        if reader.ident is not None:
            reader.join(2)
    assert snapshot_done.is_set()
    turn = snapshots[0]["data"]["turns"][0]
    assert turn["origin"] == origin
    assert turn["state"] == ("running" if transition == "start" else "ended")
    assert snapshots[0]["data"]["next_seq"] == log.next_seq


def test_goal_continuation_responses_replace_only_current_segment():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    origin = {"kind": "goal", "goal_id": "g", "attempt_id": "a"}
    _publish(state, "turn_start", turn_id="t", origin=origin)
    for raw, clean in [("First <tool>", "First "), ("Second <tool>", "Second "), ("Third <tool>", "Third")]:
        _publish(state, "reply_segment_start", turn_id="t", origin=origin)
        _publish(state, "token", turn_id="t", text=raw)
        _publish(state, "response", turn_id="t", text=clean)
    events = _events(stream, initial)
    replies = [e["data"] for e in events if e["type"] == "row.upsert" and e["data"]["kind"] == "reply"]
    assert replies[-1]["parts"][0]["text"] == "First Second Third"
    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    reply = next(row for row in snapshot["data"]["rows"] if row["kind"] == "reply")
    assert reply["parts"][0]["text"] == "First Second Third"


@pytest.mark.asyncio
async def test_native_model_continuations_clean_only_their_own_reply_segment():
    import asyncio
    from unittest.mock import AsyncMock

    from kollabor_agent.queue_processor import QueueProcessor

    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    _publish(state, "turn_start", turn_id="native", request_id="input")
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = state._event_bus
    processor._turn_lock = asyncio.Lock()
    processor._run_after_turn = AsyncMock()
    processor._turn_token = None
    processor.active_request_ids = ()
    processor.turn_completed = False
    processor.cancel_processing = False
    segments = iter(
        [("First <tool>", "First "), ("<tool>", ""), ("Last <tool>", "Last")]
    )

    async def model_call(*args, **kwargs):
        raw, clean = next(segments)
        _publish(state, "token", turn_id="native", text=raw)
        _publish(state, "response", turn_id="native", text=clean)
        return "parent"

    processor._execute_llm_turn_inner = model_call
    for first in (True, False, False):
        await processor._execute_llm_turn(first, "parent")

    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    reply = next(row for row in snapshot["data"]["rows"] if row["kind"] == "reply")
    assert reply["parts"][0]["text"] == "First Last"


@pytest.mark.asyncio
@pytest.mark.parametrize("goal", [False, True])
async def test_native_reply_segments_keep_tool_order_in_replay_and_snapshot(goal):
    import asyncio
    from unittest.mock import AsyncMock

    from kollabor_agent.queue_processor import QueueProcessor

    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    origin = {"kind": "goal", "goal_id": "g", "attempt_id": "a"} if goal else None
    _publish(state, "turn_start", turn_id="t", request_id="input", origin=origin)
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = state._event_bus
    processor._turn_lock = asyncio.Lock()
    processor._run_after_turn = AsyncMock()
    processor._turn_token = None
    processor.active_request_ids = ()
    processor.turn_completed = False
    processor.cancel_processing = False
    segments = iter([("before <tool>", "before"), ("after <tool>", "after")])

    async def model_call(*args, **kwargs):
        raw, clean = next(segments)
        _publish(state, "token", turn_id="t", text=raw)
        _publish(state, "response", turn_id="t", text=clean)
        return "parent"

    processor._execute_llm_turn_inner = model_call
    await processor._execute_llm_turn(True, "parent")
    _publish(state, "tool_start", turn_id="t", tool_id="tool", tool_name="Search")
    _publish(state, "tool_result", turn_id="t", tool_id="tool", tool_name="Search", success=True)
    await processor._execute_llm_turn(False, "parent")
    _publish(state, "turn_complete", turn_id="t", origin=origin, goal_attempt_terminal=True)

    events = _events(stream, initial)
    assert sequence_problems(events) == []
    rows = {}
    for event in events[1:]:
        if event["type"] == "row.upsert":
            rows[event["data"]["id"]] = event["data"]
        elif event["type"] == "row.append":
            data = event["data"]
            rows[data["id"]]["parts"][data["part"]]["text"] += data["text"]
    replay = [row for row in rows.values() if row["kind"] in {"reply", "tool"}]
    snapshot = stream.subscribe({"after": None, "conversation_uid": None})["events"][0]
    saved = [row for row in snapshot["data"]["rows"] if row["kind"] in {"reply", "tool"}]
    for result in (replay, saved):
        assert [row["kind"] for row in result] == ["reply", "tool", "reply"]
        assert [result[0]["parts"][0]["text"], result[2]["parts"][0]["text"]] == ["before", "after"]
        assert result[0]["id"] != result[2]["id"]
