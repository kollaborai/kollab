"""Daemon RPC adapter for the WebUI message queue.

The adapter registers queue operations on an existing ``RpcServer``. Its
``resolve_scope`` callback is a trust boundary: callers must derive a scope
from authenticated daemon context and a canonical conversation identity. RPC
parameters cannot directly choose a queue scope. This module does not provide
authentication or register itself into the daemon lifecycle.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import inspect
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from kollabor_rpc import (
    WEB_QUEUE_CAPACITY_REJECTED,
    RpcCallerContext,
    current_rpc_caller_context,
)

from .web_message_attachments import (
    AttachmentCapacityExceeded,
    DEFAULT_MAX_ATTACHMENT_BYTES,
    DEFAULT_MAX_ATTACHMENTS_PER_ITEM,
    WebMessageAttachmentRegistry,
)
from .web_message_queue import (
    CapacityExceeded,
    DEFAULT_MAX_BATCH_ITEMS,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_MAX_QUEUE_BYTES,
    InvalidIdentifier,
    QueueReceipt,
    QueueSnapshot,
    WebMessage,
    WebMessageQueue,
)

DEFAULT_MAX_SCOPES = 16
MAX_OPERATION_IDS_PER_ALLOCATION = 64


class _QueueCapacityRejected(Exception):
    """Safe RPC marker for an atomic precommit queue capacity refusal."""

    def __init__(self) -> None:
        super().__init__(WEB_QUEUE_CAPACITY_REJECTED)

    def __repr__(self) -> str:
        # RpcServer carries handler errors as repr; keep this stable code-only.
        return WEB_QUEUE_CAPACITY_REJECTED

_IMAGE_MIME_ALIASES = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
}


def _validated_image_mime(mime: str, payload: bytes) -> str:
    """Verify an upload's declared MIME against its image format signature."""
    normalized = _IMAGE_MIME_ALIASES.get(mime.strip().lower())
    if normalized is None:
        raise ValueError("unsupported image MIME type")
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        actual = "image/png"
    elif payload.startswith(b"\xff\xd8\xff"):
        actual = "image/jpeg"
    elif len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        actual = "image/webp"
    elif payload.startswith((b"GIF87a", b"GIF89a")):
        actual = "image/gif"
    else:
        raise ValueError("image bytes do not have a supported format signature")
    if actual != normalized:
        raise ValueError("image bytes do not match the declared MIME type")
    return normalized


class QueueRpcAuthorizationError(PermissionError):
    """The trusted daemon context did not authorize queue access."""


class StaleQueueScopeError(QueueRpcAuthorizationError):
    """A valid caller UID precondition no longer matches daemon state."""


class QueueScopeCapacityExceeded(RuntimeError):
    """The bounded scoped-queue registry cannot safely admit another scope."""


@dataclass(frozen=True, slots=True)
class QueueScopeBinding:
    """Trusted resolver output carrying the canonical conversation UID."""

    scope_id: str
    conversation_uid: str = field(repr=False)


ScopeResolution = str | QueueScopeBinding
ScopeResolver = Callable[
    [dict[str, Any], RpcCallerContext], ScopeResolution | Awaitable[ScopeResolution]
]
RpcChangeNotifier = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class _ResolvedQueueScope:
    scope_id: str
    principal_id: str
    conversation_uid: str | None


@dataclass(frozen=True, slots=True)
class _QueueScopeOwner:
    principal_id: str
    conversation_uid: str | None


@dataclass(frozen=True, slots=True)
class _KnownQueueScope:
    """Daemon-internal queue owner metadata for the bounded dispatcher."""

    scope_id: str
    incarnation: str
    principal_id: str
    conversation_uid: str


def daemon_conversation_scope_resolver(state_service: Any) -> ScopeResolver:
    """Bind queue access to the authenticated operator and daemon conversation.

    Every queue request must include ``expected_conversation_uid`` from the
    caller's current conversation snapshot. It is a compare-only precondition:
    the actual UID comes from this daemon's ``StateService`` and RPC parameters
    such as ``scope_id``, ``session_id``, or ``principal_id`` never choose it.
    The UID read and scope derivation are synchronous, so one request captures
    one immutable scope before any queue lock can yield. A request racing with a
    conversation switch can only touch the captured old scope; a later stale
    request is classified separately from other authorization failures so only
    claim cleanup can consult the retained old scope. Same-UID processes share one
    operator trust domain; this resolver does not distinguish users sharing an
    OS account or a shared Engine bearer token.
    """

    def resolve(
        params: dict[str, Any], caller_context: RpcCallerContext
    ) -> QueueScopeBinding:
        if (
            not caller_context.is_local_operator
            or not isinstance(caller_context.principal_id, str)
            or not caller_context.principal_id
        ):
            raise QueueRpcAuthorizationError("queue access denied")
        expected_uid = params.get("expected_conversation_uid")
        if not isinstance(expected_uid, str) or not expected_uid:
            raise QueueRpcAuthorizationError("queue access denied")
        getter = getattr(state_service, "get_conversation_uid", None)
        if not callable(getter):
            raise QueueRpcAuthorizationError("queue access denied")
        try:
            current_uid = getter()
        except Exception:
            raise QueueRpcAuthorizationError("queue access denied") from None
        if not isinstance(current_uid, str) or not current_uid:
            raise QueueRpcAuthorizationError("queue access denied")
        if expected_uid != current_uid:
            raise StaleQueueScopeError("queue access denied")
        scope_material = f"{caller_context.principal_id}\0{current_uid}".encode("utf-8")
        return QueueScopeBinding(
            "scope-" + hashlib.sha256(scope_material).hexdigest(),
            current_uid,
        )

    return resolve


def _validate_change_notifier(
    on_change: RpcChangeNotifier | None,
) -> tuple[asyncio.Queue[dict[str, Any]] | None, int]:
    """Accept only a finite asyncio queue's built-in nonblocking publisher."""
    if on_change is None:
        return None, 0
    event_queue = getattr(on_change, "__self__", None)
    publisher = getattr(on_change, "__func__", None)
    if (
        type(event_queue) is not asyncio.Queue
        or publisher is not asyncio.Queue.put_nowait
    ):
        raise TypeError("on_change must be a bounded asyncio.Queue.put_nowait method")
    maxsize = event_queue.maxsize
    if isinstance(maxsize, bool) or not isinstance(maxsize, int) or maxsize <= 0:
        raise ValueError("on_change queue must have a finite maxsize")
    return event_queue, maxsize


class WebMessageQueueRegistry:
    """Bounded daemon-lifetime registry of per-principal/conversation queues.

    A scope is retired only when it has no pending or in-doubt items, all
    receipts and identifier tombstones are beyond their retry leases, and no
    caller currently holds a registry lease. The registry never drops live
    work to make room for a new scope.
    """

    def __init__(
        self,
        *,
        max_scopes: int = DEFAULT_MAX_SCOPES,
        queue_options: Mapping[str, Any] | None = None,
    ) -> None:
        if (
            isinstance(max_scopes, bool)
            or not isinstance(max_scopes, int)
            or max_scopes <= 0
        ):
            raise ValueError("max_scopes must be a positive integer")
        self._max_scopes = max_scopes
        self._queue_options = dict(queue_options or {})
        self.max_batch_items = self._queue_options.get(
            "max_batch_items", DEFAULT_MAX_BATCH_ITEMS
        )
        self.max_payload_bytes = self._queue_options.get(
            "max_payload_bytes", DEFAULT_MAX_PAYLOAD_BYTES
        )
        self.max_queue_bytes = self._queue_options.get(
            "max_queue_bytes", DEFAULT_MAX_QUEUE_BYTES
        )
        self._queues: OrderedDict[str, WebMessageQueue] = OrderedDict()
        self._leases: dict[str, int] = {}
        self._scope_owners: dict[str, _QueueScopeOwner] = {}
        self._lock = asyncio.Lock()

    @property
    def scope_count(self) -> int:
        """Current number of retained scoped queues."""
        return len(self._queues)

    async def known_scopes(self) -> tuple[_KnownQueueScope, ...]:
        """Return bounded daemon-internal metadata for retained queue scopes."""
        async with self._lock:
            return tuple(
                _KnownQueueScope(
                    scope_id=scope_id,
                    incarnation=queue.incarnation,
                    principal_id=owner.principal_id,
                    conversation_uid=owner.conversation_uid,
                )
                for scope_id, queue in self._queues.items()
                if (owner := self._scope_owners.get(scope_id)) is not None
                and owner.conversation_uid is not None
            )

    @asynccontextmanager
    async def acquire_existing_scope(
        self, binding: _KnownQueueScope
    ) -> AsyncIterator[WebMessageQueue | None]:
        """Lease one already-retained scope without creating a replacement."""
        if not isinstance(binding, _KnownQueueScope):
            raise TypeError("trusted retained scope metadata is required")
        async with self._lock:
            queue = self._queues.get(binding.scope_id)
            owner = self._scope_owners.get(binding.scope_id)
            if (
                queue is None
                or queue.incarnation != binding.incarnation
                or owner
                != _QueueScopeOwner(binding.principal_id, binding.conversation_uid)
            ):
                queue = None
            else:
                self._leases[binding.scope_id] = (
                    self._leases.get(binding.scope_id, 0) + 1
                )
        try:
            yield queue
        finally:
            if queue is not None:
                async with self._lock:
                    remaining = self._leases.get(binding.scope_id, 0) - 1
                    self._leases[binding.scope_id] = max(0, remaining)

    def validates_attachment_owner_id(
        self,
        scope_id: str,
        incarnation: str,
        request_id: str,
    ) -> bool:
        """Check an owner ID against the queue incarnation that retains it.

        An item already in the queue remains a valid attachment owner after its
        creation lease expires. A newly allocated request ID may be retained
        only while that ID's creation lease is still valid. Callers that retain
        staged refs must hold a registry lease for this exact scope.
        """
        queue = self._queues.get(scope_id)
        if queue is None or queue.incarnation != incarnation:
            return False
        try:
            queue._token_issue_time(request_id, "item")
            if request_id in queue._item_owners:
                return True
            queue._validate_new_entity_id(request_id, "item")
            return True
        except Exception:
            return False

    async def _make_room_unlocked(self) -> None:
        if len(self._queues) < self._max_scopes:
            return
        for scope_id, queue in tuple(self._queues.items()):
            if self._leases.get(scope_id, 0):
                continue
            if await queue.can_retire():
                del self._queues[scope_id]
                del self._leases[scope_id]
                self._scope_owners.pop(scope_id, None)
                if len(self._queues) < self._max_scopes:
                    return
        raise QueueScopeCapacityExceeded(
            "queue scope capacity is full; retained work or retry leases prevent retirement"
        )

    @staticmethod
    def _validate_scope_owner(
        principal_id: str | None,
        conversation_uid: str | None,
    ) -> None:
        if principal_id is None:
            if conversation_uid is not None:
                raise ValueError("conversation UID requires an authenticated principal")
            return
        if not isinstance(principal_id, str) or not principal_id:
            raise QueueRpcAuthorizationError("queue access denied")
        if conversation_uid is not None and (
            not isinstance(conversation_uid, str) or not conversation_uid
        ):
            raise QueueRpcAuthorizationError("queue access denied")

    def _bind_scope_owner_unlocked(
        self,
        scope_id: str,
        principal_id: str | None,
        conversation_uid: str | None,
    ) -> None:
        if principal_id is None:
            return
        existing = self._scope_owners.get(scope_id)
        if existing is None:
            self._scope_owners[scope_id] = _QueueScopeOwner(
                principal_id,
                conversation_uid,
            )
            return
        if existing.principal_id != principal_id:
            raise QueueRpcAuthorizationError("queue access denied")
        if (
            conversation_uid is not None
            and existing.conversation_uid is not None
            and existing.conversation_uid != conversation_uid
        ):
            raise QueueRpcAuthorizationError("queue access denied")
        if existing.conversation_uid is None and conversation_uid is not None:
            self._scope_owners[scope_id] = _QueueScopeOwner(
                principal_id,
                conversation_uid,
            )

    @asynccontextmanager
    async def acquire(
        self,
        scope_id: str,
        *,
        principal_id: str | None = None,
        conversation_uid: str | None = None,
    ) -> AsyncIterator[WebMessageQueue]:
        """Hold a registry lease while operating on a trusted scope's queue."""
        if not isinstance(scope_id, str) or not scope_id:
            raise InvalidIdentifier("invalid queue scope")
        self._validate_scope_owner(principal_id, conversation_uid)
        async with self._lock:
            queue = self._queues.get(scope_id)
            if queue is None:
                await self._make_room_unlocked()
                queue = WebMessageQueue(scope_id=scope_id, **self._queue_options)
                self._queues[scope_id] = queue
                self._leases[scope_id] = 0
            self._bind_scope_owner_unlocked(
                scope_id,
                principal_id,
                conversation_uid,
            )
            self._queues.move_to_end(scope_id)
            self._leases[scope_id] += 1
        try:
            yield queue
        finally:
            async with self._lock:
                remaining = self._leases.get(scope_id, 0) - 1
                if remaining > 0:
                    self._leases[scope_id] = remaining
                elif scope_id in self._leases:
                    self._leases[scope_id] = 0

    @asynccontextmanager
    async def _acquire_existing_scope_candidates(
        self,
        principal_id: str,
        conversation_uid: str,
    ) -> AsyncIterator[tuple[tuple[str, WebMessageQueue], ...]]:
        """Lease only known queues already bound to this trusted identity."""
        if (
            not isinstance(principal_id, str)
            or not principal_id
            or not isinstance(conversation_uid, str)
            or not conversation_uid
        ):
            yield ()
            return
        async with self._lock:
            candidates = tuple(
                (scope_id, queue)
                for scope_id, queue in self._queues.items()
                if self._scope_owners.get(scope_id)
                == _QueueScopeOwner(principal_id, conversation_uid)
            )
            for scope_id, _ in candidates:
                self._leases[scope_id] = self._leases.get(scope_id, 0) + 1
        try:
            yield candidates
        finally:
            async with self._lock:
                for scope_id, _ in candidates:
                    remaining = self._leases.get(scope_id, 0) - 1
                    self._leases[scope_id] = max(0, remaining)


def _receipt_to_wire(receipt: QueueReceipt) -> dict[str, Any]:
    return {
        "scope_id": receipt.scope_id,
        "incarnation": receipt.incarnation,
        "operation_id": receipt.operation_id,
        "revision": receipt.revision,
        "status": receipt.status,
        "batch_id": receipt.batch_id,
        "request_ids": list(receipt.request_ids),
        "claim_id": receipt.claim_id,
        "attempt_id": receipt.attempt_id,
    }


def _snapshot_to_wire(snapshot: QueueSnapshot) -> dict[str, Any]:
    return {
        "scope_id": snapshot.scope_id,
        "incarnation": snapshot.incarnation,
        "revision": snapshot.revision,
        "batches": [
            {
                "batch_id": batch.batch_id,
                "sequence": batch.sequence,
                "status": batch.status.value,
                "request_ids": list(batch.request_ids),
                "claim_id": batch.claim_id,
                "items": [
                    {
                        "request_id": item.request_id,
                        "status": item.status.value,
                        "attempt_id": item.attempt_id,
                        "text_preview": item.text_preview,
                        "attachment_labels": list(item.attachment_labels),
                    }
                    for item in batch.items
                ],
            }
            for batch in snapshot.batches
        ],
    }


def _claim_payload_to_wire(payload: Any) -> dict[str, Any]:
    return {
        "scope_id": payload.scope_id,
        "incarnation": payload.incarnation,
        "claim_id": payload.claim_id,
        "attempt_id": payload.attempt_id,
        "batch_id": payload.batch_id,
        "items": [
            {
                "request_id": item.request_id,
                "payload_b64": base64.b64encode(item.payload).decode("ascii"),
                "attachment_refs": list(item.attachment_refs),
            }
            for item in payload.items
        ],
    }


def _required_text(params: dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _bounded_integer(
    params: dict[str, Any], name: str, *, default: int, maximum: int
) -> int:
    value = params.get(name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ValueError(f"{name} is outside the allowed range")
    return value


def _decode_items(
    params: dict[str, Any], registry: WebMessageQueueRegistry
) -> tuple[WebMessage, ...]:
    raw_items = params.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items must be a non-empty list")
    if len(raw_items) > registry.max_batch_items:
        raise ValueError("batch item bound exceeded")

    max_encoded_item = ((registry.max_payload_bytes + 2) // 3) * 4
    max_encoded_total = ((registry.max_queue_bytes + 2) // 3) * 4
    total_encoded = 0
    items: list[WebMessage] = []
    total_decoded = 0
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("invalid batch item")
        request_id = raw_item.get("request_id")
        encoded = raw_item.get("payload_b64")
        if not isinstance(request_id, str) or not isinstance(encoded, str):
            raise ValueError("invalid batch item")
        raw_refs = raw_item.get("attachment_refs", [])
        if (
            not isinstance(raw_refs, list)
            or len(raw_refs) > DEFAULT_MAX_ATTACHMENTS_PER_ITEM
            or any(not isinstance(ref, str) for ref in raw_refs)
            or len(set(raw_refs)) != len(raw_refs)
        ):
            raise ValueError("invalid attachment references")
        total_encoded += len(encoded)
        if len(encoded) > max_encoded_item or total_encoded > max_encoded_total:
            raise ValueError("payload byte bound exceeded")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except binascii.Error, ValueError:
            raise ValueError("invalid payload encoding") from None
        total_decoded += len(payload)
        if (
            len(payload) > registry.max_payload_bytes
            or total_decoded > registry.max_queue_bytes
        ):
            raise ValueError("payload byte bound exceeded")
        items.append(
            WebMessage(
                request_id=request_id,
                payload=payload,
                attachment_refs=tuple(raw_refs),
            )
        )
    return tuple(items)


def register_web_message_queue_handlers(
    rpc_server: Any,
    registry: WebMessageQueueRegistry,
    *,
    resolve_scope: ScopeResolver,
    on_change: RpcChangeNotifier | None = None,
    attachment_registry: WebMessageAttachmentRegistry | None = None,
    include_worker_methods: bool = True,
) -> None:
    """Register JSON-safe queue RPC handlers on an existing ``RpcServer``.

    The required resolver receives a trusted, in-process caller context and
    must return a canonical opaque scope ID or ``QueueScopeBinding``. Queue handlers reject requests
    without a local-operator transport context before calling it. Handler
    parameters never select a scope directly. The optional notifier must be the built-in ``put_nowait``
    method of a finite ``asyncio.Queue``. It receives only
    scope/incarnation/revision metadata, never message bytes. Full buffers drop
    invalidation hints; snapshots remain authoritative.
    """

    change_queue, change_queue_limit = _validate_change_notifier(on_change)
    if not isinstance(include_worker_methods, bool):
        raise TypeError("include_worker_methods must be a bool")
    if attachment_registry is not None and not isinstance(
        attachment_registry, WebMessageAttachmentRegistry
    ):
        raise TypeError("attachment_registry must be a WebMessageAttachmentRegistry")

    async def _scope(params: dict[str, Any]) -> _ResolvedQueueScope:
        caller_context = current_rpc_caller_context()
        if (
            not isinstance(caller_context, RpcCallerContext)
            or not caller_context.is_local_operator
            or not isinstance(caller_context.principal_id, str)
            or not caller_context.principal_id
        ):
            raise QueueRpcAuthorizationError("queue access denied")
        try:
            value = resolve_scope(params, caller_context)
            if inspect.isawaitable(value):
                value = await value
        except StaleQueueScopeError:
            raise
        except Exception:
            raise QueueRpcAuthorizationError("queue access denied") from None
        if isinstance(value, QueueScopeBinding):
            scope_id = value.scope_id
            conversation_uid = value.conversation_uid
            if not isinstance(conversation_uid, str) or not conversation_uid:
                raise QueueRpcAuthorizationError("queue access denied")
        elif isinstance(value, str):
            scope_id = value
            conversation_uid = None
        else:
            raise QueueRpcAuthorizationError("queue access denied")
        if not isinstance(scope_id, str) or not scope_id:
            raise QueueRpcAuthorizationError("queue access denied")
        return _ResolvedQueueScope(
            scope_id,
            caller_context.principal_id,
            conversation_uid,
        )

    async def _notify(queue: WebMessageQueue) -> None:
        if change_queue is None:
            return
        snapshot = await queue.snapshot()
        metadata = {
            "scope_id": snapshot.scope_id,
            "incarnation": snapshot.incarnation,
            "revision": snapshot.revision,
        }
        if change_queue.qsize() >= change_queue_limit:
            return
        try:
            change_queue.put_nowait(metadata)
        except asyncio.QueueFull:
            # Notification is an invalidation hint. Snapshot RPC is authoritative.
            return

    async def _with_queue(
        params: dict[str, Any], operation: Callable[[WebMessageQueue], Any]
    ) -> Any:
        try:
            resolved = await _scope(params)
        except StaleQueueScopeError:
            raise QueueRpcAuthorizationError("queue access denied") from None
        async with registry.acquire(
            resolved.scope_id,
            principal_id=resolved.principal_id,
            conversation_uid=resolved.conversation_uid,
        ) as queue:
            result = operation(queue)
            if inspect.isawaitable(result):
                result = await result
            return result

    async def _with_cleanup_queue(
        params: dict[str, Any],
        operation_id: str,
        target_operation_id: str,
        operation_kind: str,
        operation_fingerprint: str,
        operation: Callable[[WebMessageQueue], Any],
    ) -> Any:
        try:
            resolved = await _scope(params)
        except StaleQueueScopeError:
            caller_context = current_rpc_caller_context()
            expected_uid = params.get("expected_conversation_uid")
            if (
                not isinstance(caller_context, RpcCallerContext)
                or not caller_context.is_local_operator
                or not isinstance(caller_context.principal_id, str)
                or not caller_context.principal_id
                or not isinstance(expected_uid, str)
                or not expected_uid
            ):
                raise QueueRpcAuthorizationError("queue access denied") from None

            async with registry._acquire_existing_scope_candidates(
                caller_context.principal_id,
                expected_uid,
            ) as candidates:
                matches: list[WebMessageQueue] = []
                for _scope_id, queue in candidates:
                    try:
                        async with queue._lock:
                            # Read-only HMAC and retry-lease checks must come
                            # first. Queue mutation methods prune receipts
                            # before validating their operation IDs, so they
                            # cannot be used as probes.
                            queue._validate_operation_id(operation_id)
                            queue._validate_operation_id(target_operation_id)
                            prior = queue._operations.get(operation_id)
                            if prior is not None:
                                if (
                                    prior.kind == operation_kind
                                    and prior.fingerprint == operation_fingerprint
                                ):
                                    matches.append(queue)
                                continue
                            if operation_kind == "settle_item":
                                if (
                                    queue._active_item_attempt_id == target_operation_id
                                    and queue._active_claim is not None
                                    and queue._active_batch_id is not None
                                ):
                                    matches.append(queue)
                            elif operation_kind == "release_claim":
                                claim_id = target_operation_id
                                reservation = queue._claim_reservations.get(claim_id)
                                batch = queue._batches.get(queue._active_batch_id or "")
                                if (
                                    queue._active_claim == claim_id
                                    and batch is not None
                                    and batch.claim_id == claim_id
                                    and reservation is not None
                                    and not reservation.dispatched
                                    and queue._active_item_attempt_id is None
                                    and batch.status.value != "in_doubt"
                                ):
                                    matches.append(queue)
                    except Exception:
                        continue
                if len(matches) != 1:
                    raise QueueRpcAuthorizationError("queue access denied") from None
                try:
                    result = operation(matches[0])
                    if inspect.isawaitable(result):
                        result = await result
                    return result
                except Exception:
                    # Do not disclose which retained scope authenticated a
                    # partial or state-invalid capability pair.
                    raise QueueRpcAuthorizationError("queue access denied") from None

        async with registry.acquire(
            resolved.scope_id,
            principal_id=resolved.principal_id,
            conversation_uid=resolved.conversation_uid,
        ) as queue:
            result = operation(queue)
            if inspect.isawaitable(result):
                result = await result
            return result

    async def _allocate_ids(params: dict[str, Any]) -> dict[str, Any]:
        item_count = _bounded_integer(
            params,
            "item_count",
            default=1,
            maximum=registry.max_batch_items,
        )
        operation_count = _bounded_integer(
            params,
            "operation_count",
            default=1,
            maximum=MAX_OPERATION_IDS_PER_ALLOCATION,
        )
        attachment_operation_count = _bounded_integer(
            params,
            "attachment_operation_count",
            default=0,
            maximum=MAX_OPERATION_IDS_PER_ALLOCATION,
        )
        if item_count < 1 or operation_count < 1:
            raise ValueError("item_count and operation_count must be positive")
        if attachment_operation_count and attachment_registry is None:
            raise ValueError("attachment staging is unavailable")

        async def mint(queue: WebMessageQueue) -> dict[str, Any]:
            result = {
                "scope_id": queue.scope_id,
                "incarnation": queue.incarnation,
                "operation_ids": [
                    queue.new_operation_id() for _ in range(operation_count)
                ],
                "batch_id": queue.new_batch_id(),
                "request_ids": [queue.new_request_id() for _ in range(item_count)],
            }
            if attachment_operation_count:
                assert attachment_registry is not None
                attachment_scope = await attachment_registry.bind_scope(
                    params, current_rpc_caller_context()
                )
                if (
                    attachment_scope.binding.scope_id != queue.scope_id
                    or attachment_scope.binding.queue_incarnation != queue.incarnation
                ):
                    raise QueueRpcAuthorizationError("queue access denied")
                result["attachment_operation_ids"] = [
                    attachment_scope.new_operation_id()
                    for _ in range(attachment_operation_count)
                ]
            else:
                result["attachment_operation_ids"] = []
            return result

        return await _with_queue(params, mint)

    async def _stage_attachment(params: dict[str, Any]) -> dict[str, Any]:
        if attachment_registry is None:
            raise ValueError("attachment staging is unavailable")
        operation_id = _required_text(params, "operation_id")
        mime = _required_text(params, "mime")
        filename = params.get("filename", "Image")
        if not isinstance(filename, str) or len(filename) > 1024:
            raise ValueError("invalid image filename")
        encoded = params.get("payload_b64")
        if not isinstance(encoded, str):
            raise ValueError("invalid image payload")
        max_encoded = ((DEFAULT_MAX_ATTACHMENT_BYTES + 2) // 3) * 4
        if len(encoded) > max_encoded:
            raise ValueError("image exceeds the attachment size limit")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("invalid image payload") from None
        if not payload:
            raise ValueError("invalid image payload")
        mime = _validated_image_mime(mime, payload)
        attachment_scope = await attachment_registry.bind_scope(
            params, current_rpc_caller_context()
        )
        receipt = await attachment_scope.stage(
            operation_id, mime, payload, filename=filename
        )
        return {
            "ref": receipt.ref,
            "mime": receipt.mime,
            "size": receipt.size,
            "filename": receipt.filename,
        }

    async def _open_attachment(params: dict[str, Any]) -> dict[str, Any]:
        if attachment_registry is None:
            raise ValueError("attachment staging is unavailable")
        request_id = _required_text(params, "request_id")
        ref = _required_text(params, "ref")

        async def open_owned(queue: WebMessageQueue) -> dict[str, Any]:
            snapshot = await queue.snapshot()
            if not any(
                item.request_id == request_id and ref in item.attachment_refs
                for batch in snapshot.batches
                for item in batch.items
            ):
                raise ValueError("attachment not found")
            attachment_scope = await attachment_registry.bind_scope(
                params, current_rpc_caller_context()
            )
            if (
                attachment_scope.binding.scope_id != queue.scope_id
                or attachment_scope.binding.queue_incarnation != queue.incarnation
            ):
                raise QueueRpcAuthorizationError("queue access denied")
            staged = await attachment_scope.open_for_owner(request_id, ref)
            return {
                "ref": staged.ref,
                "mime": staged.mime,
                "size": len(staged.data),
                "payload_b64": base64.b64encode(staged.data).decode("ascii"),
            }

        return await _with_queue(params, open_owned)

    async def _snapshot(params: dict[str, Any]) -> dict[str, Any]:
        async def read(queue: WebMessageQueue) -> dict[str, Any]:
            return _snapshot_to_wire(await queue.snapshot())

        return await _with_queue(params, read)

    async def _enqueue_batch(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        batch_id = _required_text(params, "batch_id")

        async def enqueue(queue: WebMessageQueue) -> dict[str, Any]:
            items = _decode_items(params, registry)
            try:
                if any(item.attachment_refs for item in items):
                    if attachment_registry is None:
                        raise ValueError("attachment staging is unavailable")
                    attachment_scope = await attachment_registry.bind_scope(
                        params, current_rpc_caller_context()
                    )
                    if (
                        attachment_scope.binding.scope_id != queue.scope_id
                        or attachment_scope.binding.queue_incarnation != queue.incarnation
                    ):
                        raise QueueRpcAuthorizationError("queue access denied")
                    receipt = await queue.enqueue_batch_with_attachments(
                        operation_id,
                        batch_id,
                        items,
                        attachment_scope=attachment_scope,
                    )
                else:
                    receipt = await queue.enqueue_batch(operation_id, batch_id, items)
            except (CapacityExceeded, AttachmentCapacityExceeded):
                # Both queue methods validate capacity before mutating state or
                # retaining attachment ownership. Other RPC failures stay opaque.
                raise _QueueCapacityRejected() from None
            await _notify(queue)
            return _receipt_to_wire(receipt)

        return await _with_queue(params, enqueue)

    async def _claim_next(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")

        async def claim(queue: WebMessageQueue) -> dict[str, Any]:
            receipt = await queue.claim_next(operation_id)
            await _notify(queue)
            return _receipt_to_wire(receipt)

        return await _with_queue(params, claim)

    async def _claim_batch(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        batch_id = _required_text(params, "batch_id")
        send_now = params.get("send_now", False)
        if not isinstance(send_now, bool):
            raise ValueError("send_now must be a bool")

        async def claim(queue: WebMessageQueue) -> dict[str, Any]:
            receipt = await queue.claim_batch(operation_id, batch_id, send_now=send_now)
            await _notify(queue)
            return _receipt_to_wire(receipt)

        return await _with_queue(params, claim)

    async def _claim_next_item(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        claim_id = _required_text(params, "claim_id")

        async def claim(queue: WebMessageQueue) -> dict[str, Any]:
            receipt = await queue.claim_next_item(operation_id, claim_id)
            await _notify(queue)
            return _receipt_to_wire(receipt)

        return await _with_queue(params, claim)

    async def _item_payload(params: dict[str, Any]) -> dict[str, Any]:
        attempt_id = _required_text(params, "attempt_id")
        payload = await _with_queue(
            params, lambda queue: queue.item_payload(attempt_id)
        )
        return _claim_payload_to_wire(payload)

    async def _settle_item(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        attempt_id = _required_text(params, "attempt_id")
        outcome_value = params.get("outcome")
        if not isinstance(outcome_value, str):
            raise ValueError("invalid outcome")
        from .web_message_queue import ClaimOutcome

        try:
            outcome = ClaimOutcome(outcome_value)
        except ValueError:
            raise ValueError("invalid outcome") from None

        async def settle(queue: WebMessageQueue) -> dict[str, Any]:
            receipt = await queue.settle_item(operation_id, attempt_id, outcome)
            await _notify(queue)
            return _receipt_to_wire(receipt)

        return await _with_cleanup_queue(
            params,
            operation_id,
            attempt_id,
            "settle_item",
            WebMessageQueue._fingerprint(
                "settle_item",
                attempt_id,
                outcome.value,
            ),
            settle,
        )

    async def _remove_item(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        request_id = _required_text(params, "request_id")

        async def remove(queue: WebMessageQueue) -> dict[str, Any]:
            if attachment_registry is None:
                receipt = await queue.remove_item(operation_id, request_id)
            else:
                attachment_scope = await attachment_registry.bind_scope(
                    params, current_rpc_caller_context()
                )
                if (
                    attachment_scope.binding.scope_id != queue.scope_id
                    or attachment_scope.binding.queue_incarnation != queue.incarnation
                ):
                    raise QueueRpcAuthorizationError("queue access denied")
                receipt = await queue.remove_item_with_attachments(
                    operation_id,
                    request_id,
                    attachment_scope=attachment_scope,
                )
            await _notify(queue)
            return _receipt_to_wire(receipt)

        return await _with_queue(params, remove)

    async def _release_claim(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        claim_id = _required_text(params, "claim_id")

        async def release(queue: WebMessageQueue) -> dict[str, Any]:
            receipt = await queue.release_claim(operation_id, claim_id)
            await _notify(queue)
            return _receipt_to_wire(receipt)

        return await _with_cleanup_queue(
            params,
            operation_id,
            claim_id,
            "release_claim",
            WebMessageQueue._fingerprint("release_claim", claim_id),
            release,
        )

    handlers = {
        "state.web_queue.allocate_ids": _allocate_ids,
        "state.web_queue.snapshot": _snapshot,
        "state.web_queue.enqueue_batch": _enqueue_batch,
        "state.web_queue.claim_next": _claim_next,
        "state.web_queue.claim_batch": _claim_batch,
        "state.web_queue.claim_next_item": _claim_next_item,
        "state.web_queue.item_payload": _item_payload,
        "state.web_queue.settle_item": _settle_item,
        "state.web_queue.remove_item": _remove_item,
        "state.web_queue.release_claim": _release_claim,
    }
    if attachment_registry is not None:
        handlers.update(
            {
                "state.web_queue.stage_attachment": _stage_attachment,
                "state.web_queue.open_attachment": _open_attachment,
            }
        )
    if not include_worker_methods:
        allowed = {
            "state.web_queue.allocate_ids",
            "state.web_queue.snapshot",
            "state.web_queue.enqueue_batch",
            "state.web_queue.remove_item",
        }
        handlers = {method: handler for method, handler in handlers.items() if method in allowed or method in {
            "state.web_queue.stage_attachment",
            "state.web_queue.open_attachment",
        }}
    for method, handler in handlers.items():
        rpc_server.register(method, handler)
