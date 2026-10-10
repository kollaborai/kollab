"""Automatic updater dispatch for Kollab releases."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..binary import running_binary
from .git_update import _current_source_root, pip_command, run_source_update
from .version_comparator import is_newer_version

# What a binary upgrades to; /releases/latest skips drafts and prereleases.
LATEST_RELEASE = "https://api.github.com/repos/kollaborai/kollab/releases/latest"


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


def platform_name() -> str | None:
    """This machine's binary suffix as releases name them (macos-aarch64, linux-x86_64), or None."""
    system = {"Darwin": "macos", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        platform.machine().lower()
    )
    return f"{system}-{machine}" if system and machine else None


def _open(url: str, timeout: float):
    """GET url; GitHub's API refuses requests without a User-Agent."""
    request = urllib.request.Request(url, headers={"User-Agent": "kollab-upgrade"})
    return urllib.request.urlopen(request, timeout=timeout)


def _upgrade_binary(binary: Path) -> AutoUpdateResult:
    """Swap the running kollab binary for the latest release's build for this machine.

    The download must match the release's SHA-256 and start as the new version
    before it replaces anything, so a failed upgrade leaves the old binary working.
    """
    current = _installed_version()
    name = platform_name()
    if name is None:
        machine = f"{platform.system()} {platform.machine()}"
        return AutoUpdateResult(False, f"No kollab binary is built for {machine}.", "binary")
    target = binary.resolve()
    if not os.access(target.parent, os.W_OK):
        return AutoUpdateResult(
            False, f"Cannot write {target.parent}; reinstall kollab with install.sh into a folder you own.", "binary"
        )
    try:
        with _open(LATEST_RELEASE, 30) as response:
            release = json.load(response)
        latest = str(release["tag_name"]).removeprefix("v")
        if not is_newer_version(current, latest):
            return AutoUpdateResult(True, f"Kollab is already up to date (v{current}).", "binary", changed=False)
        asset = f"kollab-{name}"
        urls = {item["name"]: item["browser_download_url"] for item in release.get("assets", [])}
        if asset not in urls or f"{asset}.sha256" not in urls:
            missing = f"v{latest} has no {asset} binary yet; try again in a few minutes."
            return AutoUpdateResult(False, missing, "binary")
        with _open(urls[f"{asset}.sha256"], 30) as response:
            expected = response.read().decode().split()[0]
        fd, temp_name = tempfile.mkstemp(prefix=".kollab-upgrade-", dir=target.parent)
        temp = Path(temp_name)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as out, _open(urls[asset], 600) as response:
                while chunk := response.read(1 << 20):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != expected:
                return AutoUpdateResult(
                    False, f"The v{latest} download did not match its SHA-256; kept v{current}.", "binary"
                )
            temp.chmod(0o755)
            # Its first start unpacks its own Python; it must not inherit this binary's identity.
            env = {key: value for key, value in os.environ.items() if key not in ("SCIE", "SCIE_ARGV0", "PEX")}
            shown = subprocess.run([str(temp), "--version"], capture_output=True, text=True, timeout=600, env=env)
            # --version prints the file name it runs as, then the version.
            if shown.stdout.split()[-1:] != [latest]:
                detail = (shown.stdout + shown.stderr).strip()[-300:] or f"exit {shown.returncode}"
                return AutoUpdateResult(
                    False, f"The v{latest} binary did not start ({detail}); kept v{current}.", "binary"
                )
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return AutoUpdateResult(False, f"Could not upgrade the kollab binary: {exc}", "binary")
    return AutoUpdateResult(True, f"Kollab upgraded: v{current} -> v{latest} (via binary).", "binary")


def run_auto_update(repo_root: Path | None = None) -> AutoUpdateResult:
    """Update the running Kollab install.

    A single-file binary swaps in the latest release's binary. Source checkouts
    use the safe fast-forward updater. Installed packages are upgraded by the
    installer that owns the running interpreter.
    """
    binary = running_binary()
    if binary is not None:
        return _upgrade_binary(binary)

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
