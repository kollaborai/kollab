"""The common listing includes panel fields and current plugin values."""

import json

from kollabor_plugins.packages import files_sha256
from kollabor_plugins.registry import plugin_entries


def test_settings_include_converted_field_and_current_value(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "name": "Hello", "settings": "settings.json"}))
    (root / "settings.json").write_text(
        json.dumps(
            {"widgets": [{"type": "text_input", "config_path": "greeting", "label": "Greeting", "default": "hi"}]}
        )
    )
    (home / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": False, "files_sha256": files_sha256(root)}}, "dev": {}})
    )
    data = home / "plugin-data" / "hello"
    data.mkdir(parents=True)
    (data / "values.json").write_text(json.dumps({"greeting": "bonjour"}))
    builtin = tmp_path / "builtins"
    builtin.mkdir()
    entry = next(e for e in plugin_entries(builtin) if e["id"] == "hello")
    field = entry["settings"][0]
    assert field["path"] == "plugin-values.hello.greeting"
    assert field["value"] == "bonjour"
    assert field["editable"] is True
