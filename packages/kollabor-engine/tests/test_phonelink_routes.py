"""PhoneLink engine routes remain local-only after legacy phone routes are removed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kollabor_engine.routes.phonelink import router


def make_app():
    app = FastAPI()
    app.state.phone_link = SimpleNamespace(
        status=AsyncMock(return_value={"state": "ready"}),
        event_history=AsyncMock(return_value={"events": [{"event": "approved"}]}),
    )
    app.include_router(router)
    return app


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_loopback_webui_can_read_phonelink_state(host):
    with TestClient(make_app(), client=(host, 12345)) as client:
        response = client.get("/phonelink")

    assert response.status_code == 200
    assert response.json() == {"state": "ready"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("host", ["192.0.2.10", "10.77.0.2", "::ffff:192.0.2.10"])
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


def test_loopback_can_read_bounded_events_without_cache():
    app = make_app()
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get("/phonelink/events")

    assert response.status_code == 200
    assert response.json() == {"events": [{"event": "approved"}]}
    assert response.headers["cache-control"] == "no-store"
    app.state.phone_link.event_history.assert_awaited_once_with()


def test_non_loopback_cannot_read_phone_events():
    app = make_app()
    with TestClient(app, client=("192.0.2.8", 12345)) as client:
        response = client.get("/phonelink/events")

    assert response.status_code == 403
    app.state.phone_link.event_history.assert_not_awaited()


def test_phone_action_passes_safe_actor_inputs_to_manager():
    app = make_app()
    headers = {
        "X-Kollab-Client": "webui",
        "X-Kollab-Web-Session": "private-session-marker",
        "X-Kollab-Confirmation-Shown": "true",
        "User-Agent": "Chrome/125.0.0.0",
    }
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post("/phonelink/refresh", headers=headers)

    assert response.status_code == 200
    actor = app.state.phone_link.status.call_args.kwargs["actor"]
    assert actor["route"] == "/phonelink/refresh"
    assert actor["method"] == "POST"
    assert actor["client_kind"] == "webui"
    assert actor["remote_addr"] == "127.0.0.1"
    assert actor["user_agent"] == "Chrome/125.0.0.0"
    assert actor["web_session"] == "private-session-marker"
    assert actor["confirmation_shown"] is True
