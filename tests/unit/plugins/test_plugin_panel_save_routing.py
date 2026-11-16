"""The active /config panel routes installed plugin values to their store."""

from unittest.mock import Mock

from kollabor.panels.config import apply_config_changes


def test_panel_uses_plugin_writers(monkeypatch):
    from kollabor_plugins import settings

    save = Mock()
    enable = Mock()
    monkeypatch.setattr(settings, "save", save)
    monkeypatch.setattr(settings, "set_enabled", enable)
    service = Mock()
    service.save_key.return_value = True
    assert apply_config_changes(
        service,
        {"plugin-values.hello.greeting": "hi", "plugin-registry.hello.enabled": False, "ordinary.value": 3},
        "local",
    )
    save.assert_called_once_with("hello", {"greeting": "hi"})
    enable.assert_called_once_with("hello", False)
    service.save_key.assert_called_once_with("ordinary.value", 3, save_target="local")


def test_panel_reports_plugin_write_failure(monkeypatch):
    from kollabor_plugins import settings

    def fail(*args):
        raise ValueError("invalid setting")

    monkeypatch.setattr(settings, "save", fail)
    service = Mock()
    assert not apply_config_changes(service, {"plugin-values.hello.greeting": "bad"}, "global")
    service.save_key.assert_not_called()
