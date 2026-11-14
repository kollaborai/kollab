"""Loose plugins still load, while package folders never bypass approval."""

import json


def test_loose_plugin_does_not_scan_manifest_package(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins import PluginRegistry

    builtin = tmp_path / "bundled"
    builtin.mkdir()
    root = tmp_path / ".kollab" / "plugins"
    package = root / "hello"
    package.mkdir(parents=True)
    marker = tmp_path / "package-executed"
    (root / "loose_plugin.py").write_text(
        "class LoosePlugin:\n    @staticmethod\n    def get_default_config(): return {}\n"
    )
    (package / "__init__.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    (package / "bad_plugin.py").write_text(f"open({str(marker)!r}, 'w').close()\nclass BadPlugin: pass\n")
    (package / "kollab-plugin.json").write_text(json.dumps({"id": "hello"}))
    registry = PluginRegistry(builtin)
    registry.load_all_plugins()
    assert "LoosePlugin" in registry.list_plugins()
    assert "BadPlugin" not in registry.list_plugins()
    assert not marker.exists()
