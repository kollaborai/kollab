"""The unified plugin list exposes development service controls and state."""

import json

import pytest

from kollabor_plugins.registry import plugin_entries


@pytest.mark.parametrize(
    ("service_status", "expected_plugin_state"),
    [("stopped", "ok"), ("stopping", "stopping"), ("stop-failed", "stop-failed")],
)
def test_dev_services_are_listed(tmp_path, monkeypatch, service_status, expected_plugin_state):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    builtins = tmp_path / "builtins"
    builtins.mkdir()
    root = tmp_path / "source"
    root.mkdir()
    (root / "kollab-plugin.json").write_text(
        json.dumps(
            {
                "format": 1,
                "id": "hello",
                "name": "Hello",
                "description": "Test development plugin",
                "version": "1.0.0",
                "kollab": ">=0",
                "services": {"worker": {"command": ["true"]}},
            }
        )
    )
    home = tmp_path / ".kollab"
    home.mkdir()
    (home / "plugins.json").write_text(json.dumps({"plugins": {}, "dev": {"hello": str(root)}}))
    run = home / "plugin-data" / "hello@dev" / "run"
    run.mkdir(parents=True)
    (run / "worker.state").write_text(
        json.dumps({"desired": "stopped", "status": service_status, "exit_history": []})
    )
    entry = next(row for row in plugin_entries(builtins) if row["id"] == "hello@dev")
    assert entry["state"] == expected_plugin_state
    assert [(item["name"], item["state"], item["desired"]) for item in entry["services"]] == [
        ("worker", service_status, "stopped")
    ]
