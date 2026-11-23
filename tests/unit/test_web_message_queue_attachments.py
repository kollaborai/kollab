"""Atomic queue ownership for daemon-staged WebUI attachments."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from kollabor.state.web_message_attachments import (
    AttachmentAuthorizationError,
    AttachmentCapacityExceeded,
    AttachmentNotFound,
    AttachmentOwnerConflict,
    AttachmentScopeBinding,
    InvalidAttachment,
    WebMessageAttachmentRegistry,
)
from kollabor.state.web_message_queue import (
    CapacityExceeded,
    ClaimOutcome,
    IdempotencyConflict,
    WebMessage,
    WebMessageQueue,
)
from kollabor.state.web_message_queue_rpc import (
    QueueScopeBinding,
    WebMessageQueueRegistry,
    register_web_message_queue_handlers,
)
from kollabor_rpc import RpcCallerContext, RpcServer


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass(frozen=True)
class _Caller:
    conversation_uid: str


async def _attachment_scope(
    queue: WebMessageQueue,
    *,
    clock: _Clock | None = None,
    **limits: object,
):
    binding = AttachmentScopeBinding(queue.scope_id, queue.incarnation)

    def resolve_scope(_params: Mapping[str, object], _caller: object):
        return binding

    def validate_owner_id(actual_binding: AttachmentScopeBinding, owner_id: str):
        if actual_binding != binding:
            return False
        try:
            queue._token_issue_time(owner_id, "item")
        except Exception:
            return False
        return True

    registry = WebMessageAttachmentRegistry(
        resolve_scope=resolve_scope,
        validate_owner_id=validate_owner_id,
        clock=clock or _Clock(),
        **limits,
    )
    scope = await registry.bind_scope(
        {"expected_conversation_uid": "conversation-a"}, _Caller("conversation-a")
    )
    return registry, scope


async def _stage(scope, payload: bytes) -> str:
    receipt = await scope.stage(scope.new_operation_id(), "image/png", payload)
    return receipt.ref


async def _rpc_call(server: RpcServer, method: str, params: dict[str, object]):
    return await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "attachment-queue-test",
            "method": method,
            "params": params,
        },
        caller_context=RpcCallerContext.from_unix_peer(501),
    )


@pytest.mark.asyncio
async def test_enqueue_with_attachments_is_atomic_ordered_and_idempotent():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    registry, scope = await _attachment_scope(queue)
    first_ref = await _stage(scope, b"first-image")
    second_ref = await _stage(scope, b"second-image")
    first_id = queue.new_request_id()
    second_id = queue.new_request_id()
    items = (
        WebMessage(first_id, b"first-message", (first_ref,)),
        WebMessage(second_id, b"second-message", (second_ref,)),
    )
    operation_id = queue.new_operation_id()
    batch_id = queue.new_batch_id()

    first = await queue.enqueue_batch_with_attachments(
        operation_id,
        batch_id,
        items,
        attachment_scope=scope,
    )
    replay = await queue.enqueue_batch_with_attachments(
        operation_id,
        batch_id,
        items,
        attachment_scope=scope,
    )
    snapshot = await queue.snapshot()

    assert first == replay
    assert snapshot.revision == 1
    assert snapshot.batches[0].request_ids == (first_id, second_id)
    assert tuple(item.attachment_refs for item in snapshot.batches[0].items) == (
        (first_ref,),
        (second_ref,),
    )
    assert (await scope.open_for_owner(first_id, first_ref)).data == b"first-image"
    assert (await scope.open_for_owner(second_id, second_ref)).data == b"second-image"
    assert registry.used_bytes == len(b"first-imagesecond-image")

    claim = await queue.claim_next(queue.new_operation_id())
    attempt = await queue.claim_next_item(
        queue.new_operation_id(), claim.claim_id or ""
    )
    claimed = await queue.item_payload(attempt.attempt_id or "")
    assert claimed.items[0].attachment_refs == (first_ref,)
    assert b"first-image" not in repr(snapshot).encode()
    assert b"first-image" not in repr(claimed).encode()

    with pytest.raises(IdempotencyConflict):
        await queue.enqueue_batch_with_attachments(
            operation_id,
            batch_id,
            (
                WebMessage(first_id, b"first-message", (second_ref,)),
                WebMessage(second_id, b"second-message", (first_ref,)),
            ),
            attachment_scope=scope,
        )
    assert (await scope.open_for_owner(first_id, first_ref)).data == b"first-image"


@pytest.mark.asyncio
async def test_bad_reference_rejects_entire_batch_without_retaining_valid_prefix():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    _registry, scope = await _attachment_scope(queue)
    valid_ref = await _stage(scope, b"valid-image")
    first_id = queue.new_request_id()
    second_id = queue.new_request_id()

    with pytest.raises(AttachmentNotFound):
        await queue.enqueue_batch_with_attachments(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (
                WebMessage(first_id, b"first-message", (valid_ref,)),
                WebMessage(
                    second_id,
                    b"second-message",
                    ("att_" + "0" * 32 + "_" + "0" * 32,),
                ),
            ),
            attachment_scope=scope,
        )

    assert (await queue.snapshot()).batches == ()
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(first_id, valid_ref)


@pytest.mark.asyncio
async def test_plain_enqueue_rejects_attachment_metadata_without_atomic_store():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    _registry, scope = await _attachment_scope(queue)
    staged_ref = await _stage(scope, b"private-image")
    request_id = queue.new_request_id()

    with pytest.raises(InvalidAttachment):
        await queue.enqueue_batch(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (WebMessage(request_id, b"message", (staged_ref,)),),
        )

    assert (await queue.snapshot()).batches == ()
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(request_id, staged_ref)


@pytest.mark.asyncio
async def test_repeated_reference_cannot_be_retained_by_two_batch_items():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    _registry, scope = await _attachment_scope(queue)
    shared_ref = await _stage(scope, b"shared-image")
    first_id = queue.new_request_id()
    second_id = queue.new_request_id()

    with pytest.raises(AttachmentOwnerConflict):
        await queue.enqueue_batch_with_attachments(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (
                WebMessage(first_id, b"first-message", (shared_ref,)),
                WebMessage(second_id, b"second-message", (shared_ref,)),
            ),
            attachment_scope=scope,
        )

    assert (await queue.snapshot()).batches == ()
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(first_id, shared_ref)
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(second_id, shared_ref)


@pytest.mark.asyncio
async def test_per_item_byte_limit_rejects_all_refs_without_partial_retention():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    _registry, scope = await _attachment_scope(
        queue,
        max_attachment_bytes=8,
        max_item_bytes=10,
        max_scope_bytes=16,
        max_total_bytes=16,
    )
    first_ref = await _stage(scope, b"123456")
    second_ref = await _stage(scope, b"12345")
    request_id = queue.new_request_id()

    with pytest.raises(AttachmentCapacityExceeded):
        await queue.enqueue_batch_with_attachments(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (WebMessage(request_id, b"large-image-message", (first_ref, second_ref)),),
            attachment_scope=scope,
        )

    assert (await queue.snapshot()).batches == ()
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(request_id, first_ref)
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(request_id, second_ref)


@pytest.mark.asyncio
async def test_queue_capacity_rejection_keeps_upload_unowned_for_retry_or_expiry():
    queue = WebMessageQueue(scope_id="conversation-owner-a", max_items=1)
    registry, scope = await _attachment_scope(queue)
    existing_id = queue.new_request_id()
    await queue.enqueue_batch(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (WebMessage(existing_id, b"existing-message"),),
    )
    staged_ref = await _stage(scope, b"staged-image")
    request_id = queue.new_request_id()
    operation_id = queue.new_operation_id()
    batch_id = queue.new_batch_id()

    with pytest.raises(CapacityExceeded):
        await queue.enqueue_batch_with_attachments(
            operation_id,
            batch_id,
            (WebMessage(request_id, b"new-message", (staged_ref,)),),
            attachment_scope=scope,
        )

    assert (await queue.snapshot()).batches[0].request_ids == (existing_id,)
    assert registry.used_bytes == len(b"staged-image")
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(request_id, staged_ref)

    await queue.remove_item(queue.new_operation_id(), existing_id)
    await queue.enqueue_batch_with_attachments(
        operation_id,
        batch_id,
        (WebMessage(request_id, b"new-message", (staged_ref,)),),
        attachment_scope=scope,
    )
    assert (await scope.open_for_owner(request_id, staged_ref)).data == b"staged-image"


@pytest.mark.asyncio
async def test_expired_orphan_is_rejected_but_retained_reference_survives_pruning():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    clock = _Clock()
    registry, scope = await _attachment_scope(
        queue,
        clock=clock,
        operation_retry_lease_seconds=20,
        orphan_ttl_seconds=5,
    )
    expired_ref = await _stage(scope, b"expired-image")
    expired_id = queue.new_request_id()
    clock.advance(5)

    with pytest.raises(AttachmentNotFound):
        await queue.enqueue_batch_with_attachments(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (WebMessage(expired_id, b"expired", (expired_ref,)),),
            attachment_scope=scope,
        )
    assert (await queue.snapshot()).batches == ()
    assert registry.used_bytes == 0

    retained_ref = await _stage(scope, b"retained-image")
    retained_id = queue.new_request_id()
    await queue.enqueue_batch_with_attachments(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (WebMessage(retained_id, b"retained", (retained_ref,)),),
        attachment_scope=scope,
    )
    clock.advance(6)
    await registry.prune_expired()

    assert registry.used_bytes == len(b"retained-image")
    assert (
        await scope.open_for_owner(retained_id, retained_ref)
    ).data == b"retained-image"


@pytest.mark.asyncio
async def test_cancellation_waiting_for_attachment_lock_leaves_both_ledgers_unchanged():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    registry, scope = await _attachment_scope(queue)
    staged_ref = await _stage(scope, b"cancel-image")
    request_id = queue.new_request_id()
    operation_id = queue.new_operation_id()
    batch_id = queue.new_batch_id()
    await registry._lock.acquire()
    task = asyncio.create_task(
        queue.enqueue_batch_with_attachments(
            operation_id,
            batch_id,
            (WebMessage(request_id, b"cancel-message", (staged_ref,)),),
            attachment_scope=scope,
        )
    )
    try:
        await asyncio.sleep(0)
        assert queue._lock.locked()
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        registry._lock.release()

    assert not queue._lock.locked()
    assert (await queue.snapshot()).batches == ()
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(request_id, staged_ref)
    await queue.enqueue_batch_with_attachments(
        operation_id,
        batch_id,
        (WebMessage(request_id, b"cancel-message", (staged_ref,)),),
        attachment_scope=scope,
    )
    assert (await scope.open_for_owner(request_id, staged_ref)).data == b"cancel-image"


@pytest.mark.asyncio
async def test_attachment_scope_from_another_queue_incarnation_is_denied():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    other_queue = WebMessageQueue(scope_id="conversation-owner-b")
    binding = AttachmentScopeBinding(queue.scope_id, queue.incarnation)
    other_binding = AttachmentScopeBinding(
        other_queue.scope_id, other_queue.incarnation
    )
    queues = {binding: queue, other_binding: other_queue}

    def resolve_scope(params: Mapping[str, object], _caller: object):
        return binding if params.get("scope") == "a" else other_binding

    def validate_owner_id(actual_binding: AttachmentScopeBinding, owner_id: str):
        actual_queue = queues.get(actual_binding)
        if actual_queue is None:
            return False
        try:
            actual_queue._token_issue_time(owner_id, "item")
        except Exception:
            return False
        return True

    shared_registry = WebMessageAttachmentRegistry(
        resolve_scope=resolve_scope,
        validate_owner_id=validate_owner_id,
    )
    scope = await shared_registry.bind_scope({"scope": "a"}, _Caller("conversation-a"))
    foreign_scope = await shared_registry.bind_scope(
        {"scope": "b"}, _Caller("conversation-b")
    )
    staged_ref = await _stage(scope, b"private-image")
    request_id = queue.new_request_id()

    await queue.enqueue_batch_with_attachments(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (WebMessage(request_id, b"message", (staged_ref,)),),
        attachment_scope=scope,
    )

    with pytest.raises(AttachmentAuthorizationError):
        await foreign_scope.open_for_owner(request_id, staged_ref)
    foreign_request_id = other_queue.new_request_id()
    with pytest.raises(AttachmentNotFound):
        await foreign_scope.open_for_owner(foreign_request_id, staged_ref)

    with pytest.raises(AttachmentAuthorizationError):
        await other_queue.enqueue_batch_with_attachments(
            other_queue.new_operation_id(),
            other_queue.new_batch_id(),
            (WebMessage(foreign_request_id, b"other-message", (staged_ref,)),),
            attachment_scope=scope,
        )


@pytest.mark.asyncio
async def test_attachment_claims_keep_refs_private_from_change_notifications():
    queue_registry = WebMessageQueueRegistry(max_scopes=2)
    server = RpcServer()
    changes = asyncio.Queue(maxsize=8)
    register_web_message_queue_handlers(
        server,
        queue_registry,
        resolve_scope=lambda _params, _caller: QueueScopeBinding(
            "conversation-owner-a", "conversation-a"
        ),
        on_change=changes.put_nowait,
    )

    async with queue_registry.acquire("conversation-owner-a") as queue:
        _attachment_registry, scope = await _attachment_scope(queue)
        staged_ref = await _stage(scope, b"private-notification-image")
        request_id = queue.new_request_id()
        await queue.enqueue_batch_with_attachments(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (WebMessage(request_id, b"private-message", (staged_ref,)),),
            attachment_scope=scope,
        )
        claim_operation_id = queue.new_operation_id()

    claimed = await _rpc_call(
        server,
        "state.web_queue.claim_next",
        {
            "expected_conversation_uid": "conversation-a",
            "operation_id": claim_operation_id,
        },
    )
    assert claimed["error"] is None
    notification = changes.get_nowait()
    assert set(notification) == {"scope_id", "incarnation", "revision"}
    assert staged_ref not in repr(notification)
    assert "private-notification-image" not in repr(notification)


@pytest.mark.asyncio
async def test_terminal_outcomes_keep_refs_until_handoff_and_remove_releases_exact_item():
    queue = WebMessageQueue(scope_id="conversation-owner-a")
    registry, scope = await _attachment_scope(queue)
    staged_ref = await _stage(scope, b"permission-image")
    request_id = queue.new_request_id()
    await queue.enqueue_batch_with_attachments(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (WebMessage(request_id, b"permission-message", (staged_ref,)),),
        attachment_scope=scope,
    )

    claim = await queue.claim_next(queue.new_operation_id())
    await queue.release_claim(queue.new_operation_id(), claim.claim_id or "")
    assert (
        await scope.open_for_owner(request_id, staged_ref)
    ).data == b"permission-image"

    claim = await queue.claim_next(queue.new_operation_id())
    attempt = await queue.claim_next_item(
        queue.new_operation_id(), claim.claim_id or ""
    )
    await queue.settle_item(
        queue.new_operation_id(), attempt.attempt_id or "", ClaimOutcome.NOT_SENT
    )
    assert (
        await scope.open_for_owner(request_id, staged_ref)
    ).data == b"permission-image"

    claim = await queue.claim_next(queue.new_operation_id())
    attempt = await queue.claim_next_item(
        queue.new_operation_id(), claim.claim_id or ""
    )
    await queue.settle_item(
        queue.new_operation_id(), attempt.attempt_id or "", ClaimOutcome.IN_DOUBT
    )
    assert (
        await scope.open_for_owner(request_id, staged_ref)
    ).data == b"permission-image"

    await queue.settle_item(
        queue.new_operation_id(), attempt.attempt_id or "", ClaimOutcome.ACCEPTED
    )
    # Accepted bytes remain pinned until the durable history handoff explicitly
    # releases them; this lane does not implement that handoff.
    assert (
        await scope.open_for_owner(request_id, staged_ref)
    ).data == b"permission-image"
    await scope.release_owner(request_id)
    assert registry.used_bytes == 0

    removable_ref = await _stage(scope, b"removed-image")
    survivor_ref = await _stage(scope, b"survivor-image")
    removable_id = queue.new_request_id()
    survivor_id = queue.new_request_id()
    await queue.enqueue_batch_with_attachments(
        queue.new_operation_id(),
        queue.new_batch_id(),
        (
            WebMessage(removable_id, b"removable-message", (removable_ref,)),
            WebMessage(survivor_id, b"survivor-message", (survivor_ref,)),
        ),
        attachment_scope=scope,
    )
    with pytest.raises(InvalidAttachment):
        await queue.remove_item(queue.new_operation_id(), removable_id)
    assert (
        await scope.open_for_owner(removable_id, removable_ref)
    ).data == b"removed-image"
    assert (
        await scope.open_for_owner(survivor_id, survivor_ref)
    ).data == b"survivor-image"

    removed = await queue.remove_item_with_attachments(
        queue.new_operation_id(), removable_id, attachment_scope=scope
    )
    replay = await queue.remove_item_with_attachments(
        removed.operation_id, removable_id, attachment_scope=scope
    )

    assert replay == removed
    assert registry.used_bytes == len(b"survivor-image")
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner(removable_id, removable_ref)
    assert (
        await scope.open_for_owner(survivor_id, survivor_ref)
    ).data == b"survivor-image"
