"""Message/turn endpoint - the core SSE stream.

The turn itself runs inside the session's daemon. This route submits the user
message, then relays the daemon's structured events until the turn completes.
The wire format is unchanged from when the engine ran its own turn loop, so
clients need no modification.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException  # type: ignore[import-not-found]
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse  # type: ignore[import-not-found]

from .. import sse
from ..server import get_session_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["messages"])

# A turn can legitimately run for a long time (big builds, long tool chains).
# This only bounds silence: any event from the daemon resets it.
EVENT_IDLE_TIMEOUT_SECONDS = 600.0


class MessageRequest(BaseModel):
    content: str
    continuation: bool = False


@router.post("/{session_id}/message")
async def send_message(session_id: str, body: MessageRequest):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    # Subscribe before submitting so no event can land in the gap.
    queue = session.subscribe()

    try:
        accepted = await session.send_message(body.content)
    except Exception as e:
        session.unsubscribe(queue)
        logger.error("send_message failed for %s: %s", session_id, e)
        raise HTTPException(status_code=502, detail=f"daemon unreachable: {e}")

    if not accepted.get("accepted"):
        session.unsubscribe(queue)
        reason = accepted.get("reason", "rejected")
        status = 409 if "in flight" in reason else 400
        raise HTTPException(status_code=status, detail=reason)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=EVENT_IDLE_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield {
                        "data": sse.serialize(
                            sse.error(
                                session_id,
                                "turn_timeout",
                                f"no daemon activity for {EVENT_IDLE_TIMEOUT_SECONDS}s",
                            )
                        )
                    }
                    return

                event.setdefault("session_id", session_id)

                if event.get("type") == "daemon_closed":
                    yield {
                        "data": sse.serialize(
                            sse.error(session_id, "daemon_closed", "daemon exited")
                        )
                    }
                    return

                yield {"data": sse.serialize(event)}

                if event.get("type") == "turn_complete":
                    return
        finally:
            session.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@router.get("/{session_id}/events")
async def stream_events(session_id: str):
    """Follow a session's event stream without submitting a turn.

    A client that reloads mid-turn loses the POST /message stream, but the
    daemon keeps working - and if it is blocked on a permission prompt, the
    answer never produces visible output. This lets a reconnecting client pick
    the turn back up. Ends at turn_complete so it does not leak subscriptions.
    """
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    queue = session.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=EVENT_IDLE_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    return

                event.setdefault("session_id", session_id)
                if event.get("type") == "daemon_closed":
                    yield {
                        "data": sse.serialize(
                            sse.error(session_id, "daemon_closed", "daemon exited")
                        )
                    }
                    return

                yield {"data": sse.serialize(event)}

                if event.get("type") == "turn_complete":
                    return
        finally:
            session.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@router.post("/{session_id}/cancel")
async def cancel_turn(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.cancel_turn()
    return {"ok": True, "session_id": session_id}
