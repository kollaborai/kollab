"""An add must not replace a concurrent installation after approval starts."""

import json

import pytest

from kollabor_plugins import cli, packages


def test_concurrent_add_refuses_to_replace_first_install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    def stage(source, destination, version):
        destination.mkdir()
        manifest = {"id": "hello", "name": "Second", "uses": []}
        (destination / "kollab-plugin.json").write_text(json.dumps(manifest))
        return {
            "source": source,
            "ref": None,
            "commit": "a" * 40,
            "files_sha256": packages.files_sha256(destination),
            "manifest": manifest,
        }

    def approve(_):
        other = tmp_path / "first-stage"
        other.mkdir()
        manifest = {"id": "hello", "name": "First", "uses": []}
        (other / "kollab-plugin.json").write_text(json.dumps(manifest))
        staged = {
            "source": "https://first.example/repo",
            "ref": None,
            "commit": "b" * 40,
            "files_sha256": packages.files_sha256(other),
            "manifest": manifest,
        }
        packages.install_stage(other, staged, staged["commit"], staged["files_sha256"])
        return "yes"

    monkeypatch.setattr(cli, "stage_git_source", stage)
    monkeypatch.setattr("builtins.input", approve)
    with pytest.raises(ValueError, match="already installed; use update"):
        cli.main(["add", "https://example.invalid/repo"])
    home = tmp_path / ".kollab"
    assert json.loads((home / "plugins/hello/kollab-plugin.json").read_text())["name"] == "First"
    assert json.loads((home / "plugins.json").read_text())["plugins"]["hello"]["commit"] == "b" * 40
