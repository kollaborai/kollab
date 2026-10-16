"""The single-file kollab binary: is this process running from one, and how to start it again.

Releases ship kollab as one executable per platform (scripts/build_binary.py): a
pex scie that unpacks a portable Python and runs kollab from a pex venv. Code
that starts kollab again (re-execs, service units) must start the binary, not
that venv's interpreter, or an upgraded binary keeps running the old code.

Kollab.app on macOS ships no scie (codesign cannot sign one): its kollab is a
launcher beside an unpacked CPython (scripts/desktop_release.py), whose prefix
holds the KOLLAB-APP marker. That kollab starts again through its launcher and
never updates itself, since a write inside the app breaks its signature.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def running_binary() -> Path | None:
    """The kollab binary this process runs from, or None.

    The binary's launcher exports SCIE as its own path. Children inherit it, so
    this interpreter must also be the binary's own pex venv (marked by PEX-INFO);
    a dev checkout started from the binary's shell is not the binary.
    """
    binary = os.environ.get("SCIE")
    if binary and Path(sys.prefix, "PEX-INFO").is_file():
        return Path(binary)
    return None


def app_launcher() -> Path | None:
    """Kollab.app's kollab launcher when this interpreter is the app's runtime, or None."""
    if Path(sys.prefix, "KOLLAB-APP").is_file():
        return Path(sys.prefix).parent / "kollab"
    return None


def own_launcher() -> Path | None:
    """The executable that starts this same kollab: the binary or the app's launcher."""
    return running_binary() or app_launcher()


def kollab_argv() -> list[str]:
    """The argv prefix that starts this same kollab again."""
    launcher = own_launcher()
    if launcher is not None:
        return [str(launcher)]
    return [sys.executable, "-m", "kollabor_cli_main"]
