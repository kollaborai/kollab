"""Expected plugin management failures are actionable, not tracebacks."""
import sys

import pytest


def test_cli_formats_plugin_value_error(monkeypatch, capsys):
    from kollabor import cli
    from kollabor_plugins import cli as plugin_cli

    monkeypatch.setattr(sys, "argv", ["kollab", "plugin", "update", "missing"])

    def fail(argv):
        raise ValueError("Plugin is not installed")

    monkeypatch.setattr(plugin_cli, "main", fail)
    with pytest.raises(SystemExit) as exc:
        cli.cli_main()
    assert exc.value.code == 1
    output = capsys.readouterr().err
    assert "Plugin is not installed" in output
    assert "Traceback" not in output
