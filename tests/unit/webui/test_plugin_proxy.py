"""Remote web clients cannot perform local-only plugin management."""

import ipaddress

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from kollabor_webui import server


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/plugins/install"),
        ("PUT", "/plugins/hello/settings"),
        ("POST", "/plugins/hello/services/worker/start"),
        ("POST", "/plugins/hello/disable"),
    ],
)
def test_remote_web_management_is_denied_before_engine(tmp_path, monkeypatch, method, path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setattr(server, "interface_networks_for_hosts", lambda hosts: [ipaddress.ip_network("10.0.0.0/8")])
    upstream = FastAPI()

    @upstream.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def handler(request: Request, path: str):
        return {"upstream": path}

    app = server.create_app(transport=httpx.ASGITransport(app=upstream), bind_hosts=["127.0.0.1"])
    with TestClient(app, base_url="http://localhost", client=("10.0.0.5", 1234)) as client:
        response = client.request(method, f"/engine{path}")
    assert response.status_code == 403
