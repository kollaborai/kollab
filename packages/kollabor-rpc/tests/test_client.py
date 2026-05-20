"""Tests for kollabor_rpc.client."""

from __future__ import annotations

import asyncio
import json

import pytest

from kollabor_rpc.client import RpcClient
from kollabor_rpc.errors import (
    RpcError,
    RpcHandlerError,
    RpcMethodNotFound,
    RpcTimeoutError,
)

HEX_CHARS = set("0123456789abcdef")


class FakeStreamWriter:
    """Minimal asyncio.StreamWriter stand-in for exercising RpcClient.

    Buffers writes as bytes, counts drain() calls, and can parse the buffered
    NDJSON stream back into dicts for assertions.
    """

    def __init__(self) -> None:
        self.buffer: list[bytes] = []
        self.drained: int = 0
        self.closed: bool = False

    def write(self, data: bytes) -> None:
        self.buffer.append(data)

    async def drain(self) -> None:
        self.drained += 1

    def close(self) -> None:
        self.closed = True

    def get_all_writes(self) -> list[dict]:
        """Parse all buffered writes back into dicts."""
        lines = b"".join(self.buffer).decode("utf-8").strip().split("\n")
        return [json.loads(line) for line in lines if line]


async def _wait_for_write(writer: FakeStreamWriter, expected: int = 1) -> None:
    """Yield control until at least ``expected`` lines have been buffered."""
    for _ in range(200):
        writes = writer.get_all_writes() if writer.buffer else []
        if len(writes) >= expected:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(
        f"timed out waiting for {expected} writes; got {len(writer.buffer)}"
    )


@pytest.mark.asyncio
async def test_call_writes_request_to_writer() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer, default_timeout=5.0)

    task = asyncio.create_task(client.call("ping", {"x": 1}, timeout=2.0))
    try:
        await _wait_for_write(writer, expected=1)

        writes = writer.get_all_writes()
        assert len(writes) == 1
        msg = writes[0]
        assert msg["action"] == "rpc_request"
        assert msg["method"] == "ping"
        assert msg["params"] == {"x": 1}
        assert msg["timeout"] == 2.0
        assert isinstance(msg["request_id"], str)
        assert len(msg["request_id"]) == 32
        assert all(ch in HEX_CHARS for ch in msg["request_id"])
        assert writer.drained >= 1
    finally:
        client.close()
        with pytest.raises(RpcError):
            await task


@pytest.mark.asyncio
async def test_call_resolves_on_matching_reply() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task = asyncio.create_task(client.call("ping", {"x": 1}, timeout=2.0))
    await _wait_for_write(writer, expected=1)

    request_id = writer.get_all_writes()[0]["request_id"]
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": request_id,
            "result": {"pong": True},
        }
    )

    result = await asyncio.wait_for(task, timeout=1.0)
    assert result == {"pong": True}
    assert client._pending == {}
    assert client.pending_count == 0


@pytest.mark.asyncio
async def test_pending_count_tracks_in_flight_call() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task = asyncio.create_task(client.call("ping", timeout=2.0))
    await _wait_for_write(writer, expected=1)

    assert client.pending_count == 1

    request_id = writer.get_all_writes()[0]["request_id"]
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": request_id,
            "result": "pong",
        }
    )

    assert await asyncio.wait_for(task, timeout=1.0) == "pong"
    assert client.pending_count == 0


@pytest.mark.asyncio
async def test_on_reply_unknown_id_is_silent() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    # No pending future -- should not raise.
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": "nonexistent",
            "result": {"pong": True},
        }
    )
    # Missing request_id entirely -- also should not raise.
    client.on_reply({"action": "rpc_reply", "result": {"pong": True}})


@pytest.mark.asyncio
async def test_call_timeout_raises_rpc_timeout_error() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    with pytest.raises(RpcTimeoutError) as excinfo:
        await client.call("never_answers", timeout=0.05)

    assert "never_answers" in str(excinfo.value)
    assert "0.05" in str(excinfo.value)
    # Cleanup in finally must have popped the entry.
    assert client._pending == {}


@pytest.mark.asyncio
async def test_on_reply_with_not_found_error_raises_typed_exception() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task = asyncio.create_task(client.call("missing", timeout=2.0))
    await _wait_for_write(writer, expected=1)

    request_id = writer.get_all_writes()[0]["request_id"]
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": request_id,
            "error": "method not found: missing",
            "error_kind": "not_found",
        }
    )

    with pytest.raises(RpcMethodNotFound) as excinfo:
        await asyncio.wait_for(task, timeout=1.0)
    assert "method not found: missing" in str(excinfo.value)


@pytest.mark.asyncio
async def test_on_reply_with_handler_error_raises_typed_exception() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task = asyncio.create_task(client.call("boom", timeout=2.0))
    await _wait_for_write(writer, expected=1)

    request_id = writer.get_all_writes()[0]["request_id"]
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": request_id,
            "error": "ValueError: kaboom",
            "error_kind": "handler",
        }
    )

    with pytest.raises(RpcHandlerError) as excinfo:
        await asyncio.wait_for(task, timeout=1.0)
    assert excinfo.value.remote_message == "ValueError: kaboom"
    assert "kaboom" in str(excinfo.value)


@pytest.mark.asyncio
async def test_on_reply_with_serialization_error_raises_typed_exception() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task = asyncio.create_task(client.call("bad_result", timeout=2.0))
    await _wait_for_write(writer, expected=1)

    request_id = writer.get_all_writes()[0]["request_id"]
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": request_id,
            "error": "not JSON serializable: <object>",
            "error_kind": "serialization",
        }
    )

    with pytest.raises(RpcHandlerError) as excinfo:
        await asyncio.wait_for(task, timeout=1.0)
    assert excinfo.value.remote_message == "not JSON serializable: <object>"


@pytest.mark.asyncio
async def test_on_reply_with_generic_error_raises_rpc_error() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task = asyncio.create_task(client.call("weird", timeout=2.0))
    await _wait_for_write(writer, expected=1)

    request_id = writer.get_all_writes()[0]["request_id"]
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": request_id,
            "error": "unclassified failure",
        }
    )

    with pytest.raises(RpcError) as excinfo:
        await asyncio.wait_for(task, timeout=1.0)
    assert type(excinfo.value) is RpcError
    assert "unclassified failure" in str(excinfo.value)


@pytest.mark.asyncio
async def test_two_concurrent_calls_resolve_independently() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task_a = asyncio.create_task(client.call("alpha", {"n": 1}, timeout=2.0))
    task_b = asyncio.create_task(client.call("beta", {"n": 2}, timeout=2.0))

    await _wait_for_write(writer, expected=2)

    writes = writer.get_all_writes()
    assert len(writes) == 2

    # Match by method (write order reflects task scheduling order; do not
    # assume it).
    by_method = {msg["method"]: msg["request_id"] for msg in writes}
    rid_a = by_method["alpha"]
    rid_b = by_method["beta"]
    assert rid_a != rid_b

    # Reply to beta first, alpha second -- out of order.
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": rid_b,
            "result": {"name": "beta"},
        }
    )
    client.on_reply(
        {
            "action": "rpc_reply",
            "request_id": rid_a,
            "result": {"name": "alpha"},
        }
    )

    result_a, result_b = await asyncio.wait_for(
        asyncio.gather(task_a, task_b), timeout=1.0
    )
    assert result_a == {"name": "alpha"}
    assert result_b == {"name": "beta"}
    assert client._pending == {}


@pytest.mark.asyncio
async def test_close_cancels_pending_calls() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    task = asyncio.create_task(client.call("slow", timeout=30.0))
    await _wait_for_write(writer, expected=1)

    client.close()

    with pytest.raises(RpcError) as excinfo:
        await asyncio.wait_for(task, timeout=1.0)
    assert "closed" in str(excinfo.value)
    assert client._pending == {}


@pytest.mark.asyncio
async def test_call_after_close_raises_immediately() -> None:
    writer = FakeStreamWriter()
    client = RpcClient(writer)

    client.close()
    with pytest.raises(RpcError) as excinfo:
        await client.call("ping", {})

    assert "closed" in str(excinfo.value)
    # Writer must be untouched -- no bytes emitted post-close.
    assert writer.buffer == []
    assert writer.drained == 0
