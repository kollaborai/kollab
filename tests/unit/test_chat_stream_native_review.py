"""Regression coverage for native queue inputs projected into chat stream."""

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm.agent_hud import AgentHudEntry, merge_agent_hud_with_user_message
from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor.state.chat_stream import ChatStreamService
from kollabor_agent.queue_processor import QueueProcessor, TaggedInput, batch_request_ids
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_ai.message_content import combine_message_contents
from kollabor_events.chat_stream import sequence_problems
from kollabor_events.data_models import ConversationMessage
from kollabor_tui.display_tap import DisplayTap, publish_semantic
from kollabor_tui.turn_context import current_turn


class _QueueRuntime:
    async def input_scope(self, conversation_uid):
        return ("scope", conversation_uid, "principal")

    async def add_input(self, params):
        return {
            "accepted": True,
            "id": params["id"],
            "state": "queued",
            "attachment_refs": [f"ref-{i}" for i, _ in enumerate(params["attachments"])],
        }


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
        self._llm_service = SimpleNamespace(
            conversation_manager=None,
            conversation_history=[],
            event_bus=self._event_bus,
            _is_hidden_voice_message=lambda _message: False,
        )
        self._llm_service.add = MethodType(LLMService._add_conversation_message, self._llm_service)

    def get_conversation_uid(self):
        return "conversation-a"


def _processor(state: _State) -> QueueProcessor:
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = state._event_bus.tap
    processor.processing_queue = asyncio.Queue()
    processor.is_processing = False
    processor.current_processing_tokens = 0
    processor.processing_start_time = 0
    processor.cancel_processing = False
    processor.turn_completed = False
    processor.last_turn_error = None
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
    processor._add_message_fn = state._llm_service.add
    processor.tool_executor = SimpleNamespace(yes_approvals=None)
    processor._stop_hook_continuations = 0
    processor._stop_hook_active = False
    processor._first_turn_nudge_budget = 1
    processor.active_voice = None
    processor.message_display_service = SimpleNamespace(
        display_error_message=lambda *_args: None,
        display_cancellation_message=lambda *_args: None,
    )
    processor.cancellation_message_shown = False
    return processor


def _manager_config(conversations_dir):
    return SimpleNamespace(
        _conversations_dir=conversations_dir,
        get=lambda key, default=None: (
            True if key == "kollabor.llm.save_conversations" else default
        ),
    )


def _events(stream: ChatStreamService, initial: dict):
    return [
        initial,
        *stream.subscribe(
            {
                "after": initial["data"]["next_seq"] - 1,
                "conversation_uid": "conversation-a",
                "generation": initial["generation"],
            }
        )["events"],
    ]


def _row_events(events, kind: str):
    return [event["data"] for event in events if event["type"] == "row.upsert" and event["data"]["kind"] == kind]


@pytest.mark.asyncio
async def test_native_joined_input_does_not_regress_to_started_on_queue_reentry():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]
    await stream.add_input({"id": "web-a", "text": "first message", "attachments": []})
    await stream.add_input({"id": "web-b", "text": "second message", "attachments": []})
    processor = _processor(state)
    processor.processing_queue.put_nowait(TaggedInput("first message", ("web-a",)))
    calls = 0

    async def execute_turn(user_message_provided, current_parent_uuid, execution_context=None, request_ids=()):
        nonlocal calls
        calls += 1
        processor._stamp_turn(None, tuple(request_ids))
        publish_semantic(processor.renderer, "token", text=f"reply {calls}")
        state._llm_service.add(
            ConversationMessage(role="assistant", content=f"reply {calls}", metadata={}),
            parent_uuid="parent",
        )
        if calls == 1:
            processor.turn_completed = False
            processor.processing_queue.put_nowait(TaggedInput("second message", ("web-b",)))
            return "parent"
        publish_semantic(processor.renderer, "turn_complete", stop_reason="end_turn")
        processor._stamp_turn(None)
        processor.turn_completed = True
        return "parent"

    processor._execute_llm_turn = execute_turn

    async def process_batch(messages):
        await processor.process_message_batch(messages, current_parent_uuid="parent")

    await processor._drain_queue(None, process_batch, AsyncMock())

    events = _events(stream, initial)
    web_b_states = [
        event["data"]["state"] for event in events if event["type"] == "input.state" and event["data"]["id"] == "web-b"
    ]
    assert web_b_states == ["queued", "joined", "ended"]
    assert sequence_problems(events) == []


@pytest.mark.asyncio
async def test_idless_tui_message_while_native_turn_runs_gets_its_own_live_row():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]
    await stream.add_input({"id": "web-a", "text": "first message", "attachments": []})
    processor = _processor(state)
    processor.processing_queue.put_nowait(TaggedInput("first message", ("web-a",)))
    operator_input = {
        "source": "user",
        "timestamp": "2026-11-26T00:00:00Z",
        "text": "typed in the terminal",
    }
    hud = [AgentHudEntry(section="hub", label="peer->me", content="peer online")]
    calls = 0

    async def execute_turn(user_message_provided, current_parent_uuid, execution_context=None, request_ids=()):
        nonlocal calls
        calls += 1
        processor._stamp_turn(None, tuple(request_ids))
        publish_semantic(processor.renderer, "token", text="working")
        if calls == 1:
            processor.turn_completed = False
            processor.processing_queue.put_nowait(
                TaggedInput(
                    operator_input["text"],
                    (),
                    operator_inputs=(operator_input,),
                )
            )
            return "parent"
        publish_semantic(processor.renderer, "turn_complete", stop_reason="end_turn")
        processor._stamp_turn(None)
        processor.turn_completed = True
        return "parent"

    processor._execute_llm_turn = execute_turn

    async def process_batch(messages):
        ids = batch_request_ids(messages)
        original = combine_message_contents([message.content for message in messages])
        records = tuple(record for message in messages for record in getattr(message, "operator_inputs", ()))
        model_prompt = merge_agent_hud_with_user_message(hud, original)
        wrapped = TaggedInput(model_prompt, ids, operator_inputs=records)
        await processor.process_message_batch(
            [wrapped],
            current_parent_uuid="parent",
            request_ids=ids,
        )

    await processor._drain_queue(None, process_batch, AsyncMock())

    events = _events(stream, initial)
    rows = _row_events(events, "input")
    tui_rows = [
        row
        for row in rows
        if any(part.get("type") == "text" and part.get("text") == "typed in the terminal" for part in row["parts"])
    ]
    tui_ids = {row["id"] for row in tui_rows}
    assert len(tui_ids) == 1
    assert next(iter(tui_ids)) != "web-a"
    assert tui_rows[-1]["status"] in {"joined", "completed"}
    assert all("<agent_hud>" not in part.get("text", "") for row in rows for part in row["parts"])
    assert sequence_problems(events) == []


@pytest.mark.asyncio
async def test_image_only_admission_keeps_model_hud_out_of_input_row():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]
    await stream.add_input(
        {
            "id": "web-image",
            "text": "",
            "attachments": [
                {
                    "name": "photo.png",
                    "content_type": "image/png",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        }
    )
    processor = _processor(state)
    processor.processing_queue.put_nowait(
        TaggedInput(
            [{"type": "image", "source": {"kind": "url", "url": "https://example.invalid/photo.png"}}],
            ("web-image",),
        )
    )
    hud = [AgentHudEntry(section="hub", label="peer->me", content="peer online")]

    async def execute_turn(user_message_provided, current_parent_uuid, execution_context=None, request_ids=()):
        processor._stamp_turn(None, tuple(request_ids))
        publish_semantic(processor.renderer, "turn_complete", stop_reason="end_turn")
        processor._stamp_turn(None)
        processor.turn_completed = True
        return "parent"

    processor._execute_llm_turn = execute_turn

    async def process_batch(messages):
        ids = batch_request_ids(messages)
        model_prompt = merge_agent_hud_with_user_message(
            hud,
            combine_message_contents([message.content for message in messages]),
        )
        await processor.process_message_batch([model_prompt], current_parent_uuid="parent", request_ids=ids)

    await processor._drain_queue(None, process_batch, AsyncMock())

    events = _events(stream, initial)
    row_updates = [row for row in _row_events(events, "input") if row["id"] == "web-image"]
    row = row_updates[-1]
    assert any(part["type"] == "image" for part in row["parts"])
    assert all(
        "<agent_hud>" not in part.get("text", "") and "[image" not in part.get("text", "")
        for part in row["parts"]
        if part.get("type") == "text"
    )
    assert not any(part["type"] == "text" for part in row["parts"])
    assert sequence_problems(events) == []


def test_native_user_message_fills_turn_start_placeholders_by_authenticated_input():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]
    tap = state._event_bus.tap

    publish_semantic(
        tap,
        "turn_start",
        request_ids=["native-a", "native-b"],
        turn_id="native-placeholder-turn",
    )
    publish_semantic(
        tap,
        "user_message",
        request_id="native-a",
        request_ids=["native-a", "native-b"],
        text="<agent_hud>secret context</agent_hud>\n[image1]",
        operator_inputs=[
            {
                "source": "tui",
                "timestamp": "2026-11-26T00:00:00Z",
                "request_id": "native-a",
                "text": "first authored message",
            },
            {
                "source": "tui",
                "timestamp": "2026-11-26T00:00:01Z",
                "request_id": "native-b",
                "text": "second authored message",
            },
        ],
    )

    events = _events(stream, initial)
    latest_rows = {}
    for row in _row_events(events, "input"):
        latest_rows[row["id"]] = row
    assert {
        input_id: [part.get("text") for part in latest_rows[input_id]["parts"]] for input_id in ("native-a", "native-b")
    } == {
        "native-a": ["first authored message"],
        "native-b": ["second authored message"],
    }
    assert all(
        "<agent_hud>" not in part.get("text", "") and "[image1]" not in part.get("text", "")
        for row in latest_rows.values()
        for part in row["parts"]
    )
    assert sequence_problems(events, complete=False) == []


@pytest.mark.asyncio
async def test_plugin_add_message_projects_its_displayable_user_text_live():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]

    coordinator = state._llm_service
    coordinator._acp_owns_chat = lambda: False
    coordinator._add_conversation_message = coordinator.add
    coordinator.current_parent_uuid = "plugin-parent"
    coordinator.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(return_value="plugin-user-row")
    )
    coordinator.message_display_service = SimpleNamespace(
        show_loading=lambda *_args: None,
        hide_loading=lambda: None,
        message_coordinator=SimpleNamespace(display_message_sequence=lambda *_args: None),
    )
    coordinator.session_stats = {"messages": 0}

    result = await MessageHandler(coordinator).handle_add_message(
        {
            "messages": [{"role": "user", "content": "plugin message"}],
            "options": {
                "show_loading": False,
                "log_messages": False,
                "display_messages": True,
                "trigger_llm": False,
            },
        },
        None,
    )

    events = _events(stream, initial)
    rows = _row_events(events, "input")
    assert result["success"] is True
    latest_rows = {row["id"]: row for row in rows}
    assert len(latest_rows) == 1
    text_by_id = {
        row_id: [part.get("text") for part in row["parts"] if part["type"] == "text"]
        for row_id, row in latest_rows.items()
    }
    assert list(text_by_id.values()) == [["plugin message"]]
    input_states = {
        event["data"]["id"]: event["data"]
        for event in events
        if event["type"] == "input.state"
    }
    assert set(input_states) == set(latest_rows)
    assert all(
        info == {
            "id": input_id,
            "state": "ended",
            "turn_id": None,
            "outcome": "completed",
        }
        for input_id, info in input_states.items()
    )
    assert not any(event["type"] == "turn.state" for event in events)
    assert sequence_problems(events) == []


@pytest.mark.asyncio
async def test_plugin_multipart_input_identity_and_text_survive_snapshot_reload(tmp_path):
    state = _State()
    conversation_dir = tmp_path / "conversations"
    logger = KollaborConversationLogger(conversation_dir)
    manager = ConversationManager(_manager_config(conversation_dir), logger)
    manager.conversation_metadata["conversation_uid"] = "conversation-a"
    state._llm_service.conversation_manager = manager
    state._llm_service.conversation_logger = logger

    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]
    coordinator = state._llm_service
    coordinator._acp_owns_chat = lambda: False
    coordinator._add_conversation_message = coordinator.add
    coordinator.current_parent_uuid = "plugin-parent"
    coordinator.message_display_service = SimpleNamespace(
        show_loading=lambda *_args: None,
        hide_loading=lambda: None,
        message_coordinator=SimpleNamespace(display_message_sequence=lambda *_args: None),
    )
    coordinator.session_stats = {"messages": 0}

    await MessageHandler(coordinator).handle_add_message(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "first"},
                        {
                            "type": "image",
                            "source": {
                                "kind": "url",
                                "url": "https://example.invalid/photo.png",
                            },
                        },
                        {"type": "text", "text": "second"},
                    ],
                }
            ],
            "options": {
                "show_loading": False,
                "log_messages": False,
                "display_messages": True,
                "trigger_llm": False,
            },
        },
        None,
    )

    live_events = _events(stream, initial)
    live_rows = {row["id"]: row for row in _row_events(live_events, "input")}
    assert len(live_rows) == 1
    live_row_id, live_row = next(iter(live_rows.items()))
    assert [part.get("text") for part in live_row["parts"]] == ["first\nsecond"]
    assert "[image" not in str(live_row["parts"])
    assert sequence_problems(live_events, complete=False) == []

    same_process_snapshot = stream.subscribe({})["events"][0]["data"]
    same_process_rows = [
        row for row in same_process_snapshot["rows"] if row["kind"] == "input"
    ]
    assert len(same_process_rows) == 1
    assert same_process_rows[0]["id"] == live_row_id

    saved_snapshot = manager.snapshots_dir / f"{manager.current_session_id}_snapshot.json"
    assert saved_snapshot.exists()
    resumed_manager = ConversationManager(_manager_config(conversation_dir), logger)
    assert resumed_manager.load_conversation(saved_snapshot)
    resumed_state = SimpleNamespace(
        _event_bus=state._event_bus,
        _llm_service=SimpleNamespace(
            conversation_history=None,
            conversation_manager=resumed_manager,
            conversation_logger=logger,
        ),
        get_conversation_uid=lambda: "conversation-a",
    )
    resumed_stream = ChatStreamService(resumed_state)
    resumed_snapshot = resumed_stream.subscribe({})["events"][0]["data"]
    resumed_rows = [row for row in resumed_snapshot["rows"] if row["kind"] == "input"]
    assert len(resumed_rows) == 1
    assert resumed_rows[0]["id"] == live_row_id
    assert resumed_rows[0]["parts"] == [{"type": "text", "text": "first\nsecond"}]
    message = state._llm_service.conversation_history[0]
    assert message.metadata["input_id"] == live_row_id
    assert resumed_snapshot["inputs"] == [
        {
            "id": live_row_id,
            "state": "ended",
            "turn_id": None,
            "outcome": "completed",
        }
    ]


def test_unmarked_idless_user_payload_still_hides_model_hud_and_image_tokens():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]

    publish_semantic(
        state._event_bus.tap,
        "user_message",
        text="<agent_hud>private model context</agent_hud>\n[image1]",
    )

    events = _events(stream, initial)
    assert _row_events(events, "input") == []
    assert sequence_problems(events) == []


@pytest.mark.asyncio
async def test_dropped_unbound_input_does_not_end_running_native_turn():
    state = _State()
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]
    await stream.add_input({"id": "web-active", "text": "keep working", "attachments": []})
    await stream.add_input({"id": "voice-dropped", "text": "later input", "attachments": []})
    tap = state._event_bus.tap
    publish_semantic(
        tap,
        "turn_start",
        request_id="web-active",
        request_ids=["web-active"],
        turn_id="native-active",
    )
    publish_semantic(tap, "token", text="still working", turn_id="native-active")
    processor = _processor(state)
    token = current_turn.set({})
    try:
        processor._end_dropped(
            TaggedInput("later input", ("voice-dropped",)),
            "Queue full",
        )
    finally:
        current_turn.reset(token)

    snapshot = stream.subscribe({})["events"][0]["data"]
    turns = {turn["turn_id"]: turn for turn in snapshot["turns"]}
    inputs = {item["id"]: item for item in snapshot["inputs"]}
    assert turns["native-active"]["state"] == "running"
    assert turns["native-active"]["input_ids"] == ["web-active"]
    assert inputs["web-active"]["state"] == "started"
    assert inputs["voice-dropped"]["state"] == "ended"
    assert inputs["voice-dropped"].get("turn_id") is None
    assert all(row.get("turn_id") is None for row in snapshot["rows"] if row["kind"] == "marker")
    events = _events(stream, initial)
    assert sequence_problems(events, complete=False) == []
