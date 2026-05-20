"""Async RPC client that sends requests over an asyncio StreamWriter and resolves
responses via ``on_reply``.

The caller is responsible for wiring ``on_reply`` into whatever reads the
underlying transport (e.g. the attach client's read loop). Multiple in-flight
calls are supported -- each has its own future keyed by ``request_id``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .errors import RpcError, RpcHandlerError, RpcMethodNotFound, RpcTimeoutError
from .models import RpcRequest, new_request_id

logger = logging.getLogger(__name__)


class RpcClient:
    """Async RPC client multiplexed over a single asyncio StreamWriter.

    Usage pattern: the caller constructs the client with an already-connected
    writer, then arranges for incoming lines from the matching reader to be
    decoded and passed to :meth:`on_reply`. Each :meth:`call` returns when the
    matching reply arrives or raises on timeout/error.

    IMPORTANT: The underlying StreamReader/Writer pair MUST be created
    with a buffer large enough for the largest expected response. The
    default asyncio.open_unix_connection() uses a 64KB buffer which is
    too small for real state payloads. Use kollabor_rpc's
    open_unix_connection_with_large_buffer() helper to get a 16MB limit.

    The caller (not RpcClient) is responsible for creating the reader
    and writer -- RpcClient only holds a writer reference for sending
    requests and receives replies via on_reply() which is called by
    the caller's read loop.
    """

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        *,
        default_timeout: float = 30.0,
    ) -> None:
        """Initialize the client.

        Args:
            writer: An already-connected asyncio StreamWriter. The client does
                not own the writer and will not close it on :meth:`close`.
            default_timeout: Fallback timeout (seconds) used when
                :meth:`call` is invoked without an explicit ``timeout``.
        """
        self._writer: asyncio.StreamWriter = writer
        self._default_timeout: float = default_timeout
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._closed: bool = False

    @property
    def pending_count(self) -> int:
        """Return the number of in-flight RPC requests."""
        return len(self._pending)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send an RPC request and wait for the response.

        Concurrent calls are supported -- each has its own future keyed by the
        generated ``request_id``. The writer is guarded by an ``asyncio.Lock``
        so that interleaved bytes from concurrent writes cannot corrupt the
        NDJSON stream.

        Args:
            method: Remote method name.
            params: JSON-serializable parameter dict. Defaults to an empty dict.
            timeout: Per-call timeout (seconds). When ``None`` the client's
                ``default_timeout`` is used.

        Returns:
            The decoded ``result`` field from the matching RPC reply.

        Raises:
            RpcError: The client has been closed.
            RpcTimeoutError: No reply arrived before the effective timeout.
            RpcMethodNotFound: The server reported ``error_kind == "not_found"``.
            RpcHandlerError: The server reported ``error_kind in {"handler",
                "serialization"}``.
        """
        if self._closed:
            raise RpcError("client closed")

        params = params or {}
        effective_timeout = timeout if timeout is not None else self._default_timeout
        request_id = new_request_id()

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = fut

        req = RpcRequest(
            request_id=request_id,
            method=method,
            params=params,
            timeout=effective_timeout,
        )
        line = json.dumps(req.to_wire(), default=str) + "\n"

        try:
            async with self._lock:
                self._writer.write(line.encode("utf-8"))
                await self._writer.drain()
            try:
                return await asyncio.wait_for(fut, effective_timeout)
            except asyncio.TimeoutError:
                raise RpcTimeoutError(
                    f"rpc call {method!r} timed out after {effective_timeout}s"
                )
        finally:
            self._pending.pop(request_id, None)

    def on_reply(self, msg_data: dict[str, Any]) -> None:
        """Route an incoming ``rpc_reply`` dict to the waiting future.

        Called by the attach client's read loop. Unknown ``request_id``s are
        logged and discarded (likely stale after timeout). Synchronous;
        returns immediately after scheduling the future resolution.
        """
        request_id = msg_data.get("request_id")
        if not request_id:
            logger.warning("rpc reply missing request_id: %r", msg_data)
            return

        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            logger.debug("stale rpc reply for request_id=%s", request_id)
            return

        error = msg_data.get("error")
        error_kind = msg_data.get("error_kind")

        if error_kind == "not_found":
            fut.set_exception(RpcMethodNotFound(error or "method not found"))
        elif error_kind == "handler":
            fut.set_exception(
                RpcHandlerError(
                    error or "remote handler raised",
                    remote_message=error,
                )
            )
        elif error_kind == "serialization":
            fut.set_exception(
                RpcHandlerError(
                    error or "remote result not serializable",
                    remote_message=error,
                )
            )
        elif error:
            fut.set_exception(RpcError(error))
        else:
            fut.set_result(msg_data.get("result"))

    def close(self) -> None:
        """Cancel all pending calls with ``RpcError('rpc client closed')``.

        Safe to call multiple times. Does NOT close the underlying writer --
        the caller owns that. After ``close()`` any subsequent :meth:`call`
        raises ``RpcError`` immediately without touching the writer.
        """
        if self._closed:
            return
        self._closed = True
        for _request_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(RpcError("rpc client closed"))
        self._pending.clear()
