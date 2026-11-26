"""Native input outcomes and attachments survive JSONL reload."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.state.chat_stream import ChatStreamService
from kollabor.state.local import LocalStateService
from kollabor_agent.queue_processor import QueueProcessor, TaggedInput
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events import EventBus
from kollabor_tui.display_tap import DisplayTap


def _manager_config(conversations_dir):
    return SimpleNamespace(
        _conversations_dir=conversations_dir,
        get=lambda key, default=None: (
            False if key == "kollabor.llm.save_conversations" else default
        ),
    )


def _native_processor(history, conversation_logger, stop_reason):
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = DisplayTap()
    processor.conversation_logger = conversation_logger
    processor.processing_queue = asyncio.Queue()
    processor._cancel_processing = False
    processor._cancel_generation = 0
    processor.turn_completed = False
    processor.last_turn_error = None
    processor.last_turn_error_code = None
    processor.last_turn_error_class = None
    processor.api_service = SimpleNamespace(last_stop_reason="")
    processor.active_voice = None
    processor.active_origin_context = None
    processor._open_voice_turn = None
    processor._stop_hook_continuations = 0
    processor._stop_hook_active = False
    processor._first_turn_nudge_budget = 0
    processor._last_tool_error_sig = None
    processor.active_request_ids = ()
    processor._after_turn = []
    processor._turn_token = None
    processor.tool_executor = SimpleNamespace(yes_approvals=None)
    processor._add_message_fn = lambda message, **_: history.append(message)

    async def execute_turn(
        user_message_provided,
        current_parent_uuid,
        execution_context=None,
        request_ids=(),
    ):
        assert user_message_provided is True
        if stop_reason == "cancelled":
            processor._cancel_processing = True
        else:
            processor.last_turn_error = "provider failed"
        processor.turn_completed = True
        return current_parent_uuid

    processor._execute_llm_turn = execute_turn
    return processor


async def _resume_chat_stream(tmp_path, session_id):
    conversation_dir = tmp_path / "conversations"
    logger = KollaborConversationLogger(conversation_dir)
    manager = ConversationManager(_manager_config(conversation_dir), logger)
    llm = SimpleNamespace(
        conversation_manager=manager,
        conversation_logger=logger,
        conversation_history=[],
    )
    bus = EventBus()
    bus.register_service("display_tap", DisplayTap())
    bus.register_service("llm_service", llm)
    state = LocalStateService(llm, None, event_bus=bus)
    await state.resume_conversation(session_id)
    return state.chat_stream, state.get_conversation_uid()


def _input_rows(stream, uid):
    return [
        row
        for row in stream._transcript_rows(uid)
        if row["kind"] == "input"
    ]


async def _run_native_batch(tmp_path, stop_reason):
    conversation_logger = KollaborConversationLogger(tmp_path / "conversations")
    request_ids = ("web-image-a", "web-image-b")
    row_uuids = [
        await conversation_logger.log_user_message(
            text,
            message_metadata={"request_id": request_id},
        )
        for request_id, text in zip(request_ids, ("has image", "text only"))
    ]
    history = []
    processor = _native_processor(history, conversation_logger, stop_reason)
    processor._add_message_fn = lambda message, **_: history.append(message)
    image = {
        "type": "image",
        "source": {"kind": "url", "url": "https://example.test/only-a.png"},
    }
    messages = [
        TaggedInput(
            [{"type": "text", "text": "has image"}, image],
            (request_ids[0],),
            operator_inputs=(
                {
                    "source": "webui",
                    "timestamp": "2026-11-25T12:00:00Z",
                    "text": "has image",
                    "request_id": request_ids[0],
                },
            ),
            history_row_uuid=row_uuids[0],
        ),
        TaggedInput(
            "text only",
            (request_ids[1],),
            operator_inputs=(
                {
                    "source": "webui",
                    "timestamp": "2026-11-25T12:00:01Z",
                    "text": "text only",
                    "request_id": request_ids[1],
                },
            ),
            history_row_uuid=row_uuids[1],
        ),
    ]

    await processor.process_message_batch(messages, "parent-1")

    # The live transcript is built from the real native batch producer's
    # combined history message. An attachment belongs to its original input.
    live_state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_history=history),
        _event_bus=None,
        get_conversation_uid=lambda: "conversation-live",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )
    live_stream = ChatStreamService(live_state)
    return conversation_logger, request_ids, live_stream


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "expected_outcome"),
    [("error", "failed"), ("cancelled", "cancelled")],
)
async def test_native_terminal_outcome_survives_real_jsonl_resume(
    tmp_path, stop_reason, expected_outcome
):
    conversation_logger, request_ids, live_stream = await _run_native_batch(
        tmp_path, stop_reason
    )
    live_rows = _input_rows(live_stream, "conversation-live")
    assert [row["id"] for row in live_rows] == list(request_ids)
    assert [row["status"] for row in live_rows] == [
        expected_outcome,
        expected_outcome,
    ]

    resumed_stream, resumed_uid = await _resume_chat_stream(
        tmp_path, conversation_logger.session_id
    )
    resumed_rows = _input_rows(resumed_stream, resumed_uid)
    assert [row["id"] for row in resumed_rows] == list(request_ids)
    assert [row["status"] for row in resumed_rows] == [
        expected_outcome,
        expected_outcome,
    ]


@pytest.mark.asyncio
async def test_native_batch_keeps_each_image_on_its_original_input_after_resume(
    tmp_path,
):
    conversation_logger, request_ids, live_stream = await _run_native_batch(
        tmp_path, "end_turn"
    )
    live_rows = _input_rows(live_stream, "conversation-live")
    assert [row["id"] for row in live_rows] == list(request_ids)
    assert [part["type"] for part in live_rows[0]["parts"]].count("image") == 1
    assert not any(part["type"] == "image" for part in live_rows[1]["parts"])

    resumed_stream, resumed_uid = await _resume_chat_stream(
        tmp_path, conversation_logger.session_id
    )
    resumed_rows = _input_rows(resumed_stream, resumed_uid)
    assert [row["id"] for row in resumed_rows] == list(request_ids)
    assert [part["type"] for part in resumed_rows[0]["parts"]].count("image") == 1
    assert not any(part["type"] == "image" for part in resumed_rows[1]["parts"])
