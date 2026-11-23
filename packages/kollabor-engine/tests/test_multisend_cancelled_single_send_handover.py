"""Real transport regression for canceled single-send admission handovers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from assistant_stream.serialization import AssistantTransportResponse
from kollabor_engine.routes import messages
from test_assistant_route import _FakeRegistry, _FakeSession


class _FanoutSession(_FakeSession):
    """Give each route its own subscription and deliver daemon events to all."""

    def subscribe(self, context=None):
        queue = asyncio.Queue(maxsize=100)
        self.subscriptions.append(queue)
        return queue


@pytest.mark.asyncio
async def test_cancelled_single_send_handover_token_reaches_real_transport_client(
    monkeypatch,
):
    session = _FanoutSession([])
    admission_started = asyncio.Event()
    capability_frame_sent = asyncio.Event()
    never = asyncio.Event()

    async def block_after_admission_may_have_started(_content, *, request_id):
        assert request_id == "cancelled-single"
        assert capability_frame_sent.is_set(), (
            "the ASGI send callback must complete the capability frame before admission"
        )
        admission_started.set()
        # Model a transport call whose remote side may have accepted the
        # request, while this route never receives its admission response.
        await never.wait()

    session.send_message = AsyncMock(side_effect=block_after_admission_may_have_started)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "cancelled-single",
                        "parts": [{"type": "text", "text": "send once"}],
                    },
                }
            ],
            state={"messages": []},
        ),
    )
    assert isinstance(response, AssistantTransportResponse)

    response_body: list[bytes] = []

    async def capture_asgi_message(message):
        if message["type"] == "http.response.body":
            payload = message.get("body", b"")
            wire = payload if isinstance(payload, bytes) else str(payload).encode()
            response_body.append(wire)
            ledger = getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {})
            if ledger and next(iter(ledger)).encode("ascii") in wire:
                capability_frame_sent.set()

    stream_task = asyncio.create_task(response.stream_response(capture_asgi_message))
    await asyncio.wait_for(admission_started.wait(), timeout=1)
    assert capability_frame_sent.is_set()
    assert session.subscriptions, "the route must own an event subscription"
    original_queue = session.subscriptions[0]

    stream_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stream_task, timeout=1)

    ledger = getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {})
    assert len(ledger) == 1, "ambiguous accepted work must remain recoverable"
    token, entry = next(iter(ledger.items()))
    assert entry["status"] == "available"
    assert entry["queue"] is original_queue
    assert session.send_message.await_count == 1

    # Exercise the installed assistant_stream implementation and actual
    # AssistantTransportResponse above. Cancellation must not leave the server
    # holding a recovery capability that the disconnected client never saw.
    wire = b"".join(response_body)
    assert token.encode("ascii") in wire, (
        "the canceled single-send response retained an inaccessible handover: "
        f"token={token}, status={entry.get('status')}, "
        f"queue_owned={original_queue in session.subscriptions}, body={wire!r}"
    )


@pytest.mark.asyncio
async def test_revocation_while_capability_send_is_blocked_prevents_admission(
    monkeypatch,
):
    session = _FanoutSession([])
    session.send_message = AsyncMock(
        return_value={"accepted": True, "request_id": "revoked-before-send"}
    )
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "revoked-before-send",
                        "parts": [{"type": "text", "text": "must not submit"}],
                    },
                }
            ],
            state={"messages": []},
        ),
    )
    token_frame_started = asyncio.Event()
    release_send = asyncio.Event()
    response_body: list[bytes] = []

    async def revoke_and_block_token_frame(message):
        if message["type"] == "http.response.body":
            payload = message.get("body", b"")
            wire = payload if isinstance(payload, bytes) else str(payload).encode()
            response_body.append(wire)
            ledger = getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {})
            if ledger:
                token = next(iter(ledger))
                if token.encode("ascii") in wire:
                    assert session.subscriptions
                    session.subscriptions[0].revoked = True
                    token_frame_started.set()
                    await release_send.wait()

    stream_task = asyncio.create_task(
        response.stream_response(revoke_and_block_token_frame)
    )
    await asyncio.wait_for(token_frame_started.wait(), timeout=1)
    assert session.send_message.await_count == 0
    release_send.set()
    await asyncio.wait_for(stream_task, timeout=1)

    assert session.send_message.await_count == 0
    assert session.subscriptions == []
    assert getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {}) == {}
    wire = b"".join(response_body)
    assert b"must not submit" not in wire


@pytest.mark.asyncio
async def test_stalled_capability_send_times_out_before_admission(monkeypatch):
    session = _FanoutSession([])
    session.send_message = AsyncMock(
        return_value={"accepted": True, "request_id": "stalled-capability"}
    )
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.02)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "stalled-capability",
                        "parts": [{"type": "text", "text": "send after capability"}],
                    },
                }
            ],
            state={"messages": []},
        ),
    )
    token_frame_started = asyncio.Event()
    release_send = asyncio.Event()

    async def hold_capability_frame(message):
        if message["type"] == "http.response.body":
            payload = message.get("body", b"")
            wire = payload if isinstance(payload, bytes) else str(payload).encode()
            ledger = getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {})
            if ledger and next(iter(ledger)).encode("ascii") in wire:
                token_frame_started.set()
                await release_send.wait()

    stream_task = asyncio.create_task(response.stream_response(hold_capability_frame))
    await asyncio.wait_for(token_frame_started.wait(), timeout=1)

    async def wait_for_reservation_release():
        while getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {}):
            await asyncio.sleep(0.005)

    released_while_asgi_send_blocked = False
    try:
        await asyncio.wait_for(wait_for_reservation_release(), timeout=0.5)
        released_while_asgi_send_blocked = True
    except asyncio.TimeoutError:
        pass
    finally:
        release_send.set()
        if not stream_task.done():
            stream_task.cancel()
        await asyncio.gather(stream_task, return_exceptions=True)

    assert released_while_asgi_send_blocked
    assert session.send_message.await_count == 0
    assert session.subscriptions == []
    assert getattr(session, messages._ASSISTANT_HANDOVER_LEDGER_ATTR, {}) == {}
