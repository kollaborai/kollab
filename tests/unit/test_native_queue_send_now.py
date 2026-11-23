"""Native Send Now must stop the old turn before one priority next-turn admission."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.queue_handoff import QueueHandoffContext
from kollabor.state.web_message_queue import WebMessage, WebMessageQueue
from kollabor.state.web_message_queue_worker import (
    AdmissionAccepted,
    ConversationIdentity,
    RecordingStatus,
    WebMessageQueueWorker,
    WorkerStatus,
)
from kollabor_agent.queue_processor import QueueProcessor, TaggedInput


def _identity() -> ConversationIdentity:
    return ConversationIdentity("conversation-a", 4)


@pytest.mark.asyncio
async def test_worker_carries_send_now_to_the_native_admission_boundary():
    queue = WebMessageQueue(scope_id="conversation-a")
    batch_id = queue.new_batch_id()
    request_id = queue.new_request_id()
    await queue.enqueue_batch(
        queue.new_operation_id(), batch_id, (WebMessage(request_id, b'{"text":"B"}'),)
    )
    seen = []
    identity = _identity()

    async def dispatch(request):
        seen.append(request)
        return AdmissionAccepted(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            custody_id="custody-b",
            recording_status=RecordingStatus.RECORDED,
        )

    worker = WebMessageQueueWorker(queue, identity, lambda: identity, dispatch)
    result = await worker.dispatch_batch(batch_id, send_now=True)

    assert result.status is WorkerStatus.ADMITTED
    assert len(seen) == 1
    assert seen[0].send_now is True


def _processor_with_older_input() -> QueueProcessor:
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.processing_queue = asyncio.Queue(maxsize=4)
    processor.max_queue_size = 4
    processor._admission_reservations = {}
    processor._queue_metrics = {
        "total_enqueue_attempts": 0,
        "total_enqueue_successes": 0,
    }
    processor._cancel_generation = 0
    processor._cancel_processing = False
    processor._cancel_origin = None
    processor._is_processing = True
    processor._chain_idle_event = asyncio.Event()
    processor._chain_idle_event.clear()
    processor.note_chain_end = lambda: None
    processor.mark_progress = lambda: None
    processor.processing_queue.put_nowait(TaggedInput("older D", ("older-d",)))
    return processor


@pytest.mark.asyncio
async def test_native_send_now_waits_for_old_turn_then_admits_once_ahead_of_backlog():
    processor = _processor_with_older_input()
    settle_old_turn = asyncio.Event()
    cancel_requested = asyncio.Event()
    events = ["start:A"]

    async def old_turn():
        await settle_old_turn.wait()
        events.append("terminal:A")
        processor.is_processing = False

    old_task = asyncio.create_task(old_turn())

    llm = object.__new__(LLMService)
    llm._queue_processor = processor
    llm._conversation_revision = 4
    llm.claim_turn_serial = lambda: 8
    llm.current_parent_uuid = "parent-a"
    llm.publish_cursor = MagicMock()
    llm.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(
            side_effect=lambda *_args, **_kwargs: events.append("history:B") or "row-b"
        )
    )
    llm.message_display_service = SimpleNamespace(display_user_message=MagicMock())
    llm.create_background_task = MagicMock(side_effect=lambda *_args, **_kwargs: events.append("start:B"))
    llm.supports_image_input = MagicMock(return_value=True)

    def cancel_current_request(*, origin="human", preserve_queued=False):
        events.append("cancel:A")
        generation = processor.request_cancellation(
            origin=origin, preserve_queued=preserve_queued
        )
        cancel_requested.set()
        return generation

    llm.cancel_current_request = MagicMock(side_effect=cancel_current_request)
    original_commit = processor.commit_admission_slot

    def record_commit(*args, **kwargs):
        events.append("admit:B")
        return original_commit(*args, **kwargs)

    processor.commit_admission_slot = record_commit
    handoff = QueueHandoffContext(
        request_id="force-b",
        conversation_uid="conversation-a",
        conversation_revision=4,
        images=(),
        force_send=True,
    )

    admission = asyncio.create_task(
        llm._admit_queue_handoff_native("B", "B", handoff)
    )
    await asyncio.wait_for(cancel_requested.wait(), timeout=1)
    await asyncio.sleep(0)

    assert events == ["start:A", "cancel:A"]
    assert llm.conversation_logger.log_user_message.await_count == 0
    assert [item.content for item in processor.processing_queue._queue] == ["older D"]

    settle_old_turn.set()
    await asyncio.wait_for(old_task, timeout=1)
    result = await asyncio.wait_for(admission, timeout=1)

    assert result == {"status": "queued", "request_id": "force-b"}
    assert events == [
        "start:A",
        "cancel:A",
        "terminal:A",
        "history:B",
        "admit:B",
        "start:B",
    ]
    assert [item.request_ids for item in processor.processing_queue._queue] == [
        ("force-b",),
        ("older-d",),
    ]
    assert handoff.provider_custody_id is not None

