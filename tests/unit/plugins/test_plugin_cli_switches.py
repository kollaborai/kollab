"""Plugin management requires local, explicit terminal approval."""

from unittest.mock import Mock

import pytest

from kollabor_plugins import cli, settings


@pytest.mark.parametrize("action,enabled", [("enable", True), ("disable", False)])
def test_switch_confirms_and_calls_writer(tmp_path, monkeypatch, action, enabled):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    writer = Mock()
    monkeypatch.setattr(settings, "set_enabled", writer)
    assert cli.main([action, "hello"]) == 0
    writer.assert_called_once_with("hello", enabled)


@pytest.mark.parametrize("argv", [["enable", "hello"], ["disable", "hello"], ["dev", "off", "hello"]])
def test_management_refuses_noninteractive(tmp_path, monkeypatch, argv):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="interactive"):
        cli.main(argv)
