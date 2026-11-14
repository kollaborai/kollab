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
    assert hub.subscriber_count("hello") == 1
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        received = asyncio.create_task(anext(response.body_iterator))
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
    app.state.plugin_events.publish_change()
    event = json.loads((await asyncio.wait_for(received, timeout=1))["data"])
    assert event["topic"] == "plugins-changed"
    assert event["ts"] > 0
    await response.body_iterator.aclose()
    assert app.state.plugin_events.subscriber_count(None) == 0
