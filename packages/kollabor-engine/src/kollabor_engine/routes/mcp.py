"""MCP server management routes.

Provides endpoints for:
- Listing configured MCP servers (from global config)
- Adding/removing/updating server configurations
- Per-session MCP connection status
- Connecting/disconnecting servers for a session
- Listing tools from connected servers
"""

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, status  # type: ignore
from pydantic import BaseModel

from kollabor_config.config_utils import get_config_directory

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp"])

# Global MCP config paths
GLOBAL_MCP_DIR = get_config_directory() / "mcp"
GLOBAL_MCP_CONFIG = GLOBAL_MCP_DIR / "mcp_settings.json"

# Shell metacharacter patterns that indicate command injection attempts
_SHELL_METACHAR_PATTERN = re.compile(r"[;&|`$()<>]")


def _validate_command(command: str) -> None:
    """Validate MCP server command string for safety.

    Args:
        command: Command string to validate.

    Raises:
        HTTPException: If command contains shell metacharacters.
    """
    if not command or not command.strip():
        raise HTTPException(status_code=400, detail="Command cannot be empty")

    # Check for shell metacharacters that could enable injection
    if _SHELL_METACHAR_PATTERN.search(command):
        raise HTTPException(
            status_code=400,
            detail="Command contains forbidden shell metacharacters: ; & | ` $ ( ) < >",
        )

    # Command must start with alphanumeric or slash (path)
    if not re.match(r"^[a-zA-Z0-9_/\-]", command):
        raise HTTPException(
            status_code=400,
            detail="Command must start with alphanumeric character, /, -, or _",
        )


class MCPServerConfig(BaseModel):
    """MCP server configuration model."""

    type: str = "stdio"
    command: str
    enabled: bool = True
    description: str = ""
    env: Dict[str, str] = {}


class AddServerRequest(BaseModel):
    """Request to add a new MCP server."""

    name: str
    type: str = "stdio"
    command: str
    enabled: bool = True
    description: str = ""
    env: Dict[str, str] = {}


class UpdateServerRequest(BaseModel):
    """Request to update an MCP server (name is in path)."""

    type: str = "stdio"
    command: str
    enabled: bool = True
    description: str = ""
    env: Dict[str, str] = {}


def _load_mcp_config() -> Dict[str, Any]:
    """Load global MCP config from file.

    Returns:
        Dict with "servers" key containing server configurations.
        Returns {"servers": {}} if file doesn't exist.

    Raises:
        HTTPException: If JSON is invalid.
    """
    if not GLOBAL_MCP_CONFIG.exists():
        return {"servers": {}}

    try:
        with open(GLOBAL_MCP_CONFIG, "r") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in MCP config: {e}")


def _save_mcp_config(config: Dict[str, Any]) -> None:
    """Save global MCP config to file with atomic write and file locking.

    Uses write-to-temp + rename pattern for atomicity.
    Uses file locking to prevent concurrent write corruption.

    Creates the directory if it doesn't exist.
    """
    GLOBAL_MCP_DIR.mkdir(parents=True, exist_ok=True)

    # Acquire file lock
    lock_fd = None
    try:
        # Create/open lock file
        lock_fd = open(GLOBAL_MCP_DIR / "mcp_settings.lock", "w")
        if IS_WINDOWS:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        else:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

        # Write to temp file first (atomic pattern)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=GLOBAL_MCP_DIR,
            prefix=".mcp_settings_",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            json.dump(config, tmp_file, indent=2)
            tmp_path = tmp_file.name

        # Atomic rename (overwrites target if exists)
        os.replace(tmp_path, GLOBAL_MCP_CONFIG)

    finally:
        # Release lock
        if lock_fd is not None:
            if IS_WINDOWS:
                try:
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                except OSError:
                    pass
            else:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()


@router.get("/servers")
async def list_servers() -> Dict[str, Any]:
    """List all configured MCP servers from global config.

    Returns a read-only view of configured servers, NOT connection status.
    Connection status is per-session and available via GET /sessions/{id}/mcp
    """
    config = _load_mcp_config()
    servers = config.get("servers", {})

    enabled_count = sum(1 for s in servers.values() if s.get("enabled", True))

    return {"servers": servers, "total": len(servers), "enabled": enabled_count}


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def add_server(body: AddServerRequest) -> Dict[str, Any]:
    """Add a new MCP server configuration to global config.

    Args:
        body: Server configuration with name and connection details.

    Returns:
        Created server configuration.

    Raises:
        HTTPException 409: If server name already exists.
        HTTPException 400: If type is not 'stdio' or command is invalid.
    """
    # Validate command for safety
    _validate_command(body.command)

    config = _load_mcp_config()
    servers = config.get("servers", {})

    if body.name in servers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "ServerAlreadyExists",
                "message": f"MCP server '{body.name}' already exists",
                "suggestion": "Use PUT /mcp/servers/{name} to update",
            },
        )

    # Validate type
    if body.type != "stdio":
        raise HTTPException(
            status_code=400, detail="Only 'stdio' type is currently supported"
        )

    servers[body.name] = {
        "type": body.type,
        "command": body.command,
        "enabled": body.enabled,
        "description": body.description,
        "env": body.env,
    }

    config["servers"] = servers
    _save_mcp_config(config)

    return {
        "ok": True,
        "name": body.name,
        "config": servers[body.name],
        "file": str(GLOBAL_MCP_CONFIG),
    }


@router.delete("/servers/{server_name}")
async def remove_server(server_name: str) -> Dict[str, Any]:
    """Remove an MCP server configuration from global config.

    Note: This does NOT affect active sessions - they retain their
    connections until shutdown.

    Args:
        server_name: Name of server to remove.

    Returns:
        Confirmation of removal.

    Raises:
        HTTPException 404: If server not found.
    """
    config = _load_mcp_config()
    servers = config.get("servers", {})

    if server_name not in servers:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "ServerNotFound",
                "message": f"MCP server '{server_name}' not found",
            },
        )

    del servers[server_name]
    config["servers"] = servers
    _save_mcp_config(config)

    return {
        "ok": True,
        "name": server_name,
        "file": str(GLOBAL_MCP_CONFIG),
        "note": "Active sessions retain connection until shutdown",
    }


@router.put("/servers/{server_name}")
async def update_server(server_name: str, body: UpdateServerRequest) -> Dict[str, Any]:
    """Update an existing MCP server configuration.

    All fields are required in the request body.

    Args:
        server_name: Name of server to update.
        body: New configuration values (all fields required).

    Returns:
        Updated server configuration.

    Raises:
        HTTPException 404: If server not found.
        HTTPException 400: If command is invalid.
    """
    # Validate command for safety
    _validate_command(body.command)

    config = _load_mcp_config()
    servers = config.get("servers", {})

    if server_name not in servers:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "ServerNotFound",
                "message": f"MCP server '{server_name}' not found",
            },
        )

    # Merge with existing config, update provided fields
    servers[server_name]
    servers[server_name] = {
        "type": body.type,
        "command": body.command,
        "enabled": body.enabled,
        "description": body.description,
        "env": body.env,
    }

    config["servers"] = servers
    _save_mcp_config(config)

    return {"ok": True, "name": server_name, "config": servers[server_name]}
