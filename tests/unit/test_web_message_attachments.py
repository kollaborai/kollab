"""Bounded daemon-side staging for queued WebUI image attachments."""

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from dataclasses import dataclass

import pytest

from kollabor.state.web_message_attachments import (
    DEFAULT_MAX_ATTACHMENT_BYTES,
    AttachmentAuthorizationError,
    AttachmentCapacityExceeded,
    AttachmentIdempotencyConflict,
    AttachmentNotFound,
    AttachmentOwnerConflict,
    AttachmentScopeBinding,
    InvalidAttachment,
    InvalidAttachmentOperation,
    StaleAttachmentOperation,
    WebMessageAttachmentRegistry,
)


@dataclass(frozen=True)
class _Caller:
    is_local_operator: bool
    principal_id: str
    conversation_uid: str
    queue_incarnation: str


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _harness(**limits):
    clock = _Clock()
    valid_owners: dict[AttachmentScopeBinding, set[str]] = defaultdict(set)

    def resolve_scope(params, caller):
        if (
            not caller
            or not caller.is_local_operator
            or not caller.principal_id
            or not caller.conversation_uid
            or params.get("expected_conversation_uid") != caller.conversation_uid
        ):
            raise AttachmentAuthorizationError("attachment access denied")
        scope_id = hashlib.sha256(
            f"{caller.principal_id}\0{caller.conversation_uid}".encode()
        ).hexdigest()
        # The request's scope_id field is deliberately ignored. Incarnation is
        # supplied by the trusted daemon queue owner, not the RPC parameters.
        return AttachmentScopeBinding(scope_id, caller.queue_incarnation)

    registry = WebMessageAttachmentRegistry(
        resolve_scope=resolve_scope,
        validate_owner_id=lambda binding, owner_id: owner_id in valid_owners[binding],
        clock=clock,
        **limits,
    )

    async def bind(uid="conversation-a", *, principal="uid:501", incarnation="queue-a"):
        caller = _Caller(True, principal, uid, incarnation)
        scope = await registry.bind_scope(
            {"expected_conversation_uid": uid, "scope_id": "attacker-selected"},
            caller,
        )
        return scope

    return registry, clock, valid_owners, bind


@pytest.mark.asyncio
async def test_stage_replay_is_idempotent_and_conflicting_bytes_are_rejected():
    registry, _, _, bind = _harness()
    scope = await bind()
    operation_id = scope.new_operation_id()

    first = await scope.stage(operation_id, "image/png", b"first-image")
    replay = await scope.stage(operation_id, "image/png", b"first-image")

    assert replay == first
    assert registry.used_bytes == len(b"first-image")
    with pytest.raises(AttachmentIdempotencyConflict):
        await scope.stage(operation_id, "image/png", b"different-image")
    assert registry.used_bytes == len(b"first-image")
    assert registry.attachment_count == 1


@pytest.mark.asyncio
async def test_rejects_bad_media_and_single_image_over_limit_without_mutation():
    registry, _, _, bind = _harness(max_attachment_bytes=8)
    scope = await bind()

    with pytest.raises(InvalidAttachment):
        await scope.stage(scope.new_operation_id(), "image/svg+xml", b"svg")
    with pytest.raises(InvalidAttachment):
        await scope.stage(scope.new_operation_id(), "image/png", b"")
    with pytest.raises(AttachmentCapacityExceeded):
        await scope.stage(scope.new_operation_id(), "image/png", b"123456789")

    assert registry.used_bytes == 0
    assert registry.attachment_count == 0


@pytest.mark.asyncio
async def test_accepts_ten_mib_image_and_rejects_one_byte_over_default_limit():
    registry, _, _, bind = _harness()
    scope = await bind()
    image = b"x" * DEFAULT_MAX_ATTACHMENT_BYTES

    receipt = await scope.stage(scope.new_operation_id(), "image/png", image)
    with pytest.raises(AttachmentCapacityExceeded):
        await scope.stage(scope.new_operation_id(), "image/png", image + b"x")

    assert receipt.size == DEFAULT_MAX_ATTACHMENT_BYTES
    assert registry.used_bytes == DEFAULT_MAX_ATTACHMENT_BYTES
    assert registry.attachment_count == 1


@pytest.mark.asyncio
async def test_scope_and_global_caps_reject_before_partial_admission():
    registry, _, _, bind = _harness(
        max_attachment_bytes=10,
        max_item_bytes=10,
        max_scope_bytes=10,
        max_total_bytes=12,
        max_attachments_per_item=3,
        max_attachments=4,
        max_attachments_per_scope=3,
    )
    first_scope = await bind("conversation-a")
    second_scope = await bind("conversation-b", incarnation="queue-b")
    await first_scope.stage(first_scope.new_operation_id(), "image/png", b"12345678")
    second_op = second_scope.new_operation_id()

    with pytest.raises(AttachmentCapacityExceeded):
        await second_scope.stage(second_op, "image/png", b"12345")
    assert registry.used_bytes == 8
    assert registry.attachment_count == 1

    # A capacity rejection did not consume the operation ID.
    await second_scope.stage(second_op, "image/png", b"1234")
    assert registry.used_bytes == 12
    assert registry.attachment_count == 2


@pytest.mark.asyncio
async def test_scope_count_capacity_releases_only_after_orphan_expiry():
    registry, clock, _, bind = _harness(
        max_attachment_bytes=8,
        max_item_bytes=16,
        max_scope_bytes=16,
        max_total_bytes=32,
        max_attachments_per_item=2,
        max_attachments_per_scope=2,
        max_attachments=2,
        max_scopes=1,
        operation_retry_lease_seconds=3,
        orphan_ttl_seconds=1,
    )
    first_scope = await bind("conversation-a")
    second_scope = await bind("conversation-b", incarnation="queue-b")
    await first_scope.stage(first_scope.new_operation_id(), "image/png", b"a")
    second_operation_id = second_scope.new_operation_id()

    with pytest.raises(AttachmentCapacityExceeded):
        await second_scope.stage(second_operation_id, "image/png", b"b")
    assert registry.scope_count == 1

    clock.advance(2)
    assert await registry.prune_expired() == 1
    await second_scope.stage(second_operation_id, "image/png", b"b")
    assert registry.scope_count == 1
    assert registry.used_bytes == 1


@pytest.mark.asyncio
async def test_concurrent_same_operation_writes_one_attachment():
    registry, _, _, bind = _harness()
    scope = await bind()
    operation_id = scope.new_operation_id()

    receipts = await asyncio.gather(
        *(scope.stage(operation_id, "image/png", b"one-image") for _ in range(12))
    )

    assert all(receipt == receipts[0] for receipt in receipts)
    assert registry.attachment_count == 1
    assert registry.used_bytes == len(b"one-image")


@pytest.mark.asyncio
async def test_concurrent_capacity_race_admits_no_overflow():
    registry, _, _, bind = _harness(
        max_attachment_bytes=10,
        max_item_bytes=10,
        max_scope_bytes=10,
        max_total_bytes=10,
    )
    scope = await bind()
    operation_ids = [scope.new_operation_id(), scope.new_operation_id()]

    outcomes = await asyncio.gather(
        *(
            scope.stage(operation_id, "image/png", b"1234567")
            for operation_id in operation_ids
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert (
        sum(isinstance(outcome, AttachmentCapacityExceeded) for outcome in outcomes)
        == 1
    )
    assert registry.used_bytes == 7
    assert registry.attachment_count == 1


@pytest.mark.asyncio
async def test_retained_attachment_survives_long_wait_and_retry_lease_expiry():
    registry, clock, owners, bind = _harness(
        max_attachment_bytes=32,
        orphan_ttl_seconds=10,
        operation_retry_lease_seconds=10,
    )
    scope = await bind()
    owner = "item-a"
    owners[scope.binding].add(owner)
    operation_id = scope.new_operation_id()
    receipt = await scope.stage(operation_id, "image/jpg", b"queued-image")
    await scope.retain(owner, [receipt.ref])

    clock.advance(24 * 60 * 60)
    await registry.prune_expired()

    stored = await scope.open_for_owner(owner, receipt.ref)
    assert stored.data == b"queued-image"
    assert stored.mime == "image/jpeg"
    assert await scope.stage(operation_id, "image/jpeg", b"queued-image") == receipt
    assert registry.used_bytes == len(b"queued-image")


@pytest.mark.asyncio
async def test_unreferenced_upload_expires_and_old_id_cannot_recreate_it():
    registry, clock, owners, bind = _harness(
        max_attachment_bytes=32,
        orphan_ttl_seconds=10,
        operation_retry_lease_seconds=10,
    )
    scope = await bind()
    owners[scope.binding].add("item-a")
    operation_id = scope.new_operation_id()
    receipt = await scope.stage(operation_id, "image/png", b"temporary")

    clock.advance(11)
    assert await registry.prune_expired() == 1
    with pytest.raises(StaleAttachmentOperation):
        await scope.stage(operation_id, "image/png", b"temporary")
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner("item-a", receipt.ref)
    assert registry.used_bytes == 0
    assert registry.attachment_count == 0


@pytest.mark.asyncio
async def test_release_owner_removes_only_its_attachments_and_is_idempotent():
    registry, _, owners, bind = _harness()
    scope = await bind()
    owners[scope.binding].update({"item-a", "item-b"})
    operation_a = scope.new_operation_id()
    a = await scope.stage(operation_a, "image/png", b"a")
    b = await scope.stage(scope.new_operation_id(), "image/png", b"bb")
    await scope.retain("item-a", [a.ref])
    await scope.retain("item-b", [b.ref])
    with pytest.raises(AttachmentOwnerConflict):
        await scope.retain("item-b", [a.ref])

    assert await scope.release_owner("item-a") == 1
    assert await scope.release_owner("item-a") == 0
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner("item-a", a.ref)
    with pytest.raises(StaleAttachmentOperation):
        await scope.stage(operation_a, "image/png", b"a")
    assert (await scope.open_for_owner("item-b", b.ref)).data == b"bb"
    assert registry.used_bytes == 2
    assert registry.attachment_count == 1


@pytest.mark.asyncio
async def test_item_byte_cap_rejects_all_refs_without_partial_retention():
    registry, _, owners, bind = _harness(
        max_attachment_bytes=8,
        max_item_bytes=10,
        max_scope_bytes=20,
        max_total_bytes=40,
        max_attachments_per_item=3,
        max_attachments_per_scope=4,
    )
    scope = await bind()
    owners[scope.binding].update({"item-a", "item-b"})
    first = await scope.stage(scope.new_operation_id(), "image/png", b"1234567")
    second = await scope.stage(scope.new_operation_id(), "image/png", b"1234")

    with pytest.raises(AttachmentCapacityExceeded):
        await scope.retain("item-a", [first.ref, second.ref])
    with pytest.raises(AttachmentNotFound):
        await scope.open_for_owner("item-a", first.ref)
    assert await scope.retain("item-b", [first.ref]) is True
    assert registry.used_bytes == 11


@pytest.mark.asyncio
async def test_refs_and_owner_ids_cannot_cross_scope_or_be_spoofed():
    registry, _, owners, bind = _harness()
    scope_a = await bind("conversation-a")
    scope_b = await bind("conversation-b", incarnation="queue-b")
    other_principal = await bind(
        "conversation-a", principal="uid:502", incarnation="queue-a"
    )
    restarted_scope_a = await bind("conversation-a", incarnation="queue-a-new")
    owners[scope_a.binding].add("item-a")
    owners[scope_b.binding].add("item-b")
    owners[other_principal.binding].add("item-other-principal")
    owners[restarted_scope_a.binding].add("item-a-new")
    receipt = await scope_a.stage(scope_a.new_operation_id(), "image/png", b"private")

    with pytest.raises(AttachmentNotFound):
        await scope_b.open_for_owner("item-b", receipt.ref)
    with pytest.raises(AttachmentNotFound):
        await scope_b.retain("item-b", [receipt.ref])
    with pytest.raises(AttachmentNotFound):
        await restarted_scope_a.open_for_owner("item-a-new", receipt.ref)
    with pytest.raises(AttachmentNotFound):
        await other_principal.open_for_owner("item-other-principal", receipt.ref)
    with pytest.raises(AttachmentAuthorizationError):
        await scope_a.retain("caller-invented-id", [receipt.ref])
    with pytest.raises(InvalidAttachmentOperation):
        await scope_b.stage(scope_a.new_operation_id(), "image/png", b"private")

    assert registry.used_bytes == len(b"private")
    assert registry.attachment_count == 1


@pytest.mark.asyncio
async def test_retain_validates_every_ref_before_pinning_any():
    registry, _, owners, bind = _harness()
    scope_a = await bind("conversation-a")
    scope_b = await bind("conversation-b", incarnation="queue-b")
    owners[scope_a.binding].update({"item-a", "item-a2"})
    owners[scope_b.binding].add("item-b")
    local_receipt = await scope_a.stage(
        scope_a.new_operation_id(), "image/png", b"local"
    )
    foreign_receipt = await scope_b.stage(
        scope_b.new_operation_id(), "image/png", b"foreign"
    )

    with pytest.raises(AttachmentNotFound):
        await scope_a.retain("item-a", [local_receipt.ref, foreign_receipt.ref])
    with pytest.raises(AttachmentNotFound):
        await scope_a.open_for_owner("item-a", local_receipt.ref)
    assert await scope_a.retain("item-a2", [local_receipt.ref]) is True
    assert (await scope_a.open_for_owner("item-a2", local_receipt.ref)).data == b"local"
    assert registry.used_bytes == len(b"local") + len(b"foreign")


@pytest.mark.asyncio
async def test_scope_binding_fails_closed_for_remote_or_stale_conversation():
    registry, _, _, _ = _harness()
    trusted = _Caller(True, "uid:501", "conversation-a", "queue-a")
    first = await registry.bind_scope(
        {"expected_conversation_uid": "conversation-a", "scope_id": "forged-a"},
        trusted,
    )
    same_scope = await registry.bind_scope(
        {"expected_conversation_uid": "conversation-a", "scope_id": "forged-b"},
        trusted,
    )
    assert first.binding == same_scope.binding

    with pytest.raises(AttachmentAuthorizationError):
        await registry.bind_scope(
            {"expected_conversation_uid": "conversation-a"},
            _Caller(False, "uid:501", "conversation-a", "queue-a"),
        )
    with pytest.raises(AttachmentAuthorizationError):
        await registry.bind_scope(
            {"expected_conversation_uid": "conversation-b", "scope_id": "forged"},
            _Caller(True, "uid:501", "conversation-a", "queue-a"),
        )
