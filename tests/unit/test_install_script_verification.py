"""Installer verification must inspect the executable selected for installation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "install.sh"


def _run_verification(
    tmp_path: Path,
    *,
    installed_dir: Path,
    path_dirs: list[Path],
    version: str = "",
    installed_version: str | None = None,
) -> subprocess.CompletedProcess[str]:
    functions = tmp_path / "installer-functions.sh"
    functions.write_text(
        INSTALL_SCRIPT.read_text().split('\nmain "$@"')[0] + "\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if installed_version is not None:
        installed_dir.mkdir(parents=True, exist_ok=True)
        executable = installed_dir / "kollab"
        executable.write_text(
            f"#!/bin/sh\necho 'kollab {installed_version}'\n", encoding="utf-8"
        )
        executable.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "KOLLAB_VERSION": version,
            "PATH": os.pathsep.join(str(path) for path in path_dirs),
        }
    )
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; INSTALLED_DIR="$2"; verify_install',
            "installer-test",
            str(functions),
            str(installed_dir),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _fake_executable(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "kollab"
    executable.write_text(f"#!/bin/sh\necho 'kollab {version}'\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def test_stale_path_command_does_not_verify_missing_install(tmp_path: Path) -> None:
    stale_dir = tmp_path / "stale-bin"
    _fake_executable(stale_dir, "0.1.0")

    result = _run_verification(
        tmp_path,
        installed_dir=tmp_path / "install-bin",
        path_dirs=[stale_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert result.returncode != 0
    assert "Installation verified" not in result.stdout
    assert str(stale_dir / "kollab") in result.stdout


def test_missing_installed_executable_fails_even_when_path_has_kollab(
    tmp_path: Path,
) -> None:
    unrelated_dir = tmp_path / "unrelated-bin"
    _fake_executable(unrelated_dir, "1.0.0")

    result = _run_verification(
        tmp_path,
        installed_dir=tmp_path / "install-bin",
        path_dirs=[unrelated_dir, Path("/usr/bin"), Path("/bin")],
    )

    assert result.returncode != 0
    assert "Installation verified" not in result.stdout


def test_requested_version_must_match_installed_executable_exactly(
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "install-bin"
    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[installed_dir, Path("/usr/bin"), Path("/bin")],
        version="1.2.3",
        installed_version="1.2.30",
    )

    assert result.returncode != 0
    assert "Installation verified" not in result.stdout
    assert "1.2.3" in result.stdout
    assert "1.2.30" in result.stdout


def test_custom_install_directory_reports_actual_executable(tmp_path: Path) -> None:
    custom_dir = tmp_path / "custom tools" / "bin"
    result = _run_verification(
        tmp_path,
        installed_dir=custom_dir,
        path_dirs=[custom_dir, Path("/usr/bin"), Path("/bin")],
        version="2.0.0",
        installed_version="2.0.0",
    )

    assert result.returncode == 0
    assert str(custom_dir / "kollab") in result.stdout
    assert "2.0.0" in result.stdout


def test_normal_install_reports_matching_executable_and_version(tmp_path: Path) -> None:
    installed_dir = tmp_path / "install-bin"
    result = _run_verification(
        tmp_path,
        installed_dir=installed_dir,
        path_dirs=[installed_dir, Path("/usr/bin"), Path("/bin")],
        version="3.4.5",
        installed_version="3.4.5",
    )

    assert result.returncode == 0
    assert str(installed_dir / "kollab") in result.stdout
    assert "3.4.5" in result.stdout
