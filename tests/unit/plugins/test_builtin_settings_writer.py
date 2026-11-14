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


def test_shared_config_writer_preserves_concurrent_unrelated_edits(builtin_config, monkeypatch):
    import threading
    import time

    from kollabor_config import service as config_service

    first_writing = threading.Event()
    second_started = threading.Event()
    original = config_service.write_text_atomic

    def slow_first_write(path, text):
        if threading.current_thread().name == "first":
            first_writing.set()
            assert second_started.wait(2)
            time.sleep(0.1)
        return original(path, text)

    monkeypatch.setattr(config_service, "write_text_atomic", slow_first_write)
    first = config_service.ConfigService(builtin_config, fast_mode=True, initialize=False)
    second = config_service.ConfigService(builtin_config, fast_mode=True, initialize=False)
    outcomes = []

    def write_first():
        outcomes.append(first.save_key("plugins.example.mode", "new", save_target="global"))

    def write_second():
        assert first_writing.wait(2)
        second_started.set()
        outcomes.append(second.save_key("other", 8, save_target="global"))

    threads = [threading.Thread(target=write_first, name="first"), threading.Thread(target=write_second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2 and all(outcomes)
    result = json.loads(builtin_config.read_text())
    assert result["other"] == 8
    assert result["plugins"]["example"]["mode"] == "new"


def test_config_panel_routes_builtin_changes_through_plugin_writer(builtin_config, monkeypatch):
    from unittest.mock import Mock

    from kollabor.panels.config import apply_config_changes
    from kollabor_config import config_utils
    from kollabor_config import service as config_service

    monkeypatch.setattr(config_utils, "get_local_config_path", lambda: builtin_config)
    monkeypatch.setattr(config_service, "get_local_config_path", lambda: builtin_config)
    service = config_service.ConfigService(builtin_config, fast_mode=True, initialize=False)
    service.save_key = Mock(wraps=service.save_key)
    assert apply_config_changes(service, {
        "plugins.example.mode": "panel",
        "plugins.example.enabled": False,
        "plugins.example.secret": "panel-secret",
        "other": 9,
    }, "local")
    result = json.loads(builtin_config.read_text())
    assert result["plugins"]["example"] == {
        "mode": "panel", "enabled": False, "secret": "panel-secret",
    }
    service.save_key.assert_called_once_with("other", 9, save_target="local")


def test_builtin_writer_rejects_invalid_config_without_replacing_it(builtin_config):
    builtin_config.write_text('{"broken":')
    with pytest.raises(ValueError, match="Invalid config file"):
        settings.save("example", {"plugins.example.mode": "new"})
    assert builtin_config.read_text() == '{"broken":'


def test_builtin_enable_without_widget_respects_managed_config(builtin_config, monkeypatch):
    from kollabor.panels import config as config_panel
    from kollabor_config.managed_config import ManagedConfig
    from kollabor_plugins import registry

    monkeypatch.setattr(registry, "plugin_entries", lambda: [
        {"id": "example", "kind": "built-in", "settings": []},
    ])
    managed = ManagedConfig("key", "primary", keys=(("plugins", "example", "enabled"),))
    monkeypatch.setattr(config_panel, "read_managed_state", lambda: (managed, {}))
    before = builtin_config.read_text()
    with pytest.raises(ValueError):
        settings.set_enabled("example", False)
    assert builtin_config.read_text() == before


def test_builtin_settings_save_is_one_atomic_config_replacement(builtin_config, monkeypatch):
    from kollabor_config import service as config_service

    original = config_service.write_text_atomic
    writes = []

    def record_write(path, text):
        writes.append(path)
        return original(path, text)

    monkeypatch.setattr(config_service, "write_text_atomic", record_write)
    settings.save("example", {
        "plugins.example.mode": "new",
        "plugins.example.secret": "new-secret",
    }, allow_secrets=True)
    assert writes == [builtin_config]


def test_builtin_save_wont_replace_conflicting_config_sections(builtin_config):
    original = '{"plugins": "do-not-replace", "other": 7}'
    builtin_config.write_text(original)
    with pytest.raises(OSError):
        settings.save("example", {"plugins.example.mode": "new"})
    assert builtin_config.read_text() == original
