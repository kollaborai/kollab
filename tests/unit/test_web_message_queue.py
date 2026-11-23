"""Contract tests for the daemon-owned WebUI message queue primitive."""

from __future__ import annotations

import asyncio
from weakref import WeakKeyDictionary

import pytest
from kollabor_engine.routes.messages import _CLIENT_REQUEST_ID

from kollabor.state import web_message_queue as queue_module
from kollabor.state.web_message_queue import (
    BatchStatus,
    CapacityExceeded,
    ClaimBusy,
    ClaimNotDispatchable,
    ClaimNotFound,
    ClaimOutcome,
    IdempotencyConflict,
    IdentifierLedgerFull,
    InvalidIdentifier,
    ItemNotQueued,
    ItemStatus,
    OperationLedgerFull,
    QueueEmpty,
    QueueInDoubt,
    QueueReceipt,
    StaleIdentifier,
    StaleOperation,
    WebMessage,
    WebMessageQueue,
)

_operation_ids = WeakKeyDictionary()


def _op(queue: WebMessageQueue, label: str) -> str:
    """Give each logical test operation one queue-scoped stable token."""
    ids = _operation_ids.setdefault(queue, {})
    if label not in ids:
        ids[label] = queue.new_operation_id()
    return ids[label]


_entity_ids = WeakKeyDictionary()


def _entity_id(queue: WebMessageQueue, kind: str, label: str) -> str:
    ids = _entity_ids.setdefault(queue, {})
    if label not in ids:
        factory = queue.new_batch_id if kind == "batch" else queue.new_request_id
        ids[label] = factory()
    return ids[label]


def _batch_id(queue: WebMessageQueue, label: str) -> str:
    return _entity_id(queue, "batch", label)


def _request_id(queue: WebMessageQueue, label: str) -> str:
    return _entity_id(queue, "item", label)


def test_queue_request_ids_pass_assistant_transport_predicate_unchanged():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    operation_id = queue.new_operation_id()
    batch_id = queue.new_batch_id()
    request_id = queue.new_request_id()

    if isinstance(request_id, str) and _CLIENT_REQUEST_ID.fullmatch(request_id):
        transport_request_id = request_id
    else:
        transport_request_id = "transport-replacement"

    assert transport_request_id == request_id
    assert _CLIENT_REQUEST_ID.fullmatch(operation_id)
    assert _CLIENT_REQUEST_ID.fullmatch(batch_id)


@pytest.mark.asyncio
async def test_tampered_queue_token_is_rejected_before_mutation():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    operation_id = queue.new_operation_id()
    replacement = "0" if operation_id[-1] != "0" else "1"
    altered_operation_id = operation_id[:-1] + replacement

    with pytest.raises(InvalidIdentifier):
        await queue.claim_next(altered_operation_id)

    parts = operation_id.split("-")
    forged_operation_id = "-".join((parts[0], parts[1], parts[2], "0" * 32, "0" * 32))
    with pytest.raises(InvalidIdentifier):
        await queue.claim_next(forged_operation_id)

    assert (await queue.snapshot()).revision == 0


@pytest.mark.asyncio
async def test_enqueue_batch_is_ordered_atomic_and_idempotent_without_exposing_payloads():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    items = (
        WebMessage(_request_id(queue, "request-1"), b"first private body"),
        WebMessage(_request_id(queue, "request-2"), b"second private body"),
    )

    first, replay = await asyncio.gather(
        queue.enqueue_batch(
            _op(queue, "op-enqueue-1"), _batch_id(queue, "batch-1"), items
        ),
        queue.enqueue_batch(
            _op(queue, "op-enqueue-1"), _batch_id(queue, "batch-1"), items
        ),
    )
    snapshot = await queue.snapshot()

    assert first == replay
    assert first.scope_id == "conversation-owner-a"
    assert first.revision == 1
    assert snapshot.revision == 1
    assert len(snapshot.batches) == 1
    assert snapshot.batches[0].batch_id == _batch_id(queue, "batch-1")
    assert snapshot.batches[0].request_ids == (
        _request_id(queue, "request-1"),
        _request_id(queue, "request-2"),
    )
    assert tuple(item.payload for item in snapshot.batches[0].items) == (
        b"first private body",
        b"second private body",
    )
    assert "private body" not in repr(snapshot)
    assert "private body" not in repr(first)

    with pytest.raises(IdempotencyConflict):
        await queue.enqueue_batch(
            _op(queue, "op-enqueue-1"),
            _batch_id(queue, "batch-1"),
            (WebMessage(_request_id(queue, "request-1"), b"changed private body"),),
        )
    assert (await queue.snapshot()).revision == 1


@pytest.mark.asyncio
async def test_oversized_iterable_is_bounded_before_materialization():
    queue = WebMessageQueue(scope_id="conversation-owner-a", max_batch_items=2)
    consumed = 0

    def endless_items():
        nonlocal consumed
        while True:
            consumed += 1
            if consumed > 3:
                raise AssertionError("queue consumed beyond its bounded lookahead")
            yield WebMessage(
                _request_id(queue, f"request-endless-{consumed}"), b"message"
            )

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            _op(queue, "op-endless-generator"),
            _batch_id(queue, "batch-endless-generator"),
            endless_items(),
        )

    assert consumed == 3
    assert (await queue.snapshot()).revision == 0


@pytest.mark.asyncio
async def test_response_loss_retry_returns_committed_receipt_without_duplicating_batch():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    items = (
        WebMessage(_request_id(queue, "request-1"), b"first"),
        WebMessage(_request_id(queue, "request-2"), b"second"),
    )

    committed = await queue.enqueue_batch(
        _op(queue, "op-response-lost"), _batch_id(queue, "batch-1"), items
    )
    # The first response is treated as lost; retrying the same operation is safe.
    retried = await queue.enqueue_batch(
        _op(queue, "op-response-lost"), _batch_id(queue, "batch-1"), items
    )

    assert retried == committed
    snapshot = await queue.snapshot()
    assert snapshot.revision == committed.revision
    assert tuple(batch.batch_id for batch in snapshot.batches) == (
        _batch_id(queue, "batch-1"),
    )
    assert snapshot.batches[0].request_ids == (
        _request_id(queue, "request-1"),
        _request_id(queue, "request-2"),
    )


@pytest.mark.asyncio
async def test_capacity_rejection_leaves_a_multi_item_batch_uncommitted():
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        max_items=2,
        max_batch_items=3,
        max_queue_bytes=64,
        max_payload_bytes=16,
    )
    await queue.enqueue_batch(
        _op(queue, "op-existing"),
        _batch_id(queue, "batch-existing"),
        (
            WebMessage(_request_id(queue, "request-existing-1"), b"1234567890123456"),
            WebMessage(_request_id(queue, "request-existing-2"), b"abcdefghijklmnop"),
        ),
    )
    before = await queue.snapshot()

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            _op(queue, "op-too-large"),
            _batch_id(queue, "batch-too-large"),
            (
                WebMessage(_request_id(queue, "request-new-1"), b"1234567890"),
                WebMessage(_request_id(queue, "request-new-2"), b"abcdefghij"),
            ),
        )

    after = await queue.snapshot()
    assert after.revision == before.revision
    assert tuple(batch.batch_id for batch in after.batches) == (
        _batch_id(queue, "batch-existing"),
    )
    assert tuple(
        item.request_id for batch in after.batches for item in batch.items
    ) == (
        _request_id(queue, "request-existing-1"),
        _request_id(queue, "request-existing-2"),
    )
    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            _op(queue, "op-payload-too-large"),
            _batch_id(queue, "batch-payload-too-large"),
            (WebMessage(_request_id(queue, "request-large"), b"x" * 17),),
        )
    assert (await queue.snapshot()).revision == before.revision

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            _op(queue, "op-queue-full"),
            _batch_id(queue, "batch-queue-full"),
            (WebMessage(_request_id(queue, "request-full"), b"still blocked"),),
        )
    assert (await queue.snapshot()).revision == before.revision


@pytest.mark.asyncio
async def test_total_payload_byte_bound_rejects_batch_without_partial_commit():
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        max_items=10,
        max_batch_items=3,
        max_queue_bytes=18,
        max_payload_bytes=16,
    )
    await queue.enqueue_batch(
        _op(queue, "op-existing"),
        _batch_id(queue, "batch-existing"),
        (WebMessage(_request_id(queue, "request-existing"), b"12345678"),),
    )
    before = await queue.snapshot()

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            _op(queue, "op-byte-full"),
            _batch_id(queue, "batch-byte-full"),
            (
                WebMessage(_request_id(queue, "request-new-1"), b"123456"),
                WebMessage(_request_id(queue, "request-new-2"), b"abcdef"),
            ),
        )

    after = await queue.snapshot()
    assert after.revision == before.revision
    assert tuple(batch.batch_id for batch in after.batches) == (
        _batch_id(queue, "batch-existing"),
    )
    assert after.batches[0].request_ids == (_request_id(queue, "request-existing"),)


@pytest.mark.asyncio
async def test_partial_send_now_uncertainty_preserves_suffix_and_never_replays_prefix():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-old"),
        _batch_id(queue, "batch-old"),
        (WebMessage(_request_id(queue, "request-old"), b"older"),),
    )
    await queue.enqueue_batch(
        _op(queue, "op-send-now"),
        _batch_id(queue, "batch-send-now"),
        (
            WebMessage(_request_id(queue, "request-now-1"), b"now one"),
            WebMessage(_request_id(queue, "request-now-2"), b"now two"),
            WebMessage(_request_id(queue, "request-now-3"), b"now three"),
        ),
    )

    claim = await queue.claim_batch(
        _op(queue, "op-claim-now"), _batch_id(queue, "batch-send-now"), send_now=True
    )
    assert claim.status == "dispatching"
    assert claim.claim_id == _op(queue, "op-claim-now")
    first, first_replay = await asyncio.gather(
        queue.claim_next_item(_op(queue, "op-attempt-1"), claim.claim_id),
        queue.claim_next_item(_op(queue, "op-attempt-1"), claim.claim_id),
    )
    assert first_replay == first
    first_payload = await queue.item_payload(first.attempt_id)
    assert first.request_ids == (_request_id(queue, "request-now-1"),)
    assert tuple(item.request_id for item in first_payload.items) == (
        _request_id(queue, "request-now-1"),
    )
    assert "now one" not in repr(first_payload)
    with pytest.raises(ClaimBusy):
        await queue.claim_next_item(
            _op(queue, "op-attempt-1-interleaved"), claim.claim_id
        )
    ack_first = await queue.settle_item(
        _op(queue, "op-ack-1"), first.attempt_id, ClaimOutcome.ACCEPTED
    )
    assert (
        await queue.settle_item(
            _op(queue, "op-ack-1"), first.attempt_id, ClaimOutcome.ACCEPTED
        )
        == ack_first
    )
    prefix_replay = await queue.claim_next_item(
        _op(queue, "op-attempt-1"), claim.claim_id
    )
    assert prefix_replay.status == "accepted"
    assert prefix_replay.request_ids == (_request_id(queue, "request-now-1"),)
    with pytest.raises(ClaimNotFound):
        await queue.item_payload(first.attempt_id)

    uncertain = await queue.claim_next_item(_op(queue, "op-attempt-2"), claim.claim_id)
    assert uncertain.request_ids == (_request_id(queue, "request-now-2"),)
    batch_replay = await queue.claim_batch(
        _op(queue, "op-claim-now"), _batch_id(queue, "batch-send-now"), send_now=True
    )
    assert batch_replay.request_ids == (
        _request_id(queue, "request-now-2"),
        _request_id(queue, "request-now-3"),
    )
    await queue.settle_item(
        _op(queue, "op-ack-lost-2"), uncertain.attempt_id, ClaimOutcome.IN_DOUBT
    )

    snapshot = await queue.snapshot()
    assert tuple(batch.batch_id for batch in snapshot.batches) == (
        _batch_id(queue, "batch-send-now"),
        _batch_id(queue, "batch-old"),
    )
    assert snapshot.batches[0].status == "in_doubt"
    assert snapshot.batches[0].request_ids == (
        _request_id(queue, "request-now-2"),
        _request_id(queue, "request-now-3"),
    )
    assert tuple(item.status for item in snapshot.batches[0].items) == (
        ItemStatus.IN_DOUBT,
        ItemStatus.QUEUED,
    )
    assert all(
        item.request_id != _request_id(queue, "request-now-1")
        for batch in snapshot.batches
        for item in batch.items
    )
    assert snapshot.batches[1].status == "queued"
    replay = await queue.claim_next_item(_op(queue, "op-attempt-2"), claim.claim_id)
    assert replay.status == "in_doubt"
    assert replay.revision == snapshot.revision
    with pytest.raises(ClaimNotDispatchable):
        await queue.item_payload(uncertain.attempt_id)
    with pytest.raises(ClaimNotDispatchable):
        await queue.claim_next_item(_op(queue, "op-attempt-3-too-soon"), claim.claim_id)
    with pytest.raises(QueueInDoubt):
        await queue.claim_next(_op(queue, "op-claim-later-while-in-doubt"))

    # Resolving the uncertain item as accepted preserves the unsent suffix.
    await queue.settle_item(
        _op(queue, "op-resolve-2-accepted"), uncertain.attempt_id, ClaimOutcome.ACCEPTED
    )
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].batch_id == _batch_id(queue, "batch-send-now")
    assert snapshot.batches[0].request_ids == (_request_id(queue, "request-now-3"),)
    third = await queue.claim_next_item(_op(queue, "op-attempt-3"), claim.claim_id)
    assert third.request_ids == (_request_id(queue, "request-now-3"),)
    await queue.settle_item(
        _op(queue, "op-ack-3"), third.attempt_id, ClaimOutcome.ACCEPTED
    )

    next_batch = await queue.claim_next(_op(queue, "op-claim-old"))
    assert next_batch.batch_id == _batch_id(queue, "batch-old")
    assert next_batch.request_ids == (_request_id(queue, "request-old"),)


@pytest.mark.asyncio
async def test_definitive_not_sent_releases_a_send_now_batch_without_claiming_it_again():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-old"),
        _batch_id(queue, "batch-old"),
        (WebMessage(_request_id(queue, "request-old"), b"older"),),
    )
    await queue.enqueue_batch(
        _op(queue, "op-priority"),
        _batch_id(queue, "batch-priority"),
        (WebMessage(_request_id(queue, "request-priority"), b"priority"),),
    )
    batch_claim = await queue.claim_batch(
        _op(queue, "op-priority-claim"),
        _batch_id(queue, "batch-priority"),
        send_now=True,
    )
    item_claim = await queue.claim_next_item(
        _op(queue, "op-priority-attempt"), batch_claim.claim_id
    )
    await queue.settle_item(
        _op(queue, "op-priority-not-sent"), item_claim.attempt_id, ClaimOutcome.NOT_SENT
    )

    next_claim = await queue.claim_next(_op(queue, "op-claim-oldest"))
    assert next_claim.batch_id == _batch_id(queue, "batch-old")


@pytest.mark.asyncio
async def test_not_sent_resolution_requeues_uncertain_item_and_suffix_in_order():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-multi"),
        (
            WebMessage(_request_id(queue, "request-1"), b"one"),
            WebMessage(_request_id(queue, "request-2"), b"two"),
            WebMessage(_request_id(queue, "request-3"), b"three"),
        ),
    )
    batch_claim = await queue.claim_next(_op(queue, "op-batch-claim"))
    item_one = await queue.claim_next_item(
        _op(queue, "op-attempt-1"), batch_claim.claim_id
    )
    await queue.settle_item(
        _op(queue, "op-ack-1"), item_one.attempt_id, ClaimOutcome.ACCEPTED
    )
    item_two = await queue.claim_next_item(
        _op(queue, "op-attempt-2"), batch_claim.claim_id
    )
    await queue.settle_item(
        _op(queue, "op-uncertain-2"), item_two.attempt_id, ClaimOutcome.IN_DOUBT
    )

    resolved = await queue.settle_item(
        _op(queue, "op-resolve-2-not-sent"), item_two.attempt_id, ClaimOutcome.NOT_SENT
    )
    assert resolved.status == "queued"
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].status == "queued"
    assert snapshot.batches[0].request_ids == (
        _request_id(queue, "request-2"),
        _request_id(queue, "request-3"),
    )
    assert tuple(item.status for item in snapshot.batches[0].items) == (
        ItemStatus.QUEUED,
        ItemStatus.QUEUED,
    )

    next_batch = await queue.claim_next(_op(queue, "op-reclaim-suffix"))
    next_item = await queue.claim_next_item(
        _op(queue, "op-retry-2"), next_batch.claim_id
    )
    assert next_item.request_ids == (_request_id(queue, "request-2"),)
    await queue.settle_item(
        _op(queue, "op-ack-2"), next_item.attempt_id, ClaimOutcome.ACCEPTED
    )
    suffix = await queue.claim_next_item(
        _op(queue, "op-attempt-3"), next_batch.claim_id
    )
    assert suffix.request_ids == (_request_id(queue, "request-3"),)


@pytest.mark.asyncio
async def test_claim_can_be_released_idempotently_before_first_item_dispatch():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )
    claim = await queue.claim_next(_op(queue, "op-batch-claim"))

    released = await queue.release_claim(_op(queue, "op-release"), claim.claim_id)
    replay = await queue.release_claim(_op(queue, "op-release"), claim.claim_id)

    assert released == replay
    assert released.status == "queued"
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].status == "queued"
    assert snapshot.batches[0].claim_id is None
    next_claim = await queue.claim_next(_op(queue, "op-reclaim"))
    assert next_claim.batch_id == _batch_id(queue, "batch-1")


@pytest.mark.asyncio
async def test_release_claim_fails_after_item_claim_and_can_race_item_claim_safely():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )
    claim = await queue.claim_next(_op(queue, "op-batch-claim"))
    item = await queue.claim_next_item(_op(queue, "op-item-claim"), claim.claim_id)

    with pytest.raises(ClaimBusy):
        await queue.release_claim(_op(queue, "op-release-too-late"), claim.claim_id)
    assert (await queue.snapshot()).batches[0].items[0].status is ItemStatus.DISPATCHING
    await queue.settle_item(
        _op(queue, "op-confirm-not-sent"), item.attempt_id, ClaimOutcome.NOT_SENT
    )

    claim = await queue.claim_next(_op(queue, "op-batch-claim-again"))
    release_result, item_result = await asyncio.gather(
        queue.release_claim(_op(queue, "op-release-race"), claim.claim_id),
        queue.claim_next_item(_op(queue, "op-item-race"), claim.claim_id),
        return_exceptions=True,
    )
    if isinstance(release_result, QueueReceipt):
        assert release_result.status == "queued"
        assert isinstance(item_result, ClaimNotFound)
    else:
        assert isinstance(release_result, ClaimBusy)
        assert isinstance(item_result, QueueReceipt)
        await queue.settle_item(
            _op(queue, "op-race-confirm-not-sent"),
            item_result.attempt_id,
            ClaimOutcome.NOT_SENT,
        )
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].status == "queued"
    assert snapshot.batches[0].request_ids == (_request_id(queue, "request-1"),)


@pytest.mark.asyncio
async def test_small_operation_ledger_rejects_claim_before_dispatch_if_settlement_cannot_fit():
    queue = WebMessageQueue(scope_id="conversation-owner-a", max_operations=3)
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )

    with pytest.raises(OperationLedgerFull):
        await queue.claim_next(_op(queue, "op-claim-without-lifecycle-capacity"))

    snapshot = await queue.snapshot()
    assert snapshot.revision == 1
    assert snapshot.batches[0].status == "queued"
    assert snapshot.batches[0].items[0].status is ItemStatus.QUEUED


@pytest.mark.asyncio
async def test_claim_reserves_settlement_capacity_before_other_writes_can_consume_it():
    queue = WebMessageQueue(scope_id="conversation-owner-a", max_operations=6)
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )
    claim = await queue.claim_next(_op(queue, "op-claim"))
    item = await queue.claim_next_item(_op(queue, "op-item-claim"), claim.claim_id)

    with pytest.raises(OperationLedgerFull):
        await queue.enqueue_batch(
            _op(queue, "op-concurrent-write"),
            _batch_id(queue, "batch-2"),
            (WebMessage(_request_id(queue, "request-2"), b"another"),),
        )

    ack = await queue.settle_item(
        _op(queue, "op-settle"), item.attempt_id, ClaimOutcome.ACCEPTED
    )
    assert ack.status == "accepted"
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_in_doubt_snapshot_keeps_display_content_but_cannot_dispatch_it():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    payload = b"private body for authorized UI rendering"
    batch_id = _batch_id(queue, "batch-in-doubt-display")
    request_id = _request_id(queue, "request-in-doubt-display")
    await queue.enqueue_batch(
        _op(queue, "op-enqueue-in-doubt-display"),
        batch_id,
        (WebMessage(request_id, payload),),
    )
    claim = await queue.claim_next(_op(queue, "op-claim-in-doubt-display"))
    attempt = await queue.claim_next_item(
        _op(queue, "op-attempt-in-doubt-display"), claim.claim_id
    )
    await queue.settle_item(
        _op(queue, "op-settle-in-doubt-display"),
        attempt.attempt_id,
        ClaimOutcome.IN_DOUBT,
    )

    snapshot = await queue.snapshot()
    item = snapshot.batches[0].items[0]
    assert snapshot.batches[0].status == BatchStatus.IN_DOUBT
    assert item.status is ItemStatus.IN_DOUBT
    assert item.payload == payload

    revision = snapshot.revision
    with pytest.raises(ClaimNotDispatchable):
        await queue.item_payload(attempt.attempt_id)
    with pytest.raises(ClaimNotDispatchable):
        await queue.claim_next_item(
            _op(queue, "op-retry-in-doubt-display"), claim.claim_id
        )
    assert (await queue.snapshot()).revision == revision


@pytest.mark.asyncio
async def test_claim_reserves_release_receipt_before_other_writes_can_consume_it():
    queue = WebMessageQueue(scope_id="conversation-owner-a", max_operations=6)
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )
    claim = await queue.claim_next(_op(queue, "op-claim"))

    with pytest.raises(OperationLedgerFull):
        await queue.enqueue_batch(
            _op(queue, "op-concurrent-write"),
            _batch_id(queue, "batch-2"),
            (WebMessage(_request_id(queue, "request-2"), b"another"),),
        )

    released = await queue.release_claim(_op(queue, "op-release"), claim.claim_id)
    assert released.status == "queued"
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].status is BatchStatus.QUEUED
    assert snapshot.batches[0].request_ids == (_request_id(queue, "request-1"),)


@pytest.mark.asyncio
async def test_settle_item_rejects_forged_and_wrong_incarnation_attempt_ids():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    foreign_queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )
    claim = await queue.claim_next(_op(queue, "op-claim"))
    attempt = await queue.claim_next_item(_op(queue, "op-item-claim"), claim.claim_id)
    changed_signature = "0" if attempt.attempt_id[-1] != "0" else "1"
    forged_attempt = attempt.attempt_id[:-1] + changed_signature
    foreign_attempt = foreign_queue.new_operation_id()

    with pytest.raises(InvalidIdentifier):
        await queue.settle_item(
            _op(queue, "op-settle-foreign"),
            foreign_attempt,
            ClaimOutcome.ACCEPTED,
        )
    with pytest.raises(InvalidIdentifier):
        await queue.settle_item(
            _op(queue, "op-settle-forged"), forged_attempt, ClaimOutcome.ACCEPTED
        )

    snapshot = await queue.snapshot()
    assert snapshot.batches[0].items[0].status is ItemStatus.DISPATCHING
    assert snapshot.batches[0].items[0].attempt_id == attempt.attempt_id

    settled = await queue.settle_item(
        _op(queue, "op-settle-valid"), attempt.attempt_id, ClaimOutcome.ACCEPTED
    )
    assert settled.status == "accepted"
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_expired_active_attempt_id_remains_authenticatable_for_settlement(
    monkeypatch,
):
    now = [1_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        operation_retry_lease_seconds=10 / 1e9,
    )
    await queue.enqueue_batch(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (WebMessage(queue.new_request_id(), b"payload"),),
    )
    claim = await queue.claim_next(queue.new_operation_id())
    attempt = await queue.claim_next_item(queue.new_operation_id(), claim.claim_id)

    now[0] = 1_010
    settled = await queue.settle_item(
        queue.new_operation_id(), attempt.attempt_id, ClaimOutcome.ACCEPTED
    )

    assert settled.status == "accepted"
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_retry_lease_expires_to_explicit_stale_without_replaying_operation():
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        operation_retry_lease_seconds=0.001,
    )
    operation_id = queue.new_operation_id()
    first = await queue.enqueue_batch(
        operation_id,
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )
    await asyncio.sleep(0.01)

    with pytest.raises(StaleOperation):
        await queue.enqueue_batch(
            operation_id,
            _batch_id(queue, "batch-1"),
            (WebMessage(_request_id(queue, "request-1"), b"payload"),),
        )
    snapshot = await queue.snapshot()
    assert snapshot.revision == first.revision
    assert len(snapshot.batches) == 1


@pytest.mark.asyncio
async def test_exact_lease_boundary_is_stale_and_cannot_reuse_entity_ids(monkeypatch):
    now = [1_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        operation_retry_lease_seconds=10 / 1e9,
    )
    operation_id = queue.new_operation_id()
    batch_id = queue.new_batch_id()
    request_id = queue.new_request_id()
    await queue.enqueue_batch(
        operation_id,
        batch_id,
        (WebMessage(request_id, b"payload"),),
    )

    now[0] = 1_010
    with pytest.raises(StaleOperation):
        await queue.enqueue_batch(
            operation_id,
            batch_id,
            (WebMessage(request_id, b"payload"),),
        )
    with pytest.raises(StaleIdentifier):
        await queue.enqueue_batch(
            queue.new_operation_id(),
            batch_id,
            (WebMessage(request_id, b"payload"),),
        )
    assert (await queue.snapshot()).revision == 1


@pytest.mark.asyncio
async def test_expired_replay_and_entity_ledgers_are_pruned_without_reusing_old_ids(
    monkeypatch,
):
    now = [1_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        max_operations=12,
        max_batch_items=1,
        operation_retry_lease_seconds=3 / 1e9,
    )
    first_operation_id = ""
    first_batch_id = ""
    first_request_id = ""

    for index in range(8):
        if index:
            now[0] += 4
        operation_id = queue.new_operation_id()
        batch_id = queue.new_batch_id()
        request_id = queue.new_request_id()
        if index == 0:
            first_operation_id = operation_id
            first_batch_id = batch_id
            first_request_id = request_id
        await queue.enqueue_batch(
            operation_id,
            batch_id,
            (WebMessage(request_id, b"small payload"),),
        )
        claim = await queue.claim_next(queue.new_operation_id())
        item = await queue.claim_next_item(queue.new_operation_id(), claim.claim_id)
        await queue.settle_item(
            queue.new_operation_id(), item.attempt_id, ClaimOutcome.ACCEPTED
        )

    now[0] += 4
    await queue.enqueue_batch(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (WebMessage(queue.new_request_id(), b"current"),),
    )
    assert len(queue._operations) == 1
    assert len(queue._batch_owners) == 1
    assert len(queue._item_owners) == 1

    with pytest.raises(StaleOperation):
        await queue.enqueue_batch(
            first_operation_id,
            queue.new_batch_id(),
            (WebMessage(queue.new_request_id(), b"retry"),),
        )
    with pytest.raises(StaleIdentifier):
        await queue.enqueue_batch(
            queue.new_operation_id(),
            first_batch_id,
            (WebMessage(first_request_id, b"old entity IDs"),),
        )

    snapshot = await queue.snapshot()
    assert snapshot.revision == 33
    assert len(snapshot.batches) == 1


@pytest.mark.asyncio
async def test_entity_identifier_cap_rejects_atomically_then_recovers_after_lease():
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        max_operations=10,
        max_items=10,
        max_batch_items=1,
        max_entity_ids=3,
        operation_retry_lease_seconds=0.005,
    )
    old_batch_id = queue.new_batch_id()
    old_request_id = queue.new_request_id()
    await queue.enqueue_batch(
        queue.new_operation_id(),
        old_batch_id,
        (WebMessage(old_request_id, b"first"),),
    )
    await queue.remove_item(queue.new_operation_id(), old_request_id)
    before = await queue.snapshot()

    with pytest.raises(IdentifierLedgerFull):
        await queue.enqueue_batch(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (WebMessage(queue.new_request_id(), b"blocked"),),
        )

    assert (await queue.snapshot()).revision == before.revision
    assert len(queue._batch_owners) + len(queue._item_owners) == 2

    await asyncio.sleep(0.01)
    await queue.enqueue_batch(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (WebMessage(queue.new_request_id(), b"recovered"),),
    )
    with pytest.raises(StaleIdentifier):
        await queue.enqueue_batch(
            queue.new_operation_id(),
            old_batch_id,
            (WebMessage(old_request_id, b"old IDs"),),
        )
    assert len(queue._batch_owners) + len(queue._item_owners) == 2


@pytest.mark.asyncio
async def test_remove_is_exact_and_cannot_remove_an_item_after_its_batch_is_claimed():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (
            WebMessage(_request_id(queue, "request-remove"), b"remove me"),
            WebMessage(_request_id(queue, "request-keep"), b"keep me"),
        ),
    )
    removed = await queue.remove_item(
        _op(queue, "op-remove"), _request_id(queue, "request-remove")
    )
    replay = await queue.remove_item(
        _op(queue, "op-remove"), _request_id(queue, "request-remove")
    )
    assert removed == replay
    assert removed.status == "removed"
    assert (await queue.snapshot()).batches[0].request_ids == (
        _request_id(queue, "request-keep"),
    )

    claim = await queue.claim_next(_op(queue, "op-claim"))
    with pytest.raises(ItemNotQueued):
        await queue.remove_item(
            _op(queue, "op-remove-claimed"), _request_id(queue, "request-keep")
        )
    assert claim.request_ids == (_request_id(queue, "request-keep"),)


@pytest.mark.asyncio
async def test_remove_and_claim_race_has_one_serialized_winner():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        _op(queue, "op-enqueue"),
        _batch_id(queue, "batch-1"),
        (WebMessage(_request_id(queue, "request-1"), b"payload"),),
    )

    remove_result, claim_result = await asyncio.gather(
        queue.remove_item(_op(queue, "op-remove"), _request_id(queue, "request-1")),
        queue.claim_next(_op(queue, "op-claim")),
        return_exceptions=True,
    )

    if isinstance(remove_result, QueueReceipt):
        assert remove_result.status == "removed"
        assert isinstance(claim_result, QueueEmpty)
    else:
        assert isinstance(remove_result, ItemNotQueued)
        assert isinstance(claim_result, QueueReceipt)
        assert claim_result.request_ids == (_request_id(queue, "request-1"),)


@pytest.mark.asyncio
async def test_operation_ledger_saturates_with_explicit_rejection_and_keeps_old_replay():
    queue = WebMessageQueue(
        scope_id="conversation-owner-a",
        max_operations=1,
        operation_retry_lease_seconds=0.003,
    )
    items = (WebMessage(_request_id(queue, "request-1"), b"body"),)
    first = await queue.enqueue_batch(
        _op(queue, "op-1"), _batch_id(queue, "batch-1"), items
    )

    assert (
        await queue.enqueue_batch(
            _op(queue, "op-1"), _batch_id(queue, "batch-1"), items
        )
        == first
    )
    with pytest.raises(OperationLedgerFull):
        await queue.enqueue_batch(
            _op(queue, "op-2"),
            _batch_id(queue, "batch-2"),
            (WebMessage(_request_id(queue, "request-2"), b"another body"),),
        )
    assert (await queue.snapshot()).revision == first.revision

    await asyncio.sleep(0.01)
    second = await queue.enqueue_batch(
        _op(queue, "op-after-expiry"),
        _batch_id(queue, "batch-after-expiry"),
        (WebMessage(_request_id(queue, "request-after-expiry"), b"new"),),
    )
    assert second.revision == first.revision + 1
    with pytest.raises(StaleOperation):
        await queue.enqueue_batch(
            _op(queue, "op-1"),
            _batch_id(queue, "batch-1"),
            items,
        )
    assert (await queue.snapshot()).revision == second.revision


@pytest.mark.asyncio
async def test_a_new_incarnation_starts_empty_and_scopes_operation_receipts():
    first_queue = WebMessageQueue(scope_id="conversation-owner-a")
    second_queue = WebMessageQueue(scope_id="conversation-owner-b")
    await first_queue.enqueue_batch(
        _op(first_queue, "op-shared"),
        _batch_id(first_queue, "batch-1"),
        (WebMessage(_request_id(first_queue, "request-1"), b"body"),),
    )

    first_snapshot = await first_queue.snapshot()
    second_snapshot = await second_queue.snapshot()

    assert first_snapshot.scope_id != second_snapshot.scope_id
    assert first_snapshot.incarnation != second_snapshot.incarnation
    assert first_snapshot.batches
    assert second_snapshot.revision == 0
    assert second_snapshot.batches == ()

    # The same logical labels mint independent IDs in the other scope.
    assert _batch_id(first_queue, "batch-1") != _batch_id(second_queue, "batch-1")
    assert _request_id(first_queue, "request-1") != _request_id(
        second_queue, "request-1"
    )
    second = await second_queue.enqueue_batch(
        _op(second_queue, "op-shared"),
        _batch_id(second_queue, "batch-1"),
        (
            WebMessage(
                _request_id(second_queue, "request-1"), b"different private body"
            ),
        ),
    )
    assert second.scope_id == "conversation-owner-b"
    assert second.revision == 1
    assert (await first_queue.snapshot()).batches[0].items[0].payload == b"body"
    assert (await second_queue.snapshot()).batches[0].items[0].payload == (
        b"different private body"
    )

    with pytest.raises(InvalidIdentifier):
        await second_queue.enqueue_batch(
            _op(first_queue, "op-shared"),
            _batch_id(second_queue, "batch-cross-scope"),
            (WebMessage(_request_id(second_queue, "request-cross-scope"), b"body"),),
        )
    with pytest.raises(InvalidIdentifier):
        await second_queue.enqueue_batch(
            _op(second_queue, "op-cross-scope-batch"),
            _batch_id(first_queue, "batch-1"),
            (
                WebMessage(
                    _request_id(second_queue, "request-cross-scope-batch"), b"body"
                ),
            ),
        )
    with pytest.raises(InvalidIdentifier):
        await second_queue.enqueue_batch(
            _op(second_queue, "op-cross-scope-item"),
            _batch_id(second_queue, "batch-cross-scope-item"),
            (WebMessage(_request_id(first_queue, "request-1"), b"body"),),
        )
    assert (await second_queue.snapshot()).revision == 1
