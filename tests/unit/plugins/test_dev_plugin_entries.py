"""The common list exposes the dev entry and its shadowed installed peer."""

import json


def test_dev_entries_show_shadowed_install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.registry import plugin_entries

    builtins = tmp_path / "builtins"
    builtins.mkdir()
    installed = tmp_path / ".kollab" / "plugins" / "hello"
    installed.mkdir(parents=True)
    dev = tmp_path / "checkout"
    dev.mkdir()
    manifest = {
        "format": 1,
        "id": "hello",
        "name": "Hello",
        "version": "1.0",
        "description": "Example",
        "kollab": ">=0.1",
        "uses": [],
    }
    (installed / "kollab-plugin.json").write_text(json.dumps(manifest))
    (dev / "kollab-plugin.json").write_text(json.dumps(manifest))
    (tmp_path / ".kollab" / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(installed)}},
                "dev": {"hello": str(dev)},
            }
        )
    )
    rows = plugin_entries(builtins)
    original = next(row for row in rows if row["kind"] == "installed" and row["id"] == "hello")
    development = next(row for row in rows if row["kind"] == "dev" and row["id"] == "hello@dev")
    assert original["shadowed"] is True
    assert development["source"] == str(dev)
    assert development["enabled"] is True
