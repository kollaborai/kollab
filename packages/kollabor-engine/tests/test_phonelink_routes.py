"""PhoneLink engine routes remain local-only after legacy phone routes are removed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kollabor_engine.routes.phonelink import router


def make_app():
    app = FastAPI()
    app.state.phone_link = SimpleNamespace(status=AsyncMock(return_value={"state": "ready"}))
    app.include_router(router)
    return app


def test_loopback_webui_can_read_phonelink_state():
    with TestClient(make_app(), client=("127.0.0.1", 12345)) as client:
        response = client.get("/phonelink")

    assert response.status_code == 200
    assert response.json() == {"state": "ready"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("host", ["192.0.2.10", "10.77.0.2"])
def test_non_loopback_cannot_manage_phonelink(host):
    with TestClient(make_app(), client=(host, 12345)) as client:
        response = client.get("/phonelink")

    assert response.status_code == 403
    assert response.json() == {"detail": "PhoneLink management must use the local WebUI proxy"}


def test_engine_registers_phonelink_without_legacy_phone_routes():
    from kollabor_engine.server import create_app

    paths = create_app().openapi()["paths"]

    assert "/phonelink" in paths
    assert not any(path == "/phone" or path.startswith("/phone/") for path in paths)
