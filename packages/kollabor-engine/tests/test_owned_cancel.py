"""Owner-scoped HTTP cancellation reaches the daemon's exact-owner fence."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.client_turns import ClientTurnBroker
from kollabor.state.local import LocalStateService
from kollabor_tui.display_tap import DisplayTap
from kollabor_engine.routes import messages, permissions
from kollabor_engine.session import EngineSession


@pytest.fixture
def owner_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path)
    services = {"display_tap": DisplayTap()}
    bus = SimpleNamespace(get_service=services.get)
    orchestrator = ACPOrchestrator(bus)
    runner = SimpleNamespace(
        _active_context=SimpleNamespace(request_id="first-owner"),
        session_id="sdk-session", generation=7,
        cancel_queued=lambda _request: False, cancel=AsyncMock(),
    )
    orchestrator._runner = runner
    services["acp_orchestrator"] = orchestrator
    llm = SimpleNamespace(is_processing=True, cancel_current_request=Mock())
    state = LocalStateService(llm, None, event_bus=bus)
    session = EngineSession(session_id="owned-engine", profile=SimpleNamespace(name="owned", model=None, provider="acp"))
    flow = SimpleNamespace(advance_at_rpc=False)

    async def scoped_rpc(method, params):
        assert method == "state.acp_cancel"
        if flow.advance_at_rpc:
            runner._active_context = SimpleNamespace(request_id="replacement-owner")
            runner.generation = 8
        return await state.cancel_client_turn(params["request_id"])

    rpc = SimpleNamespace(call=AsyncMock(side_effect=scoped_rpc))
    session.daemon = SimpleNamespace(alive=True, rpc=rpc, state=state)
    session._pending_permissions["first-tool"] = {
        "tool_id": "first-tool", "details": {"request_id": "first-owner"},
    }
    registry = {session.session_id: session}
    monkeypatch.setattr(messages, "get_session_registry", lambda: registry)
    monkeypatch.setattr(permissions, "get_session_registry", lambda: registry)
    app = FastAPI()
    app.include_router(messages.router)
    app.include_router(permissions.router)
    return SimpleNamespace(app=app, session=session, state=state, rpc=rpc, llm=llm,
                           runner=runner, services=services, bus=bus, flow=flow)


@pytest.mark.asyncio
@pytest.mark.parametrize("advance_at_rpc", [False, True])
async def test_stale_permission_owner_cannot_cancel_replacement_even_after_get(owner_bridge, advance_at_rpc):
    flow = owner_bridge
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        pending = (await client.get("/sessions/owned-engine/permissions")).json()
        old_owner = pending["pending_prompts"][0]["details"]["request_id"]
        # Resolution/new admission can happen either before POST, or while
        # the RPC travels to the daemon. The engine's permission cache is old.
        flow.flow.advance_at_rpc = advance_at_rpc
        if not advance_at_rpc:
            flow.runner._active_context = SimpleNamespace(request_id="replacement-owner")
            flow.runner.generation = 8
        response = await client.post("/sessions/owned-engine/cancel-owned", json={"request_id": old_owner})
    assert response.status_code == 200
    assert response.json()["cancelled"] is False
    assert flow.runner._active_context.request_id == "replacement-owner"
    flow.rpc.call.assert_awaited_once_with("state.acp_cancel", {"request_id": "first-owner"})
    flow.runner.cancel.assert_not_awaited()
    flow.llm.cancel_current_request.assert_not_called()


@pytest.mark.asyncio
async def test_current_owner_is_cancelled_with_sdk_session_and_generation_fence(owner_bridge):
    flow = owner_bridge
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel-owned", json={"request_id": "first-owner"})
    assert response.json() == {"ok": True, "session_id": "owned-engine", "request_id": "first-owner", "cancelled": True}
    flow.runner.cancel.assert_awaited_once_with(expected_session_id="sdk-session", expected_generation=7)
    flow.llm.cancel_current_request.assert_not_called()


@pytest.mark.asyncio
async def test_no_body_keeps_existing_native_streaming_cancel_path(owner_bridge):
    flow = owner_bridge
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel")
    await flow.session._active_turn_task
    assert response.json() == {"ok": True, "session_id": "owned-engine"}
    flow.llm.cancel_current_request.assert_called_once_with()
    flow.rpc.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id,expected", [("stale-native-owner", False), ("native-owner", True)])
async def test_native_client_broker_cancels_only_its_admitted_owner(owner_bridge, request_id, expected):
    flow = owner_bridge
    flow.services.pop("acp_orchestrator")
    broker = ClientTurnBroker(flow.llm, flow.bus)
    broker.active = "native-owner"
    broker.active_turn_id = 2
    broker.active_completion = {"request_id": "native-owner", "turn_id": 2, "completed": False, "cancelled": False}
    broker.admitted.add("native-owner")
    flow.services["client_turn_broker"] = broker
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel-owned", json={"request_id": request_id})
    assert response.json()["cancelled"] is expected
    assert flow.llm.cancel_current_request.call_count == int(expected)


@pytest.mark.asyncio
async def test_unsupported_generic_native_owner_is_noop_without_session_fallback(owner_bridge):
    flow = owner_bridge
    flow.services.pop("acp_orchestrator")
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel-owned", json={"request_id": "generic-native-web-owner"})
    assert response.json()["cancelled"] is False
    flow.llm.cancel_current_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"request_id": ""}, {"request_id": 7}])
async def test_invalid_scoped_body_never_falls_back_to_current_turn(owner_bridge, body):
    flow = owner_bridge
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel-owned", json=body)
    assert response.status_code == 422
    flow.rpc.call.assert_not_awaited()
    flow.llm.cancel_current_request.assert_not_called()


@pytest.mark.asyncio
async def test_owner_endpoint_requires_a_body(owner_bridge):
    flow = owner_bridge
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel-owned")
    assert response.status_code == 422
    flow.rpc.call.assert_not_awaited()
    flow.llm.cancel_current_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"error": "scoped RPC unavailable"}, {"cancelled": "yes"}])
async def test_bad_scoped_rpc_result_never_uses_generic_fallback(owner_bridge, result):
    flow = owner_bridge
    flow.rpc.call.side_effect = None
    flow.rpc.call.return_value = result
    async with AsyncClient(transport=ASGITransport(app=flow.app, raise_app_exceptions=False), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel-owned", json={"request_id": "first-owner"})
    assert response.status_code == 500
    flow.rpc.call.assert_awaited_once_with("state.acp_cancel", {"request_id": "first-owner"})
    flow.llm.cancel_current_request.assert_not_called()


@pytest.mark.asyncio
async def test_disconnected_owned_session_is_noop_without_fallback(owner_bridge):
    flow = owner_bridge
    flow.session.daemon.rpc = None
    async with AsyncClient(transport=ASGITransport(app=flow.app), base_url="http://owned") as client:
        response = await client.post("/sessions/owned-engine/cancel-owned", json={"request_id": "first-owner"})
    assert response.json()["cancelled"] is False
    flow.llm.cancel_current_request.assert_not_called()


@pytest.mark.asyncio
async def test_held_cancel_lock_rechecks_generation_before_touching_replacement(owner_bridge, tmp_path):
    flow = owner_bridge
    runner = ACPRunner(ACPProfile("owned", (sys.executable,), cwd=str(tmp_path)))
    runner._active_context = SimpleNamespace(request_id="first-owner")
    runner.session_id, runner.generation = "sdk-session", 7
    runner.permission_bridge.cancel = Mock()
    runner._connection = SimpleNamespace(cancel=AsyncMock())
    orchestrator = flow.services["acp_orchestrator"]
    orchestrator._runner = runner
    flow.runner = runner

    # A second owner cancellation waits behind another cancel. Let the first
    # owner finish and the queue advance before that waiter gets the lock.
    await runner._cancel_lock.acquire()
    task = asyncio.create_task(orchestrator.cancel_request("first-owner"))
    try:
        await asyncio.sleep(0)
        assert not task.done()
        runner._active_context = SimpleNamespace(request_id="replacement-owner")
        runner.generation += 1
    finally:
        runner._cancel_lock.release()
    await task

    assert runner._active_context.request_id == "replacement-owner"
    runner.permission_bridge.cancel.assert_not_called()
    runner._connection.cancel.assert_not_awaited()
    flow.llm.cancel_current_request.assert_not_called()
