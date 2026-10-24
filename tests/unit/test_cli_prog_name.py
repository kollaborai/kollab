"""--version preserves its executable/version line and exposes build identity."""

import contextlib
import io
import sys
from unittest import mock

import pytest

from kollabor.build_info import BuildProvenance
from kollabor.cli import __version__, parse_arguments
from kollabor.version import get_build_provenance


def _version_lines(
    monkeypatch: pytest.MonkeyPatch, provenance: BuildProvenance | None = None
) -> list[str]:
    if provenance is not None:
        monkeypatch.setattr("kollabor.version._EMBEDDED_PROVENANCE", provenance)
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["/Users/u/.local/bin/kollab", "--version"]):
        with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--version"])
    return out.getvalue().strip().splitlines()


def test_version_names_the_launched_file_and_keeps_version_as_final_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = get_build_provenance()

    lines = _version_lines(monkeypatch)

    assert lines == [f"Build: {provenance.display}", f"kollab {__version__}"]
    assert lines[-1].split()[-1] == __version__


def test_version_escapes_argparse_format_characters_in_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = BuildProvenance("feature/a%production", "abcdef01" * 5)

    lines = _version_lines(monkeypatch, provenance)

    assert lines == ["Build: feature/a%production @ abcdef01", f"kollab {__version__}"]
