"""Removing an installed plugin preserves data unless separately confirmed."""

import json

import pytest

from kollabor_plugins import cli


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "name": "Hello", "services": {}}))
    data = home / "plugin-data" / "hello"
    data.mkdir(parents=True)
    (data / "values.json").write_text('{"important":true}')
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": False}}, "dev": {}}))
    return home, root, data


def test_remove_retains_data(tmp_path, monkeypatch):
    home, root, data = setup(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["remove", "hello"]) == 0
    assert not root.exists()
    assert (data / "values.json").exists()
    assert "hello" not in json.loads((home / "plugins.json").read_text())["plugins"]


def test_purge_needs_second_confirmation(tmp_path, monkeypatch):
    home, root, data = setup(tmp_path, monkeypatch)
    answers = iter(["yes", "no"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert cli.main(["remove", "hello", "--purge"]) == 1
    assert root.exists() and data.exists()
    assert "hello" in json.loads((home / "plugins.json").read_text())["plugins"]


def test_purge_deletes_data_when_confirmed(tmp_path, monkeypatch):
    _, root, data = setup(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.main(["remove", "hello", "--purge"]) == 0
    assert not root.exists() and not data.exists()


def test_remove_requires_terminal(tmp_path, monkeypatch):
    _, root, data = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="interactive"):
        cli.main(["remove", "hello"])
    assert root.exists() and data.exists()
