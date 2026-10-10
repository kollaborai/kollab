"""The single-file kollab binary: is this process running from one, and how to start it again.

Releases ship kollab as one executable per platform (scripts/build_binary.py): a
pex scie that unpacks a portable Python and runs kollab from a pex venv. Code
that starts kollab again (re-execs, service units) must start the binary, not
that venv's interpreter, or an upgraded binary keeps running the old code.
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


def kollab_argv() -> list[str]:
    """The argv prefix that starts this same kollab again."""
    binary = running_binary()
    if binary is not None:
        return [str(binary)]
    return [sys.executable, "-m", "kollabor_cli_main"]
