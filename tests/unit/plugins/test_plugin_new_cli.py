"""Scaffolding creates a local plugin without overwriting existing files."""
import json

import pytest

from kollabor_plugins.cli import main
from kollabor_plugins.packages import validate_manifest


def test_new_copies_template_with_requested_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert main(["new", "my-plugin"]) == 0
    root = tmp_path / "my-plugin"
    assert validate_manifest(root, "0.23.0")["id"] == "my-plugin"
    assert (root / "web" / "dist" / "index.js").is_file()
    with pytest.raises((FileExistsError, ValueError)):
        main(["new", "my-plugin"])
    assert json.loads((root / "kollab-plugin.json").read_text())["id"] == "my-plugin"


@pytest.mark.parametrize("plugin_id", ["x", "../escape", "Uppercase", "space here"])
def test_new_rejects_bad_ids(tmp_path, monkeypatch, plugin_id):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with pytest.raises(ValueError):
        main(["new", plugin_id])
