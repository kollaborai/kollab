"""GET /sessions/{id}/events follows a session across turns (#266, queued turn missing its first chunk).

The daemon starts a queued turn right after the last one: its turn_start and first token are published
within milliseconds of the last turn_complete. A stream that closed at turn_complete threw those events away
(they were already queued behind it) and the page's reconnect landed a few hundred ms later.
"""

import asyncio
import json

import pytest
from kollabor_engine.routes import messages


class _Session:
    alive = True

    def __init__(self):
        self.queue = asyncio.Queue()
        self.released = 0

    def subscribe(self):
        return self.queue

    def unsubscribe(self, queue):
        assert queue is self.queue
        self.released += 1


async def _next(response):
    try:
        frame = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=2)
    except StopAsyncIteration:
        pytest.fail("the events stream ended before every published event reached the client")
    return json.loads(frame["data"])


@pytest.fixture
def session(monkeypatch):
    fixture = _Session()
    monkeypatch.setattr(messages, "get_session_registry", lambda: {"s1": fixture})
    return fixture


@pytest.mark.asyncio
async def test_live_events_stream_keeps_a_queued_turns_first_events(session):
    published = [
        ("token", "r1", "chunk 10 of 10."),
        ("turn_complete", "r1", None),
        ("turn_start", "r2", None),
        ("token", "r2", "chunk 1 of 10."),
        ("token", "r2", "chunk 2 of 10."),
    ]
    for kind, request_id, text in published:
        session.queue.put_nowait({"type": kind, "request_id": request_id, **({"text": text} if text else {})})

    response = await messages.stream_events("s1")
    received = [await _next(response) for _ in published]

    assert [(e["type"], e["request_id"], e.get("text")) for e in received] == published
    await response.body_iterator.aclose()
    assert session.released == 1


@pytest.mark.asyncio
async def test_live_events_stream_ends_when_the_daemon_closes(session):
    session.queue.put_nowait({"type": "turn_complete", "request_id": "r1"})
    session.queue.put_nowait({"type": "daemon_closed"})

    response = await messages.stream_events("s1")
    assert (await _next(response))["type"] == "turn_complete"
    assert (await _next(response))["type"] == "error"
    with pytest.raises(StopAsyncIteration):
        await response.body_iterator.__anext__()
    assert session.released == 1
