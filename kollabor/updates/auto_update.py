"""Automatic updater dispatch for Kollab releases."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .git_update import run_source_update


@dataclass
class AutoUpdateResult:
    """Result of an automatic update attempt."""

    success: bool
    message: str
    method: str


def _run_cmd(*args: str) -> subprocess.CompletedProcess[str]:
    """Run an update command and capture text output."""
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
    )


def _current_package_root() -> Path:
    """Return the package checkout root when running from source."""
    return Path(__file__).resolve().parents[2]


def _looks_like_source_checkout(repo_root: Path) -> bool:
    return (repo_root / "pyproject.toml").exists() and (repo_root / ".git").exists()


def _output(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stdout.strip() or result.stderr.strip()).strip()
    if len(text) > 1200:
        return f"{text[:1200].rstrip()}\n..."
    return text


def _success(method: str, command: tuple[str, ...], output: str) -> AutoUpdateResult:
    detail = f" via `{method}`"
    if output:
        return AutoUpdateResult(True, f"Kollab updated{detail}.\n{output}", method)
    return AutoUpdateResult(True, f"Kollab updated{detail}.", method)


def _attempt_command(method: str, *command: str) -> AutoUpdateResult:
    result = _run_cmd(*command)
    if result.returncode == 0:
        return _success(method, command, _output(result))

    detail = _output(result) or f"{' '.join(command)} exited {result.returncode}"
    return AutoUpdateResult(False, detail, method)


def _is_brew_formula_installed() -> bool:
    if not shutil.which("brew"):
        return False
    result = _run_cmd("brew", "list", "--formula", "kollab")
    return result.returncode == 0


def run_auto_update(repo_root: Path | None = None) -> AutoUpdateResult:
    """Update Kollab using the active install style when possible.

    Source checkouts use the existing safe fast-forward updater. Installed
    packages try common isolated installers first, then fall back to the
    current Python environment.
    """
    root = (repo_root or _current_package_root()).resolve()
    if _looks_like_source_checkout(root):
        source_result = run_source_update(repo_root=root)
        return AutoUpdateResult(
            source_result.success,
            source_result.message,
            "source",
        )

    failures: list[str] = []

    if shutil.which("uv"):
        result = _attempt_command("uv", "uv", "tool", "upgrade", "kollab")
        if result.success:
            return result
        failures.append(f"uv: {result.message}")

    if shutil.which("pipx"):
        result = _attempt_command("pipx", "pipx", "upgrade", "kollab")
        if result.success:
            return result
        failures.append(f"pipx: {result.message}")

    if _is_brew_formula_installed():
        result = _attempt_command("brew", "brew", "upgrade", "kollab")
        if result.success:
            return result
        failures.append(f"brew: {result.message}")

    result = _attempt_command(
        "pip",
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "kollab",
    )
    if result.success:
        return result
    failures.append(f"pip: {result.message}")

    return AutoUpdateResult(
        False,
        "Unable to auto-update Kollab.\n" + "\n".join(failures),
        "none",
    )
