"""Automatic updater dispatch for Kollab releases."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .git_update import _current_source_root, pip_command, run_source_update


@dataclass
class AutoUpdateResult:
    """Result of an automatic update attempt."""

    success: bool
    message: str
    method: str
    changed: bool = True


# Installers that own an isolated environment; anything else is upgraded with
# the running interpreter's own pip.
_INSTALLER_COMMANDS = {
    "uv": ("uv", "tool", "upgrade", "kollab"),
    "pipx": ("pipx", "upgrade", "kollab"),
    "brew": ("brew", "upgrade", "kollab"),
}


def _run_cmd(*args: str) -> subprocess.CompletedProcess[str]:
    """Run an update command and capture text output."""
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
    )


def _looks_like_source_checkout(repo_root: Path) -> bool:
    return (repo_root / "pyproject.toml").exists() and (repo_root / ".git").exists()


def _output(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stdout.strip() or result.stderr.strip()).strip()
    if len(text) > 1200:
        return f"{text[:1200].rstrip()}\n..."
    return text


def _installed_version() -> str:
    try:
        return version("kollab")
    except PackageNotFoundError:
        return "unknown"


def _install_method() -> str:
    """Name the installer that owns the running interpreter.

    Upgrading any other copy on PATH (a stale uv tool, a pipx venv) would
    report success while the running Kollab stays old.
    """
    parts = Path(sys.prefix).resolve().parts
    if "pipx" in parts and "venvs" in parts:
        return "pipx"
    if "uv" in parts and "tools" in parts:
        return "uv"
    if "Cellar" in parts:
        return "brew"
    return "pip"


def run_auto_update(repo_root: Path | None = None) -> AutoUpdateResult:
    """Update the running Kollab install.

    Source checkouts use the safe fast-forward updater. Installed packages are
    upgraded by the installer that owns the running interpreter.
    """
    root = (repo_root or _current_source_root()).resolve()
    if _looks_like_source_checkout(root):
        source = run_source_update(repo_root=root)
        return AutoUpdateResult(source.success, source.message, "source", source.changed)

    method = _install_method()
    command = _INSTALLER_COMMANDS.get(method) or pip_command(
        "install", "--upgrade", "kollab"
    )
    before = _installed_version()
    try:
        result = _run_cmd(*command)
    except OSError as exc:
        return AutoUpdateResult(False, f"`{command[0]}` could not run: {exc}", method)
    if result.returncode != 0:
        detail = _output(result) or f"{' '.join(command)} exited {result.returncode}"
        return AutoUpdateResult(False, detail, method)

    after = _installed_version()
    if after == before:
        return AutoUpdateResult(
            True, f"Kollab is already up to date (v{after}).", method, changed=False
        )
    return AutoUpdateResult(
        True, f"Kollab upgraded: v{before} -> v{after} (via {method}).", method
    )
