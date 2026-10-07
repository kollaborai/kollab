"""An unhandled route error reaches the browser as a JSON 500 with CORS headers."""
from fastapi.testclient import TestClient
from kollabor_engine.server import create_app


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
