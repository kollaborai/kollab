"""Regression coverage for ambiguous multi-send admission acknowledgements."""

import asyncio
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
async def test_second_send_events_remain_owned_after_admission_response_is_lost(
    monkeypatch,
):
    session = _FakeSession(
        [
            {"type": "token", "text": "first reply", "request_id": "first"},
            {"type": "turn_complete", "request_id": "first"},
        ]
    )

    async def accept_then_lose_reply(_content, *, request_id):
        if request_id == "second":
            # The daemon admitted and ran this request, but the caller lost the
            # admission response before it could record the acknowledgement.
            session._events.put_nowait(
                {"type": "token", "text": "second reply", "request_id": "second"}
            )
            session._events.put_nowait(
                {"type": "turn_complete", "request_id": "second"}
            )
            raise ConnectionError("admission response lost after enqueue")
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept_then_lose_reply)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
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
                        "id": "second",
                        "parts": [{"type": "text", "text": "two"}],
                    },
                },
                {
                    "type": "add-message",
                    "message": {
                        "id": "third",
                        "parts": [{"type": "text", "text": "three"}],
                    },
                },
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)

    state = captured["controller"].state
    messages_state = state["messages"]
    assert [(item["role"], _plain_text(item)) for item in messages_state] == [
        ("user", "one"),
        ("assistant", "first reply"),
        ("user", "two"),
        ("assistant", "second reply"),
    ]
    assert session._events.empty(), "the second terminal event must be consumed"
    attempted_ids = [
        call.kwargs["request_id"] for call in session.send_message.await_args_list
    ]
    assert attempted_ids == [
        "first",
        "second",
    ], "reconciliation must not resubmit the possibly accepted message"
    error = "\n".join(captured["controller"].errors)
    assert "Acknowledged as accepted: first" in error
    assert "Possibly accepted despite the admission error: second." in error
    assert "Not submitted: third." in error
    assert "No messages were resubmitted." in error
    assert state["usage"]["stopReason"] == "error"
    assert messages_state[-1]["status"]["type"] == "incomplete"


@pytest.mark.asyncio
async def test_first_send_events_remain_owned_after_admission_response_is_lost(
    monkeypatch,
):
    session = _FakeSession([])

    async def accept_then_lose_reply(_content, *, request_id):
        assert request_id == "first"
        session._events.put_nowait(
            {"type": "token", "text": "first reply", "request_id": "first"}
        )
        session._events.put_nowait(
            {"type": "turn_complete", "request_id": "first"}
        )
        raise ConnectionError("admission response lost after enqueue")

    session.send_message = AsyncMock(side_effect=accept_then_lose_reply)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
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
                        "id": "second",
                        "parts": [{"type": "text", "text": "two"}],
                    },
                },
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)

    state = captured["controller"].state
    assert [(item["role"], _plain_text(item)) for item in state["messages"]] == [
        ("user", "one"),
        ("assistant", "first reply"),
    ]
    assert session._events.empty()
    assert [call.kwargs["request_id"] for call in session.send_message.await_args_list] == [
        "first",
    ]
    error = "\n".join(captured["controller"].errors)
    assert "Possibly accepted despite the admission error: first." in error
    assert "Not submitted: second." in error
    assert state["usage"]["stopReason"] == "error"


@pytest.mark.asyncio
async def test_first_ambiguous_send_without_events_times_out_and_keeps_handover(
    monkeypatch,
):
    session = _FakeSession([])
    clock = type("Clock", (), {"now": 0.0})()
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        messages, "_assistant_correlation_monotonic", lambda: clock.now
    )
    original_next_event = messages._assistant_next_event

    async def expire_requested_timeout(queue, controller, timeout=None):
        if timeout is not None:
            clock.now += timeout
            return None
        return await original_next_event(queue, controller, timeout)

    monkeypatch.setattr(messages, "_assistant_next_event", expire_requested_timeout)
    session.send_message = AsyncMock(
        side_effect=ConnectionError("admission response lost before events")
    )
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
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
                        "id": "second",
                        "parts": [{"type": "text", "text": "two"}],
                    },
                },
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)

    state = captured["controller"].state
    token = state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    entry = messages._assistant_handover_ledger(session)[token]
    assert "possibly accepted message(s) first" in "\n".join(
        captured["controller"].errors
    )
    assert "may still be running" in "\n".join(captured["controller"].errors)
    assert entry["status"] == "available"
    assert entry["record"]["request_ids"] == ["first"]
    assert entry["queue"] in session.subscriptions
    assert session.send_message.await_count == 1
    assert state["usage"]["stopReason"] == "error"
