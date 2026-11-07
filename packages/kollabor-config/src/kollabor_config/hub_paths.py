"""Shared paths for local Hub endpoints."""

import hashlib
import os
from pathlib import Path


def hub_socket_root() -> Path:
    """Return the common local endpoint root without creating it."""
    if os.name == "nt":
        return Path.home() / ".kollab" / "hub-sockets"
    # Keep Unix socket paths short enough for macOS.
    return Path("/tmp/kollabor-hub")


def hub_project_socket_key(project_path: str | Path) -> str:
    """Return the short stable key used to scope local Hub runtime files."""
    from .config_utils import encode_project_path

    project_id = encode_project_path(Path(project_path))
    return hashlib.sha256(project_id.encode()).hexdigest()[:12]
