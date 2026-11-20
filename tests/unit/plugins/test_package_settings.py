"""Installed package settings are validated and isolated from global config."""

import json

import pytest


def test_save_package_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.settings import save

    home = tmp_path / ".kollab"
    package = home / "plugins" / "hello"
    package.mkdir(parents=True)
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": True}}, "dev": {}}))
    (package / "kollab-plugin.json").write_text(json.dumps({"settings": "settings.json"}))
    (package / "settings.json").write_text(
        json.dumps(
            {
                "title": "Hello",
                "widgets": [
                    {"type": "spinbox", "config_path": "loop.threads", "min_value": 1, "max_value": 4, "default": 1},
                    {"type": "checkbox", "config_path": "issues.build", "default": True},
                ],
            }
        )
    )
    config = home / "config.json"
    config.write_text('{"unchanged": true}\n')
    save("hello", {"loop.threads": 3})
    save("hello", {"issues.build": False})
    values = home / "plugin-data" / "hello" / "values.json"
    assert json.loads(values.read_text()) == {"loop.threads": 3, "issues.build": False}
    assert config.read_text() == '{"unchanged": true}\n'
    for invalid in ({"loop.threads": 5}, {"loop.threads": True}, {"missing": 1}, {"enabled": False}):
        with pytest.raises(ValueError):
            save("hello", invalid)
    assert json.loads(values.read_text()) == {"loop.threads": 3, "issues.build": False}
