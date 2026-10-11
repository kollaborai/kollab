"""Agent identity pool routes for the web UI."""

import os
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query  # type: ignore[import-not-found]

from ..gem_appearance import load_appearance, record_births, save_appearance
from ..hub_bridge import HubBridge

# plugins.hub.models.COORDINATOR_IDENTITY; kept here so the route imports no plugin.
COORDINATOR_IDENTITY = "koordinator"

router = APIRouter(prefix="/agents", tags=["agents"])
_bridge = HubBridge()


def ran_here() -> Dict[str, float]:
    """Every agent that ran in this folder -> when it last did (epoch seconds).

    An agent's vault stream (hub/vaults/<name>/stream.jsonl) gets a line
    every session, so its mtime is the last time it ran here. ``_shared`` and
    ``_archived`` are not agents.
    """
    try:
        from plugins.hub.presence import get_hub_dir
        from plugins.hub.vault import find_active_stream

        vaults = list((get_hub_dir() / "vaults").iterdir())
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


@router.get("")
async def list_agent_pool(
    refresh: bool = Query(False, description="Bypass the hub presence cache"),
) -> Dict[str, Any]:
    """Return the same gem/identity pool used by normal CLI launches.

    This is deliberately separate from ``/hub/agents``: that endpoint is the
    live roster, while this endpoint tells a new web session which identities
    it may launch under.
    """
    try:
        from plugins.hub.models import load_pool_identities

        pool = load_pool_identities()
    except Exception:
        pool = []

    live = _bridge.get_agents(use_cache=not refresh)
    live_by_identity = {
        str(agent.get("identity")): agent
        for agent in live
        if isinstance(agent, dict) and agent.get("identity")
    }

    # The first agent in a folder is its koordinator, as with a plain `kollab`
    # (the hub's flock winner): offer it first while this folder has none.
    # Each folder has its own, so one running elsewhere does not take it.
    here = os.path.realpath(os.getcwd())
    last_ran = ran_here().get
    coordinator = next(
        (
            agent
            for agent in live
            if isinstance(agent, dict)
            and agent.get("identity") == COORDINATOR_IDENTITY
            and os.path.realpath(str(agent.get("project") or "")) == here
        ),
        None,
    )
    agents = [
        {
            "name": COORDINATOR_IDENTITY,
            "identity": COORDINATOR_IDENTITY,
            "agent_type": COORDINATOR_IDENTITY,
            "role_aliases": [],
            "personality": "",
            "caste": "",
            # Not a pool gem: the web UI gives it a born color.
            "color": None,
            "available": coordinator is None,
            "active": coordinator is not None,
            "pool": True,
            "state": coordinator.get("state") if coordinator else "available",
            "current_task": coordinator.get("current_task", "") if coordinator else "",
            "agent_id": coordinator.get("agent_id", "") if coordinator else "",
            "solo": bool(coordinator.get("solo")) if coordinator else False,
            "project": here if coordinator else "",
            "last_ran": last_ran(COORDINATOR_IDENTITY),
        }
    ]
    for identity in pool:
        name = str(getattr(identity, "name", "") or "")
        if not name:
            continue
        current = live_by_identity.get(name)
        agents.append(
            {
                "name": name,
                "identity": name,
                "agent_type": str(getattr(identity, "agent_type", "") or ""),
                "role_aliases": list(getattr(identity, "role_aliases", []) or []),
                "personality": str(getattr(identity, "personality", "") or ""),
                "caste": str(getattr(identity, "caste", "") or ""),
                "color": list(getattr(identity, "color_rgb", None) or (128, 128, 128)),
                "available": current is None,
                "active": current is not None,
                "pool": True,
                "state": current.get("state") if current else "available",
                "current_task": current.get("current_task", "") if current else "",
                # The live hub agent, for /hub/agents/{agent_id}/output.
                "agent_id": current.get("agent_id", "") if current else "",
                # Live but off the hub mesh (its presence says so).
                "solo": bool(current.get("solo")) if current else False,
                # The folder it runs in (presence covers every project here).
                "project": str(current.get("project") or "") if current else "",
                "last_ran": last_ran(name),
            }
        )

    # A gem's first time alive is its birth: it gets the random look it keeps.
    alive = [agent["name"] for agent in agents if agent["active"]]
    if alive:
        record_births(alive)

    # Live agents the pool does not name (koordinator, numbered gems like
    # lapis-2) are listed too, so the sidebar shows every agent. "pool": False
    # keeps them out of the launch list; a numbered gem wears its base's color.
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
