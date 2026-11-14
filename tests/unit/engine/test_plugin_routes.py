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
