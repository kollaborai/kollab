"""Real transport coverage for resuming private goal replies."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from assistant_stream.serialization import AssistantTransportResponse
from kollabor_engine.routes import messages
from test_assistant_route import _FakeRegistry, _FakeSession


class _FanoutSession(_FakeSession):
    """Give each route its own subscription and fan out daemon events."""

    def subscribe(self, context=None):
        queue = asyncio.Queue(maxsize=100)
        self.subscriptions.append(queue)
        return queue

    def publish(self, event):
        for queue in tuple(self.subscriptions):
            queue.put_nowait(dict(event))


async def _drain_real_response(response, chunks: list[bytes]) -> None:
    async def send(message):
        if message["type"] == "http.response.body":
            payload = message.get("body", b"")
            chunks.append(
                payload if isinstance(payload, bytes) else str(payload).encode()
            )

    await response.stream_response(send)


@pytest.mark.asyncio
async def test_reconnect_resumes_private_goal_reply_before_followup_terminal(
    monkeypatch,
):
    session = _FanoutSession([])
    first_poll_started = asyncio.Event()
    poll_count = 0

    async def poll_goal_reply(reply_id):
        nonlocal poll_count
        assert reply_id == "private-reply"
        poll_count += 1
        if poll_count == 1:
            first_poll_started.set()
            return {"status": "pending"}
        return {
            "status": "complete",
            "success": True,
            "conversation_uid": "conversation-1",
            "message": "private goal result",
        }

    async def send_message(_content, *, request_id, **kwargs):
        if request_id == "goal-command":
            assert kwargs["private_goal_reply"] is True
            return {
                "accepted": True,
                "request_id": "goal-request",
                "goal_request_id": "goal-request",
                "goal_reply_id": "private-reply",
            }
        if request_id == "next":
            session.publish(
                {"type": "token", "text": "next reply", "request_id": "next"}
            )
            session.publish({"type": "turn_complete", "request_id": "next"})
            return {"accepted": True, "request_id": request_id}
        raise AssertionError(f"unexpected submission {request_id}")

    session.poll_goal_reply = AsyncMock(side_effect=poll_goal_reply)
    session.send_message = AsyncMock(side_effect=send_message)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )

    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "goal-command",
                        "parts": [{"type": "text", "text": "/goal show"}],
                    },
                }
            ],
            state={"messages": [], "conversationUid": "conversation-1"},
        ),
    )
    assert isinstance(first_response, AssistantTransportResponse)
    first_chunks: list[bytes] = []
    first_task = asyncio.create_task(_drain_real_response(first_response, first_chunks))
    await asyncio.wait_for(first_poll_started.wait(), timeout=1)

    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first_task, timeout=1)

    first_wire = b"".join(first_chunks)
    ledger = messages._assistant_handover_ledger(session)
    assert len(ledger) == 1
    token, entry = next(iter(ledger.items()))
    assert token.encode("ascii") in first_wire
    assert entry["status"] == "available"
    assert entry["record"]["request_ids"] == ["private-reply"]
    assert entry["queue"] in session.subscriptions

    resumed_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "next",
                        "parts": [{"type": "text", "text": "follow-up"}],
                    },
                }
            ],
            state={
                "messages": [],
                "conversationUid": "conversation-1",
                messages._ASSISTANT_HANDOVER_STATE_KEY: token,
            },
        ),
    )
    assert isinstance(resumed_response, AssistantTransportResponse)
    resumed_chunks: list[bytes] = []
    resumed_task = asyncio.create_task(
        _drain_real_response(resumed_response, resumed_chunks)
    )
    try:
        await asyncio.wait_for(asyncio.shield(resumed_task), timeout=1)
    except asyncio.TimeoutError:
        resumed_task.cancel()
        await asyncio.gather(resumed_task, return_exceptions=True)
        poll_ids = [call.args[0] for call in session.poll_goal_reply.await_args_list]
        submitted_ids = [
            call.kwargs["request_id"] for call in session.send_message.await_args_list
        ]
        pytest.fail(
            "resumed private goal reply never reached a terminal event; "
            "the follow-up terminal cannot close the handover; "
            f"poll_ids={poll_ids}, submitted_ids={submitted_ids}"
        )

    resumed_wire = b"".join(resumed_chunks)
    assert [call.args[0] for call in session.poll_goal_reply.await_args_list] == [
        "private-reply",
        "private-reply",
    ]
    assert [
        call.kwargs["request_id"] for call in session.send_message.await_args_list
    ] == ["goal-command", "next"]
    assert b"private goal result" in resumed_wire
    assert b"next reply" in resumed_wire
    assert b"data: [DONE]" in resumed_wire
    assert token not in messages._assistant_handover_ledger(session)
    assert session.subscriptions == []


@pytest.mark.asyncio
async def test_reconnect_does_not_render_a_queued_private_reply_twice(monkeypatch):
    session = _FanoutSession([])
    first_poll_started = asyncio.Event()
    second_poll_returned = asyncio.Event()
    third_poll_returned = asyncio.Event()
    next_admission_started = asyncio.Event()
    release_later_admission = asyncio.Event()
    never = asyncio.Event()
    poll_count = 0

    async def poll_goal_reply(reply_id):
        nonlocal poll_count
        assert reply_id == "private-reply"
        poll_count += 1
        if poll_count == 1:
            first_poll_started.set()
            return {"status": "pending"}
        if poll_count == 2:
            second_poll_returned.set()
        else:
            third_poll_returned.set()
        return {
            "status": "complete",
            "success": True,
            "conversation_uid": "conversation-1",
            "message": "private goal result",
        }

    async def send_message(_content, *, request_id, **kwargs):
        if request_id == "goal-command":
            return {
                "accepted": True,
                "request_id": "goal-request",
                "goal_request_id": "goal-request",
                "goal_reply_id": "private-reply",
            }
        if request_id == "next":
            session.publish({"type": "token", "text": "next reply", "request_id": "next"})
            session.publish({"type": "turn_complete", "request_id": "next"})
            next_admission_started.set()
            await never.wait()
        if request_id == "later":
            await release_later_admission.wait()
            session.publish({"type": "token", "text": "later reply", "request_id": "later"})
            session.publish({"type": "turn_complete", "request_id": "later"})
            return {"accepted": True, "request_id": request_id}
        raise AssertionError(f"unexpected submission {request_id}")

    session.poll_goal_reply = AsyncMock(side_effect=poll_goal_reply)
    session.send_message = AsyncMock(side_effect=send_message)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )

    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "goal-command",
                        "parts": [{"type": "text", "text": "/goal show"}],
                    },
                }
            ],
            state={"messages": [], "conversationUid": "conversation-1"},
        ),
    )
    first_task = asyncio.create_task(_drain_real_response(first_response, []))
    await asyncio.wait_for(first_poll_started.wait(), timeout=1)
    first_task.cancel()
    await asyncio.gather(first_task, return_exceptions=True)

    token = next(iter(messages._assistant_handover_ledger(session)))
    second_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "next",
                        "parts": [{"type": "text", "text": "next"}],
                    },
                }
            ],
            state={messages._ASSISTANT_HANDOVER_STATE_KEY: token},
        ),
    )
    second_task = asyncio.create_task(_drain_real_response(second_response, []))
    await asyncio.wait_for(next_admission_started.wait(), timeout=1)
    await asyncio.wait_for(second_poll_returned.wait(), timeout=1)
    await asyncio.sleep(0)
    second_task.cancel()
    await asyncio.gather(second_task, return_exceptions=True)

    token = next(iter(messages._assistant_handover_ledger(session)))
    third_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "later",
                        "parts": [{"type": "text", "text": "later"}],
                    },
                }
            ],
            state={messages._ASSISTANT_HANDOVER_STATE_KEY: token},
        ),
    )
    third_chunks: list[bytes] = []
    third_task = asyncio.create_task(_drain_real_response(third_response, third_chunks))
    await asyncio.wait_for(third_poll_returned.wait(), timeout=1)
    await asyncio.sleep(0)
    release_later_admission.set()
    await asyncio.wait_for(third_task, timeout=1)

    wire = b"".join(third_chunks).decode()
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in wire.splitlines()
        if line.startswith("data: {")
    ]
    private_deltas = [
        frame["textDelta"]
        for frame in frames
        if frame.get("type") == "text-delta"
        and frame.get("textDelta") == "private goal result"
    ]
    assert private_deltas == ["private goal result"]
    assert [
        call.kwargs["request_id"] for call in session.send_message.await_args_list
    ] == ["goal-command", "next", "later"]
    assert token not in messages._assistant_handover_ledger(session)
    assert session.subscriptions == []


@pytest.mark.asyncio
async def test_replayed_goal_in_resumed_run_keeps_the_retained_reply(monkeypatch):
    """A replayed goal must not orphan the handover's retained queue (#1368)."""
    session = _FanoutSession([])
    first_poll_started = asyncio.Event()
    poll_count = 0

    async def poll_goal_reply(reply_id):
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            first_poll_started.set()
            return {"status": "pending"}
        return {
            "status": "complete",
            "success": True,
            "conversation_uid": "conversation-1",
            "message": "private goal result",
        }

    sends = 0

    async def send_message(_content, *, request_id, **kwargs):
        nonlocal sends
        sends += 1
        if sends == 1:
            return {
                "accepted": True,
                "request_id": "goal-request",
                "goal_request_id": "goal-request",
                "goal_reply_id": "private-reply",
            }
        return {
            "accepted": True,
            "completed": True,
            "replayed": True,
            "request_id": "goal-request",
            "message": "replayed goal",
        }

    session.poll_goal_reply = AsyncMock(side_effect=poll_goal_reply)
    session.send_message = AsyncMock(side_effect=send_message)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )

    def goal_request(state):
        return messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "goal-command",
                        "parts": [{"type": "text", "text": "/goal show"}],
                    },
                }
            ],
            state=state,
        )

    first = await messages.assistant_transport(
        session.session_id,
        goal_request({"messages": [], "conversationUid": "conversation-1"}),
    )
    first_chunks: list[bytes] = []
    first_task = asyncio.create_task(_drain_real_response(first, first_chunks))
    await asyncio.wait_for(first_poll_started.wait(), timeout=1)
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first_task, timeout=1)
    token = next(iter(messages._assistant_handover_ledger(session)))

    resumed = await messages.assistant_transport(
        session.session_id,
        goal_request(
            {
                "messages": [],
                "conversationUid": "conversation-1",
                messages._ASSISTANT_HANDOVER_STATE_KEY: token,
            }
        ),
    )
    chunks: list[bytes] = []
    task = asyncio.create_task(_drain_real_response(resumed, chunks))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2)
    except asyncio.TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        pytest.fail("replayed goal orphaned the retained reply; the run never ended")
    wire = b"".join(chunks)
    assert b"private goal result" in wire
    assert b"data: [DONE]" in wire


@pytest.mark.asyncio
async def test_cancelled_stream_keeps_its_queue_and_a_reconnect_gets_the_late_reply(
    monkeypatch,
):
    """Proves why a lost stream keeps its queue: the reply is not lost (#1368)."""
    session = _FanoutSession([])
    first_sent = asyncio.Event()

    async def send_message(_content, *, request_id, **kwargs):
        if request_id == "first":
            first_sent.set()
        if request_id == "next":
            session.publish({"type": "token", "text": "next reply", "request_id": "next"})
            session.publish({"type": "turn_complete", "request_id": "next"})
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=send_message)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )

    def request(message_id, state):
        return messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": message_id,
                        "parts": [{"type": "text", "text": message_id}],
                    },
                }
            ],
            state=state,
        )

    first = await messages.assistant_transport(
        session.session_id, request("first", {"messages": []})
    )
    first_task = asyncio.create_task(_drain_real_response(first, []))
    await asyncio.wait_for(first_sent.wait(), timeout=1)
    await asyncio.sleep(0.05)
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first_task, timeout=1)

    ledger = messages._assistant_handover_ledger(session)
    assert len(ledger) == 1
    token = next(iter(ledger))
    assert len(session.subscriptions) == 1  # the queue survives the lost stream

    # The reply finishes while nobody is connected.
    session.publish({"type": "token", "text": "late reply", "request_id": "first"})
    session.publish({"type": "turn_complete", "request_id": "first"})

    resumed = await messages.assistant_transport(
        session.session_id,
        request("next", {"messages": [], messages._ASSISTANT_HANDOVER_STATE_KEY: token}),
    )
    chunks: list[bytes] = []
    await asyncio.wait_for(_drain_real_response(resumed, chunks), timeout=2)
    wire = b"".join(chunks)
    assert b"late reply" in wire
    assert b"next reply" in wire
    assert b"data: [DONE]" in wire
    assert token not in messages._assistant_handover_ledger(session)
    assert session.subscriptions == []
