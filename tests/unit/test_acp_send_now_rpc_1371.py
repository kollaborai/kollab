"""#1371: carry Send Now and injected-turn ownership across state RPC."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService


class _Rpc:
    def __init__(self):
        self.handlers = {}

    def register(self, name, handler):
        self.handlers[name] = handler


@pytest.mark.asyncio
async def test_state_rpc_preserves_send_now_and_joined_turn_receipt():
    state = SimpleNamespace(
        send_message=AsyncMock(return_value={
            "accepted": True,
            "request_id": "client-B",
            "joined_request_id": "active-A",
            "joined_turn_id": 12,
            "joined_generation": 7,
        }),
    )
    rpc = _Rpc()
    register_state_handlers(rpc, state)

    capability = rpc.handlers["state.send_now_capabilities"]({})
    result = await rpc.handlers["state.send_message"]({
        "message": "message B",
        "request_id": "client-B",
        "send_now": True,
    })

    assert capability == {"send_now": True}
    assert result["joined_request_id"] == "active-A"
    state.send_message.assert_awaited_once_with(
        "message B",
        request_id="client-B",
        thread_id=None,
        origin_context=None,
        send_now=True,
    )


@pytest.mark.asyncio
async def test_local_state_preserves_join_metadata_and_send_now_flag():
    llm = SimpleNamespace(submit_human_input=AsyncMock(return_value={
        "status": "injected",
        "request_id": "client-B",
        "joined_request_id": "active-A",
        "joined_turn_id": 12,
        "joined_generation": 7,
    }))
    state = object.__new__(LocalStateService)
    state._llm_service = llm
    state._event_bus = None

    result = await state.send_message(
        "message B", request_id="client-B", send_now=True
    )

    assert result == {
        "accepted": True,
        "reason": "",
        "request_id": "client-B",
        "joined_request_id": "active-A",
        "joined_turn_id": 12,
        "joined_generation": 7,
    }
    assert llm.submit_human_input.await_args.kwargs["send_now"] is True


@pytest.mark.asyncio
async def test_engine_session_checks_send_now_capability_before_forwarding():
    from kollabor_engine.session import EngineSession

    state = SimpleNamespace(send_message=AsyncMock(return_value={
        "accepted": True,
        "request_id": "client-B",
        "joined_request_id": "active-A",
        "joined_turn_id": 12,
        "joined_generation": 7,
    }))
    rpc = SimpleNamespace(call=AsyncMock(return_value={"send_now": True}))
    session = EngineSession.__new__(EngineSession)
    session.daemon = SimpleNamespace(rpc=rpc, state=state)

    result = await session.send_message(
        "message B", request_id="client-B", send_now=True
    )

    assert result["joined_request_id"] == "active-A"
    rpc.call.assert_awaited_once_with("state.send_now_capabilities", {})
    state.send_message.assert_awaited_once_with(
        "message B",
        request_id="client-B",
        thread_id=None,
        origin_context=None,
        send_now=True,
    )


@pytest.mark.asyncio
async def test_engine_session_rejects_send_now_when_daemon_lacks_capability():
    from kollabor_engine.session import EngineSession

    state = SimpleNamespace(send_message=AsyncMock())
    rpc = SimpleNamespace(call=AsyncMock(side_effect=RuntimeError("not found")))
    session = EngineSession.__new__(EngineSession)
    session.daemon = SimpleNamespace(rpc=rpc, state=state)

    result = await session.send_message("message B", send_now=True)

    assert result["accepted"] is False
    assert "does not support Send Now" in result["reason"]
    state.send_message.assert_not_awaited()
