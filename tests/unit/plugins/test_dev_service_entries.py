"""The unified plugin list exposes development service controls and state."""

import json

from kollabor_plugins.registry import plugin_entries


def test_dev_services_are_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    builtins = tmp_path / "builtins"
    builtins.mkdir()
    root = tmp_path / "source"
    root.mkdir()
    (root / "kollab-plugin.json").write_text(
        json.dumps({"id": "hello", "name": "Hello", "services": {"worker": {"command": ["true"]}}})
    )
    home = tmp_path / ".kollab"
    home.mkdir()
    (home / "plugins.json").write_text(json.dumps({"plugins": {}, "dev": {"hello": str(root)}}))
    run = home / "plugin-data" / "hello@dev" / "run"
    run.mkdir(parents=True)
    (run / "worker.state").write_text(json.dumps({"desired": "running", "status": "stopped", "exit_history": []}))
    entry = next(row for row in plugin_entries(builtins) if row["id"] == "hello@dev")
    assert [(item["name"], item["state"], item["desired"]) for item in entry["services"]] == [
        ("worker", "stopped", "running")
    ]
