"""Only approved, enabled, unchanged package entries may be imported."""

import json


def test_package_discovery_enforces_enable_and_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins import PluginRegistry
    from kollabor_plugins.packages import files_sha256

    builtin = tmp_path / "builtins"
    builtin.mkdir()
    root = tmp_path / ".kollab" / "plugins" / "hello"
    root.mkdir(parents=True)
    marker = tmp_path / "executed"
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "python": "entry.py:HelloPlugin"}))
    (root / "entry.py").write_text(
        "from kollabor_plugins import BasePlugin\n"
        f"open({str(marker)!r}, 'w').close()\n"
        "class HelloPlugin(BasePlugin): pass\n"
    )
    record = {"enabled": False, "files_sha256": files_sha256(root)}
    registry_file = tmp_path / ".kollab" / "plugins.json"

    def write_registry():
        registry_file.write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))

    write_registry()
    registry = PluginRegistry(builtin)
    registry.load_all_plugins()
    assert "HelloPlugin" not in registry.list_plugins()
    assert not marker.exists()
    record["enabled"] = True
    write_registry()
    registry = PluginRegistry(builtin)
    registry.load_all_plugins()
    assert "HelloPlugin" in registry.list_plugins()
    assert marker.exists()
    marker.unlink()
    (root / "entry.py").write_text("raise RuntimeError('should not execute')")
    registry = PluginRegistry(builtin)
    registry.load_all_plugins()
    assert "HelloPlugin" not in registry.list_plugins()
    assert not marker.exists()
