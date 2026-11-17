"""Confirmed update and removal operate on enabled idle plugins."""

import json

from kollabor_plugins import cli
from tests.unit.plugins.test_plugin_remove_cli import setup as removal_fixture
from tests.unit.plugins.test_plugin_update_cli import fixture as update_fixture


def enable(home):
    path = home / "plugins.json"
    data = json.loads(path.read_text())
    data["plugins"]["hello"]["enabled"] = True
    path.write_text(json.dumps(data))


def test_update_preserves_enabled_state(tmp_path, monkeypatch):
    home, root = update_fixture(tmp_path, monkeypatch)
    enable(home)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["update", "hello"]) == 0
    assert (root / "new.txt").exists()
    assert json.loads((home / "plugins.json").read_text())["plugins"]["hello"]["enabled"] is True


def test_remove_enabled_preserves_data(tmp_path, monkeypatch):
    home, root, data = removal_fixture(tmp_path, monkeypatch)
    enable(home)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["remove", "hello"]) == 0
    assert not root.exists() and data.exists()
