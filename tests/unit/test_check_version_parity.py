"""Tests for scripts/check_version_parity.py (release version-drift gate)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_version_parity.py"
spec = importlib.util.spec_from_file_location("check_version_parity", SCRIPT)
check_version_parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_version_parity)

RELEASE = "0.16.2"


def _pyproject(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[project]\nname = "{path.parent.name}"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def _package_json(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "frontend", "version": version}), encoding="utf-8")


def _tauri_conf(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"$schema": "x", "productName": "Kollab", "version": version}),
        encoding="utf-8",
    )


def _make_repo(root: Path, *, engine: str = RELEASE, packages: int = 2,
               frontend: str = RELEASE, tauri: str = RELEASE) -> Path:
    """Build a minimal repo tree with every versioned surface at a version."""
    _pyproject(root / "pyproject.toml", engine)
    for i in range(packages):
        _pyproject(root / f"packages/pkg-{i}" / "pyproject.toml", engine)
    _package_json(root / "packages/kollabor-webui/frontend/package.json", frontend)
    _tauri_conf(root / "desktop/src-tauri/tauri.conf.json", tauri)
    return root


# --- collect_versions ------------------------------------------------------


def test_collect_versions_reads_every_surface(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    versions = check_version_parity.collect_versions(root)
    assert versions["pyproject.toml (engine)"] == RELEASE
    assert versions["pkg-0 (pyproject.toml)"] == RELEASE
    assert versions["pkg-1 (pyproject.toml)"] == RELEASE
    assert versions["kollabor-webui frontend (package.json)"] == RELEASE
    assert versions["desktop tauri.conf.json"] == RELEASE
    assert len(versions) == 5


def test_collect_versions_missing_file_raises(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    (root / "desktop/src-tauri/tauri.conf.json").unlink()
    with pytest.raises(FileNotFoundError, match="tauri.conf.json"):
        check_version_parity.collect_versions(root)


def test_collect_versions_no_packages_raises(tmp_path: Path) -> None:
    root = tmp_path
    _pyproject(root / "pyproject.toml", RELEASE)
    with pytest.raises(FileNotFoundError, match="packages"):
        check_version_parity.collect_versions(root)


def test_collect_versions_tolerates_jsonc_comments(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    conf = root / "desktop/src-tauri/tauri.conf.json"
    conf.write_text('{\n  // build comment\n  "version": "0.16.2"\n}\n', encoding="utf-8")
    assert check_version_parity.collect_versions(root)["desktop tauri.conf.json"] == RELEASE


def test_collect_versions_jsonc_strip_preserves_urls_in_strings(tmp_path: Path) -> None:
    # Regression: a naive `//`-comment regex corrupts "https://..." strings.
    root = _make_repo(tmp_path)
    conf = root / "desktop/src-tauri/tauri.conf.json"
    conf.write_text(
        '{\n  "$schema": "https://schema.tauri.app/config/2", // trailing\n'
        '  "version": "0.16.2"\n}\n',
        encoding="utf-8",
    )
    assert check_version_parity.collect_versions(root)["desktop tauri.conf.json"] == RELEASE


# --- find_drift ------------------------------------------------------------


def test_find_drift_no_drift_when_all_match() -> None:
    versions = {"a": RELEASE, "b": RELEASE}
    assert check_version_parity.find_drift(RELEASE, versions) == []


def test_find_drift_reports_each_drifting_surface() -> None:
    versions = {"engine": RELEASE, "frontend": "0.1.0", "tauri": "0.1.0"}
    drift = check_version_parity.find_drift(RELEASE, versions)
    assert sorted(drift) == ["frontend: 0.1.0 != 0.16.2", "tauri: 0.1.0 != 0.16.2"]


def test_find_drift_invalid_expected_version_raises() -> None:
    with pytest.raises(ValueError, match="invalid release version"):
        check_version_parity.find_drift("not-a-version", {"a": "0.16.2"})


# --- the real checkout -----------------------------------------------------


def test_real_checkout_is_in_parity() -> None:
    """The committed tree must pass its own gate (engine version = reference)."""
    root = SCRIPT.parents[1]
    versions = check_version_parity.collect_versions(root)
    expected = versions["pyproject.toml (engine)"]
    assert check_version_parity.find_drift(expected, versions) == []


# --- end-to-end via main() -------------------------------------------------


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, timeout=60
    )


def test_main_exits_zero_when_in_parity(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    result = _run_cli("--root", str(root))
    assert result.returncode == 0, result.stderr
    assert f"all 5 surfaces at {RELEASE}" in result.stdout


def test_main_exits_one_on_drift_and_names_the_surface(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, frontend="0.1.0")
    result = _run_cli("--root", str(root))
    assert result.returncode == 1
    assert "kollabor-webui frontend (package.json): 0.1.0 != 0.16.2" in result.stderr
    assert result.stdout == ""


def test_main_accepts_expected_tag_and_version(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    for expected in ("0.16.2", "v0.16.2"):
        result = _run_cli("--root", str(root), "--expected", expected)
        assert result.returncode == 0, result.stderr


def test_main_expected_overrides_engine_reference(tmp_path: Path) -> None:
    # Tag gate: the tag is authoritative; every surface including the engine
    # pyproject must match it, so a stale engine version fails the gate.
    root = _make_repo(tmp_path, engine="0.16.1")
    result = _run_cli("--root", str(root), "--expected", "v0.16.2")
    assert result.returncode == 1
    assert "pyproject.toml (engine): 0.16.1 != 0.16.2" in result.stderr


def test_main_exits_two_when_a_surface_is_missing(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    (root / "packages/kollabor-webui/frontend/package.json").unlink()
    result = _run_cli("--root", str(root))
    assert result.returncode == 2
    assert "could not run" in result.stderr


def test_engine_pyproject_is_valid_toml_with_project_version() -> None:
    data = tomllib.loads((SCRIPT.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert isinstance(data["project"]["version"], str)
