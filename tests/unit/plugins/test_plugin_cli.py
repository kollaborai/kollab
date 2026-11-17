"""Plugin management dispatches before starting the interactive application."""

import json
import sys


def test_plugin_list_dispatches_headlessly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    home.mkdir()
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": True}}, "dev": {}}))
    from kollabor import cli

    monkeypatch.setattr(sys, "argv", ["kollab", "plugin", "list"])
    monkeypatch.setattr(cli, "_update_before_launch", lambda: (_ for _ in ()).throw(AssertionError("launched app")))
    cli.cli_main()
    assert "hello" in capsys.readouterr().out
