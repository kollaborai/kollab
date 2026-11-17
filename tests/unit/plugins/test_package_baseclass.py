"""Manifest Python entries must use the existing plugin lifecycle contract."""

import json


def test_non_base_plugin_class_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins import PluginRegistry
    from kollabor_plugins.packages import files_sha256

    bundled = tmp_path / "bundled"
    bundled.mkdir()
    root = tmp_path / ".kollab" / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "python": "entry.py:NotPlugin"}))
    (root / "entry.py").write_text("class NotPlugin: pass\n")
    registry_file = tmp_path / ".kollab" / "plugins.json"
    registry_file.write_text(
        json.dumps({"plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root)}}, "dev": {}})
    )
    registry = PluginRegistry(bundled)
    registry.load_all_plugins()
    assert "NotPlugin" not in registry.list_plugins()
    assert "hello" in registry.package_errors
