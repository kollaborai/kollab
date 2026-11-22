"""A multi-message assistant request owns every turn that it admits (#1368)."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from kollabor_engine.routes import messages
from test_assistant_route import _FakeRegistry, _FakeSession, _install_fake_assistant_stream


@pytest.mark.asyncio
async def test_two_separate_turns_keep_stream_open_until_both_finish(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "first reply", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
                {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert session.send_message.await_count == 2
    assert "second reply" in str(captured["controller"].state["messages"])


@pytest.mark.asyncio
async def test_shared_turn_completes_both_messages(monkeypatch):
    session = _FakeSession([
        {"type": "turn_complete", "request_id": "unrelated"},
        {"type": "token", "text": "combined reply", "request_ids": ["first", "second"]},
        {"type": "turn_complete", "request_ids": ["first", "second"]},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert "combined reply" in str(captured["controller"].state["messages"])
    assert session.send_message.await_count == 2
