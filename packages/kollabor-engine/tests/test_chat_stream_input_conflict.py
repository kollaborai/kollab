"""A reused client input ID with different content is a 409 (#1451)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from kollabor_engine import server
from kollabor_engine.routes import stream


class _Rpc:
    async def call(self, method, params, *, timeout=None):
        assert method == "state.chat_stream.add_input"
        assert params["id"] == "web-client-1"
        return {"error": "input id is already bound to different content"}


def test_input_route_returns_conflict_when_client_id_has_different_content(monkeypatch):
    session = SimpleNamespace(daemon=SimpleNamespace(rpc=_Rpc()))
    registry = {"s1": session}
    monkeypatch.setattr(server, "get_session_registry", lambda: registry)
    monkeypatch.setattr(stream, "get_session_registry", lambda: registry)
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    client = TestClient(server.create_app())

    response = client.post(
        "/sessions/s1/inputs",
        json={"id": "web-client-1", "text": "different text", "attachments": []},
    )

    assert response.status_code == 409
