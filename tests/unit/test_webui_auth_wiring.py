"""Regression tests for the web UI being unable to authenticate to the engine.

Two bugs made the browser UI look broken on a fresh load:

1. The engine's auth middleware was registered before CORSMiddleware, so it was
   the outer layer. A short-circuited 401 never reached CORS, and the browser
   reported an opaque "blocked by CORS policy" instead of the real 401.
2. kollabor-webui never handed the engine bearer token to the page, so every
   request went out unauthenticated and the user had to paste the token by hand.
"""

from __future__ import annotations

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient


class TestEngine401CarriesCorsHeaders:
    """CORSMiddleware must wrap the auth middleware, not the other way round."""

    def test_unauthenticated_request_still_gets_cors_headers(self, monkeypatch):
        monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)

        from kollabor_engine.server import create_app

        client = TestClient(create_app())
        resp = client.get("/sessions", headers={"Origin": "http://127.0.0.1:8080"})

        assert resp.status_code == 401
        assert resp.headers.get("access-control-allow-origin") == "*"


class TestWebUiConfigShipsToken:
    """/api/config is how the page learns the engine URL and bearer token."""

    def test_config_includes_engine_url_and_token(self, monkeypatch):
        from kollabor_webui.server import create_app

        monkeypatch.setattr(
            "kollabor_engine.auth.read_token_from_disk", lambda: "test-token-123"
        )

        client = TestClient(create_app("http://127.0.0.1:9999"))
        body = client.get("/api/config").json()

        assert body["engine_url"] == "http://127.0.0.1:9999"
        assert body["token"] == "test-token-123"
