"""Development entries report approved grants, not later manifest requests."""

import json

from kollabor_plugins import cli
from kollabor_plugins.registry import plugin_entries


def test_dev_grants_do_not_follow_unapproved_manifest_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    root = tmp_path / "source"
    root.mkdir()
    manifest = {
        "format": 1,
        "id": "hello",
        "name": "Hello",
        "version": "1.0.0",
        "description": "Example",
        "kollab": ">=0.1",
        "uses": [],
    }
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    assert cli.main(["dev", str(root)]) == 0
    manifest["uses"] = ["agents"]
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    builtin = tmp_path / "builtins"
    builtin.mkdir()
    entry = next(e for e in plugin_entries(builtin) if e["id"] == "hello@dev")
    assert entry["uses"] == ["agents"]
    assert entry["granted"] == []
