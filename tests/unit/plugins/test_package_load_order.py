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


def _legacy_home(tmp_path, monkeypatch, ids, enabled=None):
    """plugins.json written before enable_order existed: enabled plugins carry no rank."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.chdir(tmp_path)
    from kollabor_plugins.packages import files_sha256

    (tmp_path / "bundled").mkdir()
    records = {}
    for plugin_id in ids:
        symbol = plugin_id.capitalize() + "Plugin"
        root = tmp_path / ".kollab" / "plugins" / plugin_id
        root.mkdir(parents=True)
        (root / "kollab-plugin.json").write_text(json.dumps({"id": plugin_id, "python": f"entry.py:{symbol}"}))
        source = f"from kollabor_plugins import BasePlugin\nclass {symbol}(BasePlugin):\n    pass\n"
        (root / "entry.py").write_text(source)
        on = True if enabled is None else plugin_id in enabled
        records[plugin_id] = {"source": "https://example.invalid/repo", "ref": "main", "commit": "a" * 40,
                              "enabled": on, "granted": [], "files_sha256": files_sha256(root)}
    (tmp_path / ".kollab" / "plugins.json").write_text(json.dumps({"plugins": records, "dev": {}}))
    return tmp_path / ".kollab" / "plugins.json"


def _load_order(tmp_path):
    from kollabor_plugins import PluginRegistry

    registry = PluginRegistry(tmp_path / "bundled")
    registry.load_all_plugins()
    return [name for name in registry.discovery.loaded_classes if name.endswith("Plugin") and name != "BasePlugin"]


def test_updating_one_legacy_plugin_keeps_the_existing_winner(tmp_path, monkeypatch):
    registry_path = _legacy_home(tmp_path, monkeypatch, ["alpha", "beta"])
    from kollabor_plugins import packages

    assert _load_order(tmp_path) == ["AlphaPlugin", "BetaPlugin"]
    record = json.loads(registry_path.read_text())["plugins"]["beta"]
    folder = tmp_path / "update"
    folder.mkdir()
    (folder / "kollab-plugin.json").write_text(json.dumps({"id": "beta", "python": "entry.py:BetaPlugin"}))
    (folder / "entry.py").write_text((tmp_path / ".kollab" / "plugins" / "beta" / "entry.py").read_text())
    info = {"source": record["source"], "ref": "main", "commit": "b" * 40,
            "files_sha256": packages.files_sha256(folder), "manifest": {"id": "beta", "uses": []}}
    packages.install_stage(folder, info, info["commit"], info["files_sha256"])
    order = json.loads(registry_path.read_text())["enable_order"]
    assert order["alpha"] < order["beta"]


def test_enabling_a_plugin_in_a_legacy_registry_ranks_it_last(tmp_path, monkeypatch):
    registry_path = _legacy_home(tmp_path, monkeypatch, ["alpha", "beta", "gamma"], enabled={"alpha", "beta"})
    from kollabor_plugins.settings import set_enabled

    set_enabled("gamma", True)
    order = json.loads(registry_path.read_text())["enable_order"]
    assert order["alpha"] < order["beta"] < order["gamma"]
