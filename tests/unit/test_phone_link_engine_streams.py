"""Real engine middleware/assistant serialization over encrypted PhoneLink packets.

Only the daemon event producer is a fixture. No model, public relay, or physical
phone is exercised; the real assistant-stream dependency owns the callback.
"""

import asyncio
import base64
import gc
import json
import secrets
from types import SimpleNamespace

import pytest
import pytest_asyncio

from tests.unit.test_phone_link import secure_phone
from tests.unit.test_phone_link import setup_link as setup_link
from tests.unit.test_relay_application_transport import until


class _Session:
    session_id = "phone-stream-fixture"
    alive = True

    def __init__(self):
        self.queues = []
        self.stops = 0
        self.inputs = []

    def subscribe(self):
        queue = asyncio.Queue()
        self.queues.append(queue)
        return queue

    def unsubscribe(self, queue):
        self.queues.remove(queue)

    def emit(self, **event):
        for queue in tuple(self.queues):
            queue.put_nowait(event.copy())

    async def send_message(self, content):
        self.inputs.append(content)
        self.emit(type="token", text="fixture-stream-token")
        return {"accepted": True, "request_id": "fixture-owner"}

    def cancel_turn(self):
        self.stops += 1
        self.emit(type="turn_complete")

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


async def _start(packet, path, body=None):
    identifier = secrets.token_hex(16)
    data = b"" if body is None else json.dumps(body).encode()
    await packet(
        op="start", id=identifier, method="GET" if body is None else "POST",
        path=path, headers=[["content-type", "application/json"]], size=len(data),
    )
    if data:
        await packet(op="write", id=identifier, offset=0, data=base64.b64encode(data).decode())
    await packet(op="run", id=identifier)
    headers = await packet(op="read", id=identifier)
    assert headers["type"] == "headers" and headers["status"] == 200
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
    while True:
        event = await packet(op="read", id=identifier)
        if event["type"] == "end":
            return b"".join(chunks)
        if event["type"] == "data":
            chunks.append(base64.b64decode(event["data"]))
        else:
            assert event["type"] == "waiting"


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
    assert b"turn_complete" in await _drain(probe.packet, observer)
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
