"""Regression tests for local development entrypoint import paths."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_python_main_prefers_workspace_packages() -> None:
    script = r"""
import runpy
from pathlib import Path

root = Path.cwd().resolve()
runpy.run_path(str(root / "main.py"), run_name="kollab_entrypoint_test")

import kollabor_config.config_utils as config_utils

actual = Path(config_utils.__file__).resolve()
expected = (
    root
    / "packages"
    / "kollabor-config"
    / "src"
    / "kollabor_config"
    / "config_utils.py"
).resolve()

assert actual == expected, f"expected {expected}, got {actual}"
assert hasattr(config_utils, "get_existing_global_config_path")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
