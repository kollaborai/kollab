"""Version detection for Kollab."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Optional

from .build_info import (
    BuildProvenance,
    is_exact_clean_release_tag,
    read_embedded_provenance,
    resolve_build_provenance,
)

# Read as data, never imported: the generated module is parsed by the AST literal reader.
_EMBEDDED_PROVENANCE: BuildProvenance | None = read_embedded_provenance(
    Path(__file__).with_name("_build_info.py")
)


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
    """Return the canonical installed or source-checkout version.

    A source checkout keeps the clean version only at an exact, clean matching
    release tag; all other source states include a local revision suffix.
    Installed releases continue to use their package metadata version.
    """
    if _is_running_from_source():
        pyproject_ver = _get_version_from_pyproject()
        source_version = pyproject_ver or "0.0.0-dev"
        source_root = Path(__file__).resolve().parent.parent
        if pyproject_ver is not None and is_exact_clean_release_tag(
            source_root, pyproject_ver
        ):
            return pyproject_ver
        provenance = resolve_build_provenance(
            source_root,
            inherited=_EMBEDDED_PROVENANCE,
        )
        return f"{source_version}+dev.g{provenance.short_sha}"
    try:
        return get_version("kollab")
    except PackageNotFoundError:
        return "0.0.0-installed"


def get_build_provenance() -> BuildProvenance:
    """Return build identity from the live source checkout or built artifact."""

    if _is_running_from_source():
        source_root = Path(__file__).resolve().parent.parent
        return resolve_build_provenance(
            source_root,
            inherited=_EMBEDDED_PROVENANCE,
        )
    if _EMBEDDED_PROVENANCE is not None:
        return _EMBEDDED_PROVENANCE
    return resolve_build_provenance()


__version__ = get_kollabor_version()
