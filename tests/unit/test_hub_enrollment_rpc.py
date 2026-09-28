from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.state.handlers import register_state_handlers
from kollabor.state.remote import RemoteStateService
from kollabor_rpc import RpcServer
from kollabor_rpc.models import RpcRequest


@pytest.mark.asyncio
async def test_hub_enrollment_uses_typed_rpc_and_returns_only_receipt():
    code = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"
    state = SimpleNamespace(
        hub_enroll=AsyncMock(
            return_value={"status": "pending", "receipt_id": "0123456789abcdef"}
        )
    )
    server = RpcServer()
    register_state_handlers(server, state)

    reply = await server.handle_request(
        RpcRequest(
            request_id="request-1",
            method="state.hub_enroll",
            params={"domain": "example.test", "code": code},
        )
    )

    assert reply.is_success
    assert reply.result == {"status": "pending", "receipt_id": "0123456789abcdef"}
    state.hub_enroll.assert_awaited_once_with("example.test", code)


@pytest.mark.asyncio
async def test_hub_enrollment_swallows_code_bearing_handler_exceptions(caplog):
    code = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"

    async def fail(domain: str, submitted_code: str):
        raise RuntimeError(f"transport rejected {submitted_code}")

    server = RpcServer()
    register_state_handlers(server, SimpleNamespace(hub_enroll=fail))

    reply = await server.handle_request(
        RpcRequest(
            request_id="request-2",
            method="state.hub_enroll",
            params={"domain": "example.test", "code": code},
        )
    )

    assert reply.is_success
    assert reply.result == {"error": "connect request could not be submitted"}
    assert code not in repr(reply)
    assert code not in caplog.text


@pytest.mark.asyncio
async def test_hub_enrollment_rejects_unknown_fields_before_state_call():
    code = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"
    state = SimpleNamespace(hub_enroll=AsyncMock())
    server = RpcServer()
    register_state_handlers(server, state)

    reply = await server.handle_request(
        RpcRequest(
            request_id="request-3",
            method="state.hub_enroll",
            params={"domain": "example.test", "code": code, "command": "connect"},
        )
    )

    assert reply.is_success
    assert reply.result == {"error": "invalid connect enrollment request"}
    state.hub_enroll.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_state_service_sends_domain_and_code_only_to_typed_method():
    code = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"
    rpc = SimpleNamespace(
        call=AsyncMock(
            return_value={"status": "pending", "receipt_id": "0123456789abcdef"}
        )
    )
    state = RemoteStateService(rpc)

    result = await state.hub_enroll("example.test", code)

    assert result == {"status": "pending", "receipt_id": "0123456789abcdef"}
    rpc.call.assert_awaited_once_with(
        "state.hub_enroll",
        {"domain": "example.test", "code": code},
        timeout=90.0,
    )


@pytest.mark.asyncio
async def test_hub_enrollment_offer_returns_code_only_on_typed_private_method():
    offer_id = "0123456789abcdef0123456789abcdef"
    code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    state = SimpleNamespace(
        hub_enrollment_offer=AsyncMock(
            return_value={
                "status": "offered",
                "offer_id": offer_id,
                "expires_at": "1790530000",
                "code": code,
            }
        )
    )
    server = RpcServer()
    register_state_handlers(server, state)

    reply = await server.handle_request(
        RpcRequest(
            request_id="request-4",
            method="state.hub_enrollment_offer",
            params={"domain": "example.test"},
        )
    )

    assert reply.is_success
    assert reply.result == {
        "status": "offered",
        "offer_id": offer_id,
        "expires_at": "1790530000",
        "code": code,
    }
    state.hub_enrollment_offer.assert_awaited_once_with("example.test")


@pytest.mark.asyncio
async def test_hub_enrollment_offer_never_returns_exception_text_or_logs_code(caplog):
    offer_id = "0123456789abcdef0123456789abcdef"
    code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"

    async def fail(_domain: str):
        raise RuntimeError(f"failed offer {code}")

    server = RpcServer()
    register_state_handlers(server, SimpleNamespace(hub_enrollment_offer=fail))

    reply = await server.handle_request(
        RpcRequest(
            request_id="request-5",
            method="state.hub_enrollment_offer",
            params={"domain": "example.test"},
        )
    )

    assert reply.is_success
    assert reply.result == {"error": "connect offer could not be created"}
    assert code not in repr(reply)
    assert code not in caplog.text


@pytest.mark.asyncio
async def test_hub_enrollment_offer_rejects_extra_fields_and_malformed_code():
    valid_offer_id = "0123456789abcdef0123456789abcdef"
    valid_code = f"K1-{valid_offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    state = SimpleNamespace(
        hub_enrollment_offer=AsyncMock(
            return_value={
                "status": "offered",
                "offer_id": valid_offer_id,
                "expires_at": "1790530000",
                "code": valid_code,
                "unexpected": "field",
            }
        )
    )
    server = RpcServer()
    register_state_handlers(server, state)

    extra_field = await server.handle_request(
        RpcRequest(
            request_id="request-6",
            method="state.hub_enrollment_offer",
            params={"domain": "example.test", "command": "connect"},
        )
    )
    malformed = await server.handle_request(
        RpcRequest(
            request_id="request-7",
            method="state.hub_enrollment_offer",
            params={"domain": "example.test"},
        )
    )

    assert extra_field.result == {"error": "invalid connect offer request"}
    assert malformed.result == {"error": "connect offer could not be created"}
    state.hub_enrollment_offer.assert_awaited_once_with("example.test")


@pytest.mark.asyncio
async def test_remote_state_service_uses_offer_rpc_and_rejects_bad_response():
    offer_id = "0123456789abcdef0123456789abcdef"
    code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    rpc = SimpleNamespace(
        call=AsyncMock(
            return_value={
                "status": "offered",
                "offer_id": offer_id,
                "expires_at": "1790530000",
                "code": code,
            }
        )
    )
    state = RemoteStateService(rpc)

    result = await state.hub_enrollment_offer("example.test")

    assert result == {
        "status": "offered",
        "offer_id": offer_id,
        "expires_at": "1790530000",
        "code": code,
    }
    rpc.call.assert_awaited_once_with(
        "state.hub_enrollment_offer", {"domain": "example.test"}, timeout=90.0
    )

    rpc.call.return_value = {"status": "offered", "code": "not-a-code"}
    with pytest.raises(ValueError, match="daemon connect offer failed"):
        await state.hub_enrollment_offer("example.test")
