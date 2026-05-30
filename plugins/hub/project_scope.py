"""Project scope resolution for hub siloing.

The hub runs in one of two modes:
  - PROJECT-SCOPED (default): state at ~/.kollab/projects/<encoded>/hub/,
    agents only see peers launched from the same project root.
  - GLOBAL: all state at ~/.kollab/hub/, all agents on this machine
    share presence/sockets/vaults regardless of which repo spawned them.

Mode is controlled by env var KOLLAB_HUB_PROJECT_SCOPED (set by the
hub plugin at init from config key plugins.hub.project_scoped). Env var
is used rather than a direct config call because presence.py is pulled
in by path helpers before the plugin system has booted, and because the
env propagates cleanly to detached-daemon subprocess spawns.

Project identity precedence:
  1. KOLLAB_PROJECT_ROOT env var (set by --project CLI flag)
  2. git rev-parse --show-toplevel
  3. Path.cwd()

The encoded form matches projects/<encoded-path>/conversations/ so hub
state lives alongside conversation data for the same project.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from kollabor_config.config_utils import encode_project_path, get_project_data_dir

logger = logging.getLogger(__name__)


def is_project_scoped() -> bool:
    """True unless KOLLAB_HUB_PROJECT_SCOPED is explicitly false."""
    raw = os.environ.get("KOLLAB_HUB_PROJECT_SCOPED")
    if raw is None or raw == "":
        return True
    return raw.lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@lru_cache(maxsize=1)
def resolve_project_root() -> Path:
    """Resolve project root using the precedence documented in the module.

    Cached because the answer cannot change within a process and the git
    subprocess call isn't free.
    """
    override = os.environ.get("KOLLAB_PROJECT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            if root:
                return Path(root).resolve()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    return Path.cwd().resolve()


def resolve_project_id() -> str:
    """Encoded project identifier suitable for path segments."""
    return encode_project_path(resolve_project_root())


def get_project_hub_dir() -> Path:
    """Hub dir when project-scoping is ON."""
    d = get_project_data_dir(resolve_project_root()) / "hub"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def get_project_socket_key() -> str:
    """Short hash of project id, safe for /tmp socket path length budget.

    Unix socket paths have a 104-byte limit on macOS. Encoded project
    ids can exceed that when combined with /tmp/kollabor-hub/<id>/<name>.sock,
    so we hash down to 12 hex chars (~48 bits of entropy, plenty to
    avoid collisions across the handful of projects anyone actually has).
    """
    pid = resolve_project_id()
    return hashlib.sha256(pid.encode()).hexdigest()[:12]
