"""Private WebUI and engine proxy trust-boundary regressions."""

from __future__ import annotations

import ipaddress
import json
from types import SimpleNamespace

import httpx
import kollabor_webui.network_security as network_security
import kollabor_webui.server as webui_server
import pytest
from fastapi import FastAPI
from kollabor_webui import build_uvicorn_config
from kollabor_webui.server import create_app
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

TEST_BIND_HOSTS = ["127.0.0.1", "10.8.0.1"]
TEST_CLIENT_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.8.0.0/24"),
)
TEST_ORIGIN = "http://10.8.0.1:8080"


def make_app(monkeypatch, engine_url="http://127.0.0.1:7433", transport=None):
    monkeypatch.setattr(
        webui_server,
        "interface_networks_for_hosts",
        lambda _hosts: TEST_CLIENT_NETWORKS,
    )
    return create_app(engine_url, transport=transport, bind_hosts=TEST_BIND_HOSTS)


async def call(app, client_ip, method, url, **kwargs):
    transport = httpx.ASGITransport(app=app, client=(client_ip, 50000))
    async with httpx.AsyncClient(
        transport=transport, base_url=TEST_ORIGIN
    ) as client:
        return await client.request(method, url, **kwargs)


def test_uvicorn_client_address_is_not_rewritten_by_proxy_headers():
    app = FastAPI()
    config = build_uvicorn_config(app)
    assert config.proxy_headers is False


@pytest.mark.asyncio
async def test_a_remote_private_page_gets_proxy_and_localhost_gets_engine(monkeypatch):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "t0ken")
    engine = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"version": "0.17.0"})
    )
    app = make_app(monkeypatch, "http://127.0.0.1:7433", engine)

    remote_response = await call(
        app, "10.8.0.2", "GET", "/api/config",
        headers={"Origin": TEST_ORIGIN},
    )
    local_response = await call(
        app, "127.0.0.1", "GET", "/api/config",
        headers={"Origin": TEST_ORIGIN},
    )
    assert remote_response.status_code == 200
    assert remote_response.headers["cache-control"] == "no-store"
    assert local_response.status_code == 200
    remote = remote_response.json()
    local = local_response.json()
    assert remote["engine_url"] == f"{TEST_ORIGIN}/engine"
    assert remote["token"] == "t0ken"
    assert set(remote) == {
        "engine_url", "token", "server_version", "engine_version", "kollab_version"
    }
    assert local["engine_url"] == "http://127.0.0.1:7433"


@pytest.mark.asyncio
async def test_public_client_cannot_bootstrap_token_with_forwarded_ip_spoof(monkeypatch):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "secret-token")
    app = make_app(monkeypatch)
    response = await call(
        app,
        "203.0.113.9",
        "GET",
        "/api/config",
        headers={
            "Origin": TEST_ORIGIN,
            "X-Forwarded-For": "10.8.0.2",
        },
    )
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert "secret-token" not in response.text


@pytest.mark.asyncio
async def test_public_direct_port_forward_cannot_bootstrap_token(monkeypatch):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "secret-token")
    app = make_app(monkeypatch)
    response = await call(
        app, "203.0.113.9", "GET", "/api/config",
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "secret-token" not in response.text


@pytest.mark.asyncio
async def test_private_client_cannot_rebind_host_or_forge_origin(monkeypatch):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "secret-token")
    app = make_app(monkeypatch)
    bad_host = await call(
        app, "10.8.0.2", "GET", "/api/config",
        headers={"Host": "attacker.example", "Origin": "http://attacker.example"},
    )
    bad_origin = await call(
        app, "10.8.0.2", "GET", "/api/config",
        headers={"Origin": "http://attacker.example"},
    )
    assert bad_host.status_code == 400
    assert bad_origin.status_code == 403
    assert bad_host.headers["cache-control"] == "no-store"
    assert bad_origin.headers["cache-control"] == "no-store"
    assert "secret-token" not in bad_host.text + bad_origin.text


@pytest.mark.asyncio
async def test_proxy_headers_middleware_cannot_make_forwarded_client_trusted(monkeypatch):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "secret-token")
    app = make_app(monkeypatch)
    # Simulate Uvicorn's middleware chain and a loopback reverse proxy that sends
    # a forged private XFF value. Production explicitly disables this middleware.
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    response = await call(
        wrapped,
        "127.0.0.1",
        "GET",
        "/api/config",
        headers={
            "Origin": TEST_ORIGIN,
            "X-Forwarded-For": "10.8.0.2",
        },
    )
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert "secret-token" not in response.text


@pytest.mark.asyncio
async def test_bootstrap_errors_are_no_store_and_redacted(monkeypatch):
    def fail_read_token():
        raise RuntimeError("not-a-real-token-diagnostic")

    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", fail_read_token)
    app = make_app(monkeypatch, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"version": "0.17.0"})
    ))
    response = await call(
        app, "10.8.0.2", "GET", "/api/config",
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert "not-a-real-token-diagnostic" not in response.text


def test_bind_hosts_are_explicit_local_addresses_only():
    assert network_security.validate_bind_hosts(
        ["127.0.0.1", "10.8.0.1", "172.20.0.1", "192.168.1.1", "fd12::1", "fe80::1"]
    )
    for address in ("0.0.0.0", "::", "203.0.113.9", "8.8.8.8", "vpn.example.net"):
        with pytest.raises(ValueError, match="KOLLAB_WEBUI_HOSTS"):
            network_security.validate_bind_hosts([address])


def test_interface_cidr_and_active_phone_peer_addresses_are_exact(monkeypatch):
    fake_entry = SimpleNamespace(address="192.168.1.20", netmask="255.255.255.0")
    monkeypatch.setattr(network_security.psutil, "net_if_addrs", lambda: {"en0": [fake_entry]})
    networks = network_security.interface_networks_for_hosts(["192.168.1.20"])
    assert network_security.client_in_networks("192.168.1.40", networks)
    assert not network_security.client_in_networks("192.168.2.40", networks)
    assert not network_security.client_in_networks("10.0.0.4", networks)
    phone_peers = network_security.networks_for_phone_peers(
        [{"address": "10.77.0.2"}]
    )
    allowed = (*networks, *phone_peers)
    assert network_security.client_in_networks("10.77.0.2", allowed)
    assert not network_security.client_in_networks("10.77.0.3", allowed)
    assert not network_security.client_in_networks("10.78.0.2", allowed)


@pytest.mark.asyncio
async def test_proxy_strips_forged_client_ip_header_and_streams_engine_reply(monkeypatch):
    seen = {}

    def engine(request: httpx.Request) -> httpx.Response:
        seen.update(
            method=request.method,
            url=str(request.url),
            auth=request.headers.get("authorization"),
            client_ip=request.headers.get("x-kollab-client-ip"),
            body=json.loads(request.content),
        )
        async def events():
            yield b"data: hi\n\n"

        return httpx.Response(201, headers={"content-type": "text/event-stream"}, content=events())

    app = make_app(monkeypatch, "http://127.0.0.1:7433", httpx.MockTransport(engine))
    response = await call(
        app,
        "10.8.0.2",
        "POST",
        "/engine/sessions/s1/assistant?x=1",
        headers={
            "Authorization": "Bearer t0ken",
            "X-Kollab-Client-IP": "203.0.113.9",
            "Origin": TEST_ORIGIN,
        },
        json={"a": 1},
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.text == "data: hi\n\n"
    assert seen == {
        "method": "POST",
        "url": "http://127.0.0.1:7433/sessions/s1/assistant?x=1",
        "auth": "Bearer t0ken",
        "client_ip": None,
        "body": {"a": 1},
    }


@pytest.mark.asyncio
async def test_uvicorn_proxy_headers_are_disabled_and_forwarded_chains_fail_closed(
    monkeypatch,
):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "secret-token")
    app = make_app(monkeypatch)
    assert build_uvicorn_config(app).proxy_headers is False

    # Simulate a loopback reverse proxy and Uvicorn's actual proxy middleware.
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    response = await call(
        wrapped,
        "127.0.0.1",
        "GET",
        "/api/config",
        headers={"Origin": TEST_ORIGIN, "X-Forwarded-For": "10.8.0.2"},
    )
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert "secret-token" not in response.text



@pytest.mark.asyncio
async def test_unreachable_engine_diagnostic_is_redacted(monkeypatch):
    def engine(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("contains-secret", request=request)

    app = make_app(monkeypatch, transport=httpx.MockTransport(engine))
    response = await call(
        app, "10.8.0.2", "GET", "/engine/health", headers={"Origin": TEST_ORIGIN}
    )
    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
    assert "contains-secret" not in response.text


@pytest.mark.asyncio
async def test_private_but_unconfigured_subnet_cannot_bootstrap_token(monkeypatch):
    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "secret-token")
    app = make_app(monkeypatch)
    response = await call(
        app,
        "10.9.0.7",
        "GET",
        "/api/config",
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "secret-token" not in response.text


@pytest.mark.asyncio
async def test_active_wireguard_phone_network_can_bootstrap_then_revocation_removes_it(
    monkeypatch, tmp_path
):
    import base64

    from kollabor_engine.phone_access import PhoneAccess, set_phone_access

    monkeypatch.setattr("kollabor_engine.auth.read_token_from_disk", lambda: "test-token")
    access = PhoneAccess(
        tmp_path / "phone-access",
        runner=lambda _command, _peers_file: None,
        apply_command="/trusted/fake-apply",
    )
    set_phone_access(access)
    try:
        access.update_settings(
            {
                "endpoint": "vpn.example.net:51820",
                "server_public_key": base64.b64encode(bytes(32)).decode("ascii"),
                "subnet": "10.77.0.0/24",
            }
        )
        peer = access.create_peer("test-phone")["peer"]
        app = make_app(
            monkeypatch,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"version": "0.17.0"})
            ),
        )

        active = await call(
            app,
            peer["address"],
            "GET",
            "/api/config",
            headers={"Origin": TEST_ORIGIN},
        )
        unrelated_private = await call(
            app,
            "10.77.1.2",
            "GET",
            "/api/config",
            headers={"Origin": TEST_ORIGIN},
        )

        untracked_phone = await call(
            app,
            "10.77.0.3",
            "GET",
            "/api/config",
            headers={"Origin": TEST_ORIGIN},
        )
        assert active.status_code == 200
        assert active.headers["cache-control"] == "no-store"
        assert unrelated_private.status_code == 403

        assert untracked_phone.status_code == 403
        assert "test-token" not in unrelated_private.text

        access.revoke_peer(peer["id"])
        revoked = await call(
            app,
            peer["address"],
            "GET",
            "/api/config",
            headers={"Origin": TEST_ORIGIN},
        )
        assert revoked.status_code == 403
    finally:
        set_phone_access(None)


def test_webui_bind_environment_rejects_public_and_wildcard_addresses(monkeypatch):
    for value in ("0.0.0.0", "::", "203.0.113.8", "8.8.8.8"):
        monkeypatch.setenv("KOLLAB_WEBUI_HOSTS", value)
        with pytest.raises(ValueError, match="KOLLAB_WEBUI_HOSTS"):
            network_security.configured_bind_hosts()
