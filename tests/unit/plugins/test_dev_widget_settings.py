"""The dev form is routed to its dev-only plugin settings namespace."""


def test_dev_settings_are_exposed_in_the_terminal(monkeypatch):
    from kollabor_plugins import registry
    from kollabor_tui.config_widgets import ConfigWidgetDefinitions

    monkeypatch.setattr(
        registry,
        "plugin_entries",
        lambda *_: [
            {
                "id": "hello@dev",
                "name": "Hello@dev",
                "kind": "dev",
                "services": [],
                "settings": [{"type": "checkbox", "config_path": "sample.enabled"}],
            }
        ],
    )
    sections = ConfigWidgetDefinitions.get_plugin_config_sections()
    assert sections[0]["widgets"][0]["config_path"] == "plugin-values.hello@dev.sample.enabled"
