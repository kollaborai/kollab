"""The common listing and terminal rows retain actionable failure messages."""

import json

from kollabor_plugins import registry
from kollabor_tui.config_widgets import ConfigWidgetDefinitions


def test_plugin_and_service_errors_reach_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.state").write_text(json.dumps({"status": "error", "error": "executable missing"}))
    entries = registry._service_entries(home, "hello", {"services": {"worker": {}}})
    assert entries[0]["error"] == "executable missing"
    monkeypatch.setattr(
        registry,
        "plugin_entries",
        lambda *_: [
            {
                "id": "hello",
                "name": "Hello",
                "kind": "installed",
                "state": "error",
                "error": "Failed to Load: missing dependency",
                "services": entries,
                "settings": [],
            }
        ],
    )
    assert "missing dependency" in ConfigWidgetDefinitions.get_available_plugins()[0]["help"]
    assert "executable missing" in ConfigWidgetDefinitions.get_plugin_config_sections()[0]["widgets"][0]["help"]
