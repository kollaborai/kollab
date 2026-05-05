"""End-to-end loopback test for RpcServer <-> RpcClient over a unix socket.

Spins up a real asyncio.start_unix_server with an RpcServer on one side,
connects via asyncio.open_unix_connection on the other side, drives an
RpcClient, and verifies full round-trip RPC calls work over the wire.

The attach client architecture is: RpcClient writes requests via its
StreamWriter, and a separate read loop on the client side (not part of
RpcClient) routes incoming replies back to client.on_reply(). This test
replicates that architecture with a small reply-router coroutine.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import pytest

from kollabor_rpc import (
    RpcClient,
    RpcHandlerError,
    RpcMethodNotFound,
    RpcServer,
    RpcTimeoutError,
)


@pytest.fixture
def socket_path() -> Path:
    """Per-test socket path under /tmp with a short unique name.

    macOS caps unix socket paths at ~104 chars; pytest's default tmp_path
    (under /var/folders/.../T/pytest-of-user/pytest-N/test_name/) routinely
    exceeds that limit for long test names. We mint a short path under /tmp
    and clean it up manually at teardown.
    """
    path = Path(f"/tmp/krpc-{uuid.uuid4().hex[:12]}.sock")
    yield path
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


async def _start_server(
    socket_path: Path, rpc_server: RpcServer
) -> asyncio.AbstractServer:
    """Start a unix server that dispatches incoming NDJSON through an RpcServer.

    Each connection is handled independently: read lines, parse JSON,
    call rpc_server.handle_wire, write the reply back as NDJSON.
    """

    async def handle_connection(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    msg = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue
                # Only dispatch rpc_request frames; ignore anything else
                if msg.get("action") != "rpc_request":
                    continue
                reply = await rpc_server.handle_wire(msg)
                writer.write((json.dumps(reply, default=str) + "\n").encode("utf-8"))
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_unix_server(handle_connection, path=str(socket_path))
    return server


async def _reply_router(
    reader: asyncio.StreamReader, client: RpcClient, stop_event: asyncio.Event
) -> None:
    """Read NDJSON from the client socket and route rpc_reply frames into the client.

    This simulates the role of application.py:_read_remote_events in the
    real attach client.
    """
    while not stop_event.is_set():
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=0.1)
        except asyncio.TimeoutError:
            continue
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8").strip())
        except json.JSONDecodeError:
            continue
        if msg.get("action") == "rpc_reply":
            client.on_reply(msg)


@pytest.mark.asyncio
async def test_ping_round_trip(socket_path: Path) -> None:
    """Happy path: register ping on server, call from client, assert result."""
    rpc_server = RpcServer()

    started_at = time.monotonic()
    daemon_pid = os.getpid()

    async def ping_handler(params: dict) -> dict:
        return {
            "status": "ok",
            "daemon_pid": daemon_pid,
            "uptime": time.monotonic() - started_at,
            "identity": "loopback-test",
        }

    rpc_server.register("ping", ping_handler)

    server = await _start_server(socket_path, rpc_server)
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        client = RpcClient(writer, default_timeout=5.0)
        stop_event = asyncio.Event()
        router_task = asyncio.create_task(_reply_router(reader, client, stop_event))

        try:
            result = await client.call("ping", {})
            assert result["status"] == "ok"
            assert result["daemon_pid"] == daemon_pid
            assert result["identity"] == "loopback-test"
            assert result["uptime"] >= 0
        finally:
            stop_event.set()
            client.close()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            router_task.cancel()
            try:
                await router_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_concurrent_ping_calls(socket_path: Path) -> None:
    """10 concurrent pings all resolve independently."""
    rpc_server = RpcServer()
    call_count = 0

    async def counter_handler(params: dict) -> dict:
        nonlocal call_count
        call_count += 1
        return {"call_number": call_count}

    rpc_server.register("counter", counter_handler)

    server = await _start_server(socket_path, rpc_server)
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        client = RpcClient(writer, default_timeout=5.0)
        stop_event = asyncio.Event()
        router_task = asyncio.create_task(_reply_router(reader, client, stop_event))

        try:
            results = await asyncio.gather(
                *[client.call("counter", {}) for _ in range(10)]
            )
            assert len(results) == 10
            call_numbers = sorted(r["call_number"] for r in results)
            assert call_numbers == list(range(1, 11))
        finally:
            stop_event.set()
            client.close()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            router_task.cancel()
            try:
                await router_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unknown_method_raises_method_not_found(socket_path: Path) -> None:
    """Calling an unregistered method raises RpcMethodNotFound on the client."""
    rpc_server = RpcServer()
    server = await _start_server(socket_path, rpc_server)
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        client = RpcClient(writer, default_timeout=5.0)
        stop_event = asyncio.Event()
        router_task = asyncio.create_task(_reply_router(reader, client, stop_event))

        try:
            with pytest.raises(RpcMethodNotFound) as exc_info:
                await client.call("nonexistent", {})
            assert "nonexistent" in str(exc_info.value)
        finally:
            stop_event.set()
            client.close()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            router_task.cancel()
            try:
                await router_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_handler_exception_raises_rpc_handler_error(socket_path: Path) -> None:
    """Handler that raises produces an RpcHandlerError on the client."""
    rpc_server = RpcServer()

    async def failing_handler(params: dict) -> dict:
        raise RuntimeError("boom in handler")

    rpc_server.register("fail", failing_handler)
    server = await _start_server(socket_path, rpc_server)
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        client = RpcClient(writer, default_timeout=5.0)
        stop_event = asyncio.Event()
        router_task = asyncio.create_task(_reply_router(reader, client, stop_event))

        try:
            with pytest.raises(RpcHandlerError) as exc_info:
                await client.call("fail", {})
            assert "boom" in str(exc_info.value)
        finally:
            stop_event.set()
            client.close()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            router_task.cancel()
            try:
                await router_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_client_timeout_when_handler_sleeps_past_limit(
    socket_path: Path,
) -> None:
    """Client raises RpcTimeoutError when the handler sleeps past the deadline."""
    rpc_server = RpcServer()

    async def slow_handler(params: dict) -> dict:
        await asyncio.sleep(2.0)
        return {"done": True}

    rpc_server.register("slow", slow_handler)
    server = await _start_server(socket_path, rpc_server)
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        client = RpcClient(writer, default_timeout=0.1)
        stop_event = asyncio.Event()
        router_task = asyncio.create_task(_reply_router(reader, client, stop_event))

        try:
            with pytest.raises(RpcTimeoutError) as exc_info:
                await client.call("slow", {}, timeout=0.1)
            assert "slow" in str(exc_info.value)
            # Verify cleanup: no pending entries after timeout
            assert len(client._pending) == 0
        finally:
            stop_event.set()
            client.close()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            router_task.cancel()
            try:
                await router_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        server.close()
        await server.wait_closed()
