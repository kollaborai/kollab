"""Bounded, in-memory queue state for daemon-owned WebUI message batches.

This module is a state primitive only. Its owner must create one instance for
each canonical conversation/authorized-principal scope and connect claims to
the native or ACP admission path. Queue contents and operation receipts live
only as long as this instance; they are not persisted across daemon restarts.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import secrets
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import islice

from .web_message_attachments import (
    DEFAULT_MAX_ATTACHMENTS_PER_ITEM,
    AttachmentAuthorizationError,
    InvalidAttachment,
    _BoundAttachmentScope,
)

DEFAULT_MAX_ITEMS = 100
DEFAULT_MAX_BATCH_ITEMS = 20
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024
DEFAULT_MAX_QUEUE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_OPERATIONS = 4096
DEFAULT_OPERATION_RETRY_LEASE_SECONDS = 900.0

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class WebMessageQueueError(Exception):
    """Base error. Messages and payload bytes are never included in errors."""


class InvalidIdentifier(WebMessageQueueError):
    """An operation, batch, item, or scope ID is invalid."""


class IdempotencyConflict(WebMessageQueueError):
    """An operation ID was reused with different arguments."""


class BatchConflict(WebMessageQueueError):
    """A batch ID is already bound to another enqueue operation."""


class ItemConflict(WebMessageQueueError):
    """A request ID is already bound to another queued item."""


class CapacityExceeded(WebMessageQueueError):
    """An item, batch, byte, or pending-queue bound was exceeded."""


class OperationLedgerFull(WebMessageQueueError):
    """The bounded idempotency ledger or claim reservation is full."""


class StaleOperation(WebMessageQueueError):
    """An operation ID is outside its explicit retry lease."""


class StaleIdentifier(WebMessageQueueError):
    """A newly admitted entity ID is outside its creation lease."""


class IdentifierLedgerFull(WebMessageQueueError):
    """The bounded batch/item identity ledger is full; no mutation was made."""


class ClaimBusy(WebMessageQueueError):
    """Another batch or item is dispatching."""


class QueueInDoubt(WebMessageQueueError):
    """An uncertain item blocks later work pending explicit resolution."""


class QueueEmpty(WebMessageQueueError):
    """No queued batch is ready to claim."""


class BatchNotFound(WebMessageQueueError):
    """The named batch is not pending in this queue scope."""


class ItemNotFound(WebMessageQueueError):
    """The named item is not pending in this queue scope."""


class ItemNotQueued(WebMessageQueueError):
    """The item is claimed or in doubt and cannot be removed."""


class ClaimNotFound(WebMessageQueueError):
    """The claim is not the current unsettled claim."""


class ClaimNotDispatchable(WebMessageQueueError):
    """The claim is in doubt or no longer owns pending payloads."""


class OutOfOrderClaim(WebMessageQueueError):
    """A non-priority claim attempted to bypass an older queued batch."""


class ClaimOutcome(StrEnum):
    ACCEPTED = "accepted"
    NOT_SENT = "not_sent"
    IN_DOUBT = "in_doubt"


class BatchStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    IN_DOUBT = "in_doubt"


class ItemStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    IN_DOUBT = "in_doubt"


@dataclass(frozen=True, slots=True)
class WebMessage:
    """One opaque queued message and its ordered staged attachment refs."""

    request_id: str
    payload: bytes = field(repr=False)
    attachment_refs: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class QueueItemSnapshot:
    """Display-safe per-item progress plus internal repr-hidden dispatch data."""

    request_id: str
    status: ItemStatus
    attempt_id: str | None
    payload: bytes = field(repr=False)
    attachment_refs: tuple[str, ...] = field(default=(), repr=False)
    text_preview: str = ""
    attachment_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueueReceipt:
    """Idempotent result of one committed queue mutation."""

    scope_id: str
    incarnation: str
    operation_id: str
    revision: int
    status: str
    batch_id: str | None = None
    request_ids: tuple[str, ...] = ()
    claim_id: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimPayload:
    """Payload for one active, non-ambiguous item attempt."""

    scope_id: str
    incarnation: str
    claim_id: str
    attempt_id: str
    batch_id: str
    items: tuple[WebMessage, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class QueueBatchSnapshot:
    """One visible batch in queue order; message bytes remain repr-hidden."""

    batch_id: str
    sequence: int
    status: BatchStatus
    request_ids: tuple[str, ...]
    claim_id: str | None
    items: tuple[QueueItemSnapshot, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """Consistent snapshot for page reload and multi-client reconciliation."""

    scope_id: str
    incarnation: str
    revision: int
    batches: tuple[QueueBatchSnapshot, ...]


@dataclass(slots=True)
class _StoredBatch:
    batch_id: str
    sequence: int
    items: list[_StoredItem]
    status: BatchStatus = BatchStatus.QUEUED
    claim_id: str | None = None
    priority_rank: int | None = None

    @property
    def payload_bytes(self) -> int:
        return sum(len(item.payload) for item in self.items)


@dataclass(frozen=True, slots=True)
class _OperationRecord:
    fingerprint: str
    receipt: QueueReceipt
    kind: str
    expires_at_ns: int


@dataclass(frozen=True, slots=True)
class _WorkerSettlementPin:
    attempt_id: str
    outcome: ClaimOutcome
    receipt: QueueReceipt


@dataclass(slots=True)
class _ClaimReservation:
    release_slots: int
    item_slots: dict[str, int]
    dispatched: bool = False


@dataclass(slots=True)
class _StoredItem:
    request_id: str
    payload: bytes = field(repr=False)
    attachment_refs: tuple[str, ...] = field(default=(), repr=False)
    attachment_labels: tuple[str, ...] = ()
    status: ItemStatus = ItemStatus.QUEUED
    attempt_id: str | None = None


class WebMessageQueue:
    """A bounded, serialized queue for opaque WebUI message envelopes.

    The instance is scoped to one canonical conversation and authorized
    principal. All public methods are async and serialized by one lock so
    enqueue, claim, settle, and remove races have one atomic winner.
    """

    def __init__(
        self,
        *,
        scope_id: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_batch_items: int = DEFAULT_MAX_BATCH_ITEMS,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES,
        max_operations: int = DEFAULT_MAX_OPERATIONS,
        max_entity_ids: int | None = None,
        operation_retry_lease_seconds: float = DEFAULT_OPERATION_RETRY_LEASE_SECONDS,
    ) -> None:
        self._validate_identifier(scope_id)
        for bound in (
            max_items,
            max_batch_items,
            max_payload_bytes,
            max_queue_bytes,
            max_operations,
        ):
            if isinstance(bound, bool) or not isinstance(bound, int) or bound <= 0:
                raise ValueError("queue bounds must be positive integers")
        if (
            isinstance(operation_retry_lease_seconds, bool)
            or not isinstance(operation_retry_lease_seconds, (int, float))
            or not math.isfinite(operation_retry_lease_seconds)
            or operation_retry_lease_seconds <= 0
        ):
            raise ValueError("operation retry lease must be a positive finite number")
        retry_lease_ns = operation_retry_lease_seconds * 1e9
        if not math.isfinite(retry_lease_ns) or retry_lease_ns < 1:
            raise ValueError("operation retry lease must be at least one nanosecond")
        if max_entity_ids is None:
            max_entity_ids = 2 * max_operations * (max_batch_items + 1) + 2 * max_items
        if isinstance(max_entity_ids, bool) or not isinstance(max_entity_ids, int):
            raise ValueError("entity identifier bound must be a positive integer")
        if max_entity_ids <= 0:
            raise ValueError("entity identifier bound must be a positive integer")

        self.scope_id = scope_id
        self.incarnation = uuid.uuid4().hex
        self._id_mac_key = secrets.token_bytes(32)
        self._last_id_issued_at_ns: int | None = None
        self._max_items = max_items
        self._max_batch_items = max_batch_items
        self._max_payload_bytes = max_payload_bytes
        self._max_queue_bytes = max_queue_bytes
        self._max_operations = max_operations
        self._max_entity_ids = max_entity_ids
        self._operation_retry_lease_ns = int(retry_lease_ns)
        self._lock = asyncio.Lock()
        self._batches: dict[str, _StoredBatch] = {}
        self._operations: dict[str, _OperationRecord] = {}
        self._claim_reservations: dict[str, _ClaimReservation] = {}
        self._worker_settlement_pin: _WorkerSettlementPin | None = None
        self._last_acknowledged_worker_settlement: _WorkerSettlementPin | None = None
        self._reserved_operations = 0
        self._claim_states: dict[str, str] = {}
        self._item_attempt_states: dict[str, str] = {}
        self._batch_owners: dict[str, str] = {}
        self._item_owners: dict[str, str] = {}
        self._active_claim: str | None = None
        self._active_batch_id: str | None = None
        self._active_item_attempt_id: str | None = None
        self._active_item_request_id: str | None = None
        self._revision = 0
        self._next_sequence = 1
        self._next_priority_rank = 1
        self._pending_items = 0
        self._pending_bytes = 0

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            raise InvalidIdentifier("invalid queue identifier")

    def _new_token(self, kind: str) -> str:
        issued_at_ns = time.monotonic_ns()
        self._last_id_issued_at_ns = issued_at_ns
        unsigned = f"{kind}-{self.incarnation}-{issued_at_ns}-{uuid.uuid4().hex}"
        mac = (
            hmac.new(
                self._id_mac_key,
                unsigned.encode("ascii"),
                hashlib.sha256,
            )
            .digest()[:16]
            .hex()
        )
        return f"{unsigned}-{mac}"

    def new_operation_id(self) -> str:
        """Mint an opaque operation ID; callers must reuse it for RPC retries."""
        return self._new_token("op")

    def new_batch_id(self) -> str:
        """Mint a batch ID scoped to this daemon incarnation and retry lease."""
        return self._new_token("batch")

    def new_request_id(self) -> str:
        """Mint an item ID scoped to this daemon incarnation and retry lease."""
        return self._new_token("item")

    def _token_issue_time(self, value: str, kind: str) -> int:
        self._validate_identifier(value)
        parts = value.split("-")
        if len(parts) != 5 or parts[0] != kind or parts[1] != self.incarnation:
            raise InvalidIdentifier("identifier belongs to another queue incarnation")
        if (
            re.fullmatch(r"[0-9]+", parts[2]) is None
            or re.fullmatch(r"[0-9a-f]{32}", parts[3]) is None
            or re.fullmatch(r"[0-9a-f]{32}", parts[4]) is None
        ):
            raise InvalidIdentifier("invalid queue identifier")
        try:
            issued_at_ns = int(parts[2])
        except ValueError as exc:
            raise InvalidIdentifier("invalid queue identifier") from exc
        unsigned = "-".join(parts[:4])
        expected_mac = (
            hmac.new(
                self._id_mac_key,
                unsigned.encode("ascii"),
                hashlib.sha256,
            )
            .digest()[:16]
            .hex()
        )
        if not hmac.compare_digest(parts[4], expected_mac):
            raise InvalidIdentifier("invalid queue identifier")
        if issued_at_ns <= 0:
            raise InvalidIdentifier("invalid queue identifier")
        if issued_at_ns > time.monotonic_ns():
            raise InvalidIdentifier("identifier timestamp is in the future")
        return issued_at_ns

    def _validate_operation_id(self, operation_id: str) -> int:
        issued_at_ns = self._token_issue_time(operation_id, "op")
        if time.monotonic_ns() - issued_at_ns >= self._operation_retry_lease_ns:
            if not self._is_operation_pinned(operation_id):
                raise StaleOperation("operation retry lease has expired")
        return issued_at_ns

    def _validate_new_entity_id(self, value: str, kind: str) -> int:
        issued_at_ns = self._token_issue_time(value, kind)
        if time.monotonic_ns() - issued_at_ns >= self._operation_retry_lease_ns:
            raise StaleIdentifier("entity ID creation lease has expired")
        return issued_at_ns

    @staticmethod
    def _payload_fingerprint(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _fingerprint(operation: str, *values: object) -> str:
        raw = json.dumps(
            [operation, *values],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(raw).hexdigest()

    def _is_operation_pinned(self, operation_id: str) -> bool:
        pending_settlement = self._worker_settlement_pin
        return operation_id in (self._active_claim, self._active_item_attempt_id) or (
            pending_settlement is not None
            and pending_settlement.receipt.operation_id == operation_id
        )

    def _ensure_no_unacknowledged_worker_settlement(self) -> None:
        if self._worker_settlement_pin is not None:
            raise ClaimBusy("a worker settlement receipt is awaiting acknowledgement")

    def _prune_expired(self) -> None:
        now_ns = time.monotonic_ns()
        expired = [
            operation_id
            for operation_id, record in self._operations.items()
            if record.expires_at_ns <= now_ns
            and not self._is_operation_pinned(operation_id)
        ]
        for operation_id in expired:
            record = self._operations.pop(operation_id)
            if record.kind == "batch_claim":
                self._claim_states.pop(operation_id, None)
            elif record.kind == "item_claim":
                self._item_attempt_states.pop(operation_id, None)

        pending_batches = set(self._batches)
        pending_items = {
            item.request_id for batch in self._batches.values() for item in batch.items
        }
        for batch_id in tuple(self._batch_owners):
            issued_at_ns = self._token_issue_time(batch_id, "batch")
            if (
                batch_id not in pending_batches
                and now_ns - issued_at_ns >= self._operation_retry_lease_ns
            ):
                del self._batch_owners[batch_id]
        for request_id in tuple(self._item_owners):
            issued_at_ns = self._token_issue_time(request_id, "item")
            if (
                request_id not in pending_items
                and now_ns - issued_at_ns >= self._operation_retry_lease_ns
            ):
                del self._item_owners[request_id]

        for claim_id, reservation in tuple(self._claim_reservations.items()):
            if not self._is_operation_pinned(claim_id):
                self._free_claim_reservation(claim_id)

    def _replay(self, operation_id: str, fingerprint: str) -> QueueReceipt | None:
        self._prune_expired()
        self._validate_operation_id(operation_id)
        record = self._operations.get(operation_id)
        if record is None:
            return None
        if record.fingerprint != fingerprint:
            raise IdempotencyConflict("operation ID was reused with different input")
        if record.kind in ("batch_claim", "item_claim"):
            states = (
                self._claim_states
                if record.kind == "batch_claim"
                else self._item_attempt_states
            )
            current_status = states.get(operation_id, record.receipt.status)
            request_ids = record.receipt.request_ids
            if record.kind == "batch_claim":
                batch = self._batches.get(record.receipt.batch_id or "")
                request_ids = (
                    tuple(item.request_id for item in batch.items)
                    if batch is not None
                    else ()
                )
            return replace(
                record.receipt,
                status=current_status,
                revision=self._revision,
                request_ids=request_ids,
            )
        return record.receipt

    def _ensure_operation_capacity(self, additional_slots: int = 1) -> None:
        self._prune_expired()
        occupied = len(self._operations) + self._reserved_operations + additional_slots
        if occupied > self._max_operations:
            raise OperationLedgerFull("operation receipt capacity is full")

    def _reserve_claim_lifecycle(
        self,
        claim_id: str,
        items: Sequence[_StoredItem],
    ) -> None:
        self._ensure_operation_capacity(2 + 3 * len(items))
        item_slots = {item.request_id: 3 for item in items}
        reservation = _ClaimReservation(release_slots=1, item_slots=item_slots)
        self._claim_reservations[claim_id] = reservation
        self._reserved_operations += reservation.release_slots + sum(
            item_slots.values()
        )

    def _consume_item_reservation(self, claim_id: str, request_id: str) -> None:
        reservation = self._claim_reservations.get(claim_id)
        remaining = reservation.item_slots.get(request_id, 0) if reservation else 0
        if reservation is None or remaining <= 0:
            raise OperationLedgerFull("claim has no reserved item receipt capacity")
        reservation.item_slots[request_id] = remaining - 1
        self._reserved_operations -= 1

    def _free_item_reservation(self, claim_id: str, request_id: str) -> None:
        reservation = self._claim_reservations.get(claim_id)
        if reservation is None:
            return
        remaining = reservation.item_slots.pop(request_id, 0)
        self._reserved_operations -= remaining

    def _consume_release_reservation(self, claim_id: str) -> None:
        reservation = self._claim_reservations.get(claim_id)
        if reservation is None or reservation.release_slots != 1:
            raise OperationLedgerFull("claim has no reserved release receipt capacity")
        reservation.release_slots = 0
        self._reserved_operations -= 1

    def _free_claim_reservation(self, claim_id: str) -> None:
        reservation = self._claim_reservations.pop(claim_id, None)
        if reservation is None:
            return
        self._reserved_operations -= reservation.release_slots + sum(
            reservation.item_slots.values()
        )

    def _record(
        self,
        fingerprint: str,
        receipt: QueueReceipt,
        *,
        kind: str,
    ) -> QueueReceipt:
        issued_at_ns = self._token_issue_time(receipt.operation_id, "op")
        self._operations[receipt.operation_id] = _OperationRecord(
            fingerprint=fingerprint,
            receipt=receipt,
            kind=kind,
            expires_at_ns=issued_at_ns + self._operation_retry_lease_ns,
        )
        return receipt

    def _receipt(
        self,
        operation_id: str,
        status: str,
        *,
        batch_id: str | None = None,
        request_ids: tuple[str, ...] = (),
        claim_id: str | None = None,
        attempt_id: str | None = None,
    ) -> QueueReceipt:
        return QueueReceipt(
            scope_id=self.scope_id,
            incarnation=self.incarnation,
            operation_id=operation_id,
            revision=self._revision,
            status=status,
            batch_id=batch_id,
            request_ids=request_ids,
            claim_id=claim_id,
            attempt_id=attempt_id,
        )

    def _ordered_batches(self) -> list[_StoredBatch]:
        return sorted(
            self._batches.values(),
            key=lambda batch: (
                0 if batch.priority_rank is not None else 1,
                -(batch.priority_rank or 0),
                batch.sequence,
            ),
        )

    def _snapshot_unlocked(self) -> QueueSnapshot:
        return QueueSnapshot(
            scope_id=self.scope_id,
            incarnation=self.incarnation,
            revision=self._revision,
            batches=tuple(
                QueueBatchSnapshot(
                    batch_id=batch.batch_id,
                    sequence=batch.sequence,
                    status=batch.status,
                    request_ids=tuple(item.request_id for item in batch.items),
                    claim_id=batch.claim_id,
                    items=tuple(
                        QueueItemSnapshot(
                            request_id=item.request_id,
                            status=item.status,
                            attempt_id=item.attempt_id,
                            text_preview=self._text_preview(item.payload),
                            attachment_labels=item.attachment_labels,
                            payload=item.payload,
                            attachment_refs=item.attachment_refs,
                        )
                        for item in batch.items
                    ),
                )
                for batch in self._ordered_batches()
            ),
        )

    async def snapshot(self) -> QueueSnapshot:
        """Return a display/reconciliation view for an authorized caller.

        The internal snapshot retains bytes and refs for worker bookkeeping,
        but the wire projection exposes only a bounded text preview and safe
        attachment basenames. Provider input may come only from ``item_payload``
        for an attempt that is currently dispatching. This state primitive
        does not enforce RPC caller authorization.
        """
        async with self._lock:
            return self._snapshot_unlocked()

    @staticmethod
    def _text_preview(payload: bytes) -> str:
        """Project the queue's JSON text envelope into a bounded display field."""
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ""
        if not isinstance(decoded, dict) or set(decoded) != {"text"}:
            return ""
        text = decoded.get("text")
        if not isinstance(text, str):
            return ""
        bounded = text[:160]
        return bounded + ("…" if len(text) > len(bounded) else "")

    async def can_retire(self) -> bool:
        """Whether an idle scoped queue can be removed without losing retries.

        The owning daemon registry may retire a queue only after all minted IDs
        have aged past their lease, all receipts and entity tombstones have been
        pruned, and no pending or active work remains.
        """
        async with self._lock:
            self._prune_expired()
            now_ns = time.monotonic_ns()
            return (
                (
                    self._last_id_issued_at_ns is None
                    or now_ns - self._last_id_issued_at_ns
                    >= self._operation_retry_lease_ns
                )
                and not self._batches
                and not self._operations
                and not self._claim_reservations
                and not self._claim_states
                and not self._item_attempt_states
                and not self._batch_owners
                and not self._item_owners
                and self._reserved_operations == 0
                and self._active_claim is None
                and self._active_batch_id is None
                and self._active_item_attempt_id is None
                and self._active_item_request_id is None
                and self._worker_settlement_pin is None
            )

    def _validate_messages(self, items: Sequence[WebMessage]) -> tuple[WebMessage, ...]:
        if isinstance(items, (str, bytes)):
            raise CapacityExceeded("batch must contain message items")
        try:
            messages = tuple(islice(items, self._max_batch_items + 1))
        except TypeError as exc:
            raise CapacityExceeded("batch must contain message items") from exc
        if not messages or len(messages) > self._max_batch_items:
            raise CapacityExceeded("batch item bound exceeded")
        request_ids: set[str] = set()
        copied: list[WebMessage] = []
        for item in messages:
            if not isinstance(item, WebMessage):
                raise CapacityExceeded("invalid batch item")
            self._validate_identifier(item.request_id)
            if item.request_id in request_ids:
                raise ItemConflict("duplicate request ID in batch")
            request_ids.add(item.request_id)
            if not isinstance(item.payload, bytes) or not item.payload:
                raise CapacityExceeded("payload must be non-empty bytes")
            if len(item.payload) > self._max_payload_bytes:
                raise CapacityExceeded("payload byte bound exceeded")
            refs = item.attachment_refs
            if (
                not isinstance(refs, (list, tuple))
                or len(refs) > DEFAULT_MAX_ATTACHMENTS_PER_ITEM
                or any(not isinstance(ref, str) for ref in refs)
                or len(set(refs)) != len(refs)
            ):
                raise InvalidAttachment("invalid attachment reference list")
            copied.append(WebMessage(item.request_id, bytes(item.payload), tuple(refs)))
        return tuple(copied)

    async def enqueue_batch(
        self,
        operation_id: str,
        batch_id: str,
        items: Sequence[WebMessage],
    ) -> QueueReceipt:
        """Append one ordered envelope, committing all messages or none."""
        self._validate_identifier(operation_id)
        self._validate_identifier(batch_id)
        messages = self._validate_messages(items)
        if any(item.attachment_refs for item in messages):
            raise InvalidAttachment(
                "attachment references require atomic queue admission"
            )
        input_fingerprint = self._fingerprint(
            "enqueue_batch",
            batch_id,
            tuple(
                (item.request_id, self._payload_fingerprint(item.payload))
                for item in messages
            ),
        )
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._validate_new_entity_id(batch_id, "batch")
            for item in messages:
                self._validate_new_entity_id(item.request_id, "item")
            self._ensure_operation_capacity()
            if batch_id in self._batch_owners:
                raise BatchConflict("batch ID is already in use")
            for item in messages:
                if item.request_id in self._item_owners:
                    raise ItemConflict("request ID is already in use")
            batch_bytes = sum(len(item.payload) for item in messages)
            if (
                self._pending_items + len(messages) > self._max_items
                or self._pending_bytes + batch_bytes > self._max_queue_bytes
            ):
                raise CapacityExceeded("pending queue capacity exceeded")
            if (
                len(self._batch_owners) + len(self._item_owners) + 1 + len(messages)
                > self._max_entity_ids
            ):
                raise IdentifierLedgerFull("batch and item identifier capacity is full")

            batch = _StoredBatch(
                batch_id=batch_id,
                sequence=self._next_sequence,
                items=[_StoredItem(item.request_id, item.payload) for item in messages],
            )
            self._next_sequence += 1
            self._batches[batch_id] = batch
            self._batch_owners[batch_id] = operation_id
            for item in messages:
                self._item_owners[item.request_id] = batch_id
            self._pending_items += len(messages)
            self._pending_bytes += batch_bytes
            self._revision += 1
            receipt = self._receipt(
                operation_id,
                BatchStatus.QUEUED.value,
                batch_id=batch_id,
                request_ids=tuple(item.request_id for item in messages),
            )
            return self._record(input_fingerprint, receipt, kind="enqueue")

    def _validate_attachment_scope(
        self, attachment_scope: _BoundAttachmentScope
    ) -> None:
        if not isinstance(attachment_scope, _BoundAttachmentScope):
            raise AttachmentAuthorizationError("attachment scope denied")
        binding = attachment_scope.binding
        if (
            binding.scope_id != self.scope_id
            or binding.queue_incarnation != self.incarnation
        ):
            raise AttachmentAuthorizationError("attachment scope denied")

    async def enqueue_batch_with_attachments(
        self,
        operation_id: str,
        batch_id: str,
        items: Sequence[WebMessage],
        *,
        attachment_scope: _BoundAttachmentScope,
    ) -> QueueReceipt:
        """Atomically enqueue a batch and pin each item's staged attachments.

        Lock order is always queue then attachment store. All queue and store
        validation completes before either retained ownership or queue state is
        mutated; both commits then run synchronously without an await.
        """
        self._validate_identifier(operation_id)
        self._validate_identifier(batch_id)
        messages = self._validate_messages(items)
        self._validate_attachment_scope(attachment_scope)
        owner_refs = tuple(
            (item.request_id, item.attachment_refs)
            for item in messages
            if item.attachment_refs
        )
        if not owner_refs:
            raise InvalidAttachment("attachment references are required")
        input_fingerprint = self._fingerprint(
            "enqueue_batch_with_attachments",
            batch_id,
            tuple(
                (item.request_id, self._payload_fingerprint(item.payload))
                for item in messages
            ),
            tuple((item.request_id, item.attachment_refs) for item in messages),
        )

        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._validate_new_entity_id(batch_id, "batch")
            for item in messages:
                self._validate_new_entity_id(item.request_id, "item")
            self._ensure_operation_capacity()
            if batch_id in self._batch_owners:
                raise BatchConflict("batch ID is already in use")
            for item in messages:
                if item.request_id in self._item_owners:
                    raise ItemConflict("request ID is already in use")
            batch_bytes = sum(len(item.payload) for item in messages)
            if (
                self._pending_items + len(messages) > self._max_items
                or self._pending_bytes + batch_bytes > self._max_queue_bytes
            ):
                raise CapacityExceeded("pending queue capacity exceeded")
            if (
                len(self._batch_owners) + len(self._item_owners) + 1 + len(messages)
                > self._max_entity_ids
            ):
                raise IdentifierLedgerFull("batch and item identifier capacity is full")

            batch = _StoredBatch(
                batch_id=batch_id,
                sequence=self._next_sequence,
                items=[
                    _StoredItem(item.request_id, item.payload, item.attachment_refs)
                    for item in messages
                ],
            )
            receipt = replace(
                self._receipt(
                    operation_id,
                    BatchStatus.QUEUED.value,
                    batch_id=batch_id,
                    request_ids=tuple(item.request_id for item in messages),
                ),
                revision=self._revision + 1,
            )
            operation_issue_time = self._token_issue_time(operation_id, "op")
            operation = _OperationRecord(
                fingerprint=input_fingerprint,
                receipt=receipt,
                kind="enqueue",
                expires_at_ns=(operation_issue_time + self._operation_retry_lease_ns),
            )

            async with attachment_scope._queue_transaction():
                retention_plan = attachment_scope._prepare_owner_retention_unlocked(
                    owner_refs
                )
                labels_by_owner = (
                    attachment_scope._labels_from_owner_retention_plan(retention_plan)
                )
                for stored_item in batch.items:
                    stored_item.attachment_labels = labels_by_owner.get(
                        stored_item.request_id, ()
                    )
                # No await or validation follows this point. The two ledgers
                # commit together from the event loop's perspective.
                attachment_scope._commit_owner_retention_unlocked(retention_plan)
                self._next_sequence += 1
                self._batches[batch_id] = batch
                self._batch_owners[batch_id] = operation_id
                for item in messages:
                    self._item_owners[item.request_id] = batch_id
                self._pending_items += len(messages)
                self._pending_bytes += batch_bytes
                self._revision = receipt.revision
                self._operations[operation_id] = operation
            return receipt

    def _claim_receipt(
        self,
        operation_id: str,
        batch: _StoredBatch,
        fingerprint: str,
    ) -> QueueReceipt:
        self._reserve_claim_lifecycle(operation_id, batch.items)
        batch.status = BatchStatus.DISPATCHING
        batch.claim_id = operation_id
        self._active_claim = operation_id
        self._active_batch_id = batch.batch_id
        self._claim_states[operation_id] = BatchStatus.DISPATCHING.value
        self._revision += 1
        receipt = self._receipt(
            operation_id,
            BatchStatus.DISPATCHING.value,
            batch_id=batch.batch_id,
            request_ids=tuple(item.request_id for item in batch.items),
            claim_id=operation_id,
        )
        return self._record(fingerprint, receipt, kind="batch_claim")

    async def claim_next(self, operation_id: str) -> QueueReceipt:
        """Claim the oldest queued envelope as one indivisible ordered group."""
        self._validate_identifier(operation_id)
        input_fingerprint = self._fingerprint("claim_next")
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._ensure_no_unacknowledged_worker_settlement()
            if self._active_claim is not None:
                active = self._batches.get(self._active_batch_id or "")
                if active is not None and active.status is BatchStatus.IN_DOUBT:
                    raise QueueInDoubt("an item is in doubt")
                raise ClaimBusy("another batch is dispatching")
            batch = next(
                (
                    candidate
                    for candidate in self._ordered_batches()
                    if candidate.status is BatchStatus.QUEUED
                ),
                None,
            )
            if batch is None:
                raise QueueEmpty("no queued batch is ready")
            return self._claim_receipt(operation_id, batch, input_fingerprint)

    async def claim_batch(
        self,
        operation_id: str,
        batch_id: str,
        *,
        send_now: bool = False,
    ) -> QueueReceipt:
        """Claim a named envelope, optionally promoting it ahead of the queue.

        Send Now promotion and claim happen under the same lock. The envelope
        keeps its internal order and remains one active claim until settled.
        """
        self._validate_identifier(operation_id)
        self._validate_identifier(batch_id)
        if not isinstance(send_now, bool):
            raise ValueError("send_now must be a bool")
        input_fingerprint = self._fingerprint("claim_batch", batch_id, send_now)
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._ensure_no_unacknowledged_worker_settlement()
            self._token_issue_time(batch_id, "batch")
            if self._active_claim is not None:
                active = self._batches.get(self._active_batch_id or "")
                if active is not None and active.status is BatchStatus.IN_DOUBT:
                    raise QueueInDoubt("an item is in doubt")
                raise ClaimBusy("another batch is dispatching")
            batch = self._batches.get(batch_id)
            if batch is None:
                raise BatchNotFound("batch is not pending")
            if batch.status is not BatchStatus.QUEUED:
                raise ClaimBusy("batch is already claimed")
            first_queued = next(
                (
                    candidate
                    for candidate in self._ordered_batches()
                    if candidate.status is BatchStatus.QUEUED
                ),
                None,
            )
            if not send_now and first_queued is not batch:
                raise OutOfOrderClaim("normal claims must preserve queue order")
            if send_now:
                self._ensure_operation_capacity(2 + 3 * len(batch.items))
                batch.priority_rank = self._next_priority_rank
                self._next_priority_rank += 1
            return self._claim_receipt(operation_id, batch, input_fingerprint)

    async def claim_next_item(
        self,
        operation_id: str,
        claim_id: str,
    ) -> QueueReceipt:
        """Claim only the next unaccepted item in an active batch envelope."""
        self._validate_identifier(operation_id)
        self._validate_identifier(claim_id)
        input_fingerprint = self._fingerprint("claim_next_item", claim_id)
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._ensure_no_unacknowledged_worker_settlement()
            self._token_issue_time(claim_id, "op")
            if self._active_claim != claim_id or self._active_batch_id is None:
                raise ClaimNotFound("claim is not active")
            batch = self._batches.get(self._active_batch_id)
            if batch is None or batch.claim_id != claim_id:
                raise ClaimNotFound("claim is not active")
            if batch.status is BatchStatus.IN_DOUBT:
                raise ClaimNotDispatchable("in-doubt item blocks the batch")
            if batch.status is not BatchStatus.DISPATCHING:
                raise ClaimNotDispatchable("batch claim is not dispatching")
            if self._active_item_attempt_id is not None:
                raise ClaimBusy("another item in the batch is unresolved")
            item = next(
                (
                    candidate
                    for candidate in batch.items
                    if candidate.status is ItemStatus.QUEUED
                ),
                None,
            )
            if item is None:
                raise QueueEmpty("no unaccepted item is ready")
            reservation = self._claim_reservations.get(claim_id)
            if reservation is None:
                raise OperationLedgerFull("claim has no reserved lifecycle capacity")
            self._consume_item_reservation(claim_id, item.request_id)
            reservation.dispatched = True
            item.status = ItemStatus.DISPATCHING
            item.attempt_id = operation_id
            self._active_item_attempt_id = operation_id
            self._active_item_request_id = item.request_id
            self._item_attempt_states[operation_id] = ItemStatus.DISPATCHING.value
            self._revision += 1
            receipt = self._receipt(
                operation_id,
                ItemStatus.DISPATCHING.value,
                batch_id=batch.batch_id,
                request_ids=(item.request_id,),
                claim_id=claim_id,
                attempt_id=operation_id,
            )
            return self._record(input_fingerprint, receipt, kind="item_claim")

    async def item_payload(self, attempt_id: str) -> ClaimPayload:
        """Read a single in-flight item; in-doubt items are never resent."""
        self._validate_identifier(attempt_id)
        async with self._lock:
            self._token_issue_time(attempt_id, "op")
            status = self._item_attempt_states.get(attempt_id)
            if status == ItemStatus.IN_DOUBT.value:
                raise ClaimNotDispatchable("in-doubt item cannot be dispatched")
            if (
                status != ItemStatus.DISPATCHING.value
                or self._active_item_attempt_id != attempt_id
                or self._active_batch_id is None
                or self._active_claim is None
                or self._active_item_request_id is None
            ):
                raise ClaimNotFound("item attempt is not active")
            batch = self._batches.get(self._active_batch_id)
            if batch is None:
                raise ClaimNotFound("item attempt is not active")
            item = next(
                (
                    candidate
                    for candidate in batch.items
                    if candidate.request_id == self._active_item_request_id
                    and candidate.attempt_id == attempt_id
                    and candidate.status is ItemStatus.DISPATCHING
                ),
                None,
            )
            if item is None:
                raise ClaimNotFound("item attempt is not active")
            return ClaimPayload(
                scope_id=self.scope_id,
                incarnation=self.incarnation,
                claim_id=self._active_claim,
                attempt_id=attempt_id,
                batch_id=batch.batch_id,
                items=(
                    WebMessage(item.request_id, item.payload, item.attachment_refs),
                ),
            )

    async def settle_item(
        self,
        operation_id: str,
        attempt_id: str,
        outcome: ClaimOutcome,
    ) -> QueueReceipt:
        """Settle one item attempt without discarding later batch items.

        `not_sent` is only valid when the admission path knows no submission was
        accepted. Resolving `in_doubt` requires a new operation ID and explicit
        evidence selecting `accepted` or `not_sent`.
        """
        self._validate_identifier(operation_id)
        self._validate_identifier(attempt_id)
        try:
            normalized_outcome = ClaimOutcome(outcome)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid claim outcome") from exc
        input_fingerprint = self._fingerprint(
            "settle_item", attempt_id, normalized_outcome.value
        )
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._token_issue_time(attempt_id, "op")
            pending_settlement = self._worker_settlement_pin
            if pending_settlement is not None:
                if pending_settlement.attempt_id == attempt_id:
                    if pending_settlement.outcome is not normalized_outcome:
                        raise IdempotencyConflict(
                            "attempt has a different unacknowledged worker outcome"
                        )
                    raise ClaimBusy(
                        "worker settlement receipt is awaiting acknowledgement"
                    )
                raise ClaimBusy(
                    "another worker settlement receipt is awaiting acknowledgement"
                )
            return self._settle_item_unlocked(
                operation_id,
                attempt_id,
                normalized_outcome,
                input_fingerprint,
            )

    async def settle_item_from_worker(
        self,
        attempt_id: str,
        outcome: ClaimOutcome,
    ) -> QueueReceipt:
        """Settle an active attempt from trusted in-process daemon code.

        This method is intentionally not registered as an RPC. Its caller must
        be the trusted worker that retained this queue and attempt when dispatch
        began. The worker may choose `accepted` or `not_sent` only from
        definitive provider evidence; ambiguous evidence must remain
        `in_doubt`.

        A fresh operation ID is minted only after authenticating the attempt and
        matching active queue state. The settlement receipt remains pinned in
        the existing bounded operation ledger until the worker validates it and
        acknowledges it. At most one receipt can be pinned per queue, and new
        claims are blocked while it is pending.
        """
        self._validate_identifier(attempt_id)
        self._token_issue_time(attempt_id, "op")
        try:
            normalized_outcome = ClaimOutcome(outcome)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid claim outcome") from exc
        input_fingerprint = self._fingerprint(
            "settle_item", attempt_id, normalized_outcome.value
        )

        async with self._lock:
            # The target token remains authenticatable while active, even after
            # its retry lease. An exact pending settlement is replayable beyond
            # that lease until the worker acknowledges the receipt.
            self._token_issue_time(attempt_id, "op")
            pending_settlement = self._worker_settlement_pin
            if pending_settlement is not None:
                if pending_settlement.attempt_id == attempt_id:
                    if pending_settlement.outcome is not normalized_outcome:
                        raise IdempotencyConflict(
                            "attempt has a different unacknowledged worker outcome"
                        )
                    return pending_settlement.receipt
                raise ClaimBusy(
                    "another worker settlement receipt is awaiting acknowledgement"
                )
            self._prune_expired()
            replay = self._find_settlement_receipt_unlocked(
                attempt_id, normalized_outcome
            )
            if replay is not None:
                self._worker_settlement_pin = _WorkerSettlementPin(
                    attempt_id=attempt_id,
                    outcome=normalized_outcome,
                    receipt=replay,
                )
                return replay

            accepted = self._find_settlement_receipt_unlocked(
                attempt_id, ClaimOutcome.ACCEPTED
            )
            not_sent = self._find_settlement_receipt_unlocked(
                attempt_id, ClaimOutcome.NOT_SENT
            )
            if normalized_outcome is ClaimOutcome.IN_DOUBT:
                if accepted is not None or not_sent is not None:
                    raise IdempotencyConflict(
                        "attempt already has a definitive settlement"
                    )
            elif normalized_outcome is ClaimOutcome.ACCEPTED and not_sent is not None:
                raise IdempotencyConflict("attempt was already settled as not sent")
            elif normalized_outcome is ClaimOutcome.NOT_SENT and accepted is not None:
                raise IdempotencyConflict("attempt was already settled as accepted")

            if self._active_item_attempt_id != attempt_id:
                raise ClaimNotFound("item attempt is not active")

            return self._settle_item_unlocked(
                None,
                attempt_id,
                normalized_outcome,
                input_fingerprint,
                pin_for_worker=True,
            )

    async def acknowledge_worker_settlement(
        self,
        attempt_id: str,
        outcome: ClaimOutcome,
        receipt: QueueReceipt,
    ) -> None:
        """Release the one bounded receipt pin after worker-side validation.

        This trusted in-process acknowledgement is not an RPC. The most recent
        acknowledgement is kept as one bounded confirmation tombstone, making
        its exact repeat a no-op. A mismatched receipt or outcome never clears a
        pending pin.
        """
        self._validate_identifier(attempt_id)
        try:
            normalized_outcome = ClaimOutcome(outcome)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid claim outcome") from exc
        if not isinstance(receipt, QueueReceipt):
            raise ValueError("invalid worker settlement receipt")
        async with self._lock:
            pending_settlement = self._worker_settlement_pin
            if pending_settlement is not None:
                if (
                    pending_settlement.attempt_id == attempt_id
                    and pending_settlement.outcome is normalized_outcome
                    and pending_settlement.receipt == receipt
                ):
                    self._worker_settlement_pin = None
                    self._last_acknowledged_worker_settlement = pending_settlement
                    return

            last_acknowledged = self._last_acknowledged_worker_settlement
            if (
                last_acknowledged is not None
                and last_acknowledged.attempt_id == attempt_id
                and last_acknowledged.outcome is normalized_outcome
                and last_acknowledged.receipt == receipt
            ):
                return

            if pending_settlement is not None:
                if (
                    pending_settlement.attempt_id != attempt_id
                    or pending_settlement.outcome is not normalized_outcome
                    or pending_settlement.receipt != receipt
                ):
                    if pending_settlement.attempt_id == attempt_id:
                        raise IdempotencyConflict(
                            "worker settlement acknowledgement conflicts with its receipt"
                        )
                    raise ClaimBusy(
                        "another worker settlement receipt is awaiting acknowledgement"
                    )

            raise ClaimNotFound("worker settlement receipt is not pending")

    def _find_settlement_receipt_unlocked(
        self,
        attempt_id: str,
        outcome: ClaimOutcome,
    ) -> QueueReceipt | None:
        fingerprint = self._fingerprint("settle_item", attempt_id, outcome.value)
        return next(
            (
                record.receipt
                for record in self._operations.values()
                if record.kind == "settle_item" and record.fingerprint == fingerprint
            ),
            None,
        )

    def _settle_item_unlocked(
        self,
        operation_id: str | None,
        attempt_id: str,
        normalized_outcome: ClaimOutcome,
        input_fingerprint: str,
        *,
        pin_for_worker: bool = False,
    ) -> QueueReceipt:
        """Commit a settlement after the caller resolved replay under lock."""
        if (
            self._active_item_attempt_id != attempt_id
            or self._active_claim is None
            or self._active_batch_id is None
            or self._active_item_request_id is None
        ):
            raise ClaimNotFound("item attempt is not active")
        batch = self._batches.get(self._active_batch_id)
        if batch is None or batch.claim_id != self._active_claim:
            raise ClaimNotFound("item attempt is not active")
        item = next(
            (
                candidate
                for candidate in batch.items
                if candidate.request_id == self._active_item_request_id
                and candidate.attempt_id == attempt_id
            ),
            None,
        )
        if item is None or item.status not in (
            ItemStatus.DISPATCHING,
            ItemStatus.IN_DOUBT,
        ):
            raise ClaimNotFound("item attempt is not active")

        claim_id = self._active_claim
        reservation = self._claim_reservations.get(claim_id)
        remaining_slots = (
            reservation.item_slots.get(item.request_id, 0)
            if reservation is not None
            else 0
        )
        if (
            item.status is ItemStatus.IN_DOUBT
            and normalized_outcome is ClaimOutcome.IN_DOUBT
        ):
            raise QueueInDoubt("in-doubt item requires explicit resolution")
        if remaining_slots <= 0:
            raise OperationLedgerFull("claim has no reserved settlement capacity")

        # Worker recovery gets a fresh operation token only after active state
        # and its pre-reserved settlement slot are confirmed under this lock.
        if operation_id is None:
            operation_id = self._new_token("op")

        batch_claim_id = claim_id
        batch_id = batch.batch_id
        request_id = item.request_id
        self._consume_item_reservation(batch_claim_id, request_id)
        changed = False
        if normalized_outcome is ClaimOutcome.ACCEPTED:
            batch.items.remove(item)
            self._pending_items -= 1
            self._pending_bytes -= len(item.payload)
            self._item_attempt_states[attempt_id] = ClaimOutcome.ACCEPTED.value
            self._active_item_attempt_id = None
            self._active_item_request_id = None
            status = ClaimOutcome.ACCEPTED.value
            changed = True
            if batch.items:
                # The confirmed item is gone; the reserved envelope keeps
                # its suffix and sequence until every item is settled.
                batch.status = BatchStatus.DISPATCHING
                self._claim_states[batch_claim_id] = BatchStatus.DISPATCHING.value
                self._free_item_reservation(batch_claim_id, request_id)
            else:
                del self._batches[batch.batch_id]
                self._active_claim = None
                self._active_batch_id = None
                self._claim_states[batch_claim_id] = ClaimOutcome.ACCEPTED.value
                self._free_item_reservation(batch_claim_id, request_id)
                self._free_claim_reservation(batch_claim_id)
        elif normalized_outcome is ClaimOutcome.NOT_SENT:
            item.status = ItemStatus.QUEUED
            item.attempt_id = None
            batch.status = BatchStatus.QUEUED
            batch.claim_id = None
            batch.priority_rank = None
            self._active_claim = None
            self._active_batch_id = None
            self._active_item_attempt_id = None
            self._active_item_request_id = None
            status = BatchStatus.QUEUED.value
            self._claim_states[batch_claim_id] = status
            self._item_attempt_states[attempt_id] = status
            self._free_claim_reservation(batch_claim_id)
            changed = True
        else:
            if item.status is not ItemStatus.IN_DOUBT:
                item.status = ItemStatus.IN_DOUBT
                batch.status = BatchStatus.IN_DOUBT
                changed = True
            status = ItemStatus.IN_DOUBT.value
            self._claim_states[batch_claim_id] = status
            self._item_attempt_states[attempt_id] = status
        if changed:
            self._revision += 1
        receipt = self._receipt(
            operation_id,
            status,
            batch_id=batch_id,
            request_ids=(request_id,),
            claim_id=batch_claim_id,
            attempt_id=attempt_id,
        )
        recorded = self._record(input_fingerprint, receipt, kind="settle_item")
        if pin_for_worker:
            # This is in the same queue-lock critical section as the settlement
            # commit, before any caller can observe or lose its acknowledgement.
            assert self._worker_settlement_pin is None
            self._worker_settlement_pin = _WorkerSettlementPin(
                attempt_id=attempt_id,
                outcome=normalized_outcome,
                receipt=recorded,
            )
        return recorded

    async def remove_item(
        self,
        operation_id: str,
        request_id: str,
    ) -> QueueReceipt:
        """Remove exactly one queued item; claimed batches cannot be edited."""
        self._validate_identifier(operation_id)
        self._validate_identifier(request_id)
        input_fingerprint = self._fingerprint("remove_item", request_id)
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._token_issue_time(request_id, "item")
            self._ensure_operation_capacity()
            owner = self._item_owners.get(request_id)
            if owner is None:
                raise ItemNotFound("item is not pending")
            batch_id = owner
            batch = self._batches.get(batch_id)
            if batch is None:
                raise ItemNotFound("item is not pending")
            if batch.status is not BatchStatus.QUEUED:
                raise ItemNotQueued("item is claimed or in doubt")
            removed = next(
                (item for item in batch.items if item.request_id == request_id),
                None,
            )
            if removed is None:
                raise ItemNotFound("item is not pending")
            if removed.attachment_refs:
                raise InvalidAttachment(
                    "attachment-bearing items require atomic queue removal"
                )
            batch.items.remove(removed)
            self._pending_items -= 1
            self._pending_bytes -= len(removed.payload)
            if not batch.items:
                del self._batches[batch_id]
            self._revision += 1
            receipt = self._receipt(
                operation_id,
                "removed",
                batch_id=batch_id,
                request_ids=(request_id,),
            )
            return self._record(input_fingerprint, receipt, kind="remove")

    async def remove_item_with_attachments(
        self,
        operation_id: str,
        request_id: str,
        *,
        attachment_scope: _BoundAttachmentScope,
    ) -> QueueReceipt:
        """Remove one queued item and release only its attachment refs."""
        self._validate_identifier(operation_id)
        self._validate_identifier(request_id)
        self._validate_attachment_scope(attachment_scope)
        input_fingerprint = self._fingerprint(
            "remove_item_with_attachments", request_id
        )
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._token_issue_time(request_id, "item")
            self._ensure_operation_capacity()
            owner = self._item_owners.get(request_id)
            if owner is None:
                raise ItemNotFound("item is not pending")
            batch = self._batches.get(owner)
            if batch is None:
                raise ItemNotFound("item is not pending")
            if batch.status is not BatchStatus.QUEUED:
                raise ItemNotQueued("item is claimed or in doubt")
            removed = next(
                (item for item in batch.items if item.request_id == request_id),
                None,
            )
            if removed is None:
                raise ItemNotFound("item is not pending")

            receipt = replace(
                self._receipt(
                    operation_id,
                    "removed",
                    batch_id=owner,
                    request_ids=(request_id,),
                ),
                revision=self._revision + 1,
            )
            operation_issue_time = self._token_issue_time(operation_id, "op")
            operation = _OperationRecord(
                fingerprint=input_fingerprint,
                receipt=receipt,
                kind="remove_with_attachments",
                expires_at_ns=(operation_issue_time + self._operation_retry_lease_ns),
            )

            async with attachment_scope._queue_transaction():
                # Releasing a missing owner is a safe no-op. Store pruning and
                # release run before the queue's non-throwing synchronous edit.
                attachment_scope._release_owner_unlocked(request_id)
                batch.items.remove(removed)
                self._pending_items -= 1
                self._pending_bytes -= len(removed.payload)
                if not batch.items:
                    del self._batches[owner]
                self._revision = receipt.revision
                self._operations[operation_id] = operation
            return receipt

    async def release_claim(
        self,
        operation_id: str,
        claim_id: str,
    ) -> QueueReceipt:
        """Release a claim only before any item has been dispatched.

        The release receipt slot is reserved atomically with the claim. A
        caller may safely use this after cancellation before the first item RPC;
        once dispatch begins, only per-item settlement can release the batch.
        """
        self._validate_identifier(operation_id)
        self._validate_identifier(claim_id)
        input_fingerprint = self._fingerprint("release_claim", claim_id)
        async with self._lock:
            replay = self._replay(operation_id, input_fingerprint)
            if replay is not None:
                return replay
            self._token_issue_time(claim_id, "op")
            if self._active_claim != claim_id or self._active_batch_id is None:
                raise ClaimNotFound("claim is not active")
            batch = self._batches.get(self._active_batch_id)
            if batch is None or batch.claim_id != claim_id:
                raise ClaimNotFound("claim is not active")
            if batch.status is BatchStatus.IN_DOUBT:
                raise QueueInDoubt("in-doubt item requires explicit resolution")
            reservation = self._claim_reservations.get(claim_id)
            if reservation is None:
                raise OperationLedgerFull("claim has no reserved lifecycle capacity")
            if reservation.dispatched or self._active_item_attempt_id is not None:
                raise ClaimBusy("claim cannot be released after item dispatch")
            self._consume_release_reservation(claim_id)
            batch.status = BatchStatus.QUEUED
            batch.claim_id = None
            batch.priority_rank = None
            self._active_claim = None
            self._active_batch_id = None
            self._claim_states[claim_id] = BatchStatus.QUEUED.value
            self._revision += 1
            receipt = self._receipt(
                operation_id,
                BatchStatus.QUEUED.value,
                batch_id=batch.batch_id,
                request_ids=tuple(item.request_id for item in batch.items),
                claim_id=claim_id,
            )
            self._free_claim_reservation(claim_id)
            return self._record(input_fingerprint, receipt, kind="release_claim")
