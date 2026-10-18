"""Agent identity pool routes for the web UI."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query  # type: ignore[import-not-found]

from ..folders import agents_in_folder, hub_folder
from ..gem_appearance import load_appearance, record_births, save_appearance
from ..hub_bridge import HubBridge

# plugins.hub.models.COORDINATOR_IDENTITY; kept here so the route imports no plugin.
COORDINATOR_IDENTITY = "koordinator"

router = APIRouter(prefix="/agents", tags=["agents"])
_bridge = HubBridge()


def _hub_dir(folder: str) -> Path:
    """The hub (presence, vaults) an agent started in ``folder`` joins.

    Looked up, never created: the New Session picker asks about folders nobody
    has run in. This engine's own folder is the hub the rest of the engine reads.
    """
    from plugins.hub.presence import get_hub_dir

    if hub_folder(folder) == hub_folder(os.getcwd()):
        return get_hub_dir()
    from kollabor_config.config_utils import get_project_data_dir
    from plugins.hub.project_scope import resolve_project_root

    return get_project_data_dir(resolve_project_root(folder)) / "hub"


def ran_in(folder: str) -> Dict[str, float]:
    """Every agent that ran in ``folder`` -> when it last did (epoch seconds).

    An agent's vault stream (hub/vaults/<name>/stream.jsonl) gets a line
    every session, so its mtime is the last time it ran there. ``_shared`` and
    ``_archived`` are not agents.
    """
    try:
        from plugins.hub.vault import find_active_stream

        vaults = list((_hub_dir(folder) / "vaults").iterdir())
    except Exception:
        return {}
    ran: Dict[str, float] = {}
    for vault in vaults:
        if vault.name.startswith((".", "_")) or not vault.is_dir():
            continue
        try:
            stat = find_active_stream(vault).stat()
        except OSError:
            continue
        if stat.st_size:
            ran[vault.name] = stat.st_mtime
    return ran


def pool_identities() -> List[Any]:
    """The personas of the engine folder's roster (name, color, caste, ...); none if the hub is unavailable."""
    try:
        from plugins.hub.rosters import roster_personas

        return list(roster_personas())
    except Exception:
        return []


def _pool_entry(
    name: str, current: Optional[Dict[str, Any]], last_ran: Optional[float], **who: Any
) -> Dict[str, Any]:
    """One launchable name: who it is, and the agent of that name that is live (if any)."""
    live = current or {}
    return {
        "name": name,
        "identity": name,
        **who,
        "available": current is None,
        "active": current is not None,
        "pool": True,
        "state": live.get("state") if current else "available",
        "current_task": live.get("current_task", ""),
        # The live hub agent, for /hub/agents/{agent_id}/output.
        "agent_id": live.get("agent_id", ""),
        # Live but off the hub mesh (its presence says so).
        "solo": bool(live.get("solo")),
        # The folder it runs in (presence covers every project here).
        "project": str(live.get("project") or ""),
        "last_ran": last_ran,
    }


@router.get("")
async def list_agent_pool(
    refresh: bool = Query(False, description="Bypass the hub presence cache"),
    workspace: str = Query("", description="Folder to launch in; empty is the engine's own"),
    scope: str = Query(
        "folder",
        pattern="^(folder|computer)$",
        description="folder: only agents in that folder's hub; computer: any folder, that one first",
    ),
) -> Dict[str, Any]:
    """Return the same gem/identity pool used by normal CLI launches, for one folder.

    This is deliberately separate from ``/hub/agents``: that endpoint is the
    live roster, while this endpoint tells a new web session which identities
    it may launch under. Every folder has its own hub (a koordinator and every
    gem each), so ``available`` / ``active`` / ``state`` / ``agent_id`` /
    ``project`` / ``last_ran`` describe ``workspace`` only: a gem running in
    another folder does not take it here. ``scope=computer`` is the sidebar's
    view instead: an agent running in any folder shows its state (this
    folder's wins when a name runs in several).
    """
    pool = pool_identities()
    folder = os.path.realpath(os.path.expanduser(workspace)) if workspace else os.path.realpath(os.getcwd())
    live = [
        agent
        for agent in _bridge.get_agents(use_cache=not refresh)
        if isinstance(agent, dict) and agent.get("identity")
    ]
    live_by_identity = {str(agent["identity"]): agent for agent in agents_in_folder(live, folder)}
    if scope == "computer":
        live_by_identity = {**{str(agent["identity"]): agent for agent in live}, **live_by_identity}

    # The first agent in a folder is its koordinator, as with a plain `kollab`
    # (the hub's flock winner): offer it first while this folder has none.
    last_ran = ran_in(folder).get
    agents = [
        _pool_entry(
            COORDINATOR_IDENTITY,
            live_by_identity.get(COORDINATOR_IDENTITY),
            last_ran(COORDINATOR_IDENTITY),
            agent_type=COORDINATOR_IDENTITY,
            role_aliases=[],
            personality="",
            caste="",
            # Not a pool gem: the web UI gives it a born color.
            color=None,
        )
    ]
    for identity in pool:
        name = str(getattr(identity, "name", "") or "")
        if not name:
            continue
        agents.append(
            _pool_entry(
                name,
                live_by_identity.get(name),
                last_ran(name),
                agent_type=str(getattr(identity, "agent", "") or ""),
                role_aliases=list(getattr(identity, "role_aliases", []) or []),
                personality=str(getattr(identity, "personality", "") or ""),
                caste=str(getattr(identity, "caste", "") or ""),
                color=list(getattr(identity, "color", None) or (128, 128, 128)),
            )
        )

    # A gem's first time alive is its birth: it gets the random look it keeps.
    alive = [agent["name"] for agent in agents if agent["active"]]
    if alive:
        record_births(alive)

    # Live agents the pool does not name (numbered gems like lapis-2) are
    # listed too, so the sidebar shows every agent. "pool": False keeps them
    # out of the launch list; a numbered gem wears its base's color.
    by_name = {agent["name"]: agent for agent in agents}
    for name, current in sorted(live_by_identity.items()):
        if name in by_name:
            continue
        base = by_name.get(name.rsplit("-", 1)[0], {})
        agents.append(
            {
                "name": name,
                "identity": name,
                "agent_type": "",
                "role_aliases": [],
                "personality": "",
                "caste": base.get("caste", ""),
                # None when no pool gem is its base: the web UI gives it a born color.
                "color": base.get("color"),
                "available": False,
                "active": True,
                "pool": False,
                "state": current.get("state") or "",
                "current_task": current.get("current_task", ""),
                "agent_id": current.get("agent_id", ""),
                "solo": bool(current.get("solo")),
                "project": str(current.get("project") or ""),
            }
        )

    return {
        "agents": agents,
        "available": [agent["name"] for agent in agents if agent["available"]],
        "active": [agent["name"] for agent in agents if agent["active"]],
        "count": len(agents),
    }


@router.get("/appearance")
async def get_gem_appearance() -> Dict[str, Any]:
    """Every gem's look: the season, the looks gems were born with, the user's picks.

    ``home`` is this engine's folder: a bare gem name is the agent there.
    """
    return {**load_appearance(), "home": os.path.realpath(os.getcwd())}


@router.put("/appearance")
async def put_gem_appearance(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Replace the user's picks and season; born looks stay. Returns what was stored."""
    try:
        stored = save_appearance(body)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save gem appearance: {exc}") from exc
    return {**stored, "home": os.path.realpath(os.getcwd())}


@router.get("/bundles")
async def list_agent_bundles() -> Dict[str, Any]:
    """List agent bundles the daemon can launch with (``--agent <name>``).

    This is the bundle tier -- system prompts + agent.json metadata -- not the
    gem identity pool above. Session creation accepts ``agent`` for any of
    these names; the web UI needs the same list the CLI's ``/agent`` picker
    shows. Local overrides global, matching AgentManager's resolution order.
    """
    try:
        from kollabor_agent.agent_manager import AgentManager
    except ImportError:
        return {"bundles": [], "count": 0}

    bundles: list = []
    try:
        manager = AgentManager()
        for agent in manager.list_agents():
            description = str(getattr(agent, "description", "") or "")
            profile = getattr(agent, "profile", None)
            bundles.append(
                {
                    "name": str(getattr(agent, "name", "") or ""),
                    "description": description,
                    "profile": profile,
                    "skills": list(getattr(agent, "default_skills", []) or []),
                }
            )
    except Exception:
        bundles = []

    bundles.sort(key=lambda bundle: bundle["name"])
    return {"bundles": bundles, "count": len(bundles)}
