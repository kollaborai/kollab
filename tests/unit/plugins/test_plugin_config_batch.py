import pytest


@pytest.mark.parametrize("caller", ["tui", "panel"])
def test_plugin_stop_failure_does_not_drop_other_batch_changes(caller, monkeypatch):
    from kollabor_plugins import services, settings

    attempts = []

    def set_enabled(plugin_id, enabled, **kwargs):
        attempts.append((plugin_id, enabled))
        if plugin_id == "a":
            raise services.ServiceStopError("worker did not stop")

    monkeypatch.setattr(settings, "set_enabled", set_enabled)

    class Config:
        def __init__(self):
            self.values = {}

        def backup_config(self, *_args):
            return None

        def set(self, path, value):
            self.values[path] = value
            return True

        def save_config(self, **_kwargs):
            return True

        def save_key(self, path, value, **_kwargs):
            self.values[path] = value
            return True

        def _notify_reload_callbacks(self):
            pass

    config = Config()
    changes = {
        "plugin-registry.a.enabled": False,
        "plugin-registry.b.enabled": False,
        "ui.theme": "dark",
    }

    if caller == "tui":
        from kollabor_tui.config_merger import ConfigMerger

        monkeypatch.setattr(ConfigMerger, "notify_plugins_config_changed", lambda *_args: None)
        result = ConfigMerger.apply_widget_changes(config, changes, "global")
    else:
        from kollabor.panels.config import apply_config_changes

        result = apply_config_changes(config, changes, "global")

    assert result is False
    assert attempts == [("a", False), ("b", False)]
    assert config.values["ui.theme"] == "dark"
