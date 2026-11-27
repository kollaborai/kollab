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
from kollabor.user_input_source import UserInputSource
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
    lease = object()

    class Dispatcher:
        async def begin_batch(self, request):
            assert request.request_ids == (request_id,)
            return lease

        async def __call__(self, request):
            seen.append(request)
            return AdmissionAccepted(
                conversation_uid=request.conversation_uid,
                conversation_revision=request.conversation_revision,
                request_id=request.request_id,
                custody_id="custody-b",
                recording_status=RecordingStatus.RECORDED,
            )

        def finish_batch(self, batch_lease, *, accepted_count, uncertain):
            assert batch_lease is lease
            assert accepted_count == 1
            assert uncertain is False
            return True

    worker = WebMessageQueueWorker(queue, identity, lambda: identity, Dispatcher())
    result = await worker.dispatch_batch(batch_id, send_now=True)

    assert result.status is WorkerStatus.ADMITTED
    assert len(seen) == 1
    assert seen[0].send_now is True
    assert seen[0].batch_lease is lease


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
    processor.active_request_ids = ("active-a",)
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
    llm.conversation_manager = SimpleNamespace(conversation_uid="conversation-a")
    llm.claim_turn_serial = lambda: 8
    llm.current_parent_uuid = "parent-a"
    llm.publish_cursor = MagicMock()

    async def append_transactional_user_history(
        _content, *, parent_uuid, message_metadata, queue_transaction
    ):
        row = {
            "uuid": "row-b",
            "parentUuid": parent_uuid,
            "message": {"metadata": message_metadata},
        }
        if queue_transaction["prepare"]("row-b", row) is not True:
            from kollabor_ai.conversation_logger import ConversationAppendRejected

            raise ConversationAppendRejected("admission rejected")
        events.append("history:B")
        queue_transaction["finalize"]("row-b", row)
        return "row-b"

    logger_mock = AsyncMock(side_effect=append_transactional_user_history)
    llm.conversation_logger = SimpleNamespace(log_user_message=logger_mock)
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

    async def force_submit(
        message,
        *,
        source,
        pre_displayed,
        request_id,
        thread_id,
        origin_context,
        queue_handoff,
        force_next_turn,
        _force_gate_held,
        _force_reservation,
        _force_gate_token,
    ):
        assert source is UserInputSource.STATE_RPC
        assert force_next_turn and _force_gate_held
        assert _force_gate_token is not None
        return await LLMService._admit_queue_handoff_native(
            llm,
            message,
            "B",
            queue_handoff,
            force_reservation=_force_reservation,
        )

    llm.submit_human_input = force_submit
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
        attempt_id="attempt-force-b",
        force_send=True,
        queue_scope_id="queue-scope",
        queue_incarnation="queue-incarnation",
        batch_id="batch-force-b",
        claim_id="claim-force-b",
    )

    admission = asyncio.create_task(
        llm._submit_forced_human_input(
            "B",
            source=UserInputSource.STATE_RPC,
            pre_displayed=False,
            request_id="force-b",
            thread_id=None,
            origin_context=None,
            queue_handoff=handoff,
        )
    )
    await asyncio.wait_for(cancel_requested.wait(), timeout=1)
    await asyncio.sleep(0)

    assert events == ["start:A", "cancel:A"]
    assert logger_mock.await_count == 0
    assert [item.content for item in processor.processing_queue._queue] == ["older D"]

    settle_old_turn.set()
    await asyncio.wait_for(old_task, timeout=1)
    result = await asyncio.wait_for(admission, timeout=1)

    assert result == {"status": "queued", "request_id": "force-b"}
    assert events == [
        "start:A",
        "cancel:A",
        "terminal:A",
        "admit:B",
        "history:B",
        "start:B",
    ]
    assert [item.request_ids for item in processor.processing_queue._queue] == [
        ("force-b",),
        ("older-d",),
    ]
    assert handoff.provider_custody_id is not None


@pytest.mark.asyncio
async def test_forced_batch_priority_admissions_keep_fifo_ahead_of_older_queue_work():
    processor = _processor_with_older_input()
    token = await processor.begin_forced_send()
    first = processor.reserve_admission_slot()
    second = processor.reserve_admission_slot()
    assert first is not None and second is not None

    assert processor.commit_admission_slot(
        first, TaggedInput("B", ("forced-b",)), priority=True
    )
    assert processor.commit_admission_slot(
        second, TaggedInput("C", ("forced-c",)), priority=True
    )

    assert [item.request_ids for item in processor.processing_queue._queue] == [
        ("forced-b",),
        ("forced-c",),
        ("older-d",),
    ]
    assert processor.end_forced_send(token)


@pytest.mark.asyncio
async def test_forced_batch_stops_matching_owner_before_admitting_all_members_once():
    processor = _processor_with_older_input()
    terminal = asyncio.Event()
    events = ["start:A"]
    old_task = asyncio.create_task(
        _settle_forced_test_turn(processor, terminal, events)
    )

    llm = object.__new__(LLMService)
    llm._queue_processor = processor
    llm.is_processing = True
    llm.create_background_task = MagicMock(side_effect=lambda *_a, **_k: events.append("process:B,C"))

    def cancel_current_request(*, origin="human", preserve_queued=False):
        events.append("cancel:A")
        return processor.request_cancellation(
            origin=origin, preserve_queued=preserve_queued
        )

    llm.cancel_current_request = MagicMock(side_effect=cancel_current_request)

    async def wait_for_stop(_generation):
        await terminal.wait()
        events.append("terminal:A")
        processor.is_processing = False
        return True

    llm._await_forced_native_stop = wait_for_stop

    async def admit(index, reservation, gate_token):
        label = ("B", "C")[index]
        request_id = ("forced-b", "forced-c")[index]
        assert processor.owns_forced_send(gate_token)
        events.append(f"admit:{label}")
        assert processor.commit_admission_slot(
            reservation, TaggedInput(label, (request_id,)), priority=True
        )
        return {"accepted": True, "request_id": request_id}

    admission = asyncio.create_task(
        llm.submit_forced_human_batch(
            count=2,
            expected_active_request_ids=("active-a",),
            submit_one=admit,
        )
    )
    await asyncio.sleep(0)

    assert events == ["start:A", "cancel:A"]
    assert [item.request_ids for item in processor.processing_queue._queue] == [
        ("older-d",),
    ]

    terminal.set()
    await asyncio.wait_for(old_task, timeout=1)
    result = await asyncio.wait_for(admission, timeout=1)

    assert result == {
        "accepted": True,
        "complete": True,
        "accepted_count": 2,
        "reason": None,
        "results": [
            {"accepted": True, "request_id": "forced-b"},
            {"accepted": True, "request_id": "forced-c"},
        ],
    }
    assert events == [
        "start:A",
        "cancel:A",
        "terminal:A",
        "admit:B",
        "admit:C",
        "process:B,C",
    ]
    assert [item.request_ids for item in processor.processing_queue._queue] == [
        ("forced-b",),
        ("forced-c",),
        ("older-d",),
    ]
    llm.cancel_current_request.assert_called_once_with(
        origin="human", preserve_queued=True
    )


async def _settle_forced_test_turn(processor, terminal, events):
    await terminal.wait()
    processor.is_processing = False
