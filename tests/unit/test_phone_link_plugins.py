"""Plugin authorization at the PhoneLink-to-engine boundary."""

import json
import shutil
from pathlib import Path

import pytest
from kollabor_engine import phone_link
from kollabor_engine.phone_link import PhoneLink, PhoneLinkError


def start(method, path, headers=None):
    return {
        "op": "start", "id": "a" * 32, "method": method, "path": path,
        "headers": headers or [], "size": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/plugins"),
    ("GET", "/plugins/events"),
    ("GET", "/plugins/hello/settings"),
    ("POST", "/plugins/hello/events"),
    ("POST", "/plugins/hello/api/action"),
])
async def test_phone_allows_remote_plugin_routes(tmp_path, method, path):
    link = PhoneLink(None, tmp_path)
    assert await link._http("phone", start(method, path)) == {"ok": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/plugins/install"),
    ("GET", "/plugins/jobs/123"),
    ("POST", "/plugins/jobs/123/confirm"),
    ("POST", "/plugins/hello/update"),
    ("PUT", "/plugins/hello/settings"),
    ("POST", "/plugins/hello/enable"),
    ("POST", "/plugins/hello/services/worker/start"),
    ("DELETE", "/plugins/hello"),
    ("PUT", "/plugins/hello/%73ettings"),
    ("PUT", "/plugins/hello/%2573ettings"),
    ("POST", "/plugins/hello/api/../enable"),
    ("POST", "/plugins/hello//api/action"),
])
async def test_phone_rejects_local_management_and_ambiguous_paths(tmp_path, method, path):
    link = PhoneLink(None, tmp_path)
    with pytest.raises(PhoneLinkError, match="only on the computer"):
        await link._http("phone", start(method, path, [
            ["Authorization", "Bearer spoof"], ["X-Kollab-Plugin-Remote", "0"],
        ]))
    assert not link.exchanges


@pytest.mark.asyncio
async def test_phone_replaces_caller_credentials_and_remote_marker(tmp_path, monkeypatch):
    seen = {}

    async def app(scope, receive, send):
        seen.update(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatch.setattr(phone_link, "get_token", lambda: "host-secret")
    link = PhoneLink(app, tmp_path)
    await link._http("phone", start("GET", "/plugins/hello/api/action", [
        ["Authorization", "Bearer spoof"], ["X-Kollab-Plugin-Remote", "0"],
        ["Accept", "application/json"],
    ]))
    await link._serve(link.exchanges[("phone", "a" * 32)])
    assert seen["headers"].count((b"authorization", b"Bearer host-secret")) == 1
    assert (b"x-kollab-plugin-remote", b"1") in seen["headers"]
    assert (b"x-kollab-plugin-remote", b"0") not in seen["headers"]
    assert (b"accept", b"application/json") in seen["headers"]


@pytest.mark.asyncio
async def test_phone_local_only_plugin_api_is_denied_by_real_engine(tmp_path, monkeypatch):
    from kollabor_engine.server import create_app

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    monkeypatch.setattr(phone_link, "get_token", lambda: "host-secret")
    source = Path(__file__).resolve().parents[2] / "bundles/plugins/hello"
    home = tmp_path / ".kollab"
    root = home / "plugins/hello"
    shutil.copytree(source, root)
    manifest_path = root / "kollab-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engine"] = "python/engine.py:make_router"
    manifest_path.write_text(json.dumps(manifest))
    (root / "python/engine.py").write_text('''from fastapi import APIRouter

def make_router(ctx):
    router = APIRouter()
    @router.post("/local")
    @ctx.local_only
    def local():
        return {"ok": True}
    return router
''')
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "files_sha256": files_sha256(root), "granted": [],
    }}, "dev": {}}))
    link = PhoneLink(create_app(), tmp_path / "phones")
    await link._http("phone", start("POST", "/plugins/hello/api/local", [
        ["x-kollab-plugin-remote", "0"],
    ]))
    exchange = link.exchanges[("phone", "a" * 32)]
    await link._serve(exchange)
    assert (await exchange.events.get())["status"] == 403
