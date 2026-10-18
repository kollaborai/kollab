"""The sidebar rows the user hid, for every client of this engine (hidden_agents.py)."""

import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, Body, HTTPException  # type: ignore[import-not-found]

from .. import hidden_agents
from ..hub_bridge import HubBridge
from ..server import get_session_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sidebar", tags=["sidebar"])


def _answer() -> Dict[str, Any]:
    """The entries clients show: those of daemons running now, plus the ones with no process."""
    rows = [session.to_dict() for session in get_session_registry().values()]
    found = HubBridge().discover_sessions(use_cache=False)
    hidden_agents.stamp_instances(rows, found)
    live: Set[str] = {row["instance"] for row in rows} | {row["session_id"] for row in found}
    return {"hidden": hidden_agents.visible(live)}


def _changed(change: Any) -> Dict[str, Any]:
    try:
        change()
    except OSError as exc:
        logger.warning("Could not save hidden agents: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not save hidden agents: {exc}") from exc
    return _answer()


@router.get("/hidden")
async def get_hidden() -> Dict[str, Any]:
    """Every hidden row still in play, oldest first."""
    return _answer()


@router.post("/hide")
async def hide_agent(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Hide a row: ``{key, label, detail?, instance?}``. Hiding it again is not an error."""
    entry = hidden_agents.parse_entry(body)
    if entry is None:
        raise HTTPException(status_code=400, detail="key and label are required text, each of bounded length")
    return _changed(lambda: hidden_agents.hide(entry))


@router.post("/unhide")
async def unhide_agent(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Show a row again: ``{key}``, or ``{all: true}`` for every row. Showing one that is not hidden is not an error."""
    key = body.get("key")
    if body.get("all") is True and key is None:
        return _changed(hidden_agents.show_all)
    if isinstance(key, str) and key and body.get("all") is None:
        return _changed(lambda: hidden_agents.show(key))
    raise HTTPException(status_code=400, detail="send either a key or all: true")
