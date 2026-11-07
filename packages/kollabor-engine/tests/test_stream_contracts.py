"""Contract tests: the exact event sequence a caller of the engine sees (#712).

Other stream tests assert that an event shows up somewhere. These pin the whole
ordered sequence, and the counts, that a caller reads from the wire. #549 round 1
had a model fallback that began after the failed turn's ``turn_complete``; the
request's stream ended on that completion and unsubscribed, so its caller never
got the notice or the answer, and no test noticed.

How they run: a scripted daemon writes frames to a real unix socket, a real
``DaemonHandle`` reads them, and the real ``POST /sessions/{id}/message`` route
streams them over HTTP. The tests parse the SSE ``data:`` lines like a client
does. ``GET /sessions/{id}/events`` is read through its response iterator.

The contracts (change one only on purpose, and say so in the PR):

* A turn's stream is its events in the order the daemon published them, ending
  with exactly one ``turn_complete``. Nothing follows it on that stream.
* A failed turn streams ``error`` once, then its ``turn_complete``.
* A cancelled turn streams ``turn_cancelled``, then a ``turn_complete`` whose
  ``stop_reason`` is ``cancelled``, and no ``error``.
* A turn that falls back to another model does it inside the turn: the
  ``model_fallback`` notice, then the new model's tokens, then exactly one
  ``turn_complete``, with no ``error`` between. The producer must not publish a
  ``turn_complete`` before the retry: the request's stream ends on the first.
  (#549 is not on main yet; the producer's own test is its to write.)
* The request's stream releases its subscription when it ends, and also when
  the caller leaves mid-turn.
* ``GET /events`` stays subscribed across ``turn_complete`` and delivers every
  later turn whole; a reconnect gets what is published after it, no replay.
"""

import asyncio
import json
import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.daemon_pool import DaemonHandle

SESSION_ID = "sess_contract"
REQUEST_ID = "req-1"


class ScriptedDaemon:
    """A daemon that writes the frames it is told to, as the real one would."""

    def __init__(self):
        self._writer = None
        self._attached = asyncio.Event()
        self._seq = 0
        self._server = None

    async def start(self) -> str:
        path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "contract.sock")
        self._server = await asyncio.start_unix_server(self._serve, path)
        return path

    async def _serve(self, reader, writer):
        await reader.readline()
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        self._writer = writer
        self._attached.set()
        await reader.read()

    async def emit(self, *frames: dict) -> None:
        await self._attached.wait()
        for frame in frames:
            self._seq += 1
            self._writer.write((json.dumps({**frame, "seq": self._seq}) + "\n").encode())
        await self._writer.drain()

    def stop(self) -> None:
        self._server.close()


class ContractSession:
    """The session the route talks to: a live handle, plus a script per message."""

    session_id = SESSION_ID
    alive = True

    def __init__(self, handle: DaemonHandle, daemon: ScriptedDaemon):
        self.handle = handle
        self.daemon = daemon
        self.script: list[dict] = []

    def subscribe(self, context=None):
        return self.handle.subscribe()

    def unsubscribe(self, queue):
        self.handle.unsubscribe(queue)

    async def send_message(self, content, **kwargs):
        request_id = kwargs["request_id"]
        script = [{**frame, "request_ids": [request_id]} for frame in self.script]
        asyncio.get_running_loop().create_task(self.daemon.emit(*script))
        return {"accepted": True, "request_id": request_id}


@pytest_asyncio.fixture
async def contract():
    daemon = ScriptedDaemon()
    path = await daemon.start()
    handle = DaemonHandle(SESSION_ID, "contract", launcher=None)
    handle.pid = os.getpid()  # a live daemon process
    await handle.connect(path)
    session = ContractSession(handle, daemon)

    from kollabor_engine.server import create_app, get_session_registry

    registry = get_session_registry()
    registry[SESSION_ID] = session
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        session.client = client
        yield session
    registry.pop(SESSION_ID, None)
    handle._closed = True
    handle._read_task.cancel()
    daemon.stop()


def _parse_sse(body: str) -> list[dict]:
    """The events a client reads: one JSON object per ``data:`` line."""
    return [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]


async def send(session: ContractSession, script: list[dict]) -> list[dict]:
    """Send one message over HTTP and return the events its stream delivered."""
    session.script = script
    response = await asyncio.wait_for(
        session.client.post(
            f"/sessions/{SESSION_ID}/message", json={"content": "hi", "request_id": REQUEST_ID}
        ),
        5,
    )
    assert response.status_code == 200
    return _parse_sse(response.text)


def kinds(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def text(events: list[dict]) -> str:
    return "".join(event.get("text", "") for event in events if event["type"] == "token")


DONE = {"type": "turn_complete", "stop_reason": "end_turn"}


@pytest.mark.asyncio
async def test_a_turn_streams_its_tokens_in_order_then_one_turn_complete(contract):
    events = await send(
        contract,
        [{"type": "token", "text": "he"}, {"type": "token", "text": "llo"}, {"type": "token", "text": "!"}, DONE],
    )
    assert kinds(events) == ["token", "token", "token", "turn_complete"]
    assert text(events) == "hello!"
    assert events[-1]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_a_tool_turn_streams_thinking_tool_and_answer_in_order(contract):
    events = await send(
        contract,
        [
            {"type": "thinking", "text": "plan"},
            {"type": "tool_start", "tool_id": "t1", "tool_name": "terminal"},
            {"type": "tool_result", "tool_id": "t1", "tool_name": "terminal", "success": True, "output": "ok"},
            {"type": "token", "text": "done"},
            DONE,
        ],
    )
    assert kinds(events) == ["thinking", "tool_start", "tool_result", "token", "turn_complete"]
    assert events[1]["tool_id"] == events[2]["tool_id"] == "t1"


@pytest.mark.asyncio
async def test_a_failed_turn_streams_one_error_then_its_completion(contract):
    events = await send(
        contract,
        [{"type": "error", "message": "provider down"}, {"type": "turn_complete", "stop_reason": "error"}],
    )
    assert kinds(events) == ["error", "turn_complete"]
    assert events[0]["message"] == "provider down"
    assert events[-1]["stop_reason"] == "error"


@pytest.mark.asyncio
async def test_a_cancelled_turn_ends_with_cancelled_and_no_error(contract):
    events = await send(
        contract,
        [
            {"type": "token", "text": "par"},
            {"type": "turn_cancelled"},
            {"type": "turn_complete", "stop_reason": "cancelled"},
        ],
    )
    assert kinds(events) == ["token", "turn_cancelled", "turn_complete"]
    assert events[-1]["stop_reason"] == "cancelled"
    assert "error" not in kinds(events)


@pytest.mark.asyncio
async def test_a_fallback_turn_streams_notice_then_answer_then_one_completion(contract):
    # The fallback happens inside the turn: the caller sees no error and no
    # first completion, only the notice, the new model's answer and one end.
    events = await send(
        contract,
        [
            {"type": "model_fallback", "reason": "usage", "to": "backup"},
            {"type": "token", "text": "from "},
            {"type": "token", "text": "backup"},
            DONE,
        ],
    )
    assert kinds(events) == ["model_fallback", "token", "token", "turn_complete"]
    assert text(events) == "from backup"
    assert kinds(events).count("turn_complete") == 1
    assert "error" not in kinds(events)


@pytest.mark.asyncio
async def test_nothing_after_the_first_turn_complete_reaches_the_requests_stream(contract):
    # The #549 round-1 shape: a fallback published after the failing turn's
    # completion. The request's stream is over by then, which is why the
    # producer has to retry inside the turn. Pin that the stream ends there.
    events = await send(
        contract,
        [
            {"type": "error", "message": "usage limit"},
            {"type": "turn_complete", "stop_reason": "error"},
            {"type": "model_fallback", "reason": "usage", "to": "backup"},
            {"type": "token", "text": "late"},
            DONE,
        ],
    )
    assert kinds(events) == ["error", "turn_complete"]
    await asyncio.sleep(0.1)
    assert not contract.handle._subscribers  # and it let go of the daemon


@pytest.mark.asyncio
async def test_the_requests_stream_unsubscribes_when_its_turn_ends(contract):
    await send(contract, [{"type": "token", "text": "x"}, DONE])
    assert not contract.handle._subscribers


@pytest.mark.asyncio
async def test_a_caller_who_leaves_mid_turn_releases_its_subscription(contract):
    from kollabor_engine.routes import messages

    response = await messages.send_message(
        SESSION_ID, messages.MessageRequest(content="hi", request_id=REQUEST_ID)
    )
    contract.script = []
    assert len(contract.handle._subscribers) == 1
    stream = response.body_iterator
    await contract.daemon.emit({"type": "token", "text": "x", "request_ids": [REQUEST_ID]})
    first = await asyncio.wait_for(stream.__anext__(), 5)
    assert '"token"' in str(first)
    await stream.aclose()  # the client went away before turn_complete
    assert not contract.handle._subscribers


async def _events_stream():
    from kollabor_engine.routes import messages

    response = await messages.stream_events(SESSION_ID)
    return response.body_iterator


async def _read(stream, count: int) -> list[dict]:
    items = [await asyncio.wait_for(stream.__anext__(), 5) for _ in range(count)]
    return [json.loads(item["data"]) for item in items]


@pytest.mark.asyncio
async def test_a_follower_stays_subscribed_across_turns_and_sees_each_whole(contract):
    stream = await _events_stream()
    first = [{"type": "token", "text": "one"}, DONE]
    # A fallback in the second turn, as a follower that stayed subscribed sees it.
    second = [
        {"type": "model_fallback", "reason": "usage", "to": "backup"},
        {"type": "token", "text": "two"},
        DONE,
    ]
    await contract.daemon.emit(*first, *second)
    events = await _read(stream, 5)
    assert kinds(events) == ["token", "turn_complete", "model_fallback", "token", "turn_complete"]
    assert kinds(events).count("turn_complete") == 2
    assert len(contract.handle._subscribers) == 1  # still following after the first completion
    await stream.aclose()
    assert not contract.handle._subscribers


@pytest.mark.asyncio
async def test_a_follower_that_reconnects_gets_only_what_comes_after(contract):
    stream = await _events_stream()
    await contract.daemon.emit({"type": "token", "text": "before"}, DONE)
    assert kinds(await _read(stream, 2)) == ["token", "turn_complete"]
    await stream.aclose()
    assert not contract.handle._subscribers

    await contract.daemon.emit({"type": "token", "text": "missed"}, DONE)  # nobody listening
    await asyncio.sleep(0.1)
    stream = await _events_stream()
    await contract.daemon.emit({"type": "token", "text": "after"}, DONE)
    events = await _read(stream, 2)
    assert kinds(events) == ["token", "turn_complete"]
    assert text(events) == "after"
    await stream.aclose()
