"""A prompt its turn left open (a Stop, or 5 min unanswered) loads as parked.

A window that loads the chat then knows the turn behind the card ended."""

import asyncio
from types import SimpleNamespace

import pytest
from kollabor_engine.routes import permissions as permissions_route
from kollabor_engine.server import get_session_registry
from kollabor_engine.session import EngineSession


@pytest.mark.asyncio
async def test_a_parked_prompt_loads_parked_until_it_is_answered():
    events: asyncio.Queue = asyncio.Queue()
    session = EngineSession(session_id="sess_parked", profile=None, approval_mode="confirm_all")
    session.daemon = SimpleNamespace(
        alive=True, pid=0, identity="", agent_name="",
        subscribe=lambda: events, unsubscribe=lambda queue: None,
    )
    registry = get_session_registry()
    registry[session.session_id] = session
    tracker = asyncio.create_task(session._track_events())
    try:
        details = {"tool_id": "t1", "command": "touch notes.txt"}
        events.put_nowait({"type": "permission_request", "tool_id": "t1", "details": details})
        events.put_nowait({"type": "permission_parked", "tool_id": "t1"})
        events.put_nowait({"type": "permission_parked", "tool_id": "never-asked"})
        for _ in range(50):
            await asyncio.sleep(0.01)
            if events.empty():
                break
        await asyncio.sleep(0.02)

        answer = await permissions_route.get_permissions(session.session_id)
        assert answer["pending"] == ["t1"]
        prompt = answer["pending_prompts"][0]
        assert prompt["details"] == {**details, "parked": True}
        assert "parked" not in details  # the stored request is not edited in place

        events.put_nowait({"type": "permission_granted", "tool_id": "t1", "parked": True})
        await asyncio.sleep(0.05)
        assert (await permissions_route.get_permissions(session.session_id))["pending"] == []
    finally:
        events.put_nowait({"type": "daemon_closed"})
        await asyncio.wait_for(tracker, 1)
        registry.pop(session.session_id, None)
