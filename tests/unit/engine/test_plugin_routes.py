"""Engine plugin list delegates to the shared plugin inventory."""

import pytest
from fastapi.testclient import TestClient
from kollabor_engine.server import create_app


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
    assert response.json() == {"schema": 1, "plugins": entries}


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
