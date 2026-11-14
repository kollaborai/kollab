"""Built-in settings use the shared plugin writer, not a route-only edit."""

import json

import pytest

from kollabor_plugins import registry, settings


@pytest.fixture
def builtin_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    path = tmp_path / ".kollab" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"plugins": {"example": {"mode": "old", "enabled": True}}, "other": 7}))

    def entries():
        return [{"id": "example", "kind": "built-in", "settings": [
            {"path": "plugins.example.mode", "type": "text_input", "editable": True},
            {"path": "plugins.example.enabled", "type": "checkbox", "editable": True},
            {"path": "plugins.example.secret", "type": "text_input", "editable": True, "secret": True},
            {"path": "plugins.example.managed", "type": "text_input", "editable": True, "managed_by": "policy"},
        ]}]

    monkeypatch.setattr(registry, "plugin_entries", entries)
    return path


def test_builtin_settings_and_enabled_use_shared_writer(builtin_config):
    settings.save("example", {"plugins.example.mode": "new"})
    settings.set_enabled("example", False)
    assert json.loads(builtin_config.read_text()) == {
        "plugins": {"example": {"mode": "new", "enabled": False}}, "other": 7,
    }


def test_builtin_settings_reject_unauthorized_or_invalid_fields(builtin_config):
    before = builtin_config.read_text()
    for values in (
        {"plugins.example.enabled": False},
        {"plugins.example.secret": "s"},
        {"plugins.example.managed": "x"},
        {"other": 1},
        {"plugins.example.mode": 3},
    ):
        with pytest.raises(ValueError):
            settings.save("example", values)
        assert builtin_config.read_text() == before
