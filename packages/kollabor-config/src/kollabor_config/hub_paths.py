"""Shared paths for local Hub endpoints."""

import os
from pathlib import Path


def hub_socket_root() -> Path:
    """Return the common local endpoint root without creating it."""
    if os.name == "nt":
        return Path.home() / ".kollab" / "hub-sockets"
    # Keep Unix socket paths short enough for macOS.
    return Path("/tmp/kollabor-hub")
