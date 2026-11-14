"""Vendored modules must be usable without sharing names across plugins."""

import json
import sys
import types

from kollabor_plugins import PluginRegistry
from kollabor_plugins.packages import files_sha256


def test_vendored_modules_load_and_clashes_are_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    home = tmp_path / ".kollab"
    builtin = tmp_path / "builtins"
    builtin.mkdir()
    records = {}
    for plugin_id, vendor_name, symbol in (
        ("good", "unique_test_vendor_1028", "GoodPlugin"),
        ("bad", "clashing_test_vendor_1028", "BadPlugin"),
    ):
        root = home / "plugins" / plugin_id
        vendor = root / "python" / "vendor"
        vendor.mkdir(parents=True)
        (vendor / f"{vendor_name}.py").write_text("VALUE = 42\n")
        (root / "kollab-plugin.json").write_text(json.dumps({"id": plugin_id, "python": f"python/entry.py:{symbol}"}))
        (root / "python" / "entry.py").write_text(
            f"import {vendor_name}\nfrom kollabor_plugins import BasePlugin\nclass {symbol}(BasePlugin):\n    value = {vendor_name}.VALUE\n"
        )
        records[plugin_id] = {"enabled": True, "files_sha256": files_sha256(root)}
    (home / "plugins.json").write_text(json.dumps({"plugins": records, "dev": {}}))
    monkeypatch.setitem(sys.modules, "clashing_test_vendor_1028", types.ModuleType("clashing_test_vendor_1028"))
    registry = PluginRegistry(builtin)
    registry.load_all_plugins()
    assert registry.discovery.loaded_classes["GoodPlugin"].value == 42
    assert "BadPlugin" not in registry.discovery.loaded_classes
    assert "clashing_test_vendor_1028" in registry.package_errors["bad"]
