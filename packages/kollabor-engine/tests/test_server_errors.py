"""An unhandled route error reaches the browser as a JSON 500 with CORS headers."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from kollabor_engine.server import create_app, get_session_registry


def test_unhandled_error_is_json_500_with_cors_headers(monkeypatch):
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("profile store exploded")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom", headers={"Origin": "http://127.0.0.1:5173"})

    assert response.status_code == 500
    assert response.json() == {"detail": "profile store exploded", "type": "RuntimeError"}
    assert response.headers.get("access-control-allow-origin") == "*"


def test_status_answers_while_agents_are_open():
    """An opened agent has no profile until its daemon answers; /status still answers."""
    registry = get_session_registry()
    registry["a1b2c3"] = SimpleNamespace(profile=None)
    registry["here"] = SimpleNamespace(profile=SimpleNamespace(provider="openai"))
    try:
        response = TestClient(create_app()).get("/status")
    finally:
        registry.pop("a1b2c3")
        registry.pop("here")

    assert response.status_code == 200
    assert response.json()["providers"] == ["openai"]
