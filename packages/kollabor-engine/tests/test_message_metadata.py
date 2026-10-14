from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_engine.routes.messages import MessageRequest
from kollabor_engine.session import EngineSession


@pytest.mark.asyncio
async def test_session_forwards_message_envelope_metadata():
    state = type("State", (), {"send_message": AsyncMock(return_value={
        "accepted": True, "request_id": "req-7", "turn_id": "turn-7",
    })})()
    session = object.__new__(EngineSession)
    session.daemon = SimpleNamespace(state=state)
    result = await session.send_message(
        "hello", request_id="req-7", thread_id="thread-7",
        origin_context={"sender": "agent-a", "authenticated": True},
    )
    assert result["turn_id"] == "turn-7"
    state.send_message.assert_awaited_once_with(
        "hello", request_id="req-7", thread_id="thread-7",
        origin_context={"sender": "agent-a", "authenticated": True},
    )


def test_message_request_accepts_separate_metadata_envelope():
    body = MessageRequest(
        content="hello", request_id="req-7", thread_id="thread-7",
        origin_context={"sender": "agent-a", "operator": False},
    )
    assert body.request_id == "req-7"
    assert body.thread_id == "thread-7"
    assert body.origin_context == {"sender": "agent-a", "operator": False}
