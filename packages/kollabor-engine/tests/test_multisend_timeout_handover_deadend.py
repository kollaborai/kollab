"""Regression coverage for an ambiguous send parked past the idle deadline."""

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest
from kollabor_engine.routes import messages
from test_assistant_route import (
    _FakeRegistry,
    _FakeSession,
    _install_fake_assistant_stream,
)


def _plain_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "")
        for part in content or []
        if isinstance(part, dict) and part.get("type") == "text"
    )


@pytest.mark.asyncio
async def test_late_ambiguous_turn_is_reconciled_before_followup_message(monkeypatch):
    session = _FakeSession([])
    clock = type("Clock", (), {"now": 0.0})()
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        messages, "_assistant_correlation_monotonic", lambda: clock.now
    )
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    original_next_event = messages._assistant_next_event

    async def expire_only_when_queue_is_empty(queue, controller, timeout=None):
        if timeout is not None and queue.empty():
            clock.now += timeout
            return {}
        return await original_next_event(queue, controller, timeout)

    monkeypatch.setattr(
        messages, "_assistant_next_event", expire_only_when_queue_is_empty
    )

    async def accept_or_lose_reply(_content, *, request_id):
        if request_id == "first":
            raise ConnectionError("admission response lost before events")
        session._events.put_nowait(
            {"type": "token", "text": "new reply", "request_id": request_id}
        )
        session._events.put_nowait(
            {"type": "turn_complete", "request_id": request_id}
        )
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept_or_lose_reply)
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "first",
                        "parts": [{"type": "text", "text": "one"}],
                    },
                },
                {
                    "type": "add-message",
                    "message": {
                        "id": "unsent",
                        "parts": [{"type": "text", "text": "two"}],
                    },
                },
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)

    state = copy.deepcopy(captured["controller"].state)
    token = state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    entry = messages._assistant_handover_ledger(session)[token]
    assert entry["status"] == "available"
    assert entry["record"]["possibly_accepted_request_ids"] == ["first"]
    assert entry["record"]["request_ids"] == ["first"]
    assert entry["queue"] in session.subscriptions

    # The admission RPC threw before events reached the first stream. They
    # arrive after its bounded idle timeout and remain owned by the handover.
    retained_queue = entry["queue"]
    retained_queue.put_nowait(
        {"type": "token", "text": "late first reply", "request_id": "first"}
    )
    retained_queue.put_nowait({"type": "turn_complete", "request_id": "first"})

    next_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "next",
                        "parts": [{"type": "text", "text": "new input"}],
                    },
                }
            ],
            state=state,
        ),
    )
    await asyncio.wait_for(drain(next_response.stream), timeout=1)

    assert [
        call.kwargs["request_id"] for call in session.send_message.await_args_list
    ] == [
        "first",
        "next",
    ], "the ambiguous first send must not be resubmitted"
    assert retained_queue.empty(), (
        "late first-turn and follow-up events must be drained"
    )
    assert token not in messages._assistant_handover_ledger(session)
    assert session.subscriptions == []
    assert [
        (message["role"], _plain_text(message))
        for message in captured["controller"].state["messages"]
    ] == [
        ("user", "one"),
        ("assistant", "late first reply"),
        ("user", "new input"),
        ("assistant", "new reply"),
    ]
    assert [
        message["status"]["type"]
        for message in captured["controller"].state["messages"]
        if message["role"] == "assistant"
    ] == ["complete", "complete"]
