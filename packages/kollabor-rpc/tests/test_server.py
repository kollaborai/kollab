"""Tests for kollabor_rpc.server.RpcServer."""

from __future__ import annotations

from typing import Any

import pytest

from kollabor_rpc.models import RpcRequest
from kollabor_rpc.server import RpcServer


def _req(
    method: str, params: dict[str, Any] | None = None, rid: str = "rid-1"
) -> RpcRequest:
    """Build a minimal RpcRequest for dispatch tests."""
    return RpcRequest(request_id=rid, method=method, params=params or {})


def test_register_and_list_methods() -> None:
    server = RpcServer()

    async def h1(params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def h2(params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def h3(params: dict[str, Any]) -> dict[str, Any]:
        return {}

    server.register("zeta.method", h1)
    server.register("alpha.method", h2)
    server.register("mu.method", h3)

    assert server.list_methods() == ["alpha.method", "mu.method", "zeta.method"]


def test_register_duplicate_raises() -> None:
    server = RpcServer()

    async def handler(params: dict[str, Any]) -> None:
        return None

    server.register("dup", handler)
    with pytest.raises(ValueError, match="already registered"):
        server.register("dup", handler)


def test_unregister_removes() -> None:
    server = RpcServer()

    async def handler(params: dict[str, Any]) -> None:
        return None

    server.register("temp", handler)
    assert "temp" in server.list_methods()
    server.unregister("temp")
    assert "temp" not in server.list_methods()


def test_unregister_unknown_is_noop() -> None:
    server = RpcServer()
    # Should not raise
    server.unregister("never.registered")
    assert server.list_methods() == []


@pytest.mark.asyncio
async def test_handle_request_success_async_handler() -> None:
    server = RpcServer()

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    server.register("state.ping", handler)

    response = await server.handle_request(_req("state.ping"))

    assert response.is_success is True
    assert response.error is None
    assert response.error_kind is None
    assert response.result == {"ok": True}


@pytest.mark.asyncio
async def test_handle_request_success_sync_handler() -> None:
    server = RpcServer()

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        return {"sync": True, "count": 42}

    server.register("state.sync", handler)

    response = await server.handle_request(_req("state.sync"))

    assert response.is_success is True
    assert response.result == {"sync": True, "count": 42}


@pytest.mark.asyncio
async def test_handle_request_method_not_found() -> None:
    server = RpcServer()

    response = await server.handle_request(_req("does.not.exist"))

    assert response.is_success is False
    assert response.error_kind == "not_found"
    assert response.error is not None
    assert "does.not.exist" in response.error
    assert response.result is None


@pytest.mark.asyncio
async def test_handle_request_handler_raises() -> None:
    server = RpcServer()

    async def bad_handler(params: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    server.register("state.explodes", bad_handler)

    response = await server.handle_request(_req("state.explodes"))

    assert response.is_success is False
    assert response.error_kind == "handler"
    assert response.error is not None
    assert "boom" in response.error


@pytest.mark.asyncio
async def test_handle_request_non_serializable_result() -> None:
    server = RpcServer()

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        # Self-referential dict cannot be JSON-serialized even with default=str.
        d: dict[str, Any] = {}
        d["self"] = d
        return d

    server.register("state.cyclic", handler)

    response = await server.handle_request(_req("state.cyclic"))

    assert response.is_success is False
    assert response.error_kind == "serialization"
    assert response.error is not None
    assert "not json-serializable" in response.error


@pytest.mark.asyncio
async def test_handle_wire_round_trip_success() -> None:
    server = RpcServer()

    async def ping(params: dict[str, Any]) -> dict[str, Any]:
        return {"pong": True}

    server.register("ping", ping)

    wire_in = {
        "action": "rpc_request",
        "request_id": "abc",
        "method": "ping",
        "params": {},
    }
    wire_out = await server.handle_wire(wire_in)

    assert wire_out["action"] == "rpc_reply"
    assert wire_out["request_id"] == "abc"
    assert wire_out["result"] == {"pong": True}
    assert wire_out["error"] is None
    assert wire_out["error_kind"] is None


@pytest.mark.asyncio
async def test_handle_wire_round_trip_not_found() -> None:
    server = RpcServer()

    wire_in = {
        "action": "rpc_request",
        "request_id": "zzz",
        "method": "ghost.method",
        "params": {},
    }
    wire_out = await server.handle_wire(wire_in)

    assert wire_out["action"] == "rpc_reply"
    assert wire_out["request_id"] == "zzz"
    assert wire_out["result"] is None
    assert wire_out["error_kind"] == "not_found"
    assert "ghost.method" in wire_out["error"]


@pytest.mark.asyncio
async def test_async_handler_receives_params() -> None:
    server = RpcServer()
    received: dict[str, Any] = {}

    async def echo(params: dict[str, Any]) -> dict[str, Any]:
        received.update(params)
        return params

    server.register("echo", echo)

    payload = {"session": "s1", "count": 3, "tags": ["a", "b"]}
    response = await server.handle_request(_req("echo", params=payload))

    assert response.is_success is True
    assert response.result == payload
    assert received == payload


@pytest.mark.asyncio
async def test_preserves_request_id() -> None:
    server = RpcServer()

    async def ok(params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": 1}

    async def bad(params: dict[str, Any]) -> None:
        raise ValueError("nope")

    server.register("ok", ok)
    server.register("bad", bad)

    success = await server.handle_request(_req("ok", rid="xyz"))
    assert success.request_id == "xyz"

    handler_err = await server.handle_request(_req("bad", rid="xyz"))
    assert handler_err.request_id == "xyz"

    missing = await server.handle_request(_req("missing", rid="xyz"))
    assert missing.request_id == "xyz"
