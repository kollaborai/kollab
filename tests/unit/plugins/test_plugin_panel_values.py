"""Installed settings shown by the terminal panel use their own store."""
import json
from unittest.mock import Mock

from kollabor.panels.config import _field


def test_panel_reads_installed_enabled_and_values_from_plugin_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".kollab"
    root.mkdir()
    (root / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": True}}, "dev": {}}))
    data = root / "plugin-data" / "hello"
    data.mkdir(parents=True)
    (data / "values.json").write_text(json.dumps({"nested.value": "saved"}))
    service = Mock()
    service.get.return_value = None
    enabled = _field({"type": "checkbox", "config_path": "plugin-registry.hello.enabled"}, service, None, {})
    setting = _field({"type": "text_input", "config_path": "plugin-values.hello.nested.value", "default": "default"}, service, None, {})
    assert enabled["value"] is True
    assert setting["value"] == "saved"
    service.get.assert_not_called()
