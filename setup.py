#!/usr/bin/env python3
"""Setuptools hooks that preserve source provenance in built artifacts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
_BUILD_INFO = Path("kollabor") / "_build_info.py"

# Build isolation does not put the project itself on sys.path yet. Load the
# standalone helper by path so the hook can embed metadata before installation.
_build_spec = importlib.util.spec_from_file_location(
    "_kollabor_build_info", ROOT / "kollabor" / "build_info.py"
)
if _build_spec is None or _build_spec.loader is None:
    raise RuntimeError("cannot load kollabor/build_info.py for packaging")
_build_module = importlib.util.module_from_spec(_build_spec)
sys.modules[_build_spec.name] = _build_module
_build_spec.loader.exec_module(_build_module)

read_embedded_provenance = _build_module.read_embedded_provenance
resolve_build_provenance = _build_module.resolve_build_provenance
write_embedded_provenance = _build_module.write_embedded_provenance


def _capture_build_provenance():
    inherited = read_embedded_provenance(ROOT / _BUILD_INFO)
    return resolve_build_provenance(ROOT, inherited=inherited)


class BuildPyWithProvenance(build_py):
    """Embed provenance in the package copied into wheels and binaries."""

    def run(self) -> None:
        super().run()
        write_embedded_provenance(
            Path(self.build_lib) / _BUILD_INFO, _capture_build_provenance()
        )


class SdistWithProvenance(sdist):
    """Carry checkout provenance into source archives for later wheel builds."""

    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        super().make_release_tree(base_dir, files)
        write_embedded_provenance(
            Path(base_dir) / _BUILD_INFO, _capture_build_provenance()
        )


setup(cmdclass={"build_py": BuildPyWithProvenance, "sdist": SdistWithProvenance})
