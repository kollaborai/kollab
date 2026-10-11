"""Folders a new web session can run in: the recent ones, and a folder browser."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query  # type: ignore[import-not-found]

from ..hub_bridge import HubBridge
from ..server import get_session_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

RECENT_FILE = "engine-workspaces.json"
RECENT_LIMIT = 12


def _recent_path() -> Path:
    from kollabor_config.config_utils import get_config_directory

    return get_config_directory() / RECENT_FILE


def _stored() -> List[str]:
    try:
        rows = json.loads(_recent_path().read_text())
    except (OSError, ValueError):
        return []
    return [str(row) for row in rows if isinstance(row, str)] if isinstance(rows, list) else []


def remember_workspace(path: Optional[str]) -> None:
    """Put a folder a session started in at the top of the recent list."""
    if not path:
        return
    folder = os.path.realpath(path)
    rows = [folder] + [row for row in _stored() if row != folder]
    try:
        target = _recent_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows[:RECENT_LIMIT]))
    except OSError as exc:
        logger.debug("Could not save recent workspaces: %s", exc)


@router.get("")
async def list_workspaces() -> Dict[str, Any]:
    """This engine's folder (the default) and the recent ones, each with its live agents.

    Recent: folders an agent on this computer runs in now, then the ones web
    sessions started in, newest first. Folders that no longer exist are left out.
    """
    home = os.path.realpath(os.getcwd())
    live: Dict[str, int] = {}
    for agent in HubBridge().get_agents(use_cache=False):
        folder = agent.get("project") if isinstance(agent, dict) else None
        if folder:
            key = os.path.realpath(str(folder))
            live[key] = live.get(key, 0) + 1
    for session in get_session_registry().values():
        folder = getattr(session, "workspace", None)
        if folder and getattr(session, "alive", False):
            live.setdefault(os.path.realpath(folder), 1)

    order: List[str] = []
    for folder in [*sorted(live, key=lambda f: -live[f]), *_stored()]:
        if folder != home and folder not in order and os.path.isdir(folder):
            order.append(folder)

    def row(folder: str) -> Dict[str, Any]:
        return {"path": folder, "name": os.path.basename(folder) or folder, "live": live.get(folder, 0)}

    return {"home": row(home), "recent": [row(folder) for folder in order[:RECENT_LIMIT]]}


@router.get("/browse")
async def browse(path: str = Query("", description="Folder to list; empty is the home folder")) -> Dict[str, Any]:
    """A folder's subfolders, for picking a workspace. Hidden folders are left out."""
    folder = Path(path).expanduser() if path else Path.home()
    try:
        folder = folder.resolve(strict=True)
        names = sorted(
            (entry.name for entry in os.scandir(folder) if entry.is_dir() and not entry.name.startswith(".")),
            key=str.lower,
        )
    except (OSError, RuntimeError) as exc:
        reason = getattr(exc, "strerror", None) or exc
        raise HTTPException(status_code=400, detail=f"Can't open {path or folder}: {reason}") from exc
    parent = folder.parent
    return {
        "path": str(folder),
        "name": folder.name or str(folder),
        "parent": str(parent) if parent != folder else None,
        "dirs": names[:500],
    }
