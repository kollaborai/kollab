"""Process controls flow through the installed-plugin settings backend."""

import json
from unittest.mock import Mock

from kollabor.panels.config import _field, apply_config_changes
from kollabor_tui.config_widgets import ConfigWidgetDefinitions


def test_process_row_shows_state_and_saved_desire(tmp_path, monkeypatch):
    import kollabor_plugins.registry as registry

    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / ".kollab" / "plugin-data" / "hello" / "run" / "worker.state"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"status": "running", "desired": "running"}))
    monkeypatch.setattr(
        registry,
        "plugin_entries",
        lambda path: [
            {
                "id": "hello",
                "name": "Hello",
                "kind": "installed",
                "settings": [],
                "services": [{"name": "worker", "state": "running", "desired": "running"}],
            }
        ],
    )
    sections = ConfigWidgetDefinitions.get_plugin_config_sections()
    row = sections[0]["widgets"][0]
    assert row["config_path"] == "plugin-service.hello.worker"
    assert "running" in row["help"].lower()
    service = Mock()
    assert _field(row, service, None, {})["value"] is True
    service.get.assert_not_called()


def test_process_toggle_dispatches_to_controls(monkeypatch):
    from kollabor_plugins import services

    control = Mock()
    monkeypatch.setattr(services, "control_service", control)
    config = Mock()
    assert apply_config_changes(config, {"plugin-service.hello.worker": False}, "global")
    control.assert_called_once_with("hello", "worker", "stop")
    config.save_key.assert_not_called()


def test_dev_process_toggle_dispatches_to_controls(monkeypatch):
    import kollabor_plugins.registry as registry
    import kollabor_plugins.services as services
    from kollabor_plugins.settings import apply_config_changes as apply_plugin_changes

    monkeypatch.setattr(
        registry,
        "plugin_entries",
        lambda _: [
            {
                "id": "hello@dev",
                "name": "Hello@dev",
                "kind": "dev",
                "settings": [],
                "services": [{"name": "worker", "state": "stopped"}],
            }
        ],
    )
    monkeypatch.setattr(ConfigWidgetDefinitions, "_packaged_plugins_dir", staticmethod(lambda: None))
    path = ConfigWidgetDefinitions.get_plugin_config_sections()[0]["widgets"][0]["config_path"]
    assert path == "plugin-service.hello@dev.worker"
    control = Mock()
    monkeypatch.setattr(services, "control_service", control)

    assert apply_plugin_changes({path: True}) == {}
    control.assert_called_once_with("hello@dev", "worker", "start")
