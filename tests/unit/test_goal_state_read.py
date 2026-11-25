"""Authenticated engine-to-daemon reads of persisted conversation goal state."""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from kollabor.goals.service import GoalService
from kollabor.state.goal_store import GoalStore
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor.state.remote import RemoteStateService
from kollabor_rpc import RpcRequest, RpcServer


class RpcTransport:
    def __init__(self, server):
        self.server = server

    async def call(self, method, params, timeout=None):
        response = await self.server.handle_request(
            RpcRequest(request_id="goal-read", method=method, params=params)
        )
        if response.error:
            raise RuntimeError(str(response.error))
        return response.result


@pytest.mark.asyncio
async def test_engine_reads_only_current_conversation_persisted_goal(tmp_path, monkeypatch):
    from kollabor_engine import server
    from kollabor_engine.routes import sessions

    store = GoalStore(tmp_path / "goals.db")
    goal = store.create("conv-a", "secret objective", "/secret/path")
    attempt = store.insert_attempt(goal.goal_id, 1, 1, 1)
    store.bind_attempt_turn(attempt.attempt_id, 1, "turn-full")
    store.create("conv-b", "other secret", "/other/path")
    current = SimpleNamespace(uid="conv-a")
    llm = SimpleNamespace(conversation_manager=SimpleNamespace(conversation_uid=lambda: current.uid))
    service = GoalService(store, "daemon")
    bus = SimpleNamespace(get_service=lambda name: service if name == "goal_service" else None)
    local = LocalStateService(llm, profile_manager=object(), event_bus=bus)
    rpc = RpcServer()
    register_state_handlers(rpc, local)
    remote = RemoteStateService(RpcTransport(rpc))
    session = SimpleNamespace(state=remote)
    monkeypatch.setattr(sessions, "get_session_registry", lambda: {"session-a": session})
    monkeypatch.setattr(server, "validate_token", lambda token: token == "valid")
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    app = server.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://engine") as client:
        path = "/sessions/session-a/goal"
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers={"Authorization": "Bearer wrong"})).status_code == 401
        response = await client.get(path, headers={"Authorization": "Bearer valid"})
        assert response.status_code == 200
        assert response.json() == {
            "conversation_uid": "conv-a",
            "goal": {"goal_id": goal.goal_id, "status": "active", "attempt": {
                "attempt_id": attempt.attempt_id, "state": "dispatched", "turn_id": "turn-full"
            }},
        }
        current.uid = "conv-empty"
        response = await client.get(path, headers={"Authorization": "Bearer valid"})
        assert response.json() == {"conversation_uid": "conv-empty", "goal": None}
        current.uid = "conv-b"
        response = await client.get(path, headers={"Authorization": "Bearer valid"})
        assert response.json()["goal"]["goal_id"] != goal.goal_id
        assert response.json()["goal"]["attempt"] is None
        assert "secret" not in response.text and "/other/path" not in response.text
        assert (await client.get("/sessions/missing/goal", headers={"Authorization": "Bearer valid"})).status_code == 404
    store.close()
