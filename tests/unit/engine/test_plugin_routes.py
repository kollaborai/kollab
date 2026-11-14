"""Engine plugin list delegates to the shared plugin inventory."""

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
