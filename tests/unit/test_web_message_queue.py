"""Contract tests for the daemon-owned WebUI message queue primitive."""

from __future__ import annotations

import asyncio

import pytest

from kollabor.state.web_message_queue import (
    CapacityExceeded,
    ClaimBusy,
    ClaimNotDispatchable,
    ClaimNotFound,
    ClaimOutcome,
    IdempotencyConflict,
    ItemNotQueued,
    ItemStatus,
    OperationLedgerFull,
    QueueEmpty,
    QueueInDoubt,
    QueueReceipt,
    WebMessage,
    WebMessageQueue,
)


@pytest.mark.asyncio
async def test_enqueue_batch_is_ordered_atomic_and_idempotent_without_exposing_payloads():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    items = (
        WebMessage("request-1", b"first private body"),
        WebMessage("request-2", b"second private body"),
    )

    first, replay = await asyncio.gather(
        queue.enqueue_batch("op-enqueue-1", "batch-1", items),
        queue.enqueue_batch("op-enqueue-1", "batch-1", items),
    )
    snapshot = await queue.snapshot()

    assert first == replay
    assert first.scope_id == "conversation-owner-a"
    assert first.revision == 1
    assert snapshot.revision == 1
    assert len(snapshot.batches) == 1
    assert snapshot.batches[0].batch_id == "batch-1"
    assert snapshot.batches[0].request_ids == ("request-1", "request-2")
    assert tuple(item.payload for item in snapshot.batches[0].items) == (
        b"first private body",
        b"second private body",
    )
    assert "private body" not in repr(snapshot)
    assert "private body" not in repr(first)

    with pytest.raises(IdempotencyConflict):
        await queue.enqueue_batch(
            "op-enqueue-1",
            "batch-1",
            (WebMessage("request-1", b"changed private body"),),
        )
    assert (await queue.snapshot()).revision == 1


@pytest.mark.asyncio
async def test_response_loss_retry_returns_committed_receipt_without_duplicating_batch():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    items = (
        WebMessage("request-1", b"first"),
        WebMessage("request-2", b"second"),
    )

    committed = await queue.enqueue_batch("op-response-lost", "batch-1", items)
    # The first response is treated as lost; retrying the same operation is safe.
    retried = await queue.enqueue_batch("op-response-lost", "batch-1", items)

    assert retried == committed
    snapshot = await queue.snapshot()
    assert snapshot.revision == committed.revision
    assert tuple(batch.batch_id for batch in snapshot.batches) == ("batch-1",)
    assert snapshot.batches[0].request_ids == ("request-1", "request-2")


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
        "op-existing",
        "batch-existing",
        (
            WebMessage("request-existing-1", b"1234567890123456"),
            WebMessage("request-existing-2", b"abcdefghijklmnop"),
        ),
    )
    before = await queue.snapshot()

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            "op-too-large",
            "batch-too-large",
            (
                WebMessage("request-new-1", b"1234567890"),
                WebMessage("request-new-2", b"abcdefghij"),
            ),
        )

    after = await queue.snapshot()
    assert after.revision == before.revision
    assert tuple(batch.batch_id for batch in after.batches) == ("batch-existing",)
    assert tuple(
        item.request_id for batch in after.batches for item in batch.items
    ) == ("request-existing-1", "request-existing-2")
    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            "op-payload-too-large",
            "batch-payload-too-large",
            (WebMessage("request-large", b"x" * 17),),
        )
    assert (await queue.snapshot()).revision == before.revision

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            "op-queue-full",
            "batch-queue-full",
            (WebMessage("request-full", b"still blocked"),),
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
        "op-existing",
        "batch-existing",
        (WebMessage("request-existing", b"12345678"),),
    )
    before = await queue.snapshot()

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch(
            "op-byte-full",
            "batch-byte-full",
            (
                WebMessage("request-new-1", b"123456"),
                WebMessage("request-new-2", b"abcdef"),
            ),
        )

    after = await queue.snapshot()
    assert after.revision == before.revision
    assert tuple(batch.batch_id for batch in after.batches) == ("batch-existing",)
    assert after.batches[0].request_ids == ("request-existing",)


@pytest.mark.asyncio
async def test_partial_send_now_uncertainty_preserves_suffix_and_never_replays_prefix():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        "op-old",
        "batch-old",
        (WebMessage("request-old", b"older"),),
    )
    await queue.enqueue_batch(
        "op-send-now",
        "batch-send-now",
        (
            WebMessage("request-now-1", b"now one"),
            WebMessage("request-now-2", b"now two"),
            WebMessage("request-now-3", b"now three"),
        ),
    )

    claim = await queue.claim_batch("op-claim-now", "batch-send-now", send_now=True)
    assert claim.status == "dispatching"
    assert claim.claim_id == "op-claim-now"
    first, first_replay = await asyncio.gather(
        queue.claim_next_item("op-attempt-1", claim.claim_id),
        queue.claim_next_item("op-attempt-1", claim.claim_id),
    )
    assert first_replay == first
    first_payload = await queue.item_payload(first.attempt_id)
    assert first.request_ids == ("request-now-1",)
    assert tuple(item.request_id for item in first_payload.items) == ("request-now-1",)
    assert "now one" not in repr(first_payload)
    with pytest.raises(ClaimBusy):
        await queue.claim_next_item("op-attempt-1-interleaved", claim.claim_id)
    ack_first = await queue.settle_item(
        "op-ack-1", first.attempt_id, ClaimOutcome.ACCEPTED
    )
    assert (
        await queue.settle_item("op-ack-1", first.attempt_id, ClaimOutcome.ACCEPTED)
        == ack_first
    )
    prefix_replay = await queue.claim_next_item("op-attempt-1", claim.claim_id)
    assert prefix_replay.status == "accepted"
    assert prefix_replay.request_ids == ("request-now-1",)
    with pytest.raises(ClaimNotFound):
        await queue.item_payload(first.attempt_id)

    uncertain = await queue.claim_next_item("op-attempt-2", claim.claim_id)
    assert uncertain.request_ids == ("request-now-2",)
    batch_replay = await queue.claim_batch(
        "op-claim-now", "batch-send-now", send_now=True
    )
    assert batch_replay.request_ids == ("request-now-2", "request-now-3")
    await queue.settle_item(
        "op-ack-lost-2", uncertain.attempt_id, ClaimOutcome.IN_DOUBT
    )

    snapshot = await queue.snapshot()
    assert tuple(batch.batch_id for batch in snapshot.batches) == (
        "batch-send-now",
        "batch-old",
    )
    assert snapshot.batches[0].status == "in_doubt"
    assert snapshot.batches[0].request_ids == ("request-now-2", "request-now-3")
    assert tuple(item.status for item in snapshot.batches[0].items) == (
        ItemStatus.IN_DOUBT,
        ItemStatus.QUEUED,
    )
    assert all(
        item.request_id != "request-now-1"
        for batch in snapshot.batches
        for item in batch.items
    )
    assert snapshot.batches[1].status == "queued"
    replay = await queue.claim_next_item("op-attempt-2", claim.claim_id)
    assert replay.status == "in_doubt"
    assert replay.revision == snapshot.revision
    with pytest.raises(ClaimNotDispatchable):
        await queue.item_payload(uncertain.attempt_id)
    with pytest.raises(ClaimNotDispatchable):
        await queue.claim_next_item("op-attempt-3-too-soon", claim.claim_id)
    with pytest.raises(QueueInDoubt):
        await queue.claim_next("op-claim-later-while-in-doubt")

    # Resolving the uncertain item as accepted preserves the unsent suffix.
    await queue.settle_item(
        "op-resolve-2-accepted", uncertain.attempt_id, ClaimOutcome.ACCEPTED
    )
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].batch_id == "batch-send-now"
    assert snapshot.batches[0].request_ids == ("request-now-3",)
    third = await queue.claim_next_item("op-attempt-3", claim.claim_id)
    assert third.request_ids == ("request-now-3",)
    await queue.settle_item("op-ack-3", third.attempt_id, ClaimOutcome.ACCEPTED)

    next_batch = await queue.claim_next("op-claim-old")
    assert next_batch.batch_id == "batch-old"
    assert next_batch.request_ids == ("request-old",)


@pytest.mark.asyncio
async def test_definitive_not_sent_releases_a_send_now_batch_without_claiming_it_again():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        "op-old",
        "batch-old",
        (WebMessage("request-old", b"older"),),
    )
    await queue.enqueue_batch(
        "op-priority",
        "batch-priority",
        (WebMessage("request-priority", b"priority"),),
    )
    batch_claim = await queue.claim_batch(
        "op-priority-claim", "batch-priority", send_now=True
    )
    item_claim = await queue.claim_next_item(
        "op-priority-attempt", batch_claim.claim_id
    )
    await queue.settle_item(
        "op-priority-not-sent", item_claim.attempt_id, ClaimOutcome.NOT_SENT
    )

    next_claim = await queue.claim_next("op-claim-oldest")
    assert next_claim.batch_id == "batch-old"


@pytest.mark.asyncio
async def test_not_sent_resolution_requeues_uncertain_item_and_suffix_in_order():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        "op-enqueue",
        "batch-multi",
        (
            WebMessage("request-1", b"one"),
            WebMessage("request-2", b"two"),
            WebMessage("request-3", b"three"),
        ),
    )
    batch_claim = await queue.claim_next("op-batch-claim")
    item_one = await queue.claim_next_item("op-attempt-1", batch_claim.claim_id)
    await queue.settle_item("op-ack-1", item_one.attempt_id, ClaimOutcome.ACCEPTED)
    item_two = await queue.claim_next_item("op-attempt-2", batch_claim.claim_id)
    await queue.settle_item(
        "op-uncertain-2", item_two.attempt_id, ClaimOutcome.IN_DOUBT
    )

    resolved = await queue.settle_item(
        "op-resolve-2-not-sent", item_two.attempt_id, ClaimOutcome.NOT_SENT
    )
    assert resolved.status == "queued"
    snapshot = await queue.snapshot()
    assert snapshot.batches[0].status == "queued"
    assert snapshot.batches[0].request_ids == ("request-2", "request-3")
    assert tuple(item.status for item in snapshot.batches[0].items) == (
        ItemStatus.QUEUED,
        ItemStatus.QUEUED,
    )

    next_batch = await queue.claim_next("op-reclaim-suffix")
    next_item = await queue.claim_next_item("op-retry-2", next_batch.claim_id)
    assert next_item.request_ids == ("request-2",)
    await queue.settle_item("op-ack-2", next_item.attempt_id, ClaimOutcome.ACCEPTED)
    suffix = await queue.claim_next_item("op-attempt-3", next_batch.claim_id)
    assert suffix.request_ids == ("request-3",)


@pytest.mark.asyncio
async def test_remove_is_exact_and_cannot_remove_an_item_after_its_batch_is_claimed():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        "op-enqueue",
        "batch-1",
        (
            WebMessage("request-remove", b"remove me"),
            WebMessage("request-keep", b"keep me"),
        ),
    )
    removed = await queue.remove_item("op-remove", "request-remove")
    replay = await queue.remove_item("op-remove", "request-remove")
    assert removed == replay
    assert removed.status == "removed"
    assert (await queue.snapshot()).batches[0].request_ids == ("request-keep",)

    claim = await queue.claim_next("op-claim")
    with pytest.raises(ItemNotQueued):
        await queue.remove_item("op-remove-claimed", "request-keep")
    assert claim.request_ids == ("request-keep",)


@pytest.mark.asyncio
async def test_remove_and_claim_race_has_one_serialized_winner():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    await queue.enqueue_batch(
        "op-enqueue",
        "batch-1",
        (WebMessage("request-1", b"payload"),),
    )

    remove_result, claim_result = await asyncio.gather(
        queue.remove_item("op-remove", "request-1"),
        queue.claim_next("op-claim"),
        return_exceptions=True,
    )

    if isinstance(remove_result, QueueReceipt):
        assert remove_result.status == "removed"
        assert isinstance(claim_result, QueueEmpty)
    else:
        assert isinstance(remove_result, ItemNotQueued)
        assert isinstance(claim_result, QueueReceipt)
        assert claim_result.request_ids == ("request-1",)


@pytest.mark.asyncio
async def test_operation_ledger_saturates_with_explicit_rejection_and_keeps_old_replay():
    queue = WebMessageQueue(scope_id="conversation-owner-a", max_operations=1)
    items = (WebMessage("request-1", b"body"),)
    first = await queue.enqueue_batch("op-1", "batch-1", items)

    assert await queue.enqueue_batch("op-1", "batch-1", items) == first
    with pytest.raises(OperationLedgerFull):
        await queue.enqueue_batch(
            "op-2",
            "batch-2",
            (WebMessage("request-2", b"another body"),),
        )
    assert (await queue.snapshot()).revision == first.revision


@pytest.mark.asyncio
async def test_a_new_incarnation_starts_empty_and_scopes_operation_receipts():
    first_queue = WebMessageQueue(scope_id="conversation-owner-a")
    second_queue = WebMessageQueue(scope_id="conversation-owner-b")
    await first_queue.enqueue_batch(
        "op-shared",
        "batch-1",
        (WebMessage("request-1", b"body"),),
    )

    first_snapshot = await first_queue.snapshot()
    second_snapshot = await second_queue.snapshot()

    assert first_snapshot.scope_id != second_snapshot.scope_id
    assert first_snapshot.incarnation != second_snapshot.incarnation
    assert first_snapshot.batches
    assert second_snapshot.revision == 0
    assert second_snapshot.batches == ()

    # Identical IDs can be used independently in a distinct authorized scope.
    second = await second_queue.enqueue_batch(
        "op-shared",
        "batch-1",
        (WebMessage("request-1", b"different private body"),),
    )
    assert second.scope_id == "conversation-owner-b"
    assert second.revision == 1
    assert (await first_queue.snapshot()).batches[0].items[0].payload == b"body"
    assert (await second_queue.snapshot()).batches[0].items[0].payload == (
        b"different private body"
    )
