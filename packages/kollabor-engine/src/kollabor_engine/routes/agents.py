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
