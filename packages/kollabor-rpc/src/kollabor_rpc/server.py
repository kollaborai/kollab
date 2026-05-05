"""RpcServer dispatcher.

Owns a registry of named handlers and routes incoming ``RpcRequest`` instances
to the matching handler. Handlers may be synchronous or asynchronous. The
dispatcher never raises out of ``handle_request``; every failure mode is
converted into a structured ``RpcResponse`` with an ``error_kind`` field the
client can inspect (``not_found``, ``handler``, or ``serialization``).

This class is transport-agnostic. The hub messenger (phase 1.6) is responsible
for pulling wire dicts off the socket and feeding them into ``handle_wire``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, Callable

from .models import RpcRequest, RpcResponse

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Any]  # sync OR async, may return coroutine

__all__ = ["RpcServer"]


class RpcServer:
    """Dispatch RPC requests to registered handlers.

    Handlers are keyed by method name. Registration is explicit and duplicate
    registrations raise. The dispatcher converts exceptions and
    non-serializable results into structured error responses rather than
    letting them propagate; callers of ``handle_request`` and ``handle_wire``
    can rely on never seeing an exception escape the dispatcher.
    """

    def __init__(self) -> None:
        """Initialize an empty handler registry."""
        self._handlers: dict[str, Handler] = {}

    def register(self, method: str, handler: Handler) -> None:
        """Register a handler for an RPC method.

        Handler may be sync or async; async handlers are awaited. Raises
        ValueError on duplicate registration.
        """
        if method in self._handlers:
            raise ValueError(f"rpc method already registered: {method}")
        self._handlers[method] = handler
        logger.debug("rpc handler registered: %s", method)

    def unregister(self, method: str) -> None:
        """Remove a previously registered handler.

        Silent no-op if the method was never registered. Useful for plugins
        that want to tear down their handlers on shutdown without tracking
        which ones they actually installed.
        """
        if self._handlers.pop(method, None) is not None:
            logger.debug("rpc handler unregistered: %s", method)

    def list_methods(self) -> list[str]:
        """Return the sorted list of currently registered method names."""
        return sorted(self._handlers.keys())

    async def handle_request(self, request: RpcRequest) -> RpcResponse:
        """Dispatch a single request to its handler.

        Never raises. All failure modes become ``RpcResponse`` objects with a
        populated ``error`` and ``error_kind`` field:

        - ``not_found``: no handler registered for ``request.method``
        - ``handler``: the handler raised an exception
        - ``serialization``: the handler's return value cannot be encoded to
          JSON even with ``default=str`` coercion
        """
        handler = self._handlers.get(request.method)
        if handler is None:
            logger.warning("rpc method not found: %s", request.method)
            return RpcResponse(
                request_id=request.request_id,
                error=f"method not found: {request.method}",
                error_kind="not_found",
            )

        try:
            result = handler(request.params)
            if inspect.iscoroutine(result) or asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - dispatcher must catch everything
            logger.warning(
                "rpc handler raised for method %s", request.method, exc_info=True
            )
            return RpcResponse(
                request_id=request.request_id,
                error=repr(exc),
                error_kind="handler",
            )

        try:
            json.dumps(result, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "rpc result not json-serializable for method %s: %s",
                request.method,
                exc,
            )
            return RpcResponse(
                request_id=request.request_id,
                error=f"result not json-serializable: {exc}",
                error_kind="serialization",
            )

        return RpcResponse(request_id=request.request_id, result=result)

    async def handle_wire(self, msg_data: dict[str, Any]) -> dict[str, Any]:
        """Wire-level convenience: accept a wire dict, return a wire dict.

        Transport adapters (hub messenger, loopback bridge) use this to avoid
        reaching into ``RpcRequest``/``RpcResponse`` themselves.
        """
        request = RpcRequest.from_wire(msg_data)
        response = await self.handle_request(request)
        return response.to_wire()
