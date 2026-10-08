"""Knocks as calls: the relay rings a device and stores nothing (Story 5).

Runs the real relay app (in-memory backend) and speaks its WebSocket protocol:
`lookup` finds the one device online under a route, `knock` rings it under the
knocker's signed ticket, and `knock_answer` travels back under that ticket.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from nacl.signing import SigningKey

from plugins.hub import knock_wire
from plugins.hub import relay_service as service
from plugins.hub.device_names import contact_route_hex
from plugins.hub.relay_backend import RedisRelayBackend

ORIGIN = "https://relay.example"
NODE_ID = "a" * 32


def _ciphertext() -> str:
    return base64.b64encode(secrets.token_bytes(48)).decode("ascii")


class Device:
    def __init__(self, client: TestClient, room: str | None = None):
        self.client = client
        self.signing = SigningKey.generate()
        self.key = self.signing.verify_key.encode().hex()
        self.room = room or secrets.token_hex(32)
        self.session = ""
        self.ws = None
        self.frames: asyncio.Queue = asyncio.Queue()
        self._reader: asyncio.Task | None = None

    async def connect(self, *, speaks_knocks: bool = True) -> "Device":
        self.session = secrets.token_hex(16)
        self.ws = await self.client.ws_connect(service.WEBSOCKET_PATH)
        challenge = await self.ws.receive_json()
        signed = service._registration_message(
            ORIGIN, challenge["nonce"], self.key, self.room, self.session
        )
        await self.ws.send_json(
            {
                "type": "register",
                "key": self.key,
                "room": self.room,
                "session": self.session,
                "signature": self.signing.sign(signed).signature.hex(),
            }
        )
        assert (await self.ws.receive_json())["type"] == "registered"
        await self.ws.receive_json()  # peers
        self._reader = asyncio.create_task(self._read())
        if speaks_knocks:  # a current client declares its links once a session, even none
            await self.ws.send_json({"type": "links", "peers": [], "issued_at": int(time.time())})
            assert (await self.next())["type"] == "links_result"
        return self

    async def _read(self) -> None:
        try:
            async for message in self.ws:
                frame = message.json()
                if frame["type"] != "peers":
                    self.frames.put_nowait(frame)
        except (asyncio.CancelledError, RuntimeError):
            pass

    async def disconnect(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()

    async def next(self, timeout: float = 2.0) -> dict:
        async with asyncio.timeout(timeout):
            return await self.frames.get()

    async def quiet(self, seconds: float = 0.2) -> bool:
        await asyncio.sleep(seconds)
        return self.frames.empty()

    async def lookup(self, route: str) -> dict:
        await self.ws.send_json({"type": "lookup", "route": route})
        return await self.next()

    def ticket(self, to: str, knock_id: str, *, ends: int | None = None, origin=ORIGIN):
        ends = int(time.time()) + knock_wire.RING_SECONDS if ends is None else ends
        return knock_wire.sign_ticket(self.signing, origin, to, knock_id, ends)

    async def knock(self, to: str, *, ticket=None, knock_id: str | None = None) -> str:
        knock_id = knock_id or secrets.token_hex(16)
        await self.ws.send_json(
            {
                "type": "knock",
                "to": to,
                "id": knock_id,
                "ticket": ticket or self.ticket(to, knock_id),
                "ciphertext": _ciphertext(),
            }
        )
        return knock_id

    async def answer(self, ticket: dict, ciphertext: str | None = None) -> None:
        await self.ws.send_json(
            {
                "type": "knock_answer",
                "id": ticket["id"],
                "ticket": ticket,
                "ciphertext": ciphertext or _ciphertext(),
            }
        )


@pytest_asyncio.fixture
async def relay():
    config = service.RelayConfig(ORIGIN, NODE_ID, dev_in_memory=True)
    client = TestClient(TestServer(service.create_app(config)))
    await client.start_server()
    devices: list[Device] = []

    async def make(room: str | None = None, *, speaks_knocks: bool = True) -> Device:
        device = await Device(client, room).connect(speaks_knocks=speaks_knocks)
        devices.append(device)
        return device

    client.make_device = make  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        for device in devices:
            await device.disconnect()
        await client.close()


@pytest.mark.asyncio
async def test_lookup_finds_the_one_device_online_under_a_route(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    found = await ana.lookup(contact_route_hex(marco.key))
    assert found == {
        "type": "lookup_result",
        "route": contact_route_hex(marco.key),
        "result": "found",
        "key": marco.key,
    }
    # no device, your own route, and two devices under one route all read the same
    assert (await ana.lookup("f" * 16))["result"] == "unavailable"
    assert (await ana.lookup(contact_route_hex(ana.key)))["key"] == ""
    relay.app["relay_state"].backend.contact_routes["a" * 16] = {"1" * 64, "2" * 64}
    assert (await ana.lookup("a" * 16)) == {
        "type": "lookup_result", "route": "a" * 16, "result": "unavailable", "key": ""
    }
    await marco.disconnect()
    await asyncio.sleep(0.05)
    assert (await ana.lookup(contact_route_hex(marco.key)))["result"] == "unavailable"
    await ana.ws.send_json({"type": "lookup", "route": "not-hex"})
    assert (await ana.next())["code"] == "invalid_frame"


@pytest.mark.asyncio
async def test_a_knock_rings_the_device_and_the_answer_comes_back(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()  # another room: a stranger
    knock_id = await ana.knock(marco.key)

    rung = await marco.next()
    assert set(rung) == {"type", "from", "session", "id", "ticket", "ciphertext"}
    assert (rung["type"], rung["from"], rung["session"], rung["id"]) == (
        "knock", ana.key, ana.session, knock_id
    )
    assert rung["ticket"]["from"] == ana.key and rung["ticket"]["to"] == marco.key
    assert await ana.quiet()  # ringing: the knocker hears nothing yet

    sealed = _ciphertext()
    await marco.answer(rung["ticket"], sealed)
    assert await marco.next() == {"type": "knock_result", "id": knock_id, "result": "answered"}
    answered = await ana.next()
    assert answered == {
        "type": "knock_answer",
        "from": marco.key,
        "session": marco.session,
        "id": knock_id,
        "ciphertext": sealed,
    }
    metrics = relay.app["relay_state"].metrics
    assert metrics["relay_knocks_rung_total"] == 1


@pytest.mark.asyncio
async def test_a_knock_on_a_device_that_is_not_online_is_unavailable_at_once(relay):
    ana = await relay.make_device()
    offline = SigningKey.generate().verify_key.encode().hex()
    knock_id = await ana.knock(offline)
    assert await ana.next() == {"type": "knock_result", "id": knock_id, "result": "unavailable"}
    assert relay.app["relay_state"].metrics["relay_knocks_unavailable_total"] == 1


@pytest.mark.asyncio
async def test_a_client_that_never_spoke_the_knock_protocol_is_never_rung(relay):
    """A 0.11 client drops its connection on a frame it does not know."""
    ana = await relay.make_device()
    old = await relay.make_device(speaks_knocks=False)

    knock_id = await ana.knock(old.key)

    assert await ana.next() == {"type": "knock_result", "id": knock_id, "result": "unavailable"}
    assert await old.quiet()
    await old.ws.send_json({"type": "lookup", "route": "0" * 16})  # any knock-protocol frame
    assert (await old.next())["type"] == "lookup_result"
    await ana.knock(old.key)
    assert (await old.next())["type"] == "knock"


@pytest.mark.asyncio
async def test_the_relay_keeps_no_knock(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    backend = relay.app["relay_state"].backend
    before = {name: repr(value) for name, value in vars(backend).items()}
    await ana.knock(marco.key)
    await marco.next()
    after = {name: repr(value) for name, value in vars(backend).items()}
    changed = {name for name in before if before[name] != after[name]}
    # only the shared address-block counters move; nothing names the knock
    assert changed <= {"enrollment_rate"}
    assert not any(
        "knock" in name or "contact_request" in name for name in vars(backend)
    )


@pytest.mark.asyncio
async def test_forged_or_stale_tickets_are_refused(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    eve = SigningKey.generate()
    now = int(time.time())

    async def refused(ticket, *, to=marco.key, knock_id=None) -> None:
        knock_id = knock_id or ticket["id"]
        await ana.knock(to, ticket=ticket, knock_id=knock_id)
        assert await ana.next() == {"type": "error", "code": "invalid_frame", "id": knock_id}

    knock_id = secrets.token_hex(16)
    # signed by someone else, for another device, another id, another directory
    await refused(knock_wire.sign_ticket(eve, ORIGIN, marco.key, knock_id, now + 60))
    await refused(ana.ticket(SigningKey.generate().verify_key.encode().hex(), knock_id))
    await refused(ana.ticket(marco.key, secrets.token_hex(16)), knock_id=knock_id)
    await refused(ana.ticket(marco.key, knock_id, origin="https://other.example"))
    # rung out already, or ringing longer than a ring
    await refused(ana.ticket(marco.key, knock_id, ends=now - knock_wire.SKEW_SECONDS - 60))
    await refused(ana.ticket(marco.key, knock_id, ends=now + knock_wire.RING_SECONDS + 600))
    # edited after signing
    edited = {**ana.ticket(marco.key, knock_id), "ends": now + 10}
    await refused(edited)
    assert await marco.quiet()


@pytest.mark.asyncio
async def test_only_the_rung_device_can_answer_and_only_while_it_rings(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    eve = await relay.make_device()
    await ana.knock(marco.key)
    ticket = (await marco.next())["ticket"]

    await eve.answer(ticket)  # a third key holding the ticket
    assert (await eve.next())["code"] == "invalid_frame"
    assert await ana.quiet()

    stale = ana.ticket(
        marco.key, secrets.token_hex(16), ends=int(time.time()) - knock_wire.SKEW_SECONDS - 60
    )
    await marco.answer(stale)
    assert (await marco.next())["code"] == "invalid_frame"

    await ana.disconnect()  # the knocker hung up
    await asyncio.sleep(0.05)
    await marco.answer(ticket)
    assert await marco.next() == {"type": "knock_result", "id": ticket["id"], "result": "unavailable"}


@pytest.mark.asyncio
async def test_knocks_are_limited_per_connection(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    for _ in range(service.KNOCKS_PER_CONNECTION):
        await ana.knock(marco.key)
        assert (await marco.next())["type"] == "knock"
    knock_id = await ana.knock(marco.key)
    assert await ana.next() == {"type": "knock_result", "id": knock_id, "result": "busy"}
    assert await marco.quiet()
    # lookups have their own budget
    assert (await ana.lookup(contact_route_hex(marco.key)))["result"] == "found"


@pytest.mark.asyncio
async def test_knocks_are_limited_per_address_block(relay, monkeypatch):
    monkeypatch.setattr(service, "KNOCKS_PER_ADDRESS_BLOCK", 2)
    marco = await relay.make_device()
    first, second = await relay.make_device(), await relay.make_device()
    await first.knock(marco.key)
    await second.knock(marco.key)
    assert [(await marco.next())["type"] for _ in range(2)] == ["knock", "knock"]
    knock_id = await first.knock(marco.key)  # same /24 (127.0.0.0/24)
    assert await first.next() == {"type": "knock_result", "id": knock_id, "result": "busy"}
    assert relay.app["relay_state"].metrics["relay_knocks_busy_total"] == 1
    assert service._address_block("203.0.113.9") == "203.0.113.0/24"
    assert service._address_block("2001:db8:1:2::9") == "2001:db8:1::/56"


@pytest.mark.asyncio
async def test_malformed_knock_frames_are_refused(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    knock_id = secrets.token_hex(16)
    good = {
        "type": "knock",
        "to": marco.key,
        "id": knock_id,
        "ticket": ana.ticket(marco.key, knock_id),
        "ciphertext": _ciphertext(),
    }
    for frame in (
        {**good, "id": "x"},
        {**good, "ciphertext": ""},
        {**good, "ciphertext": "not base64!"},
        {**good, "ciphertext": "A" * (knock_wire.MAX_CIPHERTEXT_CHARS + 4)},
        {key: value for key, value in good.items() if key != "ticket"},
    ):
        await ana.ws.send_json(frame)
        assert (await ana.next())["code"] == "invalid_frame"
    assert await marco.quiet()


@pytest.mark.asyncio
async def test_a_field_this_version_does_not_know_is_ignored(relay):
    # Versioning: a newer client may add a field. The knock still rings, and
    # the relay passes on only what it knows.
    ana = await relay.make_device()
    marco = await relay.make_device()
    knock_id = secrets.token_hex(16)
    await ana.ws.send_json(
        {
            "type": "knock",
            "to": marco.key,
            "id": knock_id,
            "ticket": ana.ticket(marco.key, knock_id),
            "ciphertext": _ciphertext(),
            "newer": 1,
        }
    )
    rung = await marco.next()
    assert rung["type"] == "knock" and rung["id"] == knock_id and "newer" not in rung


def test_cross_node_routes_carry_the_kind_and_the_knock_ticket():
    backend = RedisRelayBackend.__new__(RedisRelayBackend)
    backend._state = object()
    route = {
        "type": "route",
        "route_id": "c" * 32,
        "reply_node": "d" * 32,
        "room_hash": "b" * 64,
        "connection_id": "e" * 32,
        "to": "f" * 64,
        "to_session": "1" * 32,
        "from": "2" * 64,
        "session": "3" * 32,
        "id": "4" * 32,
        "ciphertext": "A" * 64,
    }
    ticket = json.dumps({"v": 1}, separators=(",", ":"))
    accepted = [
        {**route, "kind": "message", "ticket": ""},
        {**route, "kind": "knock", "ticket": ticket},
        {**route, "kind": "knock_answer", "ticket": ""},
    ]
    refused = [
        route,  # an older worker's frame
        {**route, "kind": "knock", "ticket": ""},
        {**route, "kind": "message", "ticket": ticket},
        {**route, "kind": "page", "ticket": ""},
        {**route, "kind": "knock", "ticket": "x" * 4096},
    ]
    for message in accepted:
        assert backend._parse_node_message(json.dumps(message)) == ("route", message)
    for message in refused:
        assert backend._parse_node_message(json.dumps(message)) is None
