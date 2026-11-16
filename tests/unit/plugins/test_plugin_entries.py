"""The registry's common listing includes disabled and damaged packages."""

import json


def test_plugin_entries_include_disabled_and_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.registry import plugin_entries

    builtin = tmp_path / "builtin"
    builtin.mkdir()
    home = tmp_path / ".kollab"
    package = home / "plugins" / "hello"
    package.mkdir(parents=True)
    manifest = {"id": "hello", "name": "Hello", "version": "1.0", "description": "Greeting", "uses": []}
    (package / "kollab-plugin.json").write_text(json.dumps(manifest))
    record = {
        "enabled": False,
        "source": "https://example.invalid/repo",
        "commit": "a" * 40,
        "files_sha256": files_sha256(package),
        "granted": [],
    }
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))
    entry = next(item for item in plugin_entries(builtin) if item["id"] == "hello")
    assert entry["kind"] == "installed"
    assert entry["state"] == "off"
    assert entry["enabled"] is False
    assert entry["name"] == "Hello"
    (package / "changed").write_text("modified")
    entry = next(item for item in plugin_entries(builtin) if item["id"] == "hello")
    assert entry["state"] == "changed-on-disk"


def test_enabled_plugin_reports_a_manually_stopping_service(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "name": "Hello"}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "granted": [], "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    monkeypatch.setattr(
        "kollabor_plugins.registry._service_entries",
        lambda *_: [{"name": "worker", "state": "stopping", "stop_deadline": 1234}],
    )
    hello = next(item for item in plugin_entries(builtin) if item["id"] == "hello")
    assert hello["enabled"] is True
    assert hello["state"] == "stopping"
