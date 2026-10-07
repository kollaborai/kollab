"""Agent identity pool routes for the web UI."""

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query  # type: ignore[import-not-found]

from ..gem_appearance import load_appearance, record_births, save_appearance
from ..hub_bridge import HubBridge

router = APIRouter(prefix="/agents", tags=["agents"])
_bridge = HubBridge()


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

    agents = []
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
                "state": current.get("state") if current else "available",
                "current_task": current.get("current_task", "") if current else "",
                # The live hub agent, for /hub/agents/{agent_id}/output.
                "agent_id": current.get("agent_id", "") if current else "",
            }
        )

    # A gem's first time alive is its birth: it gets the random look it keeps.
    alive = [agent["name"] for agent in agents if agent["active"]]
    if alive:
        record_births(alive)

    return {
        "agents": agents,
        "available": [agent["name"] for agent in agents if agent["available"]],
        "active": [agent["name"] for agent in agents if agent["active"]],
        "count": len(agents),
    }


@router.get("/appearance")
async def get_gem_appearance() -> Dict[str, Any]:
    """Every gem's look: the season, the looks gems were born with, the user's picks."""
    return load_appearance()


@router.put("/appearance")
async def put_gem_appearance(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Replace the user's picks and season; born looks stay. Returns what was stored."""
    try:
        return save_appearance(body)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save gem appearance: {exc}") from exc


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
