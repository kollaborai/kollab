"""Regression coverage for native queue inputs projected into chat stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm.agent_hud import AgentHudEntry, merge_agent_hud_with_user_message
from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor.state.chat_stream import (
    _NATIVE_PLUGIN_INPUT_HISTORY_LIMIT,
    ChatStreamService,
    register_chat_stream_handlers,
)
from kollabor_agent.queue_processor import (
    QueuedInput,
    QueueProcessor,
    TaggedInput,
    batch_request_ids,
)
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_ai.message_content import combine_message_contents
from kollabor_events.chat_stream import sequence_problems
from kollabor_events.data_models import ConversationMessage
from kollabor_rpc import RpcRequest, RpcServer
from kollabor_tui.display_tap import DisplayTap, publish_semantic
from kollabor_tui.turn_context import current_turn


class _QueueRuntime:
    def __init__(self):
        self.calls = []

    async def input_scope(self, conversation_uid):
        return ("scope", conversation_uid, "principal")

    async def add_input(self, params):
        return {
            "accepted": True,
            "id": params["id"],
            "state": "queued",
            "attachment_refs": [f"ref-{i}" for i, _ in enumerate(params["attachments"])],
        }

    async def cancel_queued_input(self, params):
        self.calls.append(("cancel", params))
        return {"found": False, "cancelled": False}

    async def send_now(self, params):
        self.calls.append(("send-now", params))
        return {"found": False}


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


def _plugin_queue_processor(state: _State, logger, *, max_queue_size: int):
    processor = _processor(state)
    processor.conversation_history = state._llm_service.conversation_history
    processor.processing_queue = asyncio.Queue(maxsize=max_queue_size)
    processor.max_queue_size = max_queue_size
    processor.task_config = SimpleNamespace(
        queue=SimpleNamespace(
            overflow_strategy="drop_newest",
            log_queue_events=False,
            block_timeout=0.1,
        )
    )
    processor._queue_metrics = {
        "total_enqueue_attempts": 0,
        "total_enqueue_successes": 0,
        "drop_oldest_count": 0,
        "drop_newest_count": 0,
        "block_count": 0,
        "block_timeout_count": 0,
    }
    processor.conversation_logger = logger
    processor.event_bus = state._event_bus
    processor._native_stream_inputs = {}
    processor._native_stream_messages = {}
    processor._persisted_native_stream_ids = set()
    return processor


def _prepare_triggered_plugin_stream(state: _State, tmp_path, *, max_queue_size: int):
    conversation_dir = tmp_path / "conversations"
    logger = KollaborConversationLogger(conversation_dir)
    manager = ConversationManager(_manager_config(conversation_dir), logger)
    manager.conversation_metadata["conversation_uid"] = "conversation-a"
    coordinator = state._llm_service
    coordinator.conversation_manager = manager
    coordinator.conversation_logger = logger
    coordinator._acp_owns_chat = lambda: False
    coordinator._profile_selects_acp = lambda: (False, None)
    coordinator._active_acp = lambda: None
    coordinator._add_conversation_message = coordinator.add
    coordinator.current_parent_uuid = None
    coordinator.is_processing = False
    coordinator.session_stats = {"messages": 0}
    coordinator.api_service = SimpleNamespace(model="test-model")
    coordinator.create_background_task = lambda *_args, **_kwargs: None
    coordinator.message_display_service = SimpleNamespace(
        show_loading=lambda *_args: None,
        hide_loading=lambda: None,
        message_coordinator=SimpleNamespace(display_message_sequence=lambda *_args: None),
    )
    processor = _plugin_queue_processor(state, logger, max_queue_size=max_queue_size)
    coordinator._queue_processor = processor
    coordinator._enqueue_with_overflow_strategy = processor.enqueue
    native_send_now = getattr(LLMService, "send_now_native_plugin_input", None)
    if callable(native_send_now):
        coordinator.send_now_native_plugin_input = MethodType(
            native_send_now, coordinator
        )
    state._event_bus.services["llm_service"] = coordinator
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    rpc = RpcServer()
    register_chat_stream_handlers(rpc, stream)
    return logger, coordinator, processor, stream, rpc


async def _rpc_call(server: RpcServer, method: str, params: dict):
    response = await server.handle_request(
        RpcRequest(
            request_id=f"{method}-{uuid.uuid4().hex}",
            method=method,
            params=params,
        )
    )
    assert response.error is None
    return response.result


async def _add_triggered_plugin_input(coordinator, text: str) -> str:
    result = await MessageHandler(coordinator).handle_add_message(
        {
            "messages": [{"role": "user", "content": text}],
            "options": {
                "show_loading": False,
                "display_messages": True,
                "trigger_llm": True,
            },
        },
        None,
    )
    assert result["success"] is True
    queue_items = [
        item
        for item in coordinator._queue_processor.processing_queue._queue
        if isinstance(item, TaggedInput)
    ]
    queue_item = queue_items[-1]
    return queue_item.request_ids[0]


@pytest.mark.asyncio
async def test_durable_voice_request_input_can_be_admitted_to_native_queue(tmp_path):
    state = _State()
    logger, _coordinator, processor, _stream, _rpc = (
        _prepare_triggered_plugin_stream(state, tmp_path, max_queue_size=2)
    )
    request_id = "voice-durable-input"
    history_row_uuid = await logger.log_user_message(
        "voice transcript", message_metadata={"request_id": request_id}
    )
    voice_input = QueuedInput(
        "voice transcript",
        {"name": "test voice"},
        history_row_uuid=history_row_uuid,
        request_ids=(request_id,),
    )

    accepted = await processor.enqueue(voice_input)

    assert accepted is True
    assert list(processor.processing_queue._queue) == [voice_input]


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


@pytest.mark.asyncio
async def test_plugin_cancel_rpc_controls_exact_native_tagged_input(tmp_path):
    state = _State()
    logger, coordinator, processor, _stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=2
    )
    input_id = await _add_triggered_plugin_input(coordinator, "cancel plugin input")

    result = await _rpc_call(
        rpc, "state.chat_stream.cancel_queued_input", {"id": input_id}
    )

    assert result == {"found": True, "cancelled": True}
    assert processor.processing_queue.empty()
    assert state._event_bus.services["web_message_queue_runtime"].calls == []
    user_record = next(
        json.loads(line)
        for line in logger.session_file.read_text().splitlines()
        if json.loads(line).get("type") == "user"
    )
    assert user_record["message"]["metadata"]["chat_stream_outcome"] == "cancelled"
    snapshot = await _rpc_call(rpc, "state.chat_stream.subscribe", {})
    input_state = next(
        event["data"]
        for event in snapshot["events"]
        if event["type"] == "snapshot"
    )
    assert input_state["inputs"] == [
        {
            "id": input_id,
            "state": "ended",
            "turn_id": None,
            "outcome": "cancelled",
        }
    ]


@pytest.mark.parametrize("outcome", ["completed", "cancelled"])
@pytest.mark.asyncio
async def test_plugin_input_admission_and_terminal_outcome_survive_jsonl_reload(
    tmp_path, outcome
):
    state = _State()
    logger, coordinator, processor, _stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=2
    )
    input_id = await _add_triggered_plugin_input(
        coordinator, f"plugin {outcome} across reload"
    )

    if outcome == "cancelled":
        result = await _rpc_call(
            rpc, "state.chat_stream.cancel_queued_input", {"id": input_id}
        )
        assert result == {"found": True, "cancelled": True}
    else:
        await processor._persist_native_input_outcomes((input_id,), "end_turn")

    user_record = next(
        json.loads(line)
        for line in logger.session_file.read_text().splitlines()
        if json.loads(line).get("type") == "user"
    )
    persisted_metadata = user_record["message"]["metadata"]
    assert "chat_stream_pending_input_id" not in persisted_metadata
    assert persisted_metadata["input_id"] == input_id
    assert persisted_metadata["chat_stream_outcome"] == outcome

    resumed_manager = ConversationManager(
        _manager_config(tmp_path / "conversations"), logger
    )
    assert resumed_manager.load_session(logger.session_id)
    resumed_state = SimpleNamespace(
        _event_bus=state._event_bus,
        _llm_service=SimpleNamespace(
            conversation_history=None,
            conversation_manager=resumed_manager,
            conversation_logger=logger,
        ),
        get_conversation_uid=lambda: "conversation-a",
    )
    resumed = ChatStreamService(resumed_state).subscribe({})["events"][0]["data"]

    rows = [row for row in resumed["rows"] if row["kind"] == "input"]
    assert len(rows) == 1
    assert rows[0]["id"] == input_id
    assert rows[0]["parts"] == [
        {"type": "text", "text": f"plugin {outcome} across reload"}
    ]
    assert resumed["inputs"] == [
        {
            "id": input_id,
            "state": "ended",
            "turn_id": None,
            "outcome": outcome,
        }
    ]


@pytest.mark.asyncio
async def test_plugin_send_now_rpc_prioritizes_exact_native_tagged_input(tmp_path):
    state = _State()
    _logger, coordinator, processor, _stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    scheduled = []
    coordinator.create_background_task = lambda _task, *, name=None: scheduled.append(name)
    first_id = await _add_triggered_plugin_input(coordinator, "older plugin input")
    selected_id = await _add_triggered_plugin_input(coordinator, "send this plugin input now")
    scheduled.clear()

    result = await _rpc_call(
        rpc, "state.chat_stream.send_now", {"id": selected_id}
    )

    assert result == {"found": True, "accepted": True, "state": "queued"}
    assert [item.request_ids[0] for item in processor.processing_queue._queue] == [
        selected_id,
        first_id,
    ]
    assert scheduled == ["force_send_plugin_input"]
    assert state._event_bus.services["web_message_queue_runtime"].calls == []


@pytest.mark.asyncio
async def test_second_plugin_send_now_reports_queued_while_reservation_is_held(tmp_path):
    state = _State()
    _logger, coordinator, processor, _stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    coordinator.create_background_task = lambda *_args, **_kwargs: None
    first_id = await _add_triggered_plugin_input(coordinator, "send this first")
    second_id = await _add_triggered_plugin_input(coordinator, "still queued second")

    first = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": first_id})
    second = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": second_id})

    assert first == {"found": True, "accepted": True, "state": "queued"}
    assert second == {"found": True, "accepted": False, "state": "queued"}
    assert [item.request_ids[0] for item in processor.processing_queue._queue] == [
        first_id,
        second_id,
    ]


@pytest.mark.asyncio
async def test_plugin_send_now_recovers_turn_id_when_input_starts_during_gate(tmp_path):
    state = _State()
    _logger, coordinator, processor, _stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    coordinator.create_background_task = lambda *_args, **_kwargs: None
    input_id = await _add_triggered_plugin_input(
        coordinator, "send this plugin input now"
    )
    native_send = coordinator.send_now_native_plugin_input
    turn_id = "native-turn-started-during-send-now"

    async def start_while_send_waits(request_id):
        queued = processor.processing_queue.get_nowait()
        processor.processing_queue.task_done()
        assert queued.request_ids == (input_id,)
        processor.active_request_ids = (input_id,)
        processor.is_processing = True
        publish_semantic(
            processor.renderer,
            "turn_start",
            request_id=input_id,
            request_ids=[input_id],
            turn_id=turn_id,
            input_source="native",
        )
        return await native_send(request_id)

    coordinator.send_now_native_plugin_input = start_while_send_waits

    result = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": input_id})

    assert result == {
        "found": True,
        "accepted": False,
        "state": "started",
        "turn_id": turn_id,
    }


@pytest.mark.asyncio
async def test_plugin_send_now_prefers_recorded_joined_turn_over_coordinator_started(
    tmp_path,
):
    state = _State()
    _logger, coordinator, _processor, stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    input_id = await _add_triggered_plugin_input(coordinator, "join the active turn")
    turn_id = "native-turn-already-running"

    async def start_joined_turn(_request_id):
        stream._set_turn_state("conversation-a", turn_id, "running")
        publish_semantic(
            state._event_bus.tap,
            "turn_start",
            request_id=input_id,
            request_ids=[input_id],
            turn_id=turn_id,
            input_source="native",
        )
        return {
            "found": True,
            "accepted": False,
            "state": "started",
        }

    coordinator.send_now_native_plugin_input = start_joined_turn

    result = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": input_id})

    assert result == {
        "found": True,
        "accepted": False,
        "state": "joined",
        "turn_id": turn_id,
    }


@pytest.mark.asyncio
async def test_plugin_send_now_recovers_started_turn_when_coordinator_loses_input_id(
    tmp_path,
):
    state = _State()
    _logger, coordinator, _processor, stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    input_id = await _add_triggered_plugin_input(coordinator, "start during lookup")
    turn_id = "native-turn-started-before-active-id"

    async def drain_before_active_id(_request_id):
        publish_semantic(
            state._event_bus.tap,
            "turn_start",
            request_id=input_id,
            request_ids=[input_id],
            turn_id=turn_id,
            input_source="native",
        )
        return {"found": False}

    coordinator.send_now_native_plugin_input = drain_before_active_id

    result = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": input_id})

    assert result == {
        "found": True,
        "accepted": False,
        "state": "started",
        "turn_id": turn_id,
    }


@pytest.mark.asyncio
async def test_plugin_send_now_retry_after_end_keeps_native_identity(tmp_path):
    state = _State()
    _logger, coordinator, _processor, stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    input_id = await _add_triggered_plugin_input(coordinator, "already completed")
    turn_id = "native-turn-already-completed"
    publish_semantic(
        state._event_bus.tap,
        "turn_start",
        request_id=input_id,
        request_ids=[input_id],
        turn_id=turn_id,
        input_source="native",
    )
    stream._set_input_state(
        "conversation-a", input_id, "ended", turn_id=turn_id, outcome="completed"
    )

    result = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": input_id})

    assert result == {
        "found": True,
        "accepted": False,
        "state": "ended",
        "turn_id": turn_id,
    }


@pytest.mark.asyncio
async def test_native_plugin_retry_survives_other_conversations_lifecycle_churn(tmp_path):
    state = _State()
    _logger, coordinator, _processor, stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    input_id = await _add_triggered_plugin_input(coordinator, "retry after lifecycle churn")
    turn_id = "native-turn-retained-with-admission"
    publish_semantic(
        state._event_bus.tap,
        "turn_start",
        request_id=input_id,
        request_ids=[input_id],
        turn_id=turn_id,
        input_source="native",
    )
    stream._set_input_state(
        "conversation-a", input_id, "ended", turn_id=turn_id, outcome="completed"
    )

    for index in range(_NATIVE_PLUGIN_INPUT_HISTORY_LIMIT + 1):
        uid = f"other-conversation-{index}"
        other_input_id = f"plugin-{index}"
        key = (uid, other_input_id)
        stream._native_plugin_inputs.add(key)
        stream._set_input_state(
            uid, other_input_id, "ended", turn_id=f"turn-{index}", outcome="completed"
        )

    # Native origin must live as long as the admission record so unrelated
    # conversations cannot evict the retry identity.
    result = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": input_id})

    assert result == {
        "found": True,
        "accepted": False,
        "state": "ended",
        "turn_id": turn_id,
    }
    assert stream._admission_inputs["conversation-a"][input_id]["_native_plugin"] is True
    assert "_native_plugin" not in stream._inputs["conversation-a"][input_id]


@pytest.mark.asyncio
async def test_plugin_send_now_isolates_selected_input_from_queue_siblings(tmp_path):
    state = _State()
    _logger, coordinator, processor, _stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=3
    )
    coordinator.create_background_task = lambda *_args, **_kwargs: None
    first_id = await _add_triggered_plugin_input(coordinator, "older plugin input")
    selected_id = await _add_triggered_plugin_input(coordinator, "send only this now")
    last_id = await _add_triggered_plugin_input(coordinator, "keep this behind")

    result = await _rpc_call(rpc, "state.chat_stream.send_now", {"id": selected_id})
    batches = []

    async def process_batch(messages):
        batches.append([item.request_ids[0] for item in messages])
        processor.turn_completed = True

    assert result == {"found": True, "accepted": True, "state": "queued"}
    await processor.process_queue(None, process_batch, AsyncMock())

    assert batches == [[selected_id], [first_id, last_id]]


@pytest.mark.asyncio
async def test_plugin_queued_stream_control_is_not_published_before_queue_admission(
    tmp_path,
):
    state = _State()
    logger, coordinator, processor, stream, rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=2
    )
    logger_entered = asyncio.Event()
    release_logger = asyncio.Event()
    original_log = logger.log_user_message

    async def gated_log(*args, **kwargs):
        logger_entered.set()
        await release_logger.wait()
        return await original_log(*args, **kwargs)

    logger.log_user_message = gated_log
    handler_task = asyncio.create_task(
        MessageHandler(coordinator).handle_add_message(
            {
                "messages": [{"role": "user", "content": "admit before controls"}],
                "options": {
                    "show_loading": False,
                    "display_messages": True,
                    "trigger_llm": True,
                },
            },
            None,
        )
    )
    try:
        await asyncio.wait_for(logger_entered.wait(), timeout=1)
        input_id = coordinator.conversation_history[0].metadata[
            "chat_stream_pending_input_id"
        ]
        snapshot_event = next(
            event
            for event in stream.subscribe({})["events"]
            if event["type"] == "snapshot"
        )
        assert all(
            item["id"] != input_id
            for item in snapshot_event["data"]["inputs"]
        )
        assert all(
            row["id"] != input_id
            for row in snapshot_event["data"]["rows"]
            if row["kind"] == "input"
        )
        assert all(
            part.get("text") != "admit before controls"
            for row in snapshot_event["data"]["rows"]
            if row["kind"] == "input"
            for part in row["parts"]
        )
    finally:
        release_logger.set()
        result = await handler_task

    assert result["success"] is True
    input_id = coordinator.conversation_history[0].metadata["input_id"]
    assert [item.request_ids for item in processor.processing_queue._queue] == [
        (input_id,)
    ]
    admitted_snapshot = await _rpc_call(rpc, "state.chat_stream.subscribe", {})
    admitted_data = next(
        event["data"]
        for event in admitted_snapshot["events"]
        if event["type"] == "snapshot"
    )
    admitted_row = next(
        row for row in admitted_data["rows"] if row["id"] == input_id
    )
    assert admitted_row["status"] == "queued"
    assert admitted_data["inputs"] == [
        {"id": input_id, "state": "queued", "turn_id": None}
    ]
    admitted = await _rpc_call(rpc, "state.chat_stream.cancel_queued_input", {"id": input_id})
    assert admitted == {"found": True, "cancelled": True}
    assert processor.processing_queue.empty()


@pytest.mark.asyncio
async def test_plugin_logging_without_history_initializes_message_metadata(tmp_path):
    state = _State()
    logger, coordinator, _processor, _stream, _rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=2
    )

    result = await MessageHandler(coordinator).handle_add_message(
        {
            "messages": [{"role": "user", "content": "log without history"}],
            "options": {
                "show_loading": False,
                "display_messages": False,
                "add_to_history": False,
                "log_messages": True,
                "trigger_llm": False,
            },
        },
        None,
    )

    assert result["success"] is True
    assert coordinator.conversation_history == []
    record = next(
        json.loads(line)
        for line in logger.session_file.read_text().splitlines()
        if json.loads(line).get("type") == "user"
    )
    assert record["message"]["content"] == "log without history"
    assert record["message"]["metadata"] == {}


@pytest.mark.asyncio
async def test_plugin_batch_capture_uses_authored_text_not_model_image_payload():
    captured = {}

    class RecordingProcessor:
        async def process_message_batch(self, **kwargs):
            captured.update(kwargs)
            return "assistant-row"

    service = object.__new__(LLMService)
    service._queue_processor = RecordingProcessor()
    service._pending_agent_hud = []
    service.current_parent_uuid = "before"
    service._turn_serial = 0
    service._cursor_winner_serial = -1
    service._continue_serial = None
    plugin_input = TaggedInput(
        content=[
            {"type": "text", "text": "authored plugin text"},
            {
                "type": "image",
                "source": {"kind": "url", "url": "https://example.invalid/x.png"},
            },
        ],
        request_ids=("plugin-input",),
        history_row_uuid="history-row",
        stream_display_content="authored plugin text",
    )

    await service._process_message_batch([plugin_input])

    assert captured["stream_inputs"] == (
        {
            "request_id": "plugin-input",
            "history_row_uuid": "history-row",
            "content": "authored plugin text",
        },
    )


@pytest.mark.asyncio
async def test_external_cancel_persists_plugin_input_terminal_outcome(tmp_path):
    state = _State()
    logger, coordinator, processor, _stream, _rpc = _prepare_triggered_plugin_stream(
        state, tmp_path, max_queue_size=2
    )
    input_id = await _add_triggered_plugin_input(coordinator, "cancel on external stop")
    processor._cancel_generation = 0

    processor.request_cancellation(origin="external")
    await asyncio.sleep(0.05)

    record = next(
        json.loads(line)
        for line in logger.session_file.read_text().splitlines()
        if json.loads(line).get("type") == "user"
    )
    assert record["message"]["metadata"]["input_id"] == input_id
    assert record["message"]["metadata"]["chat_stream_outcome"] == "failed"
    assert processor.processing_queue.empty()


@pytest.mark.asyncio
async def test_triggered_plugin_drop_persists_identity_and_failure_to_jsonl(tmp_path):
    state = _State()
    conversation_dir = tmp_path / "conversations"
    logger = KollaborConversationLogger(conversation_dir)
    await logger.log_conversation_start()
    manager = ConversationManager(_manager_config(conversation_dir), logger)
    manager.conversation_metadata["conversation_uid"] = "conversation-a"
    coordinator = state._llm_service
    coordinator.conversation_manager = manager
    coordinator.conversation_logger = logger
    coordinator._acp_owns_chat = lambda: False
    coordinator._profile_selects_acp = lambda: (False, None)
    coordinator._active_acp = lambda: None
    coordinator._add_conversation_message = coordinator.add
    coordinator.current_parent_uuid = None
    coordinator.is_processing = False
    coordinator.session_stats = {"messages": 0}
    coordinator.api_service = SimpleNamespace(model="test-model")
    coordinator.create_background_task = lambda *_args, **_kwargs: None
    coordinator.message_display_service = SimpleNamespace(
        show_loading=lambda *_args: None,
        hide_loading=lambda: None,
        message_coordinator=SimpleNamespace(display_message_sequence=lambda *_args: None),
    )
    processor = _plugin_queue_processor(state, logger, max_queue_size=0)
    coordinator._queue_processor = processor
    coordinator._enqueue_with_overflow_strategy = processor.enqueue
    state._event_bus.services["llm_service"] = coordinator
    stream = ChatStreamService(state)
    stream.attach_display_tap()
    initial = stream.subscribe({})["events"][0]

    result = await MessageHandler(coordinator).handle_add_message(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "authored plugin text"},
                        {
                            "type": "image",
                            "source": {
                                "kind": "url",
                                "url": "https://example.invalid/plugin.png",
                            },
                        },
                    ],
                }
            ],
            "options": {
                "show_loading": False,
                "display_messages": True,
                "trigger_llm": True,
            },
        },
        None,
    )

    events = _events(stream, initial)
    input_rows = {row["id"]: row for row in _row_events(events, "input")}
    assert result["success"] is False
    assert len(input_rows) == 1
    input_id, row = next(iter(input_rows.items()))
    assert row["status"] == "failed"
    assert [part.get("text") for part in row["parts"]] == ["authored plugin text"]
    assert sequence_problems(events) == []

    user_records = [
        json.loads(line)
        for line in logger.session_file.read_text().splitlines()
        if json.loads(line).get("type") == "user"
    ]
    assert len(user_records) == 1
    logged_metadata = user_records[0]["message"]["metadata"]
    assert logged_metadata["input_id"] == input_id
    assert logged_metadata["request_id"] == input_id
    assert logged_metadata["chat_stream_display_text"] == "authored plugin text"
    assert logged_metadata["chat_stream_outcome"] == "failed"

    resumed_manager = ConversationManager(_manager_config(conversation_dir), logger)
    assert resumed_manager.load_session(logger.session_id)
    resumed_user = next(
        message for message in resumed_manager.messages if message["role"] == "user"
    )
    assert resumed_user["metadata"]["input_id"] == input_id
    assert resumed_user["metadata"]["chat_stream_display_text"] == "authored plugin text"
    assert resumed_user["metadata"]["chat_stream_outcome"] == "failed"
    resumed_state = SimpleNamespace(
        _event_bus=state._event_bus,
        _llm_service=SimpleNamespace(
            conversation_history=None,
            conversation_manager=resumed_manager,
            conversation_logger=logger,
        ),
        get_conversation_uid=lambda: "conversation-a",
    )
    resumed = ChatStreamService(resumed_state).subscribe({})["events"][0]["data"]
    resumed_rows = [item for item in resumed["rows"] if item["kind"] == "input"]
    assert len(resumed_rows) == 1
    assert resumed_rows[0]["id"] == input_id
    assert resumed_rows[0]["status"] == "failed"
    assert resumed_rows[0]["parts"] == [
        {"type": "text", "text": "authored plugin text"}
    ]
    assert resumed["inputs"] == [
        {
            "id": input_id,
            "state": "ended",
            "turn_id": None,
            "outcome": "failed",
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
