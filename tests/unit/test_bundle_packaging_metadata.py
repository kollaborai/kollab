"""Packaging metadata must include complete bundled skill assets."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_BUNDLE_EXTENSIONS = {".py", ".xml", ".xsd", ".html", ".txt"}
PYPROJECTS = [
    ROOT / "pyproject.toml",
    *sorted((ROOT / "packages").glob("*/pyproject.toml")),
]


def _pattern_covers_extension(pattern: str, extension: str) -> bool:
    return pattern in {"*", "**/*"} or pattern.endswith(f"*{extension}")


def test_setuptools_package_data_covers_skill_scripts_and_schemas() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    bundle_patterns = pyproject["tool"]["setuptools"]["package-data"]["bundles"]

    missing = [
        extension
        for extension in REQUIRED_BUNDLE_EXTENSIONS
        if not any(
            _pattern_covers_extension(pattern, extension) for pattern in bundle_patterns
        )
    ]

    assert not missing


def test_manifest_covers_skill_scripts_and_schemas_for_sdist() -> None:
    manifest_lines = [
        line.strip()
        for line in (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    bundle_lines = [
        line for line in manifest_lines if line.startswith("recursive-include bundles ")
    ]

    missing = [
        extension
        for extension in REQUIRED_BUNDLE_EXTENSIONS
        if not any(line == "recursive-include bundles *" for line in bundle_lines)
        and not any(f"*{extension}" in line.split() for line in bundle_lines)
    ]

    assert not missing


def test_pyproject_license_metadata_uses_spdx_strings() -> None:
    for pyproject_path in PYPROJECTS:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        license_value = pyproject["project"].get("license")
        classifiers = pyproject["project"].get("classifiers", [])

        assert license_value in (None, "MIT"), pyproject_path
        assert "License :: OSI Approved :: MIT License" not in classifiers
