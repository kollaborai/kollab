"""Automatic updater dispatch for Kollab releases."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..binary import app_launcher, running_binary
from .git_update import _current_source_root, pip_command, run_source_update
from .version_comparator import is_newer_version

logger = logging.getLogger(__name__)

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


def _open(url: str, timeout: float, headers: dict | None = None):
    """GET url; GitHub's API refuses requests without a User-Agent."""
    merged = {"User-Agent": "kollab-upgrade", **(headers or {})}
    request = urllib.request.Request(url, headers=merged)
    return urllib.request.urlopen(request, timeout=timeout)


def _in_app() -> bool:
    """True when the running app called us from a worker thread.

    /upgrade, the startup auto-update and the web UI run the updater with
    asyncio.to_thread. There the terminal belongs to the UI (stderr would
    corrupt it) and Python refuses to install signal handlers.
    """
    return threading.current_thread() is not threading.main_thread()


def _status(text: str) -> None:
    """One status line: stderr for ``kollab --update``, the log inside the app."""
    if _in_app():
        logger.info(text)
    else:
        print(text, file=sys.stderr, flush=True)


def _print_progress(done: int, total: int | None) -> None:
    """Download progress on stderr; stdout stays a consumed contract.

    Interactive terminals get an updating ``\r`` line; pipes keep the
    one-line-per-chunk form (still stderr, so ``kollab --update |`` and
    pipe mode never see it). Inside the app there is no progress line.
    """
    if _in_app():
        return
    try:
        is_tty = sys.stderr.isatty()
    except (AttributeError, ValueError):  # replaced/closed stream
        is_tty = False
    if total:
        percent = min(100, done * 100 // total)
        line = f"downloading... {done / (1 << 20):.1f}/{total / (1 << 20):.1f} MB ({percent}%)"
    else:
        line = f"downloading... {done / (1 << 20):.1f} MB"
    end = "\r" if is_tty else "\n"
    print(line, file=sys.stderr, end=end, flush=True)


def _cleanup_stale_partials(directory: Path, *, max_age: float = 3600.0) -> int:
    """Delete abandoned .kollab-upgrade-* partials (older than max_age).

    A fresh partial may belong to a concurrently running upgrade and is
    left alone. Returns how many files were removed.
    """
    removed = 0
    now = time.time()
    try:
        candidates = list(directory.glob(".kollab-upgrade-*"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if not path.is_file():
                continue
            if now - path.stat().st_mtime < max_age:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _download_with_progress(response, out, digest) -> None:
    """Stream the asset, printing progress and failing loudly on a stall.

    No bytes for 30 seconds raises TimeoutError; the caller retries once.
    """
    headers = getattr(response, "headers", None)
    total: int | None = None
    try:
        length = headers.get("Content-Length") if headers else None
        total = int(length) if length else None
    except (TypeError, ValueError):
        total = None
    last_byte_at = time.monotonic()
    done = 0
    while True:
        chunk = response.read(1 << 20)
        if not chunk:
            break
        now = time.monotonic()
        if now - last_byte_at > 30.0:
            raise TimeoutError("download stalled: no data for 30s")
        digest.update(chunk)
        out.write(chunk)
        done += len(chunk)
        last_byte_at = now
        _print_progress(done, total)
    if done and not _in_app():
        print("", file=sys.stderr, flush=True)  # end the progress line


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
        stale = _cleanup_stale_partials(target.parent)
        if stale:
            _status(f"removed {stale} abandoned upgrade download(s)")
        fd, temp_name = tempfile.mkstemp(prefix=".kollab-upgrade-", dir=target.parent)
        temp = Path(temp_name)
        try:
            # An interrupted upgrade must not leave the partial behind:
            # unlink on Ctrl-C and on SIGTERM, then re-raise/die. Only the main
            # thread may set handlers; inside the app the finally below unlinks
            # it, and a partial a kill leaves is swept by the next upgrade.
            previous_term = signal.getsignal(signal.SIGTERM)
            catch_term = not _in_app()

            def _unlink_on_signal(signum, frame):
                temp.unlink(missing_ok=True)
                if signum == signal.SIGTERM:
                    signal.signal(signal.SIGTERM, previous_term)
                    os.kill(os.getpid(), signal.SIGTERM)
                    raise SystemExit(128 + signum)
                raise KeyboardInterrupt

            if catch_term:
                signal.signal(signal.SIGTERM, _unlink_on_signal)
            try:
                digest = hashlib.sha256()
                attempt = 0
                while True:
                    request_headers = {"User-Agent": "kollab-upgrade"}
                    resume_from = 0
                    if attempt and temp.exists():
                        try:
                            resume_from = temp.stat().st_size
                        except OSError:
                            resume_from = 0
                    if resume_from:
                        request_headers["Range"] = f"bytes={resume_from}-"
                    if attempt == 0:
                        out_fd, mode = fd, "wb"
                    else:
                        out_fd = os.open(
                            temp, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644
                        )
                        mode = "ab"
                    with (
                        os.fdopen(out_fd, mode) as out,
                        _open(urls[asset], 600, headers=request_headers) as response,
                    ):
                        if resume_from:
                            code = getattr(response, "status", None)
                            if code is None:
                                code = getattr(response, "code", None)
                            if code != 206:
                                # Not a partial: the body repeats from zero.
                                out.seek(0)
                                out.truncate()
                                digest = hashlib.sha256()
                        try:
                            _download_with_progress(response, out, digest)
                            break
                        except (TimeoutError, OSError) as exc:
                            if attempt >= 1:
                                raise TimeoutError(f"{exc} (after retry)") from exc
                            attempt += 1
                            _status("download interrupted; retrying once...")
            finally:
                if catch_term:
                    signal.signal(signal.SIGTERM, previous_term)
            if digest.hexdigest() != expected:
                return AutoUpdateResult(
                    False, f"The v{latest} download did not match its SHA-256; kept v{current}.", "binary"
                )
            temp.chmod(0o755)
            _status("verifying the new binary (first run unpacks its Python; can take a minute)...")
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
    except (TimeoutError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return AutoUpdateResult(False, f"Could not upgrade the kollab binary: {exc}", "binary")
    return AutoUpdateResult(True, f"Kollab upgraded: v{current} -> v{latest} (via binary).", "binary")


def run_auto_update(repo_root: Path | None = None) -> AutoUpdateResult:
    """Update the running Kollab install.

    A single-file binary swaps in the latest release's binary. Source checkouts
    use the safe fast-forward updater. Installed packages are upgraded by the
    installer that owns the running interpreter.
    """
    if app_launcher() is not None:
        # Any write inside Kollab.app breaks its signature: the app updates whole.
        return AutoUpdateResult(
            False, "This Kollab runs inside Kollab.app; update it by installing the latest Kollab.app.", "app",
            changed=False,
        )
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
