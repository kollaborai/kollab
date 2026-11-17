"""Attached development plugins cannot be toggled as built-in plugins."""


def test_dev_row_is_read_only(monkeypatch):
    from kollabor_plugins import registry
    from kollabor_tui.config_widgets import ConfigWidgetDefinitions

    monkeypatch.setattr(
        registry, "plugin_entries", lambda *_: [{"id": "hello@dev", "name": "Hello@dev", "kind": "dev", "state": "ok"}]
    )
    row = ConfigWidgetDefinitions.get_available_plugins()[0]
    assert row["type"] == "label"
    assert "plugin dev off" in row["help"]
