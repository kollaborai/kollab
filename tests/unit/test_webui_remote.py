"""The web UI opened from another machine (KOLLAB_WEBUI_HOSTS, a WireGuard
address say): the page cannot reach the engine's 127.0.0.1, so /api/config
hands it this server's /engine proxy, which forwards and streams the engine."""

from __future__ import annotations

import json

import httpx
import pytest
from kollabor_webui import _listen
from kollabor_webui.server import create_app


async def call(app, client_ip, method, url, **kwargs):
    transport = httpx.ASGITransport(app=app, client=(client_ip, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://10.8.0.1:8080") as client:
        return await client.request(method, url, **kwargs)


@pytest.mark.asyncio
async def test_a_remote_page_gets_the_proxy_and_a_local_one_the_engine(monkeypatch):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "t0ken")
    app = create_app("http://127.0.0.1:7433")

    remote = (await call(app, "10.8.0.2", "GET", "/api/config")).json()
    local = (await call(app, "127.0.0.1", "GET", "/api/config")).json()

    assert remote == {"engine_url": "http://10.8.0.1:8080/engine", "token": "t0ken"}
    assert local["engine_url"] == "http://127.0.0.1:7433"


@pytest.mark.asyncio
async def test_the_proxy_forwards_the_request_and_streams_the_reply():
    seen = {}

    def engine(request: httpx.Request) -> httpx.Response:
        seen.update(
            method=request.method,
            url=str(request.url),
            auth=request.headers.get("authorization"),
            body=json.loads(request.content),
        )
        async def events():
            yield b"data: hi\n\n"

        return httpx.Response(201, headers={"content-type": "text/event-stream"}, content=events())

    app = create_app("http://127.0.0.1:7433", transport=httpx.MockTransport(engine))
    response = await call(
        app,
        "10.8.0.2",
        "POST",
        "/engine/sessions/s1/assistant?x=1",
        headers={"Authorization": "Bearer t0ken"},
        json={"a": 1},
    )

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == "data: hi\n\n"
    assert seen == {
        "method": "POST",
        "url": "http://127.0.0.1:7433/sessions/s1/assistant?x=1",
        "auth": "Bearer t0ken",
        "body": {"a": 1},
    }


@pytest.mark.asyncio
async def test_an_unreachable_engine_is_a_502():
    def engine(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    app = create_app(transport=httpx.MockTransport(engine))

    assert (await call(app, "10.8.0.2", "GET", "/engine/health")).status_code == 502


def test_an_extra_address_that_cannot_bind_is_skipped():
    # 203.0.113.0/24 is TEST-NET-3: never an address of this machine.
    sockets = _listen(["127.0.0.1", "203.0.113.7"], 0)
    try:
        assert [sock.getsockname()[0] for sock in sockets] == ["127.0.0.1"]
    finally:
        for sock in sockets:
            sock.close()
