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
from kollabor_events.data_models import ConversationMessage
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
            processor.turn_completed = True
        elif stop_reason == "error":
            processor.last_turn_error = "provider failed"
            processor.turn_completed = True
        else:
            processor.turn_completed = stop_reason == "end_turn"
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
    assert [row["status"] for row in live_rows] == ["completed", "completed"]
    assert [part["type"] for part in live_rows[0]["parts"]].count("image") == 1
    assert not any(part["type"] == "image" for part in live_rows[1]["parts"])

    resumed_stream, resumed_uid = await _resume_chat_stream(
        tmp_path, conversation_logger.session_id
    )
    resumed_rows = _input_rows(resumed_stream, resumed_uid)
    assert [row["id"] for row in resumed_rows] == list(request_ids)
    assert [row["status"] for row in resumed_rows] == ["completed", "completed"]
    assert [part["type"] for part in resumed_rows[0]["parts"]].count("image") == 1
    assert not any(part["type"] == "image" for part in resumed_rows[1]["parts"])
    restored_inputs = [
        message
        for message in resumed_stream._state_service._llm_service.conversation_history
        if message.metadata.get("request_id") in request_ids
    ]
    assert [message.metadata["chat_stream_input_count"] for message in restored_inputs] == [
        2,
        2,
    ]


@pytest.mark.asyncio
async def test_historical_untracked_user_row_keeps_its_text_after_resume(tmp_path):
    conversation_logger = KollaborConversationLogger(tmp_path / "conversations")
    await conversation_logger.log_user_message("hello from old history")

    resumed_stream, resumed_uid = await _resume_chat_stream(
        tmp_path, conversation_logger.session_id
    )
    rows = _input_rows(resumed_stream, resumed_uid)

    assert len(rows) == 1
    assert rows[0]["parts"] == [
        {"type": "text", "text": "hello from old history"}
    ]


@pytest.mark.asyncio
async def test_acp_stream_input_count_and_text_survive_resume(tmp_path):
    conversation_logger = KollaborConversationLogger(tmp_path / "conversations")
    request_id = "acp-resume-input"
    await conversation_logger.log_user_message(
        "inspect this change",
        message_metadata={
            "provider": "acp",
            "request_id": request_id,
            "source": "cli",
            "thread_id": "acp-thread",
            "turn_id": 21,
            "chat_stream_input_count": 1,
        },
    )

    resumed_stream, resumed_uid = await _resume_chat_stream(
        tmp_path, conversation_logger.session_id
    )
    rows = _input_rows(resumed_stream, resumed_uid)
    restored = next(
        message
        for message in resumed_stream._state_service._llm_service.conversation_history
        if message.metadata.get("request_id") == request_id
    )

    assert restored.metadata["chat_stream_input_count"] == 1
    assert [row["id"] for row in rows] == [request_id]
    assert rows[0]["parts"] == [
        {"type": "text", "text": "inspect this change"}
    ]


@pytest.mark.asyncio
async def test_structured_hub_input_metadata_and_note_survive_resume(tmp_path):
    conversation_logger = KollaborConversationLogger(tmp_path / "conversations")
    request_id = "hub-resume-input"
    note = {
        "type": "hub-note",
        "from": "peer",
        "to": "koordinator",
        "text": "Please inspect this change.",
        "observed": False,
        "is_direct": True,
    }
    await conversation_logger.log_user_message(
        note["text"],
        message_metadata={
            "provider": "acp",
            "request_id": request_id,
            "source": "hub_message",
            "thread_id": "hub-thread",
            "turn_id": 22,
            "chat_stream_input_count": 1,
            "hub_message": True,
            "hub_message_id": request_id,
            "hub_admission": "input",
            "hub_note": note,
        },
    )

    resumed_stream, resumed_uid = await _resume_chat_stream(
        tmp_path, conversation_logger.session_id
    )
    rows = _input_rows(resumed_stream, resumed_uid)
    restored = next(
        message
        for message in resumed_stream._state_service._llm_service.conversation_history
        if message.metadata.get("request_id") == request_id
    )

    assert restored.metadata["chat_stream_input_count"] == 1
    assert restored.metadata["hub_admission"] == "input"
    assert restored.metadata["hub_note"] == note
    assert [row["id"] for row in rows] == [request_id]
    assert rows[0]["parts"] == [note]


def test_single_input_operator_text_keeps_its_unambiguous_image():
    input_id = "web-single-image"
    history = [
        ConversationMessage(
            role="user",
            content=[
                {"type": "text", "text": "describe this"},
                {
                    "type": "image",
                    "source": {
                        "kind": "url",
                        "url": "https://example.test/single.png",
                    },
                },
            ],
            metadata={
                "request_id": input_id,
                "chat_stream_input_count": 1,
                "operator_inputs": [
                    {
                        "source": "webui",
                        "timestamp": "2026-11-25T12:00:00Z",
                        "text": "describe this",
                        "request_id": input_id,
                    }
                ],
            },
        )
    ]
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_history=history),
        _event_bus=None,
        get_conversation_uid=lambda: "conversation-single-image",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )

    rows = _input_rows(ChatStreamService(state), "conversation-single-image")

    assert [row["id"] for row in rows] == [input_id]
    assert rows[0]["parts"] == [
        {"type": "text", "text": "describe this"},
        {"type": "image", "url": "https://example.test/single.png"},
    ]


def test_single_tracked_id_does_not_claim_image_from_untracked_batch_member():
    input_id = "web-tracked-next-to-untracked"
    history = [
        ConversationMessage(
            role="user",
            content=[
                {"type": "text", "text": "tracked input"},
                {"type": "text", "text": "untracked input"},
                {
                    "type": "image",
                    "source": {
                        "kind": "url",
                        "url": "https://example.test/untracked.png",
                    },
                },
            ],
            metadata={
                "request_id": input_id,
                "request_ids": [input_id],
                "chat_stream_input_count": 2,
                "operator_inputs": [
                    {
                        "source": "webui",
                        "timestamp": "2026-11-25T12:00:00Z",
                        "text": "tracked input",
                        "request_id": input_id,
                    }
                ],
            },
        )
    ]
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_history=history),
        _event_bus=None,
        get_conversation_uid=lambda: "conversation-mixed-tracked-untracked",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )

    rows = _input_rows(
        ChatStreamService(state), "conversation-mixed-tracked-untracked"
    )

    assert [row["id"] for row in rows] == [input_id]
    assert rows[0]["parts"] == [{"type": "text", "text": "tracked input"}]


@pytest.mark.asyncio
async def test_watchdog_ending_a_continuing_native_turn_persists_failure(tmp_path):
    conversation_logger = KollaborConversationLogger(tmp_path / "conversations")
    request_id = "web-watchdog-ending"
    row_uuid = await conversation_logger.log_user_message(
        "keep going", message_metadata={"request_id": request_id}
    )
    processor = _native_processor([], conversation_logger, "continuing")
    processor.active_request_ids = (request_id,)
    await processor.process_message_batch(
        [
            TaggedInput(
                "keep going",
                (request_id,),
                history_row_uuid=row_uuid,
            )
        ],
        "parent-watchdog",
    )
    assert processor.turn_completed is False

    end_requests_result = processor._end_requests("watchdog stopped the turn")
    if asyncio.iscoroutine(end_requests_result):
        await end_requests_result

    resumed_stream, resumed_uid = await _resume_chat_stream(
        tmp_path, conversation_logger.session_id
    )
    rows = _input_rows(resumed_stream, resumed_uid)

    assert [row["id"] for row in rows] == [request_id]
    assert rows[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_native_batch_records_untracked_input_count_for_projection(tmp_path):
    conversation_logger = KollaborConversationLogger(tmp_path / "conversations")
    request_id = "web-tracked-input"
    row_uuid = await conversation_logger.log_user_message(
        "tracked input", message_metadata={"request_id": request_id}
    )
    history = []
    processor = _native_processor(history, conversation_logger, "end_turn")
    operator_input = {
        "source": "webui",
        "timestamp": "2026-11-25T12:00:00Z",
        "text": "tracked input",
        "request_id": request_id,
    }
    await processor.process_message_batch(
        [
            TaggedInput(
                "tracked input",
                (request_id,),
                operator_inputs=(operator_input,),
                history_row_uuid=row_uuid,
            ),
            [
                {"type": "text", "text": "untracked input"},
                {
                    "type": "image",
                    "source": {
                        "kind": "url",
                        "url": "https://example.test/untracked.png",
                    },
                },
            ],
        ],
        "parent-batch",
    )

    assert history[0].metadata["chat_stream_input_count"] == 2
    # Simulate an older combined row without the per-input content map. The
    # count still proves that its image owner is ambiguous.
    history[0].metadata.pop("chat_stream_inputs")
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_history=history),
        _event_bus=None,
        get_conversation_uid=lambda: "conversation-native-batch-count",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )
    rows = _input_rows(ChatStreamService(state), "conversation-native-batch-count")

    assert [row["id"] for row in rows] == [request_id]
    assert rows[0]["parts"] == [{"type": "text", "text": "tracked input"}]
