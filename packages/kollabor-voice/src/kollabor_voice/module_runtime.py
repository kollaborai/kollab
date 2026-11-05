"""Launch voice package modules without importing the host interpreter's packages."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_MODULE_BOOTSTRAP = """\
import importlib.util, runpy, sys
package_dir, module_name = sys.argv[1:3]
spec = importlib.util.spec_from_file_location(
    "kollabor_voice", package_dir + "/__init__.py",
    submodule_search_locations=[package_dir],
)
package = importlib.util.module_from_spec(spec)
sys.modules["kollabor_voice"] = package
spec.loader.exec_module(package)
sys.argv = [module_name, *sys.argv[3:]]
runpy.run_module(module_name, run_name="__main__")
"""


def module_command(python: str | Path, module_name: str, *args: object) -> list[str]:
    """Run a Kollab voice module from its exact package directory under Python -I."""
    return [
        str(python),
        "-I",
        "-c",
        _MODULE_BOOTSTRAP,
        str(_PACKAGE_DIR),
        module_name,
        *(str(arg) for arg in args),
    ]


def isolated_env(source: Mapping[str, str]) -> dict[str, str]:
    """Preserve caller settings while preventing inherited Python import paths."""
    env = dict(source)
    env["PYTHONPATH"] = ""
    return env
