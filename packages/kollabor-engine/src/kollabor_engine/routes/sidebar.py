"""Engine-owned hidden rows and ordering for the web UI sidebar."""

import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, Body, HTTPException  # type: ignore[import-not-found]

from .. import hidden_agents, sidebar_order
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


@router.get("/order")
async def get_order() -> Dict[str, Any]:
    """Return the engine-owned order, its revision, and any recovery warning."""
    return sidebar_order.read_order_state().to_dict()


@router.post("/order")
async def set_order(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Save an order only when its expected revision is still current."""
    keys = sidebar_order.parse_order(body.get("order"))
    if keys is None:
        raise HTTPException(status_code=400, detail="order must be a list of bounded text keys")
    revision = body.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise HTTPException(status_code=400, detail="revision must be a non-negative integer")
    try:
        return sidebar_order.write_order(keys, revision).to_dict()
    except sidebar_order.SidebarOrderConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Sidebar order changed in another client; the current order was refreshed. Retry your move.",
        ) from exc
    except sidebar_order.SidebarOrderUnreadable as exc:
        raise HTTPException(
            status_code=422,
            detail="Sidebar order storage is unreadable and was preserved; check the recovery warning before retrying.",
        ) from exc
    except OSError as exc:
        logger.warning("Could not save sidebar order: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not save sidebar order: {exc}") from exc
