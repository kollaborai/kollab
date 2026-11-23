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
