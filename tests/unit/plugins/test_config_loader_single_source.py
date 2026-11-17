"""Plugin settings use the registry view, not a second discovery manager."""

import json

from kollabor_config.loader import ConfigLoader
from kollabor_plugins.packages import files_sha256
from kollabor_tui.config_widgets import ConfigWidgetDefinitions


def test_loader_never_constructs_legacy_manager_and_manifest_settings_are_visible(tmp_path, monkeypatch):
    import kollabor_config.loader as loader

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    home = tmp_path / ".kollab"
    root = home / "plugins/hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "name": "Hello", "settings": "settings.json"}))
    (root / "settings.json").write_text(json.dumps({"widgets": [{"type": "checkbox", "config_path": "enabled"}]}))
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {
                        "enabled": False,
                        "files_sha256": files_sha256(root),
                    }
                },
                "dev": {},
            }
        )
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy plugin config manager constructed")

    monkeypatch.setattr(loader, "PluginConfigManager", forbidden, raising=False)

    class Registry:
        discovery = object()

        def get_merged_config(self):
            return {"plugins": {"existing": {"enabled": True}}}

    config_loader = ConfigLoader(object(), Registry())
    assert config_loader.get_plugin_configs()["plugins"]["existing"]["enabled"]
    sections = ConfigWidgetDefinitions.get_plugin_config_sections()
    hello = next(section for section in sections if section.get("title") == "Hello")
    assert hello["widgets"][0]["config_path"] == "plugin-values.hello.enabled"
