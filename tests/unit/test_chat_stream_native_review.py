"""Regression coverage for native queue inputs projected into chat stream."""

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm.agent_hud import AgentHudEntry, merge_agent_hud_with_user_message
from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.chat_stream import ChatStreamService
from kollabor_agent.queue_processor import QueueProcessor, TaggedInput, batch_request_ids
from kollabor_ai.message_content import combine_message_contents
from kollabor_events.chat_stream import sequence_problems
from kollabor_events.data_models import ConversationMessage
from kollabor_tui.display_tap import DisplayTap, publish_semantic


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
    assert len(tui_rows) == 1
    assert tui_rows[0]["id"] != "web-a"
    assert tui_rows[0]["status"] in {"joined", "completed"}
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
    processor._end_dropped(
        TaggedInput("later input", ("voice-dropped",)),
        "Queue full",
    )

    snapshot = stream.subscribe({})["events"][0]["data"]
    turns = {turn["turn_id"]: turn for turn in snapshot["turns"]}
    inputs = {item["id"]: item for item in snapshot["inputs"]}
    assert turns["native-active"]["state"] == "running"
    assert turns["native-active"]["input_ids"] == ["web-active"]
    assert inputs["web-active"]["state"] == "started"
    assert inputs["voice-dropped"]["state"] == "ended"
    assert inputs["voice-dropped"].get("turn_id") is None
    events = _events(stream, initial)
    assert sequence_problems(events, complete=False) == []
