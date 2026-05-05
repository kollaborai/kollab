"""Hub mesh REST routes for kollabor-engine.

Exposes agent presence, output, status, and messaging through
the engine REST API by reading presence files and querying
agent unix sockets via HubBridge.

All endpoints require bearer token auth (server-level middleware).
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query  # type: ignore[import-not-found]
from pydantic import BaseModel

from ..hub_bridge import HubBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hub", tags=["hub"])

# Shared bridge instance (stateless, safe to reuse)
_bridge = HubBridge()


class SendMessageRequest(BaseModel):
    target: str  # identity or agent_id
    content: str
    from_identity: str = "api"


@router.get("/agents")
async def list_agents(
    refresh: bool = Query(False, description="Bypass cache, re-read presence"),
):
    """List all active hub agents from presence files."""
    agents = _bridge.get_agents(use_cache=not refresh)
    return {"agents": agents, "count": len(agents)}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, ping: bool = Query(False)):
    """Get details for a single hub agent."""
    agent = _bridge.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    result: Dict[str, Any] = {"agent": agent}

    if ping:
        alive = await _bridge.ping_agent(agent_id)
        result["socket_alive"] = alive

    return result


@router.get("/agents/{agent_id}/output")
async def get_agent_output(
    agent_id: str,
    lines: int = Query(100, ge=1, le=1000),
):
    """Fetch recent output from an agent via unix socket."""
    agent = _bridge.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    output = await _bridge.get_agent_output(agent_id, lines=lines)
    if output is None:
        return {
            "agent_id": agent_id,
            "output": None,
            "error": "Agent socket unavailable or timed out",
        }

    return {"agent_id": agent_id, "output": output}


@router.get("/agents/{agent_id}/status")
async def get_agent_status(agent_id: str):
    """Fetch current status from an agent via unix socket."""
    agent = _bridge.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    status = await _bridge.get_agent_status(agent_id)
    if status is None:
        return {
            "agent_id": agent_id,
            "status": None,
            "error": "Agent socket unavailable or timed out",
        }

    return {"agent_id": agent_id, "status": status}


@router.post("/messages")
async def send_message(body: SendMessageRequest):
    """Send a message to a hub agent via unix socket.

    Target should be an identity string (e.g. 'koordinator').
    The 'to' field is set correctly so TRIGGER_LLM_CONTINUE fires.
    """
    success = await _bridge.send_message_to_identity(
        body.target,
        body.content,
        from_identity=body.from_identity,
    )

    if not success:
        raise HTTPException(
            status_code=503,
            detail=f"Could not deliver message to '{body.target}' "
            "(agent offline or socket unavailable)",
        )

    return {"ok": True, "target": body.target, "delivered": True}


@router.get("/feed")
async def hub_feed():
    """SSE endpoint streaming hub events (roster changes, messages).

    Note: Full event streaming requires a watcher on presence/message
    files. This initial version provides a snapshot on connect and
    will be extended with live events in phase 7b (WebSocket).
    """
    from ..sse import serialize

    agents = _bridge.get_agents()

    # For now, return initial snapshot as SSE event
    event = {
        "type": "hub_snapshot",
        "agents": agents,
        "count": len(agents),
    }

    async def snapshot_generator():
        yield f"data: {serialize(event)}\n\n"

    from starlette.responses import StreamingResponse  # type: ignore[import-not-found]

    return StreamingResponse(
        snapshot_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
