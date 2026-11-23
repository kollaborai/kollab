"""Regression coverage for ambiguous multi-send admission acknowledgements."""

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
            return {}
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


@pytest.mark.asyncio
async def test_single_send_lost_admission_response_drains_exact_turn_without_ledger(
    monkeypatch,
):
    session = _FakeSession([])

    async def accept_then_lose_reply(_content, *, request_id):
        session._events.put_nowait(
            {"type": "token", "text": "single reply", "request_id": request_id}
        )
        session._events.put_nowait(
            {"type": "turn_complete", "request_id": request_id}
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
                        "id": "single",
                        "parts": [{"type": "text", "text": "one"}],
                    },
                }
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)

    state = captured["controller"].state
    assert [(item["role"], _plain_text(item)) for item in state["messages"]] == [
        ("user", "one"),
        ("assistant", "single reply"),
    ]
    assert session._events.empty()
    assert session.subscriptions == []
    assert session.send_message.await_count == 1
    assert "may have been accepted" in "\n".join(captured["controller"].errors)
    assert state["usage"]["stopReason"] == "error"
    assert getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {}) == {}


@pytest.mark.asyncio
async def test_single_ambiguous_send_reports_full_handover_capacity(monkeypatch):
    session = _FakeSession([])
    clock = type("Clock", (), {"now": 0.0})()
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        messages, "_assistant_correlation_monotonic", lambda: clock.now
    )
    original_next_event = messages._assistant_next_event

    async def expire_requested_timeout(queue, controller, timeout=None):
        if timeout is not None and queue.empty():
            clock.now += timeout
            return {}
        return await original_next_event(queue, controller, timeout)

    monkeypatch.setattr(messages, "_assistant_next_event", expire_requested_timeout)
    ledger = messages._assistant_handover_ledger(session)
    for index in range(messages._ASSISTANT_HANDOVER_MAX_ENTRIES):
        token = f"{index:032x}"
        ledger[token] = {
            "expires_at": 3600.0,
            "status": "available",
            "record": {"request_ids": [f"parked-{index}"]},
            "queue": None,
            "context": None,
        }

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
                        "id": "single",
                        "parts": [{"type": "text", "text": "one"}],
                    },
                }
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)

    error = "\n".join(captured["controller"].errors)
    assert session.send_message.await_count == 1
    assert len(ledger) == messages._ASSISTANT_HANDOVER_MAX_ENTRIES
    assert session.subscriptions == [], "no capability can retain a full ledger slot"
    assert "single" in error
    assert "may still be running" in error
    assert "handover capacity" in error
    assert "late events cannot be guaranteed" in error
    assert "No messages were resubmitted." in error
    assert messages._ASSISTANT_HANDOVER_STATE_KEY not in captured["controller"].state
    assert captured["controller"].state["usage"]["stopReason"] == "error"


@pytest.mark.asyncio
async def test_revocation_after_ambiguous_admission_does_not_publish_or_persist(
    monkeypatch,
):
    from types import SimpleNamespace

    from kollabor_engine.session_context import (
        SESSION_READ_CONTEXT_SCOPE_KEY,
        SessionReadContext,
    )

    class PhoneSession(_FakeSession):
        def subscribe(self, context=None):
            self.read_context = context
            return super().subscribe()

    session = PhoneSession([])
    context = SessionReadContext(owner_id="owner", phone_id="phone")
    request = SimpleNamespace(
        scope={"state": {SESSION_READ_CONTEXT_SCOPE_KEY: context}}
    )
    persisted = []

    def save_media(content, session_id):
        persisted.append((content, session_id))
        return []

    monkeypatch.setattr(messages, "_save_sent_media", save_media)

    async def revoke_then_lose_reply(_content, *, request_id):
        session._events.put_nowait(
            {"type": "token", "text": "private late reply", "request_id": request_id}
        )
        context.revoked = True
        raise ConnectionError("admission response lost after enqueue")

    session.send_message = AsyncMock(side_effect=revoke_then_lose_reply)
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
                        "id": "private",
                        "parts": [
                            {"type": "text", "text": "private input"},
                            {"type": "image", "image": "data:image/png;base64,aA=="},
                        ],
                    },
                },
                {
                    "type": "add-message",
                    "message": {
                        "id": "unsent",
                        "parts": [{"type": "text", "text": "next"}],
                    },
                },
            ],
            state={"messages": []},
        ),
        request,
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)

    state = captured["controller"].state
    assert persisted == []
    assert captured["controller"].errors == []
    assert "private input" not in repr(state)
    assert "private late reply" not in repr(state)
    assert state["messages"] == []
    assert session.subscriptions == []


@pytest.mark.asyncio
async def test_cancellation_during_admission_keeps_ambiguous_send_owned(monkeypatch):
    session = _FakeSession([])
    admission_started = asyncio.Event()

    async def enqueue_then_wait_for_admission_reply(_content, *, request_id):
        session._events.put_nowait(
            {"type": "token", "text": "reply after disconnect", "request_id": request_id}
        )
        session._events.put_nowait(
            {"type": "turn_complete", "request_id": request_id}
        )
        admission_started.set()
        await asyncio.Event().wait()
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=enqueue_then_wait_for_admission_reply)
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
                        "id": "next",
                        "parts": [{"type": "text", "text": "two"}],
                    },
                },
            ],
            state={"messages": []},
        ),
    )
    stream_task = asyncio.create_task(drain(response.stream))
    await asyncio.wait_for(admission_started.wait(), timeout=1)
    state = copy.deepcopy(captured["controller"].state)
    token = state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    retained_queue = session.subscriptions[0]

    stream_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stream_task

    entry = messages._assistant_handover_ledger(session)[token]
    assert entry["status"] == "available"
    assert entry["queue"] is retained_queue
    assert retained_queue in session.subscriptions
    assert entry["record"]["possibly_accepted_request_ids"] == ["first"]
    assert entry["record"]["request_ids"] == ["first"]
