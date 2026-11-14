"""An attached dev checkout replaces, rather than doubles, its installed peer."""

import json


def test_dev_checkout_shadows_installed_package(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins import PluginRegistry
    from kollabor_plugins.packages import files_sha256

    home = tmp_path / "home" / ".kollab"
    installed = home / "plugins" / "hello"
    installed.mkdir(parents=True)
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    dev = tmp_path / "dev"
    dev.mkdir()
    marker = tmp_path / "installed-executed"
    (installed / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "python": "entry.py:InstalledPlugin"}))
    (installed / "entry.py").write_text(
        f"open({str(marker)!r},'w').close()\nfrom kollabor_plugins import BasePlugin\nclass InstalledPlugin(BasePlugin): pass\n"
    )
    (dev / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "python": "entry.py:DevPlugin"}))
    (dev / "entry.py").write_text("from kollabor_plugins import BasePlugin\nclass DevPlugin(BasePlugin): pass\n")
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(installed)}},
                "dev": {"hello": str(dev)},
            }
        )
    )
    registry = PluginRegistry(bundled)
    registry.load_all_plugins()
    assert "DevPlugin" in registry.list_plugins()
    assert "InstalledPlugin" not in registry.list_plugins()
    assert not marker.exists()
