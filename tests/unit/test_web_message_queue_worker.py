"""Lifecycle tests for the daemon-owned WebUI queue worker boundary."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from kollabor.state import web_message_queue as queue_module
from kollabor.state.web_message_queue import (
    BatchStatus,
    ClaimBusy,
    ClaimNotFound,
    ClaimOutcome,
    ClaimPayload,
    IdempotencyConflict,
    ItemStatus,
    QueueInDoubt,
    WebMessage,
    WebMessageQueue,
)
from kollabor.state.web_message_queue_worker import (
    AdmissionAccepted,
    AdmissionRejectedBeforeCustody,
    ConversationIdentity,
    RecordingStatus,
    WebMessageQueueWorker,
    WorkerStatus,
)


def _identity(uid: str = "conversation-a", revision: int = 7) -> ConversationIdentity:
    return ConversationIdentity(uid=uid, revision=revision)


async def _enqueue(
    queue: WebMessageQueue, *payloads: bytes
) -> tuple[str, tuple[str, ...]]:
    batch_id = queue.new_batch_id()
    items = tuple(WebMessage(queue.new_request_id(), body) for body in payloads)
    await queue.enqueue_batch(queue.new_operation_id(), batch_id, items)
    return batch_id, tuple(item.request_id for item in items)


def _accepted(request) -> AdmissionAccepted:
    return AdmissionAccepted(
        conversation_uid=request.conversation_uid,
        conversation_revision=request.conversation_revision,
        request_id=request.request_id,
        custody_id=f"custody-{request.request_id}",
        recording_status=RecordingStatus.UNKNOWN,
    )


@pytest.mark.asyncio
async def test_worker_dispatches_only_item_payload_in_order_and_settles_custody(
    monkeypatch,
):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"first private", b"second private")
    identity = _identity()
    calls = []

    async def dispatch(request):
        calls.append(request)
        return _accepted(request)

    async def forbidden_snapshot():
        raise AssertionError("snapshot is not a dispatch source")

    read_snapshot = queue.snapshot
    monkeypatch.setattr(queue, "snapshot", forbidden_snapshot)
    worker = WebMessageQueueWorker(queue, identity, lambda: identity, dispatch)

    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.ADMITTED
    assert [request.request_id for request in calls] == list(request_ids)
    assert [request.payload for request in calls] == [
        b"first private",
        b"second private",
    ]
    assert all(request.conversation_uid == identity.uid for request in calls)
    assert all(request.conversation_revision == identity.revision for request in calls)
    assert all(request.queue_scope_id == queue.scope_id for request in calls)
    assert all(request.queue_incarnation == queue.incarnation for request in calls)
    assert all(request.attempt_id for request in calls)
    assert all(request.claim_id for request in calls)
    assert all(request.batch_id == batch_id for request in calls)
    assert all(request.queue_revision > 0 for request in calls)
    assert result.items[0].recording_status is RecordingStatus.UNKNOWN
    assert b"first private" not in repr(result).encode()
    assert (await read_snapshot()).batches == ()


@pytest.mark.asyncio
async def test_send_now_batch_uses_one_gate_for_the_entire_ordered_batch():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"first", b"second", b"third")
    identity = _identity()
    events = []
    lease = object()

    class BatchDispatcher:
        async def begin_batch(self, request):
            events.append(("begin", request.request_ids))
            return lease

        async def __call__(self, request):
            events.append(
                (
                    "dispatch",
                    request.request_id,
                    request.send_now,
                    getattr(request, "batch_lease", None),
                )
            )
            return _accepted(request)

        def finish_batch(self, batch_lease, *, accepted_count, uncertain):
            events.append(("finish", batch_lease, accepted_count, uncertain))
            return True

    worker = WebMessageQueueWorker(
        queue, identity, lambda: identity, BatchDispatcher()
    )

    result = await worker.dispatch_batch(batch_id, send_now=True)

    assert result.status is WorkerStatus.ADMITTED
    assert events == [
        ("begin", request_ids),
        ("dispatch", request_ids[0], True, lease),
        ("dispatch", request_ids[1], True, lease),
        ("dispatch", request_ids[2], True, lease),
        ("finish", lease, 3, False),
    ]


@pytest.mark.asyncio
async def test_failed_send_now_finalizer_retains_the_lease_and_blocks_another_run():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"first")
    identity = _identity()
    lease = object()
    calls = []
    finish_results = iter((False, True))
    finish_calls = 0

    class FinalizerFailureDispatcher:
        async def begin_batch(self, request):
            return lease

        async def __call__(self, request):
            calls.append(request.request_id)
            return _accepted(request)

        def finish_batch(self, batch_lease, *, accepted_count, uncertain):
            nonlocal finish_calls
            finish_calls += 1
            assert batch_lease is lease
            assert accepted_count == 1
            assert uncertain is False
            return next(finish_results)

    worker = WebMessageQueueWorker(
        queue, identity, lambda: identity, FinalizerFailureDispatcher()
    )

    result = await worker.dispatch_batch(batch_id, send_now=True)
    blocked = await worker.dispatch_batch(batch_id, send_now=True)
    retained_lease = worker._dispatch_batch_lease
    closed = await worker.close()

    assert result.status is WorkerStatus.IN_DOUBT
    assert blocked.status is WorkerStatus.IN_DOUBT
    assert retained_lease is lease
    assert worker._dispatch_batch_lease is None
    assert worker._forced_in_doubt is True
    assert closed is True
    assert finish_calls == 2
    assert calls == [request_ids[0]]
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_payload_metadata_mismatch_is_proven_not_sent():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"first", b"second")
    original_item_payload = queue.item_payload
    called = False

    async def mismatched_payload(attempt_id):
        payload = await original_item_payload(attempt_id)
        return ClaimPayload(
            scope_id=payload.scope_id,
            incarnation=payload.incarnation,
            claim_id=payload.claim_id,
            attempt_id=payload.attempt_id,
            batch_id=payload.batch_id,
            items=(WebMessage("wrong-request-id", payload.items[0].payload),),
        )

    async def dispatch(request):
        nonlocal called
        called = True
        return _accepted(request)

    queue.item_payload = mismatched_payload
    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert not called
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids
    assert all(item.status is ItemStatus.QUEUED for item in snapshot.batches[0].items)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_uid", "response_revision"),
    (("different-conversation", 7), ("conversation-a", 8)),
)
async def test_mismatched_custody_identity_is_in_doubt_and_never_retried(
    response_uid, response_revision
):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    calls = []

    async def dispatch(request):
        calls.append(request)
        accepted = _accepted(request)
        return AdmissionAccepted(
            conversation_uid=response_uid,
            conversation_revision=response_revision,
            request_id=accepted.request_id,
            custody_id=accepted.custody_id,
        )

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.IN_DOUBT
    assert len(calls) == 1
    snapshot = await queue.snapshot()
    item = snapshot.batches[0].items[0]
    assert item.request_id == request_ids[0]
    assert item.status is ItemStatus.IN_DOUBT
    with pytest.raises(QueueInDoubt):
        await worker.dispatch_batch(batch_id)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_explicit_pre_admission_rejection_requeues_current_and_suffix():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"first", b"second")
    calls = []

    async def dispatch(request):
        calls.append(request)
        return AdmissionRejectedBeforeCustody(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            reason_code="permission-denied-before-admission",
        )

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert len(calls) == 1
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].status is BatchStatus.QUEUED
    assert snapshot.batches[0].request_ids == request_ids
    assert [item.status for item in snapshot.batches[0].items] == [
        ItemStatus.QUEUED,
        ItemStatus.QUEUED,
    ]


@pytest.mark.asyncio
async def test_malformed_rejection_receipt_is_reported_in_doubt():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, _ = await _enqueue(queue, b"private")

    async def dispatch(request):
        return AdmissionRejectedBeforeCustody(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            reason_code="not a safe code",
        )

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.IN_DOUBT
    assert result.items[0].outcome.value == "in_doubt"
    assert result.items[0].reason_code is None
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT


@pytest.mark.asyncio
async def test_admission_is_separate_from_failed_history_recording():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, _ = await _enqueue(queue, b"private")

    async def dispatch(request):
        return AdmissionAccepted(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            custody_id="accepted-before-history-error",
            recording_status=RecordingStatus.FAILED,
        )

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.ADMITTED
    assert result.items[0].recording_status is RecordingStatus.FAILED
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_rejection_after_accepted_prefix_preserves_only_unaccepted_suffix():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"one", b"two", b"three")
    calls = []

    async def dispatch(request):
        calls.append(request)
        if len(calls) == 1:
            return _accepted(request)
        return AdmissionRejectedBeforeCustody(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            reason_code="permission-denied",
        )

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert [request.request_id for request in calls] == list(request_ids[:2])
    assert [item.outcome.value for item in result.items] == ["accepted", "not_sent"]
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids[1:]
    assert all(item.status is ItemStatus.QUEUED for item in snapshot.batches[0].items)


@pytest.mark.asyncio
async def test_dispatch_exception_after_call_is_in_doubt_and_suffix_stays_queued():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"first", b"second")
    calls = []

    async def dispatch(request):
        calls.append(request)
        raise TimeoutError("ack lost after downstream may have accepted")

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.IN_DOUBT
    assert len(calls) == 1
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids
    assert [item.status for item in snapshot.batches[0].items] == [
        ItemStatus.IN_DOUBT,
        ItemStatus.QUEUED,
    ]
    with pytest.raises(QueueInDoubt):
        await worker.dispatch_batch(batch_id)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_partial_batch_stops_at_first_ambiguous_item():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"one", b"two", b"three")
    calls = []

    async def dispatch(request):
        calls.append(request)
        if len(calls) == 1:
            return _accepted(request)
        raise RuntimeError("lost acknowledgement")

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.IN_DOUBT
    assert [request.request_id for request in calls] == list(request_ids[:2])
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids[1:]
    assert [item.status for item in snapshot.batches[0].items] == [
        ItemStatus.IN_DOUBT,
        ItemStatus.QUEUED,
    ]


@pytest.mark.parametrize(
    ("current_uid", "current_revision"),
    (("conversation-b", 8), ("conversation-a", 8)),
)
@pytest.mark.asyncio
async def test_identity_mismatch_before_claim_leaves_batch_queued(
    current_uid, current_revision
):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    called = False

    async def dispatch(request):
        nonlocal called
        called = True
        return _accepted(request)

    worker = WebMessageQueueWorker(
        queue,
        _identity(),
        lambda: _identity(current_uid, current_revision),
        dispatch,
    )

    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert not called
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids
    assert snapshot.batches[0].status is BatchStatus.QUEUED


@pytest.mark.asyncio
async def test_stop_before_claim_does_not_claim_or_dispatch():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    called = False

    async def dispatch(request):
        nonlocal called
        called = True
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    worker.request_stop()
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.STOPPED
    assert not called
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids
    assert snapshot.batches[0].status is BatchStatus.QUEUED


@pytest.mark.asyncio
async def test_identity_change_after_accepted_prefix_requeues_unsubmitted_suffix():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"one", b"two", b"three")
    calls = []
    identity_reads = 0
    identity = _identity()

    async def current_identity():
        nonlocal identity_reads
        identity_reads += 1
        # Initial, pre-claim, and post-payload checks match; the next item is stale.
        return identity if identity_reads <= 3 else _identity("conversation-b", 8)

    async def dispatch(request):
        calls.append(request)
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, identity, current_identity, dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.NOT_SENT
    assert [request.request_id for request in calls] == [request_ids[0]]
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids[1:]
    assert all(item.status is ItemStatus.QUEUED for item in snapshot.batches[0].items)


@pytest.mark.asyncio
async def test_stop_during_dispatch_waits_for_outcome_then_preserves_suffix():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"one", b"two")
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def dispatch(request):
        calls.append(request)
        entered.set()
        await finish.wait()
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    task = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered.wait(), 1)
    worker.request_stop()
    finish.set()
    result = await asyncio.wait_for(task, 1)

    assert result.status is WorkerStatus.STOPPED
    assert [request.request_id for request in calls] == [request_ids[0]]
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == (request_ids[1],)
    assert snapshot.batches[0].items[0].status is ItemStatus.QUEUED


@pytest.mark.asyncio
async def test_cancel_during_dispatch_settles_in_doubt_before_propagating():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, _ = await _enqueue(queue, b"private")
    entered = asyncio.Event()

    async def dispatch(_request):
        entered.set()
        await asyncio.Event().wait()

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    task = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT


@pytest.mark.asyncio
async def test_bounded_close_marks_noncooperative_dispatch_in_doubt_and_ignores_late_ack():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, _ = await _enqueue(queue, b"private")
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def dispatch(request):
        entered.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            # Simulate a dispatcher that cannot be stopped once handed off.
            await finish.wait()
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    task = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered.wait(), 1)

    stopped = await worker.close(timeout=0.01)

    assert stopped is False
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT
    finish.set()
    result = await asyncio.wait_for(task, 1)
    assert result.status is WorkerStatus.IN_DOUBT
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT


@pytest.mark.asyncio
async def test_worker_settlement_survives_expired_claim_operation_lease():
    queue = WebMessageQueue(
        scope_id="conversation-owner-a", operation_retry_lease_seconds=0.001
    )
    batch_id, _ = await _enqueue(queue, b"private")
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def dispatch(request):
        entered.set()
        await finish.wait()
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    task = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.sleep(0.01)
    finish.set()

    result = await asyncio.wait_for(task, 1)

    assert result.status is WorkerStatus.ADMITTED
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_worker_replays_pinned_acceptance_receipt_after_settlement_lease(
    monkeypatch,
):
    now = [1_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        operation_retry_lease_seconds=10 / 1e9,
    )
    batch_id, request_ids = await _enqueue(queue, b"private")
    dispatch_calls = []
    settlement_receipts = []

    async def dispatch(request):
        dispatch_calls.append(request.request_id)
        return _accepted(request)

    settle_item = queue.settle_item_from_worker

    async def lose_settlement_ack(attempt_id, outcome):
        receipt = await settle_item(attempt_id, outcome)
        settlement_receipts.append((attempt_id, outcome, receipt))
        if len(settlement_receipts) == 1:
            # The queue committed, but the worker never received its receipt.
            now[0] += 10
            raise RuntimeError("settlement acknowledgement was lost")
        return receipt

    monkeypatch.setattr(queue, "settle_item_from_worker", lose_settlement_ack)
    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await asyncio.gather(worker.dispatch_batch(batch_id), return_exceptions=True)
    assert len(result) == 1 and isinstance(result[0], RuntimeError)
    assert (await queue.snapshot()).batches == ()

    attempt_id, _, receipt = settlement_receipts[0]
    assert receipt.attempt_id == attempt_id
    with pytest.raises(IdempotencyConflict):
        await queue.settle_item_from_worker(attempt_id, ClaimOutcome.NOT_SENT)
    with pytest.raises(IdempotencyConflict):
        await queue.settle_item(
            queue.new_operation_id(), attempt_id, ClaimOutcome.NOT_SENT
        )
    with pytest.raises(IdempotencyConflict):
        await queue.acknowledge_worker_settlement(
            attempt_id,
            ClaimOutcome.ACCEPTED,
            replace(receipt, status="in_doubt"),
        )
    assert await queue.settle_item_from_worker(
        attempt_id, ClaimOutcome.ACCEPTED
    ) == receipt

    next_batch_id, _ = await _enqueue(queue, b"next")
    with pytest.raises(ClaimBusy):
        await queue.claim_batch(queue.new_operation_id(), next_batch_id)

    assert await worker.close(timeout=0) is True
    await queue.acknowledge_worker_settlement(
        attempt_id, ClaimOutcome.ACCEPTED, receipt
    )
    with pytest.raises(ClaimNotFound):
        await queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)
    with pytest.raises(ClaimNotFound):
        await queue.acknowledge_worker_settlement(
            attempt_id,
            ClaimOutcome.ACCEPTED,
            replace(receipt, status="queued"),
        )
    assert dispatch_calls == [request_ids[0]]
    snapshot = await queue.snapshot()
    assert tuple(batch.batch_id for batch in snapshot.batches) == (next_batch_id,)
    claim = await queue.claim_batch(queue.new_operation_id(), next_batch_id)
    assert claim.status == BatchStatus.DISPATCHING.value
    await queue.release_claim(queue.new_operation_id(), claim.claim_id)


@pytest.mark.asyncio
async def test_worker_cancelled_after_commit_replays_pinned_settlement(monkeypatch):
    now = [1_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        operation_retry_lease_seconds=10 / 1e9,
    )
    batch_id, request_ids = await _enqueue(queue, b"private")
    dispatch_calls = []
    settlement_calls = 0

    async def dispatch(request):
        dispatch_calls.append(request.request_id)
        return _accepted(request)

    settle_item = queue.settle_item_from_worker

    async def cancel_after_commit(attempt_id, outcome):
        nonlocal settlement_calls
        settlement_calls += 1
        if settlement_calls == 1:
            await settle_item(attempt_id, outcome)
            now[0] += 10
            raise asyncio.CancelledError
        if settlement_calls == 2:
            raise RuntimeError("cleanup did not receive settlement receipt")
        return await settle_item(attempt_id, outcome)

    monkeypatch.setattr(queue, "settle_item_from_worker", cancel_after_commit)
    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await asyncio.gather(worker.dispatch_batch(batch_id), return_exceptions=True)

    assert len(result) == 1 and isinstance(result[0], asyncio.CancelledError)
    assert settlement_calls == 2
    assert await worker.close(timeout=0) is True
    assert settlement_calls == 3
    assert dispatch_calls == [request_ids[0]]
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_wrong_item_receipt_ids_are_ambiguous_and_not_replayed():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    calls = []

    async def dispatch(request):
        calls.append(request)
        accepted = _accepted(request)
        return AdmissionAccepted(
            conversation_uid=accepted.conversation_uid,
            conversation_revision=accepted.conversation_revision,
            request_id="wrong-request-id",
            custody_id=accepted.custody_id,
        )

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    result = await worker.dispatch_batch(batch_id)

    assert result.status is WorkerStatus.IN_DOUBT
    assert len(calls) == 1
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].request_id == request_ids[0]
    assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT


@pytest.mark.asyncio
async def test_duplicate_worker_invocations_are_serialized():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, _ = await _enqueue(queue, b"private")
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def dispatch(request):
        calls.append(request)
        entered.set()
        await finish.wait()
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    first = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.sleep(0)
    assert len(calls) == 1
    finish.set()
    second_result = await asyncio.wait_for(second, 1)
    first_result = await asyncio.wait_for(first, 1)

    assert first_result.status is WorkerStatus.ADMITTED
    assert second_result.status is WorkerStatus.BUSY
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_overlapping_retry_does_not_resend_after_proven_rejection():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def dispatch(request):
        calls.append(request)
        entered.set()
        await finish.wait()
        return AdmissionRejectedBeforeCustody(
            conversation_uid=request.conversation_uid,
            conversation_revision=request.conversation_revision,
            request_id=request.request_id,
            reason_code="permission-denied",
        )

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    first = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered.wait(), 1)
    overlapping_retry = await worker.dispatch_batch(batch_id)
    assert overlapping_retry.status is WorkerStatus.BUSY
    finish.set()
    first_result = await asyncio.wait_for(first, 1)

    assert first_result.status is WorkerStatus.NOT_SENT
    assert len(calls) == 1
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids
    assert snapshot.batches[0].status is BatchStatus.QUEUED


@pytest.mark.asyncio
async def test_bounded_close_before_dispatch_requeues_without_calling_dispatcher():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    identity = _identity()
    identity_reads = 0
    pre_dispatch = asyncio.Event()
    never_continue = asyncio.Event()
    calls = []

    async def current_identity():
        nonlocal identity_reads
        identity_reads += 1
        if identity_reads == 3:
            pre_dispatch.set()
            await never_continue.wait()
        return identity

    async def dispatch(request):
        calls.append(request)
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, identity, current_identity, dispatch)
    task = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(pre_dispatch.wait(), 1)

    stopped = await worker.close(timeout=0.01)

    assert stopped is True
    assert task.cancelled()
    assert calls == []
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == request_ids
    assert snapshot.batches[0].status is BatchStatus.QUEUED
    assert snapshot.batches[0].items[0].status is ItemStatus.QUEUED


@pytest.mark.parametrize("blocked_identity_read", (1, 2, 3, 4))
@pytest.mark.asyncio
async def test_close_fences_identity_reader_that_suppresses_cancellation(
    blocked_identity_read,
):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"one", b"two")
    identity = _identity()
    identity_reads = 0
    identity_blocked = asyncio.Event()
    cancellation_seen = asyncio.Event()
    resume_identity = asyncio.Event()
    calls = []

    async def current_identity():
        nonlocal identity_reads
        identity_reads += 1
        if identity_reads == blocked_identity_read:
            identity_blocked.set()
            try:
                await resume_identity.wait()
            except asyncio.CancelledError:
                cancellation_seen.set()
                await resume_identity.wait()
        return identity

    async def dispatch(request):
        calls.append(request.request_id)
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, identity, current_identity, dispatch)
    task = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(identity_blocked.wait(), 1)

    stopped = await worker.close(timeout=0.001)
    assert stopped is False
    await asyncio.wait_for(cancellation_seen.wait(), 1)
    resume_identity.set()
    task_result = await asyncio.wait_for(
        asyncio.gather(task, return_exceptions=True), 1
    )

    assert len(task_result) == 1
    expected_calls = [request_ids[0]] if blocked_identity_read == 4 else []
    assert calls == expected_calls
    assert not isinstance(task_result[0], BaseException)
    expected_status = (
        WorkerStatus.NOT_SENT if blocked_identity_read == 3 else WorkerStatus.CLOSED
    )
    assert task_result[0].status is expected_status
    expected_pending = request_ids[1:] if blocked_identity_read == 4 else request_ids
    snapshot = await queue.snapshot()
    assert len(snapshot.batches) == 1
    assert snapshot.batches[0].status is BatchStatus.QUEUED
    assert snapshot.batches[0].request_ids == expected_pending
    assert all(item.status is ItemStatus.QUEUED for item in snapshot.batches[0].items)


@pytest.mark.asyncio
async def test_cancelled_close_finishes_dispatch_terminalization_before_propagating(
    monkeypatch,
):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, _ = await _enqueue(queue, b"private")
    entered_dispatch = asyncio.Event()
    finish_dispatch = asyncio.Event()
    lock_held = asyncio.Event()
    release_lock = asyncio.Event()
    settlement_started = asyncio.Event()

    async def dispatch(request):
        entered_dispatch.set()
        try:
            await finish_dispatch.wait()
        except asyncio.CancelledError:
            # The provider boundary has been crossed and cannot be stopped.
            await finish_dispatch.wait()
        return _accepted(request)

    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    active = asyncio.create_task(worker.dispatch_batch(batch_id))
    await asyncio.wait_for(entered_dispatch.wait(), 1)

    settle_item = queue.settle_item_from_worker

    async def observe_settlement(attempt_id, outcome):
        settlement_started.set()
        return await settle_item(attempt_id, outcome)

    monkeypatch.setattr(queue, "settle_item_from_worker", observe_settlement)

    async def hold_queue_lock():
        async with queue._lock:
            lock_held.set()
            await release_lock.wait()

    lock_holder = asyncio.create_task(hold_queue_lock())
    await asyncio.wait_for(lock_held.wait(), 1)
    closing = asyncio.create_task(worker.close(timeout=0))
    try:
        await asyncio.wait_for(settlement_started.wait(), 1)
        closing.cancel()
        release_lock.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 1)

        snapshot = await queue.snapshot()
        assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT
        assert snapshot.batches[0].status is BatchStatus.IN_DOUBT
    finally:
        release_lock.set()
        finish_dispatch.set()
        await asyncio.gather(lock_holder, return_exceptions=True)
        active_result = await asyncio.gather(active, return_exceptions=True)

    assert len(active_result) == 1
    assert not isinstance(active_result[0], BaseException)
    assert active_result[0].status is WorkerStatus.IN_DOUBT
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT
    assert snapshot.batches[0].status is BatchStatus.IN_DOUBT


@pytest.mark.asyncio
async def test_close_retries_failed_terminalization_after_worker_exits(monkeypatch):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    entered_dispatch = asyncio.Event()
    finish_dispatch = asyncio.Event()
    calls = []
    settlements = []

    async def dispatch(request):
        calls.append(request.request_id)
        entered_dispatch.set()
        try:
            await finish_dispatch.wait()
        except asyncio.CancelledError:
            # Simulate provider work that cannot be cancelled after admission.
            await finish_dispatch.wait()
        return _accepted(request)

    settle_item = queue.settle_item_from_worker

    async def fail_first_settlement(attempt_id, outcome):
        settlements.append((attempt_id, outcome))
        if len(settlements) <= 2:
            raise RuntimeError("temporary settlement failure")
        return await settle_item(attempt_id, outcome)

    monkeypatch.setattr(queue, "settle_item_from_worker", fail_first_settlement)
    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    active = asyncio.create_task(worker.dispatch_batch(batch_id))
    try:
        await asyncio.wait_for(entered_dispatch.wait(), 1)
        assert await worker.close(timeout=0) is False

        finish_dispatch.set()
        result = await asyncio.wait_for(active, 1)
        assert result.status is WorkerStatus.IN_DOUBT
        snapshot = await queue.snapshot()
        assert snapshot.batches[0].items[0].status is ItemStatus.DISPATCHING

        assert await worker.close(timeout=0) is False
        assert len(settlements) == 2
        assert settlements[1] == settlements[0]
        snapshot = await queue.snapshot()
        assert snapshot.batches[0].items[0].status is ItemStatus.DISPATCHING

        assert await worker.close(timeout=0) is True
        assert len(settlements) == 3
        assert settlements[2] == settlements[0]
        assert calls == [request_ids[0]]

        snapshot = await queue.snapshot()
        assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT
        assert snapshot.batches[0].status is BatchStatus.IN_DOUBT
    finally:
        finish_dispatch.set()
        await asyncio.gather(active, return_exceptions=True)


@pytest.mark.asyncio
async def test_close_retries_original_acceptance_after_precommit_settle_failure(
    monkeypatch,
):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    entered_dispatch = asyncio.Event()
    finish_accept_settlement = asyncio.Event()
    accept_settlement_started = asyncio.Event()
    settlement_outcomes = []
    dispatch_calls = []

    async def dispatch(request):
        entered_dispatch.set()
        dispatch_calls.append(request.request_id)
        return _accepted(request)

    settle_item = queue.settle_item_from_worker

    async def fail_accept_settlement(attempt_id, outcome):
        settlement_outcomes.append(outcome)
        if outcome is ClaimOutcome.ACCEPTED:
            accepted_call = settlement_outcomes.count(ClaimOutcome.ACCEPTED)
            if accepted_call == 1:
                accept_settlement_started.set()
                try:
                    await finish_accept_settlement.wait()
                except asyncio.CancelledError:
                    await finish_accept_settlement.wait()
            if accepted_call <= 2:
                raise RuntimeError("accepted settlement failed before commit")
        return await settle_item(attempt_id, outcome)

    monkeypatch.setattr(queue, "settle_item_from_worker", fail_accept_settlement)
    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    active = asyncio.create_task(worker.dispatch_batch(batch_id))
    try:
        await asyncio.wait_for(entered_dispatch.wait(), 1)
        await asyncio.wait_for(accept_settlement_started.wait(), 1)

        assert await worker.close(timeout=0) is False
        finish_accept_settlement.set()
        result = await asyncio.gather(active, return_exceptions=True)
        assert len(result) == 1 and isinstance(result[0], RuntimeError)

        snapshot = await queue.snapshot()
        assert snapshot.batches[0].request_ids == request_ids
        assert snapshot.batches[0].status is BatchStatus.DISPATCHING
        assert snapshot.batches[0].items[0].status is ItemStatus.DISPATCHING

        assert await worker.close(timeout=0) is True
        assert settlement_outcomes == [
            ClaimOutcome.ACCEPTED,
            ClaimOutcome.ACCEPTED,
            ClaimOutcome.ACCEPTED,
        ]
        assert dispatch_calls == [request_ids[0]]

        snapshot = await queue.snapshot()
        assert snapshot.batches == ()
    finally:
        finish_accept_settlement.set()
        await asyncio.gather(active, return_exceptions=True)


@pytest.mark.asyncio
async def test_close_recovers_settlement_failure_after_dispatch_task_exits(monkeypatch):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    settlement_outcomes = []
    dispatch_calls = []

    async def dispatch(request):
        dispatch_calls.append(request.request_id)
        return _accepted(request)

    settle_item = queue.settle_item_from_worker

    async def fail_first_accept_settlement(attempt_id, outcome):
        settlement_outcomes.append(outcome)
        if outcome is ClaimOutcome.ACCEPTED and settlement_outcomes.count(
            ClaimOutcome.ACCEPTED
        ) == 1:
            raise RuntimeError("accepted settlement failed before commit")
        return await settle_item(attempt_id, outcome)

    monkeypatch.setattr(queue, "settle_item_from_worker", fail_first_accept_settlement)
    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)

    result = await asyncio.gather(worker.dispatch_batch(batch_id), return_exceptions=True)
    assert len(result) == 1 and isinstance(result[0], RuntimeError)

    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].status is ItemStatus.DISPATCHING
    retry = await worker.dispatch_batch(batch_id)
    assert retry.status is WorkerStatus.IN_DOUBT
    assert dispatch_calls == [request_ids[0]]
    assert await worker.close(timeout=0) is True
    assert settlement_outcomes == [ClaimOutcome.ACCEPTED, ClaimOutcome.ACCEPTED]
    assert dispatch_calls == [request_ids[0]]
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_close_replays_committed_acceptance_after_lost_settlement_ack(monkeypatch):
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    batch_id, request_ids = await _enqueue(queue, b"private")
    entered_dispatch = asyncio.Event()
    first_settlement_started = asyncio.Event()
    release_first_settlement = asyncio.Event()
    accepted_settlement_committed = asyncio.Event()
    settlement_outcomes = []
    dispatch_calls = []

    async def dispatch(request):
        dispatch_calls.append(request.request_id)
        entered_dispatch.set()
        return _accepted(request)

    settle_item = queue.settle_item_from_worker

    async def lose_acceptance_ack(attempt_id, outcome):
        settlement_outcomes.append(outcome)
        if outcome is ClaimOutcome.ACCEPTED:
            accepted_call = settlement_outcomes.count(ClaimOutcome.ACCEPTED)
            if accepted_call == 1:
                first_settlement_started.set()
                try:
                    await release_first_settlement.wait()
                except asyncio.CancelledError:
                    await release_first_settlement.wait()
            receipt = await settle_item(attempt_id, outcome)
            assert receipt.status == ClaimOutcome.ACCEPTED.value
            if accepted_call == 2:
                accepted_settlement_committed.set()
            if accepted_call <= 2:
                raise RuntimeError("accepted settlement acknowledgement was lost")
            return receipt
        return await settle_item(attempt_id, outcome)

    monkeypatch.setattr(queue, "settle_item_from_worker", lose_acceptance_ack)
    worker = WebMessageQueueWorker(queue, _identity(), lambda: _identity(), dispatch)
    active = asyncio.create_task(worker.dispatch_batch(batch_id))
    try:
        await asyncio.wait_for(entered_dispatch.wait(), 1)
        await asyncio.wait_for(first_settlement_started.wait(), 1)

        assert await worker.close(timeout=0) is False
        assert accepted_settlement_committed.is_set()
        snapshot = await queue.snapshot()
        assert snapshot.batches == ()

        release_first_settlement.set()
        result = await asyncio.gather(active, return_exceptions=True)
        assert len(result) == 1 and isinstance(result[0], RuntimeError)

        assert await worker.close(timeout=0) is True
        assert settlement_outcomes == [
            ClaimOutcome.ACCEPTED,
            ClaimOutcome.ACCEPTED,
            ClaimOutcome.ACCEPTED,
        ]
        assert dispatch_calls == [request_ids[0]]
        assert (await queue.snapshot()).batches == ()
    finally:
        release_first_settlement.set()
        await asyncio.gather(active, return_exceptions=True)
