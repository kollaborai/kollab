"""Real engine middleware/assistant serialization over encrypted PhoneLink packets.

Only the daemon event producer is a fixture. No model, public relay, or physical
phone is exercised; the real assistant-stream dependency owns the callback.
"""

import asyncio
import base64
import gc
import gzip
import json
import secrets
from types import SimpleNamespace

import pytest
import pytest_asyncio
from kollabor_engine.phone_link import (
    CHUNK,
    LEGACY_CHUNK,
    PHONE_FRAME_MAX_BYTES,
    decode_phone_frame,
    encode_phone_frame,
)

from tests.unit.test_phone_link import secure_phone
from tests.unit.test_phone_link import setup_link as setup_link
from tests.unit.test_relay_application_transport import until


class _Session:
    session_id = "phone-stream-fixture"
    alive = True

    def __init__(self):
        self.queues = []
        self.subscription_contexts = {}
        self.stops = 0
        self.inputs = []

        self.history = []
        self.last_turn_error = None

    def subscribe(self, context=None):
        queue = asyncio.Queue()
        self.queues.append(queue)
        self.subscription_contexts[queue] = context
        return queue

    def unsubscribe(self, queue):
        if queue in self.queues:
            self.queues.remove(queue)
        self.subscription_contexts.pop(queue, None)

    def close_for_context(self, context):
        from kollabor_engine.session_context import PHONE_STREAM_REVOKED

        closed = 0
        for queue, subscribed_context in tuple(self.subscription_contexts.items()):
            if subscribed_context is not context:
                continue
            self.unsubscribe(queue)
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(PHONE_STREAM_REVOKED)
            closed += 1
        return closed

    def emit(self, **event):
        for queue in tuple(self.queues):
            queue.put_nowait(event.copy())

    async def send_message(self, content, **kwargs):
        self.inputs.append(content)
        self.emit(type="token", text="fixture-stream-token")
        return {"accepted": True, "request_id": "fixture-owner"}

    def cancel_turn(self):
        self.stops += 1
        self.emit(type="turn_complete")

    async def get_history_nonblocking(self):
        return {
            "history": self.history,
            "source": "mirror",
            "stale": False,
            "last_turn_error": self.last_turn_error,
        }


    async def refresh_history(self):
        return []


@pytest_asyncio.fixture
async def engine_stream(setup_link, monkeypatch):
    from kollabor_engine import server
    from kollabor_engine.routes import messages

    # Exercise authentication rather than the engine package's test bypass.
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    app = server.create_app()
    session = _Session()
    registry = {session.session_id: session}
    monkeypatch.setattr(server, "get_session_registry", lambda: registry)
    monkeypatch.setattr(messages, "get_session_registry", lambda: registry)
    monkeypatch.setattr(server, "validate_token", lambda token: token == "host-only-token")
    manager = setup_link[0]
    manager.app = app
    packet = await secure_phone(setup_link)
    # Disconnect cleanup must finish without waiting for cyclic GC to close
    # abandoned nested async generators.
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield SimpleNamespace(manager=manager, packet=packet, session=session, link=setup_link)
    finally:
        if gc_was_enabled:
            gc.enable()


_COALESCED_FIRST_READS = {}



async def _start(packet, path, body=None, headers=None):
    identifier = secrets.token_hex(16)
    data = b"" if body is None else json.dumps(body).encode()
    await packet(
        op="start", id=identifier, method="GET" if body is None else "POST",
        path=path,
        headers=headers if headers is not None else [["content-type", "application/json"]],
        size=len(data),
    )
    if data:
        await packet(op="write", id=identifier, offset=0, data=base64.b64encode(data).decode())
    await packet(op="run", id=identifier)
    headers = await packet(op="read", id=identifier)
    assert headers["type"] == "headers" and headers["status"] == 200
    if "data" in headers or headers.get("end") is True:
        _COALESCED_FIRST_READS[identifier] = headers
    return identifier


async def _assistant(probe):
    return await _start(probe.packet, f"/sessions/{probe.session.session_id}/assistant", {
        "commands": [{"type": "add-message", "message": {
            "parts": [{"type": "text", "text": "disposable fixture"}],
        }}],
        "state": {"messages": []},
    })


async def _drain(packet, identifier):
    chunks = []

    first = _COALESCED_FIRST_READS.pop(identifier, None)
    if first and "data" in first:
        chunks.append(base64.b64decode(first["data"]))
    if first and first.get("end") is True:
        return b"".join(chunks)
    while True:
        event = await packet(op="read", id=identifier)
        if event["type"] == "end":
            return b"".join(chunks)
        if event["type"] == "data":
            chunks.append(base64.b64decode(event["data"]))
        else:
            assert event["type"] == "waiting"


async def _read_until(packet, identifier, needle):
    """Read a stream that does not end by itself until `needle` has arrived."""
    chunks = []
    first = _COALESCED_FIRST_READS.pop(identifier, None)
    if first and "data" in first:
        chunks.append(base64.b64decode(first["data"]))
    while needle not in b"".join(chunks):
        event = await packet(op="read", id=identifier)
        if event["type"] == "end":
            break
        if event["type"] == "data":
            chunks.append(base64.b64decode(event["data"]))
        else:
            assert event["type"] == "waiting"
    return b"".join(chunks)


def test_phone_frame_codec_roundtrips_and_bounds_decompression():
    original = json.dumps({"history": "synthetic " * 20_000}).encode()
    compressed = encode_phone_frame(original)

    assert compressed.startswith(b"KLPZ1")
    assert len(compressed) < len(original)
    assert decode_phone_frame(compressed) == original
    assert decode_phone_frame(b'{"ok":true}') == b'{"ok":true}'
    with pytest.raises(ValueError, match="maximum frame size"):
        encode_phone_frame(b"x" * (PHONE_FRAME_MAX_BYTES + 1))
    bomb = b"KLPZ1" + gzip.compress(b"x" * (PHONE_FRAME_MAX_BYTES + 1), mtime=0)
    with pytest.raises(ValueError, match="Decompressed PhoneLink packet"):
        decode_phone_frame(bomb)


@pytest.mark.asyncio
async def test_large_compressed_history_page_uses_one_read_roundtrip(setup_link):
    packet = await secure_phone(
        setup_link,
        frame_compression=True,
        max_chunk=CHUNK,
        coalesce_reads=True,
        zero_body_autorun=True,
    )
    manager, offer, *_ = setup_link
    session = manager.sessions[offer["id"]]
    assert session.frame_compression is True
    assert session.chunk_size == CHUNK

    calls = 0

    async def counted_packet(**message):
        nonlocal calls
        calls += 1
        return await packet(**message)

    identifier = secrets.token_hex(16)
    started = await counted_packet(
        op="start",
        id=identifier,
        method="GET",
        path="/sessions/history-page",
        headers=[],
        size=0,
    )
    assert started["ok"] is True
    first = await counted_packet(op="read", id=identifier)

    assert first["type"] == "headers"
    assert first["end"] is True
    body = base64.b64decode(first["data"])
    assert len(body) == 150_000
    assert body.startswith(b'{"history":"') and body.endswith(b'"}')
    # One phone-session setup RTT plus start and one coalesced read = 3.
    assert calls == 2
    assert not manager.exchanges


@pytest.mark.asyncio
async def test_legacy_phone_stream_splits_large_asgi_body_at_negotiated_limit(setup_link):
    # Omitting max_chunk is the supported 8 KiB uncompressed phone protocol.
    packet = await secure_phone(setup_link)
    manager, offer, *_ = setup_link
    assert manager.sessions[offer["id"]].chunk_size == LEGACY_CHUNK

    identifier = secrets.token_hex(16)
    started = await packet(
        op="start",
        id=identifier,
        method="GET",
        path="/sessions/history-page",
        headers=[],
        size=0,
    )
    assert started["ok"] is True
    await packet(op="run", id=identifier)
    headers = await packet(op="read", id=identifier)
    assert headers["type"] == "headers" and headers["status"] == 200

    async def collect_chunks():
        chunks = []
        while True:
            event = await packet(op="read", id=identifier)
            if event["type"] == "end":
                return chunks
            if event["type"] == "waiting":
                await asyncio.sleep(0)
                continue
            assert event["type"] == "data"
            chunk = base64.b64decode(event["data"])
            assert 0 < len(chunk) <= LEGACY_CHUNK
            chunks.append(chunk)

    chunks = await asyncio.wait_for(collect_chunks(), timeout=2)
    prefix, suffix = b'{"history":"', b'"}'
    expected = prefix + b"x" * (150_000 - len(prefix) - len(suffix)) + suffix
    assert b"".join(chunks) == expected




@pytest_asyncio.fixture
async def compressed_history_engine_stream(setup_link, monkeypatch):
    from kollabor_engine import history_paging, server
    from kollabor_engine.routes import messages, sessions

    monkeypatch.setattr(history_paging, "_CURSOR_SECRET", b"h" * 32)
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    app = server.create_app()
    session = _Session()
    session.history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"synthetic-history-{index:03d} " + ("fixture-corpus-" * 190),
        }
        for index in range(219)
    ]
    session.history.append({
        "role": "tool",
        "tool_call_id": "synthetic-tool-219",
        "metadata": {"tool_call_id": "synthetic-tool-219"},
        "content": "x" * 1_200_000,
    })
    registry = {session.session_id: session}
    monkeypatch.setattr(server, "get_session_registry", lambda: registry)
    monkeypatch.setattr(messages, "get_session_registry", lambda: registry)
    monkeypatch.setattr(sessions, "get_session_registry", lambda: registry)
    monkeypatch.setattr(server, "validate_token", lambda token: token == "host-only-token")
    manager = setup_link[0]
    manager.app = app
    packet = await secure_phone(
        setup_link,
        frame_compression=True,
        max_chunk=CHUNK,
        coalesce_reads=True,
        zero_body_autorun=True,
    )
    yield SimpleNamespace(manager=manager, session=session, link=setup_link, packet=packet)


@pytest.mark.asyncio
async def test_real_history_page_stays_bounded_and_loads_in_three_phone_roundtrips(
    compressed_history_engine_stream,
):
    probe = compressed_history_engine_stream
    full_history_bytes = len(json.dumps(probe.session.history).encode())
    assert full_history_bytes > 1_800_000
    calls = 0

    async def counted_packet(**message):
        nonlocal calls
        calls += 1
        return await probe.packet(**message)

    identifier = secrets.token_hex(16)
    assert (await counted_packet(
        op="start",
        id=identifier,
        method="GET",
        path=f"/sessions/{probe.session.session_id}/history?limit=50",
        headers=[],
        size=0,
    ))["ok"] is True
    first = await counted_packet(op="read", id=identifier)

    assert first["type"] == "headers"
    assert first["end"] is True
    body = base64.b64decode(first["data"])
    page = json.loads(body)
    assert len(body) <= 150_000
    assert len(page["history"]) == 50
    assert page["history"][-1]["metadata"]["history_output_ref"]
    assert calls == 2  # one phone-session handshake + these two = three RTTs
    assert not probe.manager.exchanges



@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["events", "message", "assistant"])
async def test_phone_sse_routes_use_verified_server_scope_context(engine_stream, route):
    probe = engine_stream
    observed = []
    target_path = f"/sessions/{probe.session.session_id}/{route}"

    @probe.manager.app.middleware("http")
    async def capture_server_scope(request, call_next):
        if request.url.path == target_path:
            observed.append(
                (request.scope.get("state"), request.headers.get("x-kollab-phone-id"))
            )
        return await call_next(request)

    headers = [
        ["content-type", "application/json"],
        ["x-kollab-phone-id", "attacker-controlled"],
    ]
    path = target_path
    body = None
    if route == "events":
        path += "?owner_id=attacker-controlled&phone_id=attacker-controlled"
    elif route == "message":
        body = {
            "content": "disposable fixture",
            "owner_id": "attacker-controlled",
            "phone_id": "attacker-controlled",
        }
    else:
        body = {
            "commands": [{"type": "add-message", "message": {
                "parts": [{"type": "text", "text": "disposable fixture"}],
            }}],
            "state": {"messages": []},
            "owner_id": "attacker-controlled",
            "phone_id": "attacker-controlled",
        }

    stream_id = await _start(probe.packet, path, body=body, headers=headers)
    await until(lambda: bool(probe.session.subscription_contexts))
    assert observed and observed[0][1] is None  # arbitrary phone headers are filtered
    state = observed[0][0]
    assert isinstance(state, dict)
    context = state.get("kollabor_engine.session_read_context")
    assert context is not None
    assert context.owner_id == "local"
    assert context.phone_id == probe.link[1]["id"]
    assert context.phone_id != "attacker-controlled"
    assert context.revoked is False
    assert list(probe.session.subscription_contexts.values()) == [context]

    assert probe.session.close_for_context(context) == 1
    await asyncio.wait_for(_drain(probe.packet, stream_id), timeout=2)
    assert not probe.session.queues


@pytest.mark.asyncio
async def test_phone_revoke_purges_real_daemon_subscriber(engine_stream, monkeypatch):
    from kollabor_engine import daemon_pool
    from kollabor_engine import phone_link as phone_link_module
    from kollabor_engine.session_context import PHONE_STREAM_REVOKED, SessionReadContext

    probe = engine_stream
    pool = daemon_pool.DaemonPool()
    handle = daemon_pool.DaemonHandle(probe.session.session_id, "fixture", None)
    pool._daemons[handle.session_id] = handle
    monkeypatch.setattr(phone_link_module, "get_daemon_pool", lambda: pool)

    session = probe.session

    def subscribe(context=None):
        queue = handle.subscribe(context=context)
        session.queues.append(queue)
        session.subscription_contexts[queue] = context
        return queue

    def unsubscribe(queue):
        handle.unsubscribe(queue)
        if queue in session.queues:
            session.queues.remove(queue)
        session.subscription_contexts.pop(queue, None)

    session.subscribe = subscribe
    session.unsubscribe = unsubscribe

    stream_id = await _start(
        probe.packet, f"/sessions/{session.session_id}/events"
    )
    await until(lambda: len(handle._subscribers) == 1)
    queue = next(iter(handle._subscribers))
    context = handle._subscriber_contexts[queue]
    assert context is probe.manager._read_contexts[probe.link[1]["id"]]

    exchange = probe.manager.exchanges[(probe.link[1]["id"], stream_id)]
    exchange_task = exchange.task
    assert exchange_task is not None

    other_context = SessionReadContext(owner_id="local", phone_id="other-phone")
    other_queue = handle.subscribe(context=other_context)

    await probe.manager.revoke(probe.link[1]["id"])
    await asyncio.gather(exchange_task, return_exceptions=True)
    await until(
        lambda: queue not in handle._subscribers
        and stream_id not in probe.manager.exchanges
    )

    assert context.revoked is True
    assert context not in probe.manager._read_contexts.values()
    assert queue not in handle._subscribers
    assert other_queue in handle._subscribers
    assert not session.queues

    handle._fan_out({"type": "token", "text": "after revoke"})
    assert other_queue.get_nowait()["text"] == "after revoke"
    remaining = []
    while not queue.empty():
        remaining.append(queue.get_nowait())
    assert all(event is PHONE_STREAM_REVOKED for event in remaining)
    handle.unsubscribe(other_queue)



@pytest.mark.asyncio
async def test_explicit_stop_finishes_assistant_and_event_stream(engine_stream):
    probe = engine_stream
    observer = await _start(probe.packet, f"/sessions/{probe.session.session_id}/events")
    assistant = await _assistant(probe)
    assert probe.session.inputs == ["disposable fixture"]
    assert len(probe.session.queues) == 2
    stop = await _start(probe.packet, f"/sessions/{probe.session.session_id}/cancel", {})
    assert json.loads(await _drain(probe.packet, stop))["ok"] is True
    assert b"fixture-stream-token" in await _drain(probe.packet, assistant)
    # /events follows the session across turns: read up to turn_complete, then leave.
    assert b"turn_complete" in await _read_until(probe.packet, observer, b"turn_complete")
    await probe.packet(op="cancel", id=observer)
    await until(lambda: not probe.session.queues)
    assert probe.session.stops == 1
    assert not probe.manager.exchanges


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "revoke"])
@pytest.mark.parametrize("buffered", [True, False])
async def test_stream_loss_releases_real_assistant_callback_without_stopping_work(engine_stream, action, buffered):
    probe = engine_stream
    assistant = await _assistant(probe)
    exchange = probe.manager.exchanges[(probe.link[1]["id"], assistant)]
    # The browser can leave while downstream send is backpressured, between
    # generator yields. A test that only cancels an idle producer misses this.
    for _ in range(64):
        probe.session.emit(type="token", text="fixture-burst-token")
    await until(exchange.events.full)
    if not buffered:
        while not exchange.events.empty() or exchange.pending is not None:
            assert (await probe.packet(op="read", id=assistant))["type"] == "data"
            await asyncio.sleep(0.01)
    if action == "cancel":
        await probe.packet(op="cancel", id=assistant)
    else:
        await probe.manager.revoke(probe.link[1]["id"])
    await until(lambda: not probe.session.queues and exchange.task.done())
    assert not probe.manager.exchanges
    assert probe.session.stops == 0
