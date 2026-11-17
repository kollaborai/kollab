"""Management commands report successful enable/disable changes."""
from unittest.mock import Mock

import pytest

from kollabor_plugins import cli, settings


@pytest.mark.parametrize("action,expected", [("enable", "Enabled hello"), ("disable", "Disabled hello")])
def test_switch_reports_success(tmp_path, monkeypatch, capsys, action, expected):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    monkeypatch.setattr(settings, "set_enabled", Mock())
    assert cli.main([action, "hello"]) == 0
    assert expected in capsys.readouterr().out
