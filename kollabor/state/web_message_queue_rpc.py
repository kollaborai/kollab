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
import inspect
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from .web_message_queue import (
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


class QueueRpcAuthorizationError(PermissionError):
    """The trusted daemon context did not authorize queue access."""


class QueueScopeCapacityExceeded(RuntimeError):
    """The bounded scoped-queue registry cannot safely admit another scope."""


ScopeResolver = Callable[[dict[str, Any]], str | Awaitable[str]]
RpcChangeNotifier = Callable[[dict[str, Any]], Any]


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
        self._lock = asyncio.Lock()

    @property
    def scope_count(self) -> int:
        """Current number of retained scoped queues."""
        return len(self._queues)

    async def _make_room_unlocked(self) -> None:
        if len(self._queues) < self._max_scopes:
            return
        for scope_id, queue in tuple(self._queues.items()):
            if self._leases.get(scope_id, 0):
                continue
            if await queue.can_retire():
                del self._queues[scope_id]
                del self._leases[scope_id]
                if len(self._queues) < self._max_scopes:
                    return
        raise QueueScopeCapacityExceeded(
            "queue scope capacity is full; retained work or retry leases prevent retirement"
        )

    @asynccontextmanager
    async def acquire(self, scope_id: str) -> AsyncIterator[WebMessageQueue]:
        """Hold a registry lease while operating on a trusted scope's queue."""
        if not isinstance(scope_id, str) or not scope_id:
            raise InvalidIdentifier("invalid queue scope")
        async with self._lock:
            queue = self._queues.get(scope_id)
            if queue is None:
                await self._make_room_unlocked()
                queue = WebMessageQueue(scope_id=scope_id, **self._queue_options)
                self._queues[scope_id] = queue
                self._leases[scope_id] = 0
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
                        "payload_b64": base64.b64encode(item.payload).decode("ascii"),
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
        items.append(WebMessage(request_id=request_id, payload=payload))
    return tuple(items)


def register_web_message_queue_handlers(
    rpc_server: Any,
    registry: WebMessageQueueRegistry,
    *,
    resolve_scope: ScopeResolver,
    on_change: RpcChangeNotifier | None = None,
) -> None:
    """Register JSON-safe queue RPC handlers on an existing ``RpcServer``.

    The required resolver is responsible for authorizing the caller and
    returning a canonical opaque scope ID. Handler parameters never select a
    scope directly. The optional notifier must be the built-in ``put_nowait``
    method of a finite ``asyncio.Queue``. It receives only
    scope/incarnation/revision metadata, never message bytes. Full buffers drop
    invalidation hints; snapshots remain authoritative.
    """

    change_queue, change_queue_limit = _validate_change_notifier(on_change)

    async def _scope(params: dict[str, Any]) -> str:
        try:
            value = resolve_scope(params)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            raise QueueRpcAuthorizationError("queue access denied") from None
        if not isinstance(value, str) or not value:
            raise QueueRpcAuthorizationError("queue access denied")
        return value

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
        scope_id = await _scope(params)
        async with registry.acquire(scope_id) as queue:
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
        if item_count < 1 or operation_count < 1:
            raise ValueError("item_count and operation_count must be positive")

        def mint(queue: WebMessageQueue) -> dict[str, Any]:
            return {
                "scope_id": queue.scope_id,
                "incarnation": queue.incarnation,
                "operation_ids": [
                    queue.new_operation_id() for _ in range(operation_count)
                ],
                "batch_id": queue.new_batch_id(),
                "request_ids": [queue.new_request_id() for _ in range(item_count)],
            }

        return await _with_queue(params, mint)

    async def _snapshot(params: dict[str, Any]) -> dict[str, Any]:
        async def read(queue: WebMessageQueue) -> dict[str, Any]:
            return _snapshot_to_wire(await queue.snapshot())

        return await _with_queue(params, read)

    async def _enqueue_batch(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        batch_id = _required_text(params, "batch_id")

        async def enqueue(queue: WebMessageQueue) -> dict[str, Any]:
            items = _decode_items(params, registry)
            receipt = await queue.enqueue_batch(operation_id, batch_id, items)
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

        return await _with_queue(params, settle)

    async def _remove_item(params: dict[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(params, "operation_id")
        request_id = _required_text(params, "request_id")

        async def remove(queue: WebMessageQueue) -> dict[str, Any]:
            receipt = await queue.remove_item(operation_id, request_id)
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

        return await _with_queue(params, release)

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
    for method, handler in handlers.items():
        rpc_server.register(method, handler)
