"""Built-in plugin entries use effective configuration and panel field shapes."""

import json
from types import SimpleNamespace

from kollabor_plugins import registry


def test_builtin_entry_uses_effective_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    home = tmp_path / ".kollab"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"plugins": {"demo": {"enabled": False, "greeting": "bonjour"}}}))

    class DemoPlugin:
        @classmethod
        def get_config_widgets(cls):
            return {"widgets": [{"type": "text_input", "config_path": "plugins.demo.greeting", "default": "hi"}]}

    class FakeRegistry:
        discovery = SimpleNamespace(
            plugin_classes={"DemoPlugin": DemoPlugin},
            plugin_configs={"DemoPlugin": {"plugins": {"demo": {"enabled": True, "greeting": "hi"}}}},
        )
        package_errors = {}

        def __init__(self, *args, **kwargs):
            pass

        def discover_classes_only(self):
            return [DemoPlugin]

        def get_merged_config(self):
            return {"plugins": {"demo": {"enabled": True, "greeting": "hi"}}}

    monkeypatch.setattr(registry, "PluginRegistry", FakeRegistry)
    builtin = tmp_path / "builtins"
    builtin.mkdir()
    entry = next(e for e in registry.plugin_entries(builtin) if e["id"] == "demo")
    assert entry["enabled"] is False
    assert entry["settings"][0]["path"] == "plugins.demo.greeting"
    assert entry["settings"][0]["value"] == "bonjour"


def test_builtin_names_are_human_readable_and_preserve_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    class UsageLedgerPlugin:
        name = "usage_ledger"

    class McpPlugin:
        name = "mcp"

    class CustomPlugin:
        name = "custom_plugin"
        display_name = "Custom Display"

    classes = [UsageLedgerPlugin, McpPlugin, CustomPlugin]

    class FakeRegistry:
        discovery = SimpleNamespace(plugin_configs={})
        package_errors = {}

        def __init__(self, *args, **kwargs):
            pass

        def discover_classes_only(self):
            return classes

        def get_merged_config(self):
            return {}

    monkeypatch.setattr(registry, "PluginRegistry", FakeRegistry)
    entries = registry.plugin_entries(tmp_path)
    assert {entry["id"]: entry["name"] for entry in entries} == {
        "usage_ledger": "Usage Ledger",
        "mcp": "MCP",
        "custom_plugin": "Custom Display",
    }
