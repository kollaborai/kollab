"""Trusted daemon-worker settlement for expired RPC operation IDs."""

from __future__ import annotations

import asyncio
import base64

import pytest

from kollabor.state import web_message_queue as queue_module
from kollabor.state.web_message_queue import (
    ClaimBusy,
    ClaimNotFound,
    ClaimOutcome,
    IdempotencyConflict,
    InvalidIdentifier,
    ItemStatus,
    QueueReceipt,
    StaleOperation,
    WebMessage,
    WebMessageQueue,
)
from kollabor.state.web_message_queue_rpc import (
    WebMessageQueueRegistry,
    daemon_conversation_scope_resolver,
    register_web_message_queue_handlers,
)
from kollabor_rpc import RpcCallerContext, RpcServer


def _queue(*, max_operations: int = 6) -> WebMessageQueue:
    return WebMessageQueue(
        scope_id="conversation-owner-a",
        max_operations=max_operations,
        operation_retry_lease_seconds=10 / 1e9,
    )


async def _active_attempt(queue: WebMessageQueue) -> str:
    request_id = queue.new_request_id()
    batch_id = queue.new_batch_id()
    await queue.enqueue_batch(
        queue.new_operation_id(),
        batch_id,
        (WebMessage(request_id, b"queued body"),),
    )
    claim = await queue.claim_batch(queue.new_operation_id(), batch_id)
    attempt = await queue.claim_next_item(queue.new_operation_id(), claim.claim_id)
    return attempt.attempt_id


def _expire_lease(now: list[int]) -> None:
    now[0] += 11


@pytest.mark.asyncio
async def test_worker_settles_active_attempt_after_operation_lease_expiry(monkeypatch):
    now = [1_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = _queue(max_operations=6)
    attempt_id = await _active_attempt(queue)
    stale_operation_id = queue.new_operation_id()
    _expire_lease(now)

    with pytest.raises(StaleOperation):
        await queue.settle_item(stale_operation_id, attempt_id, ClaimOutcome.ACCEPTED)

    receipt = await queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)
    snapshot = await queue.snapshot()

    assert receipt.status == ClaimOutcome.ACCEPTED.value
    assert receipt.operation_id != stale_operation_id
    assert queue._token_issue_time(receipt.operation_id, "op") == now[0]
    assert snapshot.batches == ()
    assert len(queue._operations) <= queue._max_operations
    assert queue._reserved_operations == 0


@pytest.mark.asyncio
async def test_worker_settlement_replays_same_outcome_and_rejects_conflict(monkeypatch):
    now = [2_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = _queue()
    attempt_id = await _active_attempt(queue)
    _expire_lease(now)

    first = await queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)
    operations_after_commit = tuple(queue._operations)
    revision_after_commit = queue._revision

    replay = await queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)
    assert replay == first
    assert first.operation_id in queue._operations
    assert len(queue._operations) <= len(operations_after_commit)
    assert queue._revision == revision_after_commit

    with pytest.raises(IdempotencyConflict):
        await queue.settle_item_from_worker(attempt_id, ClaimOutcome.NOT_SENT)
    assert queue._revision == revision_after_commit
    assert set(queue._operations).issubset(operations_after_commit)


@pytest.mark.asyncio
async def test_worker_receipt_expiry_does_not_repeat_terminal_mutation(monkeypatch):
    now = [2_125]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = _queue()
    attempt_id = await _active_attempt(queue)
    _expire_lease(now)

    receipt = await queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)
    revision = queue._revision
    await queue.acknowledge_worker_settlement(
        attempt_id, ClaimOutcome.ACCEPTED, receipt
    )
    _expire_lease(now)

    with pytest.raises(ClaimNotFound):
        await queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)

    assert queue._revision == revision
    assert receipt.operation_id not in queue._operations
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_worker_can_requeue_expired_attempt_when_provider_proves_not_sent(
    monkeypatch,
):
    now = [2_250]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = _queue()
    attempt_id = await _active_attempt(queue)
    _expire_lease(now)

    receipt = await queue.settle_item_from_worker(attempt_id, ClaimOutcome.NOT_SENT)
    snapshot = await queue.snapshot()

    assert receipt.status == "queued"
    assert queue._active_item_attempt_id is None
    assert queue._reserved_operations == 0
    assert len(snapshot.batches) == 1
    assert snapshot.batches[0].items[0].status is ItemStatus.QUEUED
    assert snapshot.batches[0].items[0].attempt_id is None


@pytest.mark.asyncio
async def test_worker_settlement_rejects_wrong_queue_and_nonactive_attempt(monkeypatch):
    now = [2_500]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    owner_queue = _queue()
    foreign_queue = WebMessageQueue(scope_id="conversation-owner-b")
    attempt_id = await _active_attempt(owner_queue)
    owner_before = (
        owner_queue._revision,
        owner_queue._active_item_attempt_id,
        tuple(owner_queue._operations),
    )

    with pytest.raises(InvalidIdentifier):
        await foreign_queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)
    with pytest.raises(ClaimNotFound):
        await owner_queue.settle_item_from_worker(
            owner_queue.new_operation_id(), ClaimOutcome.ACCEPTED
        )

    assert (
        owner_queue._revision,
        owner_queue._active_item_attempt_id,
        tuple(owner_queue._operations),
    ) == owner_before
    assert (await owner_queue.snapshot()).batches[0].items[
        0
    ].status is ItemStatus.DISPATCHING


@pytest.mark.asyncio
async def test_worker_keeps_ambiguous_attempt_in_doubt_until_explicit_resolution(
    monkeypatch,
):
    now = [3_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = _queue()
    attempt_id = await _active_attempt(queue)
    _expire_lease(now)

    uncertain = await queue.settle_item_from_worker(attempt_id, ClaimOutcome.IN_DOUBT)
    operations_after_uncertainty = tuple(queue._operations)
    snapshot = await queue.snapshot()

    assert uncertain.status == ClaimOutcome.IN_DOUBT.value
    assert snapshot.batches[0].items[0].status is ItemStatus.IN_DOUBT
    assert queue._active_item_attempt_id == attempt_id
    assert (
        await queue.settle_item_from_worker(attempt_id, ClaimOutcome.IN_DOUBT)
        == uncertain
    )
    assert tuple(queue._operations) == operations_after_uncertainty
    await queue.acknowledge_worker_settlement(
        attempt_id, ClaimOutcome.IN_DOUBT, uncertain
    )

    accepted = await queue.settle_item(
        queue.new_operation_id(), attempt_id, ClaimOutcome.ACCEPTED
    )
    assert accepted.status == ClaimOutcome.ACCEPTED.value
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
async def test_worker_recovery_is_not_registered_as_stale_rpc_or_payload_path(
    monkeypatch,
):
    class ConversationState:
        uid = "conversation-old"

        def get_conversation_uid(self):
            return self.uid

    now = [4_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    registry = WebMessageQueueRegistry(
        max_scopes=2,
        queue_options={"operation_retry_lease_seconds": 10 / 1e9},
    )
    server = RpcServer()
    state = ConversationState()
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=daemon_conversation_scope_resolver(state),
    )
    owner = RpcCallerContext.from_unix_peer(501)

    async def call(method: str, params: dict) -> dict:
        return await server.handle_wire(
            {
                "action": "rpc_request",
                "request_id": "worker-settlement-test",
                "method": method,
                "params": params,
            },
            caller_context=owner,
        )

    allocated = await call(
        "state.web_queue.allocate_ids",
        {
            "expected_conversation_uid": "conversation-old",
            "item_count": 1,
            "operation_count": 4,
        },
    )
    assert allocated["error"] is None
    ids = allocated["result"]
    enqueued = await call(
        "state.web_queue.enqueue_batch",
        {
            "expected_conversation_uid": "conversation-old",
            "operation_id": ids["operation_ids"][0],
            "batch_id": ids["batch_id"],
            "items": [
                {
                    "request_id": ids["request_ids"][0],
                    "payload_b64": base64.b64encode(b"secret").decode("ascii"),
                }
            ],
        },
    )
    assert enqueued["error"] is None
    claimed = await call(
        "state.web_queue.claim_batch",
        {
            "expected_conversation_uid": "conversation-old",
            "operation_id": ids["operation_ids"][1],
            "batch_id": ids["batch_id"],
        },
    )
    attempt = await call(
        "state.web_queue.claim_next_item",
        {
            "expected_conversation_uid": "conversation-old",
            "operation_id": ids["operation_ids"][2],
            "claim_id": claimed["result"]["claim_id"],
        },
    )
    assert attempt["error"] is None
    queue = registry._queues[ids["scope_id"]]

    _expire_lease(now)
    state.uid = "conversation-new"
    stale_payload = await call(
        "state.web_queue.item_payload",
        {
            "expected_conversation_uid": "conversation-old",
            "attempt_id": attempt["result"]["attempt_id"],
        },
    )
    stale_allocator = await call(
        "state.web_queue.allocate_ids",
        {
            "expected_conversation_uid": "conversation-old",
            "item_count": 1,
            "operation_count": 1,
        },
    )
    assert stale_payload["error_kind"] == "handler"
    assert stale_allocator["error_kind"] == "handler"
    assert registry.scope_count == 1
    assert "state.web_queue.settle_item_from_worker" not in server.list_methods()
    unregistered = await call(
        "state.web_queue.settle_item_from_worker",
        {
            "expected_conversation_uid": "conversation-old",
            "attempt_id": attempt["result"]["attempt_id"],
            "outcome": "accepted",
        },
    )
    assert unregistered["error_kind"] == "not_found"

    receipt = await queue.settle_item_from_worker(
        attempt["result"]["attempt_id"], ClaimOutcome.ACCEPTED
    )
    assert receipt.status == ClaimOutcome.ACCEPTED.value
    assert registry.scope_count == 1
    assert (await queue.snapshot()).batches == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_first", (False, True))
async def test_worker_and_public_settlement_race_has_one_terminal_mutation(
    monkeypatch, worker_first
):
    now = [5_000]
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now[0])
    queue = _queue()
    attempt_id = await _active_attempt(queue)
    _expire_lease(now)
    public_operation_id = queue.new_operation_id()

    public_settlement = queue.settle_item(
        public_operation_id, attempt_id, ClaimOutcome.ACCEPTED
    )
    worker_settlement = queue.settle_item_from_worker(attempt_id, ClaimOutcome.ACCEPTED)
    settlements = (
        (worker_settlement, public_settlement)
        if worker_first
        else (public_settlement, worker_settlement)
    )
    results = await asyncio.gather(*settlements, return_exceptions=True)
    receipts = [result for result in results if isinstance(result, QueueReceipt)]
    errors = [result for result in results if isinstance(result, Exception)]
    snapshot = await queue.snapshot()

    assert receipts
    assert len(receipts) <= 2
    assert all(receipt.status == ClaimOutcome.ACCEPTED.value for receipt in receipts)
    assert len({receipt.revision for receipt in receipts}) == 1
    if len(receipts) == 2:
        assert receipts[0] == receipts[1]
    assert snapshot.batches == ()
    assert queue._active_item_attempt_id is None
    assert (
        sum(record.kind == "settle_item" for record in queue._operations.values()) == 1
    )
    worker_receipt = results[0] if worker_first else results[1]
    assert isinstance(worker_receipt, QueueReceipt)
    if worker_first:
        assert len(errors) == 1 and isinstance(errors[0], ClaimBusy)
    else:
        assert errors == []
    await queue.acknowledge_worker_settlement(
        attempt_id, ClaimOutcome.ACCEPTED, worker_receipt
    )
