"""Auth token generation and validation for the engine server."""

import logging
import os
import secrets

from kollabor_config.config_utils import get_config_directory, resolve_global_path

logger = logging.getLogger(__name__)

_TOKEN_FILE = get_config_directory() / "engine.token"
_current_token: str = ""


def generate_token() -> str:
    """Generate a new auth token, write to disk, return it."""
    global _current_token
    _current_token = secrets.token_urlsafe(32)
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(_current_token)
    _TOKEN_FILE.chmod(0o600)
    logger.info(f"Engine auth token written to {_TOKEN_FILE}")
    return _current_token


def get_token() -> str:
    return _current_token


def validate_token(token: str) -> bool:
    # Bypass auth for tests if env var is set
    if os.environ.get("KOLLAB_ENGINE_BYPASS_AUTH") == "1":
        return True
    # Check in-memory token first
    if _current_token and secrets.compare_digest(token, _current_token):
        return True
    # Fallback to reading from disk (handles multi-process scenarios)
    for token_file in (_TOKEN_FILE, resolve_global_path("engine.token")):
        if token_file.exists():
            disk_token = token_file.read_text().strip()
            if secrets.compare_digest(token, disk_token):
                return True
    return False


def read_token_from_disk() -> str:
    """Read token from disk (for clients)."""
    for token_file in (_TOKEN_FILE, resolve_global_path("engine.token")):
        if token_file.exists():
            return token_file.read_text().strip()
    return ""
