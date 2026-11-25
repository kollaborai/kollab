"""A claimed queue item must be fenced again at real provider admission."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor.state.local import LocalStateService
from kollabor.state.web_message_attachments import (
    AttachmentScopeBinding,
    WebMessageAttachmentRegistry,
)
from kollabor.state.web_message_queue import ClaimOutcome, WebMessage, WebMessageQueue
from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher
from kollabor.state.web_message_queue_worker import (
    ConversationIdentity,
    WebMessageQueueWorker,
    WorkerStatus,
)
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_agent.queue_processor import QueueProcessor
from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_tui.display_tap import DisplayTap


def _native_queue() -> QueueProcessor:
    """A real native admission queue with enough room for a stale send."""
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = DisplayTap()
    processor.mark_progress = lambda: None
    processor.processing_queue = asyncio.Queue(maxsize=4)
    processor.max_queue_size = 4
    processor.dropped_messages = 0
    processor._cancel_generation = 0
    processor._queue_metrics = {
        "total_enqueue_attempts": 0,
        "total_enqueue_successes": 0,
        "drop_oldest_count": 0,
        "drop_newest_count": 0,
        "block_count": 0,
        "block_timeout_count": 0,
    }
    processor.task_config = SimpleNamespace(
        queue=SimpleNamespace(
            overflow_strategy="block", block_timeout=0, log_queue_events=False
        )
    )
    return processor


async def _state_service(processor: QueueProcessor) -> LocalStateService:
    manager = ConversationManager.__new__(ConversationManager)
    manager.current_session_id = "session-a"
    manager.conversation_metadata = {"conversation_uid": "conversation-a"}
    llm = object.__new__(LLMService)
    llm.event_bus = EventBus()
    llm.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai")
    )
    llm._message_handler = MessageHandler(llm)
    llm._queue_processor = processor
    llm.message_display_service = SimpleNamespace(display_user_message=MagicMock())
    llm.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(return_value="parent-2")
    )
    llm.current_parent_uuid = "parent-1"
    llm.is_processing = True
    llm.create_background_task = MagicMock()
    llm.tool_executor = SimpleNamespace(
        yes_approvals=SimpleNamespace(on_human_input=MagicMock())
    )
    llm.conversation_manager = manager
    await llm.event_bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=llm._handle_user_input,
        )
    )
    return LocalStateService(llm, profile_manager=object(), event_bus=None)


@pytest.mark.asyncio
async def test_conversation_switch_between_worker_preflight_and_dispatch_is_not_sent():
    processor = _native_queue()
    state = await _state_service(processor)
    old_identity = state.get_conversation_identity()
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    request_id = queue.new_request_id()
    batch_id = queue.new_batch_id()
    await queue.enqueue_batch(
        queue.new_operation_id(),
        batch_id,
        (WebMessage(request_id, b'{"text":"queued for old conversation"}'),),
    )
    binding = AttachmentScopeBinding(queue.scope_id, queue.incarnation)
    attachments = WebMessageAttachmentRegistry(
        resolve_scope=lambda _params, _caller: binding,
        validate_owner_id=lambda _binding, _owner: True,
    )
    real_dispatch = WebMessageQueueDispatcher(state, attachments)

    async def switch_then_dispatch(request):
        # The worker has already passed its preflight identity check. Change
        # the real conversation identity immediately before the adapter's
        # atomic admission recheck.
        async with state._llm_service.conversation_transition():
            state._llm_service.conversation_manager.reset_session("session-b")
        return await real_dispatch(request)

    worker = WebMessageQueueWorker(
        queue,
        ConversationIdentity(old_identity.uid, old_identity.revision),
        state.get_conversation_identity,
        switch_then_dispatch,
    )

    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert result.items[0].request_id == request_id
    assert result.items[0].outcome is ClaimOutcome.NOT_SENT
    assert state.get_conversation_uid() != old_identity.uid
    assert processor.processing_queue.empty(), (
        "stale item reached native provider custody"
    )
    assert state._llm_service.conversation_logger.log_user_message.await_count == 0
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].request_id == request_id
    assert snapshot.batches[0].items[0].status.value == "queued"
