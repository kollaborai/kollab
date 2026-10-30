"""PhoneLink engine routes remain local-only after legacy phone routes are removed."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import kollabor_engine.routes.phonelink as routes
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kollabor_engine.phone_link import PhoneLinkHeldElsewhere, held_elsewhere_message
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


OWNER = {"pid": 4242, "port": 7433, "started": 0.0, "cwd": ""}


def held_app(**methods):
    app = FastAPI()
    app.state.phone_link = SimpleNamespace(**methods)
    app.include_router(router)
    return app


def held(owner=OWNER):
    return AsyncMock(side_effect=PhoneLinkHeldElsewhere(owner))


def route_to(monkeypatch, handler):
    """Answer the forwarded request with ``handler``; returns the list of requests it saw."""
    seen = []

    def answer(request):
        seen.append(request)
        return handler(request)

    real = httpx.AsyncClient
    transport = httpx.MockTransport(answer)
    monkeypatch.setattr(
        routes.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw)
    )
    monkeypatch.setattr(routes, "read_token_from_disk", lambda: "tok")
    return seen


def test_a_non_holder_forwards_to_the_holder(monkeypatch):
    seen = route_to(
        monkeypatch,
        lambda r: httpx.Response(200, json={"state": "ready", "phones": []}),
    )
    with TestClient(held_app(status=held()), client=("127.0.0.1", 1)) as client:
        response = client.get("/phonelink", headers={"x-kollab-client": "webui"})
    assert response.status_code == 200
    assert response.json() == {"state": "ready", "phones": []}
    assert response.headers["cache-control"] == "no-store"
    (sent,) = seen
    assert str(sent.url) == "http://127.0.0.1:7433/phonelink"
    assert sent.headers["authorization"] == "Bearer tok"
    assert sent.headers["x-kollab-phonelink-forwarded"] == "1"
    assert sent.headers["x-kollab-client"] == "webui"


def test_a_forwarded_post_keeps_its_body(monkeypatch):
    seen = route_to(monkeypatch, lambda r: httpx.Response(200, json={"id": "p1"}))
    with TestClient(held_app(offer=held()), client=("127.0.0.1", 1)) as client:
        response = client.post("/phonelink/phones", json={"name": "Pixel"})
    assert response.status_code == 200
    (sent,) = seen
    assert sent.method == "POST"
    assert str(sent.url) == "http://127.0.0.1:7433/phonelink/phones"
    assert json.loads(sent.content) == {"name": "Pixel"}


def test_the_holders_error_passes_through(monkeypatch):
    route_to(
        monkeypatch,
        lambda r: httpx.Response(
            400, json={"detail": "PhoneLink needs a Kollab connection."}
        ),
    )
    with TestClient(held_app(status=held()), client=("127.0.0.1", 1)) as client:
        response = client.get("/phonelink")
    assert response.status_code == 400
    assert response.json() == {"detail": "PhoneLink needs a Kollab connection."}


def test_an_unreachable_holder_is_named(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    route_to(monkeypatch, refuse)
    with TestClient(held_app(status=held()), client=("127.0.0.1", 1)) as client:
        response = client.get("/phonelink")
    assert response.status_code == 400
    assert "port 7433" in response.json()["detail"]


def test_no_record_keeps_the_plain_error(monkeypatch):
    seen = route_to(monkeypatch, lambda r: httpx.Response(200, json={}))
    with TestClient(held_app(status=held(None)), client=("127.0.0.1", 1)) as client:
        response = client.get("/phonelink")
    assert response.status_code == 400
    assert response.json() == {"detail": held_elsewhere_message(None)}
    assert seen == []


def test_a_forwarded_call_is_never_forwarded_again(monkeypatch):
    seen = route_to(monkeypatch, lambda r: httpx.Response(200, json={}))
    with TestClient(held_app(status=held()), client=("127.0.0.1", 1)) as client:
        response = client.get(
            "/phonelink", headers={"x-kollab-phonelink-forwarded": "1"}
        )
    assert response.status_code == 400
    assert seen == []


def test_a_forward_keeps_the_audit_headers(monkeypatch):
    seen = route_to(monkeypatch, lambda r: httpx.Response(200, json={}))
    audit = {"x-kollab-web-session": "ws-1", "x-kollab-confirmation-shown": "1"}
    with TestClient(held_app(status=held()), client=("127.0.0.1", 1)) as client:
        client.get("/phonelink", headers=audit)
    (sent,) = seen
    assert sent.headers["x-kollab-web-session"] == "ws-1"
    assert sent.headers["x-kollab-confirmation-shown"] == "1"
