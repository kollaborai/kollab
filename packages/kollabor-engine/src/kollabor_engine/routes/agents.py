"""Agent identity pool routes for the web UI."""

from typing import Any, Dict

from fastapi import APIRouter, Query  # type: ignore[import-not-found]

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
                "available": current is None,
                "active": current is not None,
                "state": current.get("state") if current else "available",
                "current_task": current.get("current_task", "") if current else "",
            }
        )

    return {
        "agents": agents,
        "available": [agent["name"] for agent in agents if agent["available"]],
        "active": [agent["name"] for agent in agents if agent["active"]],
        "count": len(agents),
    }
    return {
        "agents": agents,
        "available": [agent["name"] for agent in agents if agent["available"]],
        "active": [agent["name"] for agent in agents if agent["active"]],
        "count": len(agents),
    }


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



