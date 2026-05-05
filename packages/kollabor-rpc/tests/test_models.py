"""Tests for kollabor_rpc.models."""

from __future__ import annotations

import string

from kollabor_rpc.models import RpcRequest, RpcResponse, new_request_id

HEX_CHARS = set(string.hexdigits.lower())


def test_new_request_id_returns_hex_string() -> None:
    rid = new_request_id()
    assert isinstance(rid, str)
    assert len(rid) == 32
    assert all(ch in HEX_CHARS for ch in rid)


def test_new_request_id_unique_across_1000_calls() -> None:
    ids = {new_request_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_rpc_request_to_wire_shape() -> None:
    req = RpcRequest(
        request_id="abc",
        method="ping",
        params={"x": 1},
        timeout=5.0,
    )
    wire = req.to_wire()
    assert wire == {
        "action": "rpc_request",
        "request_id": "abc",
        "method": "ping",
        "params": {"x": 1},
        "timeout": 5.0,
    }
    assert set(wire.keys()) == {"action", "request_id", "method", "params", "timeout"}


def test_rpc_request_round_trip() -> None:
    req = RpcRequest(
        request_id="xyz",
        method="state.get_conversation",
        params={"session": "s1", "nested": {"a": [1, 2]}},
        timeout=10.5,
    )
    rebuilt = RpcRequest.from_wire(req.to_wire())
    assert rebuilt == req


def test_rpc_request_from_wire_defaults() -> None:
    req = RpcRequest.from_wire({"request_id": "x", "method": "y"})
    assert req.request_id == "x"
    assert req.method == "y"
    assert req.params == {}
    assert req.timeout == 30.0


def test_rpc_response_to_wire_includes_action() -> None:
    resp = RpcResponse(request_id="x", result={"ok": True})
    wire = resp.to_wire()
    assert wire["action"] == "rpc_reply"
    assert wire["request_id"] == "x"
    assert wire["result"] == {"ok": True}
    assert wire["error"] is None
    assert wire["error_kind"] is None


def test_rpc_response_success_shape() -> None:
    resp = RpcResponse(request_id="x", result={"ok": True})
    assert resp.is_success is True


def test_rpc_response_error_shape() -> None:
    resp = RpcResponse(
        request_id="x",
        error="boom",
        error_kind="handler",
    )
    assert resp.is_success is False
    assert resp.error == "boom"
    assert resp.error_kind == "handler"


def test_rpc_response_round_trip() -> None:
    # Success case with None result preserved
    resp_none = RpcResponse(request_id="a", result=None)
    rebuilt_none = RpcResponse.from_wire(resp_none.to_wire())
    assert rebuilt_none == resp_none
    assert rebuilt_none.result is None

    # Error case with all fields populated
    resp_err = RpcResponse(
        request_id="b",
        result=None,
        error="not found",
        error_kind="not_found",
    )
    rebuilt_err = RpcResponse.from_wire(resp_err.to_wire())
    assert rebuilt_err == resp_err


def test_rpc_response_from_wire_defaults() -> None:
    resp = RpcResponse.from_wire({"request_id": "x"})
    assert resp.request_id == "x"
    assert resp.result is None
    assert resp.error is None
    assert resp.error_kind is None
    assert resp.is_success is True
