"""--version and --help name the program as launched, on every Python (3.14 changed argparse's default)."""

import contextlib
import io
import sys
from unittest import mock

import pytest

from kollabor.cli import __version__, parse_arguments


def test_version_names_the_launched_file_not_python():
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["/Users/u/.local/bin/kollab", "--version"]):
        with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--version"])
    assert out.getvalue().strip() == f"kollab {__version__}"
