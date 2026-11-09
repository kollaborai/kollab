"""Runtime version identity for the engine process (issue #630).

The engine runs as its own process (``python -m kollabor_engine serve``) and
must not import the kollab core package, so it derives the same PEP 440
identity independently: the engine base version plus a local segment
(``0.18.5+<sha8>``) when running from a source checkout, and the exact
distribution version when installed. An unresolvable revision reports
``+unknown`` so a dev build can never masquerade as a release build.
"""

from __future__ import annotations

import os
import re
import subprocess
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

UNKNOWN = "unknown"
_GIT_TIMEOUT_SECONDS = 2.0
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _package_root() -> Path:
    """Directory of the engine distribution (contains pyproject.toml)."""

    return Path(__file__).resolve().parents[2]


def _is_running_from_source() -> bool:
    """True when the engine package lives in a source checkout tree."""

    return (_package_root() / "pyproject.toml").is_file()


def _base_version() -> str:
    """Engine base version: pyproject when in a checkout, dist metadata when installed."""

    if _is_running_from_source():
        try:
            text = (_package_root() / "pyproject.toml").read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.strip().startswith("version ="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    try:
        return pkg_version("kollabor-engine")
    except PackageNotFoundError:
        return "0.0.0-installed"


def _is_valid_sha(value: str) -> bool:
    return bool(_SHA_PATTERN.fullmatch(value))


def _resolve_revision() -> str:
    """Current commit SHA of the checkout, from git or KOLLAB_BUILD_SHA."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(_package_root()),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except Exception:
        result = None
    if result is not None:
        sha = result.stdout.strip().lower()
        if _is_valid_sha(sha):
            return sha
    env_sha = os.environ.get("KOLLAB_BUILD_SHA", "").strip().lower()
    if _is_valid_sha(env_sha):
        return env_sha
    return UNKNOWN


def get_engine_runtime_version() -> str:
    """PEP 440 runtime identity reported by /version and /status."""

    base = _base_version()
    if not _is_running_from_source():
        return base
    sha = _resolve_revision()
    short_sha = sha[:8] if sha != UNKNOWN else UNKNOWN
    return f"{base}+{short_sha}"
