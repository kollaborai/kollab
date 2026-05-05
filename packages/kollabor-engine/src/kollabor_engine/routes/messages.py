"""Message/turn endpoint - the core SSE stream."""

import logging

from fastapi import APIRouter, HTTPException  # type: ignore[import-not-found]
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse  # type: ignore[import-not-found]

from .. import sse
from ..server import get_session_registry
from ..turn_runner import TurnRunner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["messages"])

_turn_runner = TurnRunner()


class MessageRequest(BaseModel):
    content: str
    continuation: bool = False


@router.post("/{session_id}/message")
async def send_message(session_id: str, body: MessageRequest):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session._active_turn_task and not session._active_turn_task.done():
        raise HTTPException(status_code=409, detail="Turn already in progress")

    async def event_generator():
        async for event in _turn_runner.run(session, body.content):
            yield {"data": sse.serialize(event)}

    return EventSourceResponse(event_generator())


@router.post("/{session_id}/cancel")
async def cancel_turn(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.cancel_turn()
    return {"ok": True, "session_id": session_id}
