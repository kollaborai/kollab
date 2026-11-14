"""The terminal plugin list must consume the shared registry entries."""


def test_plugin_widget_list_uses_registry(monkeypatch):
    from kollabor_plugins import registry
    from kollabor_tui.config_widgets import ConfigWidgetDefinitions

    monkeypatch.setattr(
        registry,
        "plugin_entries",
        lambda *_args: [
            {
                "id": "hello",
                "name": "Hello",
                "version": "1.0",
                "kind": "installed",
                "source": "https://example.invalid/repo",
                "state": "off",
                "enabled": False,
                "shadowed": False,
                "settings": [],
                "settings_section": None,
            }
        ],
    )
    rows = ConfigWidgetDefinitions.get_available_plugins()
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Hello"
    assert rows[0]["plugin_kind"] == "installed"
    assert rows[0]["config_path"] == "plugin-registry.hello.enabled"
