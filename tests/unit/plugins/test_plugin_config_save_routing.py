"""Installed plugin settings must not be persisted to ordinary config.json."""

from unittest.mock import Mock

from kollabor_tui.config_merger import ConfigMerger


def test_plugin_paths_use_plugin_writers(monkeypatch):
    from kollabor_plugins import settings

    save = Mock()
    enabled = Mock()
    monkeypatch.setattr(settings, "save", save)
    monkeypatch.setattr(settings, "set_enabled", enabled)
    service = Mock()
    service.set.return_value = True
    service.save_config.return_value = True
    changes = {
        "plugin-values.hello.greeting": "hello",
        "plugin-values.hello.nested.value": 7,
        "plugin-registry.hello.enabled": False,
        "ordinary.option": 2,
    }
    assert ConfigMerger.apply_widget_changes(service, changes)
    save.assert_called_once_with("hello", {"greeting": "hello", "nested.value": 7})
    enabled.assert_called_once_with("hello", False)
    service.set.assert_called_once_with("ordinary.option", 2)


def test_plugin_writer_failure_returns_false(monkeypatch):
    from kollabor_plugins import settings

    def fail(*args):
        raise ValueError("invalid plugin setting")

    monkeypatch.setattr(settings, "save", fail)
    service = Mock()
    service.set.return_value = True
    service.save_config.return_value = True
    assert not ConfigMerger.apply_widget_changes(service, {"plugin-values.hello.greeting": "x"})
    service.set.assert_not_called()
