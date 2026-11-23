"""Permission answers are bound to one prompt instance, even if tool IDs repeat."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from kollabor_engine.routes import permissions
from kollabor_engine.server import get_session_registry
from kollabor_engine.session import EngineSession


@pytest.mark.asyncio
async def test_resolution_rpc_carries_owner_and_does_not_remove_a_reused_tool_id():
    session = EngineSession(session_id="owned", profile=None)
    session._pending_permissions[("reused", "owner-A")] = {
        "tool_id": "reused", "permission_id": "owner-A",
    }
    session._pending_permissions[("reused", "owner-B")] = {
        "tool_id": "reused", "permission_id": "owner-B",
    }

    async def respond(method, params):
        assert method == "permission.respond_owned"
        assert params == {
            "tool_id": "reused", "permission_id": "owner-A", "control": "DENY",
        }
        return {"ok": True}

    rpc = SimpleNamespace(call=AsyncMock(side_effect=respond))
    session.daemon = SimpleNamespace(rpc=rpc)

    assert await session.resolve_permission(
        "reused", "deny", "once", permission_id="owner-A"
    )
    assert set(session._pending_permissions) == {("reused", "owner-B")}
    rpc.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_permission_route_rejects_ownerless_and_stale_answers(monkeypatch):
    session = EngineSession(session_id="owned", profile=None)
    session._pending_permissions[("reused", "owner-B")] = {
        "tool_id": "reused", "permission_id": "owner-B",
    }
    session.resolve_permission = AsyncMock(return_value=False)
    registry = {session.session_id: session}
    monkeypatch.setattr(permissions, "get_session_registry", lambda: registry)
    app = FastAPI()
    app.include_router(permissions.router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://owned") as client:
        ownerless = await client.post(
            "/sessions/owned/permission",
            json={"tool_id": "reused", "decision": "deny", "scope": "once"},
        )
        stale = await client.post(
            "/sessions/owned/permission",
            json={
                "tool_id": "reused", "permission_id": "owner-A",
                "decision": "deny", "scope": "once",
            },
        )

    assert ownerless.status_code == 422
    assert stale.status_code == 409
    session.resolve_permission.assert_awaited_once_with(
        tool_id="reused", decision="deny", scope="once", option_id=None, permission_id="owner-A"
    )


@pytest.mark.asyncio
async def test_pending_prompts_with_reused_tool_id_are_resolved_by_exact_owner():
    events: asyncio.Queue = asyncio.Queue()
    session = EngineSession(session_id="owned-events", profile=None)
    session.daemon = SimpleNamespace(
        alive=True, pid=0, identity="", agent_name="",
        subscribe=lambda: events, unsubscribe=lambda queue: None,
    )
    registry = get_session_registry()
    registry[session.session_id] = session
    tracker = asyncio.create_task(session._track_events())
    try:
        events.put_nowait({
            "type": "permission_request", "tool_id": "reused", "permission_id": "owner-A",
            "details": {"tool_id": "reused", "permission_id": "owner-A"},
        })
        events.put_nowait({
            "type": "permission_request", "tool_id": "reused", "permission_id": "owner-B",
            "details": {"tool_id": "reused", "permission_id": "owner-B"},
        })
        for _ in range(50):
            answer_before = await permissions.get_permissions(session.session_id)
            if len(answer_before["pending_prompts"]) == 2:
                break
            await asyncio.sleep(0.01)
        assert answer_before["pending"] == ["reused"]
        assert {prompt["permission_id"] for prompt in answer_before["pending_prompts"]} == {"owner-A", "owner-B"}

        events.put_nowait({"type": "permission_parked", "tool_id": "reused", "permission_id": "owner-A"})
        events.put_nowait({"type": "permission_denied", "tool_id": "reused", "permission_id": "owner-A"})
        for _ in range(50):
            if events.empty():
                await asyncio.sleep(0.01)
                if events.empty():
                    break
            else:
                await asyncio.sleep(0.01)

        answer = await permissions.get_permissions(session.session_id)
        assert answer["pending"] == ["reused"]
        assert answer["pending_prompts"] == [{
            "type": "permission_request", "tool_id": "reused", "permission_id": "owner-B",
            "details": {"tool_id": "reused", "permission_id": "owner-B"},
        }]
    finally:
        events.put_nowait({"type": "daemon_closed"})
        await asyncio.wait_for(tracker, 1)
        registry.pop(session.session_id, None)
