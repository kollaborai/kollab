"""Version detection for Kollab."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Optional


def _get_version_from_pyproject() -> Optional[str]:
    """Read version from pyproject.toml for development mode."""
    try:
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text()
            for line in content.splitlines():
                if line.startswith("version ="):
                    # Extract version from: version = "0.4.10"
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _is_running_from_source() -> bool:
    """Check if running from source (development) vs installed package."""
    try:
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        return pyproject_path.exists()
    except Exception:
        return False


def get_kollabor_version() -> str:
    """Get Kollabor version from appropriate source.

    Returns version from pyproject.toml when running from source,
    otherwise returns installed package version.
    """
    if _is_running_from_source():
        pyproject_ver = _get_version_from_pyproject()
        if pyproject_ver is not None:
            return pyproject_ver
        return "0.0.0-dev"
    try:
        return get_version("kollab")
    except PackageNotFoundError:
        return "0.0.0-installed"


__version__ = get_kollabor_version()
