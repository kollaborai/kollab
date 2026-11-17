"""Development packages use their own settings store and declared form."""

import json

from kollabor_plugins.registry import plugin_entries
from kollabor_plugins.settings import config_value, save


def test_development_settings_use_the_dev_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    home = tmp_path / ".kollab"
    home.mkdir()
    folder = tmp_path / "checkout"
    folder.mkdir()
    (folder / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "settings": "settings.json"}))
    (folder / "settings.json").write_text(
        json.dumps({"widgets": [{"type": "checkbox", "config_path": "sample.enabled", "default": True}]})
    )
    (home / "plugins.json").write_text(json.dumps({"plugins": {}, "dev": {"hello": str(folder)}}))
    builtin = tmp_path / "builtins"
    builtin.mkdir()
    entry = next(row for row in plugin_entries(builtin) if row["id"] == "hello@dev")
    assert entry["settings"][0]["config_path"] == "sample.enabled"
    save("hello@dev", {"sample.enabled": False})
    assert config_value("plugin-values.hello@dev.sample.enabled") is False
    assert json.loads((home / "plugin-data/hello@dev/values.json").read_text()) == {"sample.enabled": False}
