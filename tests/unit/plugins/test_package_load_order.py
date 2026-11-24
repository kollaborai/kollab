"""Installed plugins load in enable order, so the earliest-enabled one wins command conflicts (#1028)."""

import json


def test_packages_load_in_enable_order_not_registry_order(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins import PluginRegistry
    from kollabor_plugins.packages import files_sha256

    (tmp_path / "bundled").mkdir()
    records = {}
    for plugin_id, symbol in (("alpha", "AlphaPlugin"), ("beta", "BetaPlugin")):
        root = tmp_path / ".kollab" / "plugins" / plugin_id
        root.mkdir(parents=True)
        (root / "kollab-plugin.json").write_text(json.dumps({"id": plugin_id, "python": f"entry.py:{symbol}"}))
        source = f"from kollabor_plugins import BasePlugin\nclass {symbol}(BasePlugin):\n    pass\n"
        (root / "entry.py").write_text(source)
        records[plugin_id] = {"enabled": True, "files_sha256": files_sha256(root)}
    # alpha is first in the file, but beta was enabled first.
    (tmp_path / ".kollab" / "plugins.json").write_text(json.dumps(
        {"plugins": records, "dev": {}, "enable_order": {"beta": 1, "alpha": 2}, "enable_counter": 2}
    ))
    registry = PluginRegistry(tmp_path / "bundled")
    registry.load_all_plugins()
    loaded = [name for name in registry.discovery.loaded_classes if name in {"AlphaPlugin", "BetaPlugin"}]
    assert loaded == ["BetaPlugin", "AlphaPlugin"]
