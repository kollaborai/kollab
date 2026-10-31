"""Auth token generation and validation for the engine server."""

import logging
import os
import secrets
import sys
from pathlib import Path

from kollabor_config.config_utils import get_config_directory

logger = logging.getLogger(__name__)

_current_token: str = ""


def _token_file() -> Path:
    """Resolve the token path at call time so KOLLAB_CONFIG_DIR always wins.

    A module-level path froze the resolution to whenever this module was first
    imported; a process that imported it before the env var was set (a test
    stack, a long-lived manager) then wrote the shared ~/.kollab token. The
    override is honored here at every call so an isolated engine never touches
    the shared token, whatever order import and env setup happened in.
    """
    override = os.environ.get("KOLLAB_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser() / "engine.token"
    return get_config_directory() / "engine.token"


def generate_token() -> str:
    """Generate a new auth token, write to disk, return it."""
    global _current_token
    _current_token = secrets.token_urlsafe(32)
    token_file = _token_file()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(_current_token)
    token_file.chmod(0o600)
    logger.info(f"Engine auth token written to {token_file}")
    return _current_token


def get_token() -> str:
    return _current_token


def validate_token(token: str) -> bool:
    # Bypass auth for tests if env var is set
    if os.environ.get("KOLLAB_ENGINE_BYPASS_AUTH") == "1" and "pytest" in sys.modules:
        return True
    # Check in-memory token first
    if _current_token and secrets.compare_digest(token, _current_token):
        return True
    # Fallback to reading from disk (handles multi-process scenarios)
    token_file = _token_file()
    if token_file.exists():
        disk_token = token_file.read_text().strip()
        if secrets.compare_digest(token, disk_token):
            return True
    return False


def read_token_from_disk() -> str:
    """Read token from disk (for clients)."""
    token_file = _token_file()
    if token_file.exists():
        return token_file.read_text().strip()
    return ""
