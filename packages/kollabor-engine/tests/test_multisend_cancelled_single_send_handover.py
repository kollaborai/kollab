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
    never = asyncio.Event()

    async def block_after_admission_may_have_started(_content, *, request_id):
        assert request_id == "cancelled-single"
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
            response_body.append(message.get("body", b""))

    stream_task = asyncio.create_task(response.stream_response(capture_asgi_message))
    await asyncio.wait_for(admission_started.wait(), timeout=1)
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
