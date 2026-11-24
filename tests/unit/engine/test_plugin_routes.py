"""Engine plugin list delegates to the shared plugin inventory."""

import sys

import pytest
from fastapi.testclient import TestClient
from kollabor_engine.server import create_app


@pytest.fixture(autouse=True)
def no_live_plugin_services(monkeypatch):
    """Route tests never start real plugin service processes."""
    import asyncio

    from kollabor_plugins import services

    async def idle_supervisor():
        await asyncio.Event().wait()

    monkeypatch.setattr(services, "supervise_services", idle_supervisor)


def test_plugin_list_uses_shared_inventory(tmp_path, monkeypatch):
    from kollabor_plugins import registry

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    entries = [{"id": "hello", "name": "Hello"}]
    monkeypatch.setattr(registry, "plugin_entries", lambda: entries)
    with TestClient(create_app()) as client:
        response = client.get("/plugins")
    assert response.status_code == 200
    data = response.json()
    assert data.pop("server_time") > 0
    assert data == {"schema": 1, "plugins": [registry._normalize_plugin_entry(entries[0])]}


def test_no_plugins_disables_engine_plugin_routes(monkeypatch):
    from kollabor_plugins import registry

    monkeypatch.setattr(sys, "argv", ["kollabor_engine", "serve", "--no-plugins"])
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    inventory_calls = []
    monkeypatch.setattr(registry, "plugin_entries", lambda: inventory_calls.append(True) or [])
    with TestClient(create_app()) as client:
        response = client.get("/plugins")
    assert response.status_code == 404
    assert inventory_calls == []


def test_plugin_settings_read_installed_development_and_builtin(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from kollabor_plugins import registry

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    installed = home / "plugins" / "hello"
    development = tmp_path / "dev-hello"
    shutil.copytree(source, installed)
    shutil.copytree(source, development)
    (home / "plugins.json").write_text(json.dumps({
        "plugins": {"hello": {"enabled": False}}, "dev": {"hello": str(development)}
    }))
    data = home / "plugin-data" / "hello"
    data.mkdir(parents=True)
    (data / "values.json").write_text(json.dumps({"greeting": "Saved"}))
    monkeypatch.setattr(registry, "plugin_entries", lambda: [{
        "id": "built-in", "kind": "built-in", "settings": [
            {"path": "plugins.built-in.enabled", "value": True, "secret": False},
            {"path": "plugins.built-in.mode", "value": "safe", "secret": False},
            {"path": "plugins.built-in.token", "value": None, "secret": True},
        ],
    }])
    with TestClient(create_app()) as client:
        assert client.get("/plugins/hello/settings").json() == {"greeting": "Saved"}
        assert client.get("/plugins/hello@dev/settings").json() == {"greeting": "Hello"}
        assert client.get("/plugins/built-in/settings").json() == {
            "plugins.built-in.mode": "safe"
        }
        assert client.get("/plugins/missing/settings").status_code == 404
        assert client.get("/plugins/hello%40other/settings").status_code == 404
        (data / "values.json").write_text("{broken", encoding="utf-8")
        assert client.get("/plugins/hello/settings").status_code == 409
        (installed / "kollab-plugin.json").write_text("{broken", encoding="utf-8")
        assert client.get("/plugins/hello/settings").status_code == 409


@pytest.mark.asyncio
async def test_plugin_event_streams_heartbeat_and_disconnect(tmp_path, monkeypatch):
    import asyncio
    import json
    import shutil
    from pathlib import Path
    from types import SimpleNamespace

    import httpx
    from kollabor_engine.routes import plugins as plugin_routes

    from kollabor_plugins import registry

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    shutil.copytree(source, home / "plugins" / "hello")
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {}}, "dev": {}}))
    monkeypatch.setattr(registry, "plugin_entries", lambda: [{
        "id": "hello", "services": [{"name": "hello", "heartbeat": None}],
    }])
    app = create_app()
    hub = app.state.plugin_events
    response = await plugin_routes.plugin_events(SimpleNamespace(app=app), "hello")
    other = hub.subscribe("other")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        received = asyncio.create_task(anext(response.body_iterator))
        await asyncio.sleep(0)
        assert hub.subscriber_count("hello") == 1
        sent = await client.post("/plugins/hello/events", json={
            "topic": "heartbeat", "data": {"service": "hello"}, "ts": -1,
        })
        assert sent.status_code == 200
        event = json.loads((await asyncio.wait_for(received, timeout=1))["data"])
        assert event["topic"] == "heartbeat"
        assert event["data"] == {"service": "hello"}
        assert event["ts"] > 0
        assert other.empty()
        assert (await client.get("/plugins")).json()["plugins"][0]["services"][0]["heartbeat"] == event["ts"]
        assert (await client.post("/plugins/hello/events", json={
            "topic": "heartbeat", "data": {"service": "unknown"},
        })).status_code == 422
        assert (await client.post("/plugins/missing/events", json={"topic": "x", "data": {}})).status_code == 404
    await response.body_iterator.aclose()
    hub.unsubscribe("other", other)
    assert hub.subscriber_count("hello") == 0
    assert hub.subscriber_count("other") == 0


@pytest.mark.asyncio
async def test_global_plugin_change_stream(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    from kollabor_engine.routes import plugins as plugin_routes

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    app = create_app()
    response = await plugin_routes.plugin_changes(SimpleNamespace(app=app))
    received = asyncio.create_task(anext(response.body_iterator))
    await asyncio.sleep(0)
    app.state.plugin_events.publish_change()
    event = json.loads((await asyncio.wait_for(received, timeout=1))["data"])
    assert event["topic"] == "plugins-changed"
    assert event["ts"] > 0
    await response.body_iterator.aclose()
    assert app.state.plugin_events.subscriber_count(None) == 0


def test_plugin_events_are_available_to_remote_pages():
    from kollabor_engine.routes.plugins import remote_plugin_route_allowed

    assert remote_plugin_route_allowed("GET", "/plugins/hello/events")
    assert remote_plugin_route_allowed("POST", "/plugins/hello/events")
    assert not remote_plugin_route_allowed("DELETE", "/plugins/hello/events")
    assert not remote_plugin_route_allowed("PUT", "/plugins/hello/settings")


def test_plugin_usage_incremental_replay_and_local_day(tmp_path, monkeypatch):
    import json
    import shutil
    from datetime import datetime, timedelta
    from pathlib import Path

    from kollabor_engine.routes import plugins as plugin_routes

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    shutil.copytree(source, home / "plugins" / "hello")
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {}}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    now = datetime.now().astimezone()
    yesterday = now - timedelta(days=1)
    today_row = {"ts": now.timestamp(), "model": "example", "input": 10, "output": 10, "labels": {}}
    old_row = {"ts": yesterday.timestamp(), "model": "example", "input": 1, "output": 2, "labels": {}}
    next_row = {"ts": now.timestamp(), "model": "example", "input": 2, "output": 3, "labels": {}}
    next_line = json.dumps(next_row)
    usage = run / "usage.jsonl"
    usage.write_text(json.dumps(today_row) + "\n" + json.dumps(old_row) + "\n" + next_line[:10])
    with TestClient(create_app()) as client:
        first = client.get("/plugins/hello/usage")
        assert first.status_code == 200
        assert first.json() == {"date": now.date().isoformat(), "total": 20, "cap": 2000000, "remaining": 1999980}
        assert client.get("/plugins/hello/usage").json() == first.json()
        previous = json.loads((run / f"usage-{yesterday.date().isoformat()}.json").read_text())
        assert previous["total"] == 3
        with usage.open("a") as stream:
            stream.write(next_line[10:] + "\n")
        real_write = plugin_routes.write_text_atomic

        def fail_offset(path, content):
            if Path(path).name == "usage.offset":
                raise OSError("simulated crash before offset commit")
            return real_write(path, content)

        monkeypatch.setattr(plugin_routes, "write_text_atomic", fail_offset)
        assert client.get("/plugins/hello/usage").status_code == 503
        monkeypatch.setattr(plugin_routes, "write_text_atomic", real_write)
        assert client.get("/plugins/hello/usage").json()["total"] == 25
    with TestClient(create_app()) as restarted:
        assert restarted.get("/plugins/hello/usage").json()["total"] == 25
        usage.write_text(json.dumps({**today_row, "input": 1, "output": 1}) + "\n")
        assert restarted.get("/plugins/hello/usage").json()["total"] == 27
        with usage.open("a") as stream:
            stream.write("{bad json}\n")
        assert restarted.get("/plugins/hello/usage").status_code == 409


def test_plugin_usage_is_checkpointed_in_bounded_batches(tmp_path, monkeypatch):
    import json
    import shutil
    from datetime import datetime
    from pathlib import Path

    from kollabor_engine.routes import plugins as plugin_routes

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    shutil.copytree(source, home / "plugins" / "hello")
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {}}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    row = {"ts": datetime.now().timestamp(), "model": "example", "input": 1, "output": 0, "labels": {}}
    (run / "usage.jsonl").write_text((json.dumps(row) + "\n") * 1500)
    offsets = []
    real_write = plugin_routes._write_usage_json

    def record_offset(path, value):
        if path.name == "usage.offset":
            offsets.append(value["offset"])
        return real_write(path, value)

    monkeypatch.setattr(plugin_routes, "_write_usage_json", record_offset)
    with TestClient(create_app()) as client:
        assert client.get("/plugins/hello/usage").json()["total"] == 1500
    assert len(offsets) >= 2
    assert offsets == sorted(set(offsets))


@pytest.mark.asyncio
async def test_engine_context_routes_events_notices_and_heartbeats(tmp_path, monkeypatch):
    import asyncio
    import json
    import shutil
    from pathlib import Path

    from fastapi import HTTPException
    from kollabor_engine.routes import plugins as plugin_routes

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    record = {"enabled": True, "files_sha256": files_sha256(root), "granted": []}
    registry_path = home / "plugins.json"
    registry_path.write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))
    hub = plugin_routes.PluginEventHub()
    own = hub.subscribe("hello")
    global_events = hub.subscribe(None)
    other = hub.subscribe("other")
    context = plugin_routes._engine_context("hello", hub)
    context.events.publish("heartbeat", {"service": "hello"})
    heartbeat = await asyncio.wait_for(own.get(), 1)
    assert context.services.status()[0]["heartbeat"] == heartbeat["ts"]
    context.notify("A pull request is waiting.", {"label": "Open"})
    notice = await asyncio.wait_for(global_events.get(), 1)
    assert notice["topic"] == "notice"
    assert notice["data"] == {"plugin_id": "hello", "text": "A pull request is waiting.", "button": {"label": "Open"}}
    assert other.empty()
    with pytest.raises(ValueError, match="heartbeat"):
        context.events.publish("heartbeat", {"service": "not-declared"})
    with pytest.raises(ValueError, match="notice"):
        context.notify("two\nlines")
    with pytest.raises(PermissionError):
        context.secrets.get("TOKEN")
    record["enabled"] = False
    registry_path.write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))
    with pytest.raises(HTTPException) as disabled:
        plugin_routes._engine_context("hello", hub)
    assert disabled.value.status_code == 403
    hub.unsubscribe("hello", own)
    hub.unsubscribe(None, global_events)
    hub.unsubscribe("other", other)


def test_finished_agent_run_usage_is_reconciled_once_per_day(tmp_path, monkeypatch):
    import json
    import shutil
    from datetime import datetime, timedelta
    from pathlib import Path

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    shutil.copytree(source, home / "plugins" / "hello")
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {}}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    now = datetime.now().astimezone()
    yesterday = now - timedelta(days=1)
    rows = [
        {"run_id": "one", "status": "done", "finished_at": now.isoformat(),
         "result": {"usage": {"input": 10, "output": 4}}},
        {"run_id": "two", "status": "running", "tokens_so_far": 7},
    ]
    runs = run / "runs.json"
    runs.write_text(json.dumps({"runs": rows}))
    with TestClient(create_app()) as client:
        assert client.get("/plugins/hello/usage").json()["total"] == 14
        assert client.get("/plugins/hello/usage").json()["total"] == 14
        rows[1] = {"run_id": "two", "status": "timeout", "finished_at": yesterday.timestamp(),
                   "result": {"usage": {"input": 2, "output": 1}}}
        runs.write_text(json.dumps({"runs": rows}))
        assert client.get("/plugins/hello/usage").json()["total"] == 14
        previous = json.loads((run / f"usage-{yesterday.date().isoformat()}.json").read_text())
        assert previous["total"] == 3
    with TestClient(create_app()) as restarted:
        assert restarted.get("/plugins/hello/usage").json()["total"] == 14
        rows.append({"run_id": "one", "status": "done", "finished_at": now.isoformat(),
                     "result": {"usage": {"input": 3, "output": 3}}})
        runs.write_text(json.dumps({"runs": rows}))
        assert restarted.get("/plugins/hello/usage").status_code == 409


def test_dispatcher_loads_only_approved_enabled_plugin_and_respects_local_only(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    manifest_path = root / "kollab-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engine"] = "python/engine.py:make_router"
    manifest_path.write_text(json.dumps(manifest))
    entry = root / "python" / "engine.py"
    entry.write_text('''from fastapi import APIRouter

def make_router(ctx):
    ctx.data.mkdir(parents=True, exist_ok=True)
    path = ctx.data / "loads"
    count = int(path.read_text()) + 1 if path.exists() else 1
    path.write_text(str(count))
    router = APIRouter()
    @router.get("/hello")
    def hello():
        return {"id": ctx.id, "load": count}
    @router.post("/local")
    @ctx.local_only
    def local():
        return {"ok": True}
    return router
''')
    record = {"enabled": True, "files_sha256": files_sha256(root), "granted": []}
    registry = home / "plugins.json"
    registry.write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))
    with TestClient(create_app()) as client:
        assert client.get("/plugins/hello/api/hello").json() == {"id": "hello", "load": 1}
        assert client.get("/plugins/hello/api/hello").json()["load"] == 1
        assert client.post("/plugins/hello/api/local").status_code == 200
        assert client.post("/plugins/hello/api/local", headers={"x-kollab-plugin-remote": "1"}).status_code == 403
        record["enabled"] = False
        registry.write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))
        assert client.get("/plugins/hello/api/hello").status_code == 404
        record["enabled"] = True
        registry.write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))
        assert client.get("/plugins/hello/api/hello").json()["load"] == 2
        entry.write_text(entry.read_text() + "\n# changed without approval\n")
        assert client.get("/plugins/hello/api/hello").status_code == 409
        assert (home / "plugin-data" / "hello" / "loads").read_text() == "2"


def test_dispatcher_reloads_development_python_and_rejects_manifest_changes(tmp_path, monkeypatch):
    import hashlib
    import json
    import shutil
    from pathlib import Path

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    home.mkdir()
    root = tmp_path / "hello-development"
    shutil.copytree(source, root)
    manifest_path = root / "kollab-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engine"] = "python/engine.py:make_router"
    manifest_path.write_text(json.dumps(manifest))
    entry = root / "python" / "engine.py"

    def write_version(version):
        entry.write_text(
            "from fastapi import APIRouter\n"
            "def make_router(ctx):\n"
            "    router = APIRouter()\n"
            "    @router.get('/version')\n"
            "    def version():\n"
            f"        return {{'version': {version}}}\n"
            "    return router\n"
        )

    write_version(1)
    (home / "plugins.json").write_text(json.dumps({"plugins": {}, "dev": {"hello": str(root)}}))
    data = home / "plugin-data" / "hello@dev"
    data.mkdir(parents=True)
    (data / "approval.json").write_text(json.dumps({
        "folder": str(root), "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(), "granted": [],
    }))
    with TestClient(create_app()) as client:
        assert client.get("/plugins/hello@dev/api/version").json() == {"version": 1}
        write_version(2)
        assert client.get("/plugins/hello@dev/api/version").json() == {"version": 2}
        manifest["description"] = "Modified without approval"
        manifest_path.write_text(json.dumps(manifest))
        assert client.get("/plugins/hello@dev/api/version").status_code == 409


def test_dispatcher_vendor_collisions_are_reported_and_released(tmp_path, monkeypatch):
    import json
    import shutil
    import sys
    from pathlib import Path

    from kollabor_plugins import registry
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    records = {}
    for plugin_id, value in (("hello", 1), ("other", 2)):
        root = home / "plugins" / plugin_id
        shutil.copytree(source, root)
        path = root / "kollab-plugin.json"
        manifest = json.loads(path.read_text())
        manifest.update({"id": plugin_id, "engine": "python/engine.py:make_router"})
        path.write_text(json.dumps(manifest))
        vendor = root / "python" / "vendor"
        vendor.mkdir()
        (vendor / "plugin_vendor_collision_1028.py").write_text(f"VALUE = {value}\n")
        (root / "python" / "engine.py").write_text('''from fastapi import APIRouter
import plugin_vendor_collision_1028 as vendor

def make_router(ctx):
    router = APIRouter()
    @router.get("/value")
    def value():
        return {"value": vendor.VALUE}
    return router
''')
        records[plugin_id] = {"enabled": True, "files_sha256": files_sha256(root), "granted": []}
    path = home / "plugins.json"
    path.write_text(json.dumps({"plugins": records, "dev": {}}))
    monkeypatch.setattr(registry, "plugin_entries", lambda: [
        {"id": plugin_id, "state": "ok", "error": None, "services": []} for plugin_id in records
    ])
    with TestClient(create_app()) as client:
        assert client.get("/plugins/hello/api/value").json() == {"value": 1}
        assert client.get("/plugins/hello/api/value").json() == {"value": 1}
        assert client.get("/plugins/other/api/value").status_code == 503
        other = next(row for row in client.get("/plugins").json()["plugins"] if row["id"] == "other")
        assert other["state"] == "error"
        assert other["error"] == "Python entry failed while loading; see the process log for details."
        records["hello"]["enabled"] = False
        path.write_text(json.dumps({"plugins": records, "dev": {}}))
        assert client.get("/plugins/hello/api/value").status_code == 404
        assert client.get("/plugins/other/api/value").json() == {"value": 2}
    assert "plugin_vendor_collision_1028" not in sys.modules


def test_engine_router_load_failure_is_visible_on_startup(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from kollabor_plugins import registry
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    manifest_path = root / "kollab-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engine"] = "python/engine.py:make_router"
    manifest_path.write_text(json.dumps(manifest))
    (root / "python" / "engine.py").write_text(
        'def make_router(ctx):\n    raise ValueError("bad router secret-value")\n'
    )
    (home / "plugins.json").write_text(json.dumps({
        "plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root), "granted": []}}, "dev": {},
    }))
    monkeypatch.setattr(registry, "plugin_entries", lambda: [
        {"id": "hello", "state": "ok", "error": None, "services": []},
    ])
    with TestClient(create_app()) as client:
        entry = client.get("/plugins").json()["plugins"][0]
        assert entry["state"] == "error"
        assert entry["error"] == "Python entry failed while loading; see the process log for details."
        assert "ValueError" not in entry["error"]
        assert "secret-value" not in entry["error"]
        assert client.get("/plugins/hello/api/anything").status_code == 503


def test_plugin_tokens_are_private_stable_and_scoped(tmp_path, monkeypatch):
    import json
    import shutil
    import stat
    from pathlib import Path

    from kollabor_engine import server

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    monkeypatch.setattr(server, "validate_token", lambda token: token == "full-user-token")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    records = {}
    for plugin_id in ("hello", "other"):
        root = home / "plugins" / plugin_id
        shutil.copytree(source, root)
        manifest_path = root / "kollab-plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update({"id": plugin_id, "engine": "python/engine.py:make_router"})
        manifest_path.write_text(json.dumps(manifest))
        (root / "python" / "engine.py").write_text('''from fastapi import APIRouter, WebSocket

def make_router(ctx):
    router = APIRouter()
    @router.get("/who")
    def who():
        return {"id": ctx.id, "has_token": bool(ctx.token)}
    @router.websocket("/socket")
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"id": ctx.id})
        await ws.close()
    return router
''')
        records[plugin_id] = {"enabled": True, "files_sha256": files_sha256(root), "granted": []}
    (home / "plugins.json").write_text(json.dumps({"plugins": records, "dev": {}}))
    with TestClient(create_app()) as client:
        hello_path = home / "plugin-data" / "hello" / "run" / "token"
        other_path = home / "plugin-data" / "other" / "run" / "token"
        hello_token = hello_path.read_text().strip()
        other_token = other_path.read_text().strip()
        assert hello_token and other_token and hello_token != other_token
        assert stat.S_IMODE(hello_path.stat().st_mode) == 0o600
        own = {"Authorization": f"Bearer {hello_token}"}
        assert client.get("/plugins/hello/api/who", headers=own).json() == {"id": "hello", "has_token": True}
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/plugins/hello/api/socket"):
                pass
        with client.websocket_connect("/plugins/hello/api/socket", headers=own) as websocket:
            assert websocket.receive_json() == {"id": "hello"}
        assert client.get("/plugins/hello/settings", headers=own).status_code == 200
        assert client.get("/plugins/hello/usage", headers=own).status_code == 200
        assert client.post("/plugins/hello/events", headers=own, json={"topic": "ready", "data": {}}).status_code == 200
        assert client.get("/plugins/other/api/who", headers=own).status_code == 401
        assert client.get("/plugins", headers=own).status_code == 401
        assert client.post("/plugins/hello/enable", headers=own).status_code == 401
        assert client.post("/plugins/install", headers=own).status_code == 401
        assert client.put("/plugins/hello/settings", headers=own, json={"greeting": "Hi"}).status_code != 401
        from kollabor_engine.routes.plugins import _plugin_token_target
        assert _plugin_token_target("GET", "/plugins/hello/api/../other") is None
        assert _plugin_token_target("GET", "/plugins/hello/api/%2e%2e/other") is None
        assert _plugin_token_target("GET", "/plugins/hello//settings") is None
        assert _plugin_token_target("POST", "/plugins/hello/update") is None
        assert _plugin_token_target("POST", "/plugins/hello/services/hello/start") == "hello"
        assert client.get("/plugins", headers={"Authorization": "Bearer full-user-token"}).status_code == 200
    with TestClient(create_app()):
        assert hello_path.read_text().strip() == hello_token
        assert other_path.read_text().strip() == other_token


def test_plugin_websocket_cannot_bypass_engine_auth(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from kollabor_engine import server
    from starlette.websockets import WebSocketDisconnect

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    monkeypatch.setattr(server, "validate_token", lambda token: token == "full-user-token")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    manifest_path = root / "kollab-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engine"] = "python/engine.py:make_router"
    manifest_path.write_text(json.dumps(manifest))
    (root / "python" / "engine.py").write_text('''from fastapi import APIRouter, WebSocket

def make_router(ctx):
    router = APIRouter()
    @router.websocket("/socket")
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.send_text("ok")
        await ws.close()
    return router
''')
    (home / "plugins.json").write_text(json.dumps({
        "plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root), "granted": []}}, "dev": {},
    }))
    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/plugins/hello/api/socket"):
                pass
        with client.websocket_connect(
            "/plugins/hello/api/socket", headers={"Authorization": "Bearer full-user-token"}
        ) as websocket:
            assert websocket.receive_text() == "ok"


def test_plugin_token_mint_refuses_symlinks(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from kollabor_engine.routes import plugins as plugin_routes

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    (home / "plugins.json").write_text(json.dumps({
        "plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root), "granted": []}}, "dev": {},
    }))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("do not alter")
    (run / "token").symlink_to(outside)
    context = plugin_routes._engine_context("hello", plugin_routes.PluginEventHub())
    with pytest.raises(ValueError, match="symlink"):
        plugin_routes.PluginTokenStore().mint(context)
    assert outside.read_text() == "do not alter"


def test_forwarded_engine_requests_cannot_use_plugin_management(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    (home / "plugins.json").write_text(json.dumps({
        "plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root), "granted": []}}, "dev": {},
    }))
    remote = {"x-kollab-plugin-remote": "1"}
    with TestClient(create_app()) as client:
        for method, path in [
            ("PUT", "/plugins/hello/settings"),
            ("POST", "/plugins/install"),
            ("POST", "/plugins/hello/services/hello/start"),
            ("POST", "/plugins/hello/disable"),
        ]:
            assert client.request(method, path, headers=remote).status_code == 403
        assert client.get("/plugins/hello/settings", headers=remote).status_code == 200
        assert client.post("/plugins/hello/events", headers=remote, json={"topic": "x", "data": {}}).status_code == 200
        assert client.post("/plugins/hello/agents", headers=remote).status_code != 403
        assert client.delete("/plugins/hello/agents/run-1", headers=remote).status_code != 403


def test_package_settings_write_validates_and_publishes_change(tmp_path, monkeypatch):
    import asyncio
    import json
    import shutil
    from pathlib import Path

    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    (home / "plugins.json").write_text(json.dumps({
        "plugins": {"hello": {"enabled": False, "files_sha256": files_sha256(root), "granted": []}}, "dev": {},
    }))
    app = create_app()

    async def subscribe():
        return app.state.plugin_events.subscribe(None)

    async def next_event(queue):
        return await asyncio.wait_for(queue.get(), 1)

    with TestClient(app) as client:
        queue = client.portal.call(subscribe)
        assert client.put("/plugins/hello/settings", json={"greeting": "Welcome"}).json() == {"greeting": "Welcome"}
        assert client.portal.call(next_event, queue)["topic"] == "plugins-changed"
        assert client.get("/plugins/hello/settings").json() == {"greeting": "Welcome"}
        assert client.put("/plugins/hello/settings", json={"greeting": 4}).status_code == 422
        assert client.put("/plugins/hello/settings", json={"enabled": True}).status_code == 422
        assert client.put("/plugins/missing/settings", json={"greeting": "Hi"}).status_code == 404
        assert client.put("/plugins/hello/settings", json={"greeting": "Remote"},
                          headers={"x-kollab-plugin-remote": "1"}).status_code == 403
        assert client.get("/plugins/hello/settings").json() == {"greeting": "Welcome"}
        app.state.plugin_events.unsubscribe(None, queue)
    assert app.state.plugin_events.subscriber_count(None) == 0


def test_builtin_plugin_settings_preserve_unrelated_config(tmp_path, monkeypatch):
    import json

    from kollabor_config import config_utils
    from kollabor_plugins import registry

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    path = tmp_path / ".kollab" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"plugins": {"example": {"mode": "old", "enabled": True}}, "other": {"keep": 7}}))
    monkeypatch.setattr(config_utils, "get_global_config_path", lambda: path)

    def entries():
        value = json.loads(path.read_text())["plugins"]["example"]["mode"]
        return [{"id": "example", "kind": "built-in", "state": "ok", "services": [], "settings": [
            {"path": "plugins.example.mode", "value": value, "type": "text_input", "editable": True},
            {"path": "plugins.example.enabled", "value": True, "type": "checkbox", "editable": True},
            {"path": "plugins.example.secret", "value": None, "type": "text_input", "editable": True, "secret": True},
            {"path": "plugins.example.managed", "value": "x", "type": "text_input", "editable": True,
             "managed_by": "primary"},
        ]}]

    monkeypatch.setattr(registry, "plugin_entries", entries)
    with TestClient(create_app()) as client:
        assert client.put("/plugins/example/settings", json={"plugins.example.mode": "new"}).json() == {
            "plugins.example.mode": "new", "plugins.example.managed": "x",
        }
        saved = json.loads(path.read_text())
        assert saved["plugins"]["example"] == {"mode": "new", "enabled": True}
        assert saved["other"] == {"keep": 7}
        for key in ("plugins.example.enabled", "plugins.example.secret", "plugins.example.managed", "other.keep"):
            assert client.put("/plugins/example/settings", json={key: "leak"}).status_code == 422
        assert json.loads(path.read_text()) == saved
        assert client.post("/plugins/example/disable").json() == {"enabled": False}
        assert json.loads(path.read_text())["plugins"]["example"]["enabled"] is False
        assert client.post("/plugins/example/enable").json() == {"enabled": True}
        assert json.loads(path.read_text())["plugins"]["example"]["enabled"] is True


def test_service_actions_restart_and_redacted_log(tmp_path, monkeypatch):
    import asyncio
    import json
    import shutil
    from pathlib import Path

    from kollabor_engine import auth

    from kollabor_ai import profile_manager
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setattr(auth, "_token_file", lambda: tmp_path / "absent-engine-token")
    monkeypatch.setattr(auth, "get_token", lambda: "user-token-value")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    (home / "plugins.json").write_text(json.dumps({
        "plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root),
                              "granted": ["secret:EXAMPLE"]}}, "dev": {},
    }))
    calls = []
    monkeypatch.setattr(
        services, "control_service", lambda plugin, service, action: calls.append((plugin, service, action))
    )
    monkeypatch.setattr(services, "_recorded_process_alive", lambda path: False)
    monkeypatch.setattr(profile_manager, "_keyring_get", lambda name: "secret-value" if name == "EXAMPLE" else None)
    app = create_app()
    with TestClient(app) as client:
        run = home / "plugin-data" / "hello" / "run"
        token = (run / "token").read_text()
        log = run / "hello.log"
        log.write_text(f"first secret-value {token} user-token-value\nsecond\n")
        response = client.get("/plugins/hello/services/hello/log?tail=2")
        assert response.status_code == 200
        assert "first" in response.text and "second" in response.text
        assert "secret-value" not in response.text and token not in response.text
        assert "user-token-value" not in response.text
        assert client.get("/plugins/hello/services/hello/log?tail=1").text == "second"
        assert client.post("/plugins/hello/services/hello/start").status_code == 200
        assert client.post("/plugins/hello/services/hello/stop").status_code == 200
        assert client.post("/plugins/hello/services/hello/restart").status_code == 202

        async def wait_for_restart():
            for _ in range(100):
                if len(calls) >= 4:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("restart did not finish")

        client.portal.call(wait_for_restart)
        assert calls == [("hello", "hello", "start"), ("hello", "hello", "stop"),
                         ("hello", "hello", "stop"), ("hello", "hello", "start")]
        assert client.post("/plugins/hello/services/missing/start").status_code == 404
        assert client.post(
            "/plugins/hello/services/hello/start", headers={"x-kollab-plugin-remote": "1"}
        ).status_code == 403
        outside = tmp_path / "outside-log"
        outside.write_text("private outside contents")
        log.rename(run / "hello.log.saved")
        log.symlink_to(outside)
        assert client.get("/plugins/hello/services/hello/log").status_code == 409
        assert outside.read_text() == "private outside contents"


def test_package_enable_disable_remove_are_guarded_and_preserve_data(tmp_path, monkeypatch):
    import json
    import shutil
    from pathlib import Path

    from kollabor_plugins import packages, services, settings
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    source = Path(__file__).resolve().parents[3] / "bundles" / "plugins" / "hello"
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    shutil.copytree(source, root)
    registry_path = home / "plugins.json"
    registry_path.write_text(json.dumps({
        "plugins": {"hello": {"enabled": False, "files_sha256": files_sha256(root), "granted": []}}, "dev": {},
    }))
    data = home / "plugin-data" / "hello"
    run = data / "run"
    run.mkdir(parents=True)
    (data / "values.json").write_text('{"greeting":"keep"}')
    calls = []
    real_set_enabled = settings.set_enabled

    def enable(plugin_id, value):
        calls.append(("enabled", plugin_id, value))
        return real_set_enabled(plugin_id, value)

    monkeypatch.setattr(settings, "set_enabled", enable)
    monkeypatch.setattr(services, "stop_services", lambda plugin_id: calls.append(("stop", plugin_id)))
    monkeypatch.setattr(services, "supervisor_tick", lambda: calls.append(("tick",)))
    monkeypatch.setattr(services, "_recorded_process_alive", lambda path: False)
    monkeypatch.setattr(
        packages, "remove_package", lambda plugin_id, **kwargs: calls.append(("remove", plugin_id, kwargs))
    )
    app = create_app()
    with TestClient(app) as client:
        assert client.post("/plugins/hello/enable").status_code == 200
        assert json.loads(registry_path.read_text())["plugins"]["hello"]["enabled"] is True
        (run / "runs.json").write_text(json.dumps({"runs": [{"run_id": "active", "status": "running"}]}))
        assert client.post("/plugins/hello/disable").status_code == 409
        assert json.loads(registry_path.read_text())["plugins"]["hello"]["enabled"] is True
        (run / "runs.json").write_text(json.dumps({"runs": [{"run_id": "uncertain", "status": []}]}))
        assert client.post("/plugins/hello/disable").status_code == 409
        (run / "runs.json").write_text(json.dumps({"runs": []}))
        assert client.post("/plugins/hello/disable").status_code == 200
        assert json.loads(registry_path.read_text())["plugins"]["hello"]["enabled"] is False
        assert client.post("/plugins/hello/remove", json={"purge": True}).status_code == 422
        from kollabor_config.file_lock import locked_file
        with locked_file(run / "hello.lock", private=True):
            assert client.post("/plugins/hello/remove").status_code == 409
        assert not any(call[0] == "remove" for call in calls)
        assert client.post("/plugins/hello/remove").status_code == 200
        assert (data / "values.json").read_text() == '{"greeting":"keep"}'
        assert any(call[0] == "remove" and call[2]["purge"] is False for call in calls)
        assert client.post("/plugins/missing/enable").status_code == 404
        assert client.post("/plugins/hello/enable", headers={"x-kollab-plugin-remote": "1"}).status_code == 403


def test_disable_partial_stop_failure_publishes_and_revokes(tmp_path, monkeypatch):
    import json
    from unittest.mock import AsyncMock, Mock

    from kollabor_plugins import settings
    from kollabor_plugins.services import ServiceStopError

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "name": "Hello"}))
    path = home / "plugins.json"
    path.write_text(json.dumps({"plugins": {"hello": {"enabled": True}}, "dev": {}}))

    def partial_disable(plugin_id, enabled, **_):
        assert plugin_id == "hello" and enabled is False
        path.write_text(json.dumps({"plugins": {"hello": {"enabled": False}}, "dev": {}}))
        raise ServiceStopError("Could not stop background processes")

    monkeypatch.setattr(settings, "set_enabled", partial_disable)
    app = create_app()
    with TestClient(app) as client:
        app.state.plugin_dispatcher.invalidate = AsyncMock(return_value=False)
        app.state.plugin_service_controls.cancel_plugin = AsyncMock()
        app.state.plugin_events.publish_change = Mock()
        response = client.post("/plugins/hello/disable")
        assert response.status_code == 409
        assert "Could not stop" in response.json()["detail"]
        app.state.plugin_dispatcher.invalidate.assert_awaited_once_with("hello")
        app.state.plugin_service_controls.cancel_plugin.assert_awaited_once_with("hello")
        app.state.plugin_events.publish_change.assert_called_once()


def test_service_signal_failure_publishes_inventory_change(tmp_path, monkeypatch):
    import json
    from unittest.mock import AsyncMock, Mock

    from kollabor_plugins.services import ServiceSignalError

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    root = tmp_path / ".kollab" / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "services": {
        "worker": {"command": ["/bin/true"]},
    }}))
    (root.parent.parent / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": False}}, "dev": {}}))
    app = create_app()
    with TestClient(app) as client:
        app.state.plugin_service_controls.control = AsyncMock(side_effect=ServiceSignalError("denied"))
        app.state.plugin_events.publish_change = Mock()
        response = client.post("/plugins/hello/services/worker/stop")
        assert response.status_code == 503
        app.state.plugin_events.publish_change.assert_called_once()


def test_remove_partial_stop_failure_publishes_and_revokes(tmp_path, monkeypatch):
    import json
    from unittest.mock import AsyncMock, Mock

    from kollabor_plugins import settings
    from kollabor_plugins.services import ServiceStopError

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "name": "Hello"}))
    path = home / "plugins.json"
    path.write_text(json.dumps({"plugins": {"hello": {"enabled": True}}, "dev": {}}))

    def partial_disable(plugin_id, enabled, **_):
        assert plugin_id == "hello" and enabled is False
        path.write_text(json.dumps({"plugins": {"hello": {"enabled": False}}, "dev": {}}))
        raise ServiceStopError("Could not stop background processes")

    monkeypatch.setattr(settings, "set_enabled", partial_disable)
    app = create_app()
    with TestClient(app) as client:
        app.state.plugin_dispatcher.invalidate = AsyncMock(return_value=False)
        app.state.plugin_service_controls.cancel_plugin = AsyncMock()
        app.state.plugin_events.publish_change = Mock()
        response = client.post("/plugins/hello/remove")
        assert response.status_code == 409
        assert root.exists(), "partial stop failure must not remove the plugin"
        app.state.plugin_dispatcher.invalidate.assert_awaited_once_with("hello")
        app.state.plugin_service_controls.cancel_plugin.assert_awaited_once_with("hello")
        app.state.plugin_events.publish_change.assert_called_once()
