"""Updates require approval of a pinned stage and preserve plugin state."""
import json
from pathlib import Path

import pytest

from kollabor_plugins import cli
from kollabor_plugins.packages import files_sha256


def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "version": "1.0.0"}))
    (root / "old.txt").write_text("old")
    data = home / "plugin-data" / "hello"
    data.mkdir(parents=True)
    (data / "values.json").write_text('{"message":"keep"}')
    old = {"source": "https://example.invalid/repo", "ref": "main", "commit": "a" * 40, "enabled": False, "granted": [], "files_sha256": files_sha256(root)}
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": old}, "dev": {}}))

    def stage(source, path, version):
        assert source == "https://example.invalid/repo#main"
        path.mkdir()
        manifest = {"id": "hello", "name": "Hello", "version": "2.0.0", "uses": [], "services": {}}
        (path / "kollab-plugin.json").write_text(json.dumps(manifest))
        (path / "new.txt").write_text("new")
        return {"source": old["source"], "ref": "main", "commit": "b" * 40, "files_sha256": files_sha256(path), "manifest": manifest}

    monkeypatch.setattr(cli, "stage_git_source", stage)
    return home, root


def test_update_confirms_commit_and_preserves_data(tmp_path, monkeypatch, capsys):
    home, root = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["update", "hello"]) == 0
    assert (root / "new.txt").read_text() == "new"
    assert not (root / "old.txt").exists()
    entry = json.loads((home / "plugins.json").read_text())["plugins"]["hello"]
    assert entry["commit"] == "b" * 40 and entry["enabled"] is False
    assert json.loads((home / "plugin-data" / "hello" / "values.json").read_text())["message"] == "keep"
    output = capsys.readouterr().out
    assert "a" * 40 in output and "b" * 40 in output


def test_update_rejects_registry_changed_during_confirmation(tmp_path, monkeypatch):
    home, root = fixture(tmp_path, monkeypatch)

    def confirm(prompt):
        path = home / "plugins.json"
        registry = json.loads(path.read_text())
        registry["plugins"]["hello"]["commit"] = "c" * 40
        path.write_text(json.dumps(registry))
        return "yes"

    monkeypatch.setattr("builtins.input", confirm)
    with pytest.raises(ValueError, match="changed"):
        cli.main(["update", "hello"])
    assert (root / "old.txt").exists()
    assert json.loads((home / "plugins.json").read_text())["plugins"]["hello"]["commit"] == "c" * 40


def test_update_rejects_mismatched_plugin_id(tmp_path, monkeypatch):
    _, root = fixture(tmp_path, monkeypatch)
    stage = cli.stage_git_source

    def bad_stage(source, path, version):
        result = stage(source, path, version)
        result["manifest"]["id"] = "other"
        return result

    monkeypatch.setattr(cli, "stage_git_source", bad_stage)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    with pytest.raises(ValueError, match="id"):
        cli.main(["update", "hello"])
    assert (root / "old.txt").exists()
