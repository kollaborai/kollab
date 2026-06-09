"""Regression tests for local development entrypoint import paths."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run_entrypoint_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


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

    result = _run_entrypoint_script(script)

    assert result.returncode == 0, result.stderr or result.stdout


def test_cli_entrypoint_skips_installed_site_packages_without_workspace() -> None:
    script = r"""
import importlib.util
import sys
import tempfile
from pathlib import Path

root = Path.cwd().resolve()
spec = importlib.util.spec_from_file_location(
    "kollabor_cli_main_under_test",
    root / "kollabor_cli_main.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as tmp:
    installed_root = Path(tmp).resolve()
    original_path = ["existing-path"]

    sys.path[:] = original_path.copy()
    module._prepend_dev_workspace_paths(installed_root)

    assert sys.path == original_path, sys.path
"""

    result = _run_entrypoint_script(script)

    assert result.returncode == 0, result.stderr or result.stdout


def test_cli_entrypoint_prepends_source_workspace_packages() -> None:
    script = r"""
import importlib.util
import sys
import tempfile
from pathlib import Path

root = Path.cwd().resolve()
spec = importlib.util.spec_from_file_location(
    "kollabor_cli_main_under_test",
    root / "kollabor_cli_main.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as tmp:
    source_root = Path(tmp).resolve()
    agent_src = source_root / "packages" / "kollabor-agent" / "src"
    config_src = source_root / "packages" / "kollabor-config" / "src"
    agent_src.mkdir(parents=True)
    config_src.mkdir(parents=True)

    sys.path[:] = ["existing-path"]
    module._prepend_dev_workspace_paths(source_root)

    expected = [str(source_root), str(agent_src), str(config_src)]
    assert sys.path[:3] == expected, sys.path
    assert sys.path[3:] == ["existing-path"], sys.path
"""

    result = _run_entrypoint_script(script)

    assert result.returncode == 0, result.stderr or result.stdout
