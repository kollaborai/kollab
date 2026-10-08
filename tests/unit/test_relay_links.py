"""Cross-room links: two keys that consented to each other reach each other.

Runs the real relay app (in-memory backend) and speaks its WebSocket protocol.
The relay must route between rooms only for two keys that each declared the
other (a `links` frame on their own session), and must keep same-room routing
as it was.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from nacl.signing import SigningKey

from plugins.hub import relay_backend
from plugins.hub import relay_service as service

ORIGIN = "https://relay.example"
NODE_ID = "a" * 32


def _ciphertext() -> str:
    return base64.b64encode(secrets.token_bytes(24)).decode("ascii")


def links_frame(peers, *, issued_at: int | None = None, **extra) -> dict:
    """A declaration, built the way the client builds it."""
    return {
        "type": "links",
        "peers": sorted(peers),
        "issued_at": int(time.time()) if issued_at is None else issued_at,
        **extra,
    }


class Device:
    """One relay peer: a key in a room, speaking the wire protocol."""

    def __init__(self, client: TestClient, room: str | None = None):
        self.client = client
        self.signing = SigningKey.generate()
        self.key = self.signing.verify_key.encode().hex()
        self.room = room or secrets.token_hex(32)
        self.session = ""
        self.ws = None
        self.peers: list[dict] = []
        self.messages: asyncio.Queue = asyncio.Queue()
        self.errors: asyncio.Queue = asyncio.Queue()
        self.results: asyncio.Queue = asyncio.Queue()
        self._changed = asyncio.Event()
        self._reader: asyncio.Task | None = None

    async def connect(self) -> "Device":
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
        self.peers = (await self.ws.receive_json())["peers"]
        self._reader = asyncio.create_task(self._read())
        return self

    async def _read(self) -> None:
        try:
            async for message in self.ws:
                frame = message.json()
                if frame["type"] == "peers":
                    self.peers = frame["peers"]
                    self._changed.set()
                elif frame["type"] == "message":
                    self.messages.put_nowait(frame)
                elif frame["type"] == "error":
                    self.errors.put_nowait(frame)
                else:
                    self.results.put_nowait(frame)
                self._changed.set()
        except (asyncio.CancelledError, RuntimeError):
            pass

    async def disconnect(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()

    async def send(self, to: str) -> str:
        message_id = secrets.token_hex(16)
        await self.ws.send_json(
            {"type": "send", "to": to, "id": message_id, "ciphertext": _ciphertext()}
        )
        return message_id

    async def declare(self, peers, **kwargs) -> str:
        """The relay's `links_result`, or the code of the error it sent instead."""
        await self.ws.send_json(links_frame(peers, **kwargs))
        return await self.answer("links_result")

    async def answer(self, kind: str, timeout: float = 2.0) -> str:
        async with asyncio.timeout(timeout):
            while True:
                if not self.errors.empty():
                    return self.errors.get_nowait()["code"]
                while not self.results.empty():
                    frame = self.results.get_nowait()
                    if frame["type"] == kind:
                        return frame["result"]
                self._changed.clear()
                await self._changed.wait()

    def sees(self, other: "Device") -> bool:
        return any(row["key"] == other.key for row in self.peers)

    async def until(self, predicate, timeout: float = 2.0) -> None:
        async with asyncio.timeout(timeout):
            while not predicate():
                self._changed.clear()
                await self._changed.wait()

    async def next_message(self, timeout: float = 2.0) -> dict:
        async with asyncio.timeout(timeout):
            return await self.messages.get()

    async def next_error(self, timeout: float = 2.0) -> dict:
        async with asyncio.timeout(timeout):
            return await self.errors.get()


@pytest_asyncio.fixture
async def relay(monkeypatch):
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 1000)
    config = service.RelayConfig(ORIGIN, NODE_ID, dev_in_memory=True)
    client = TestClient(TestServer(service.create_app(config)))
    await client.start_server()
    devices: list[Device] = []

    async def make(room: str | None = None) -> Device:
        device = await Device(client, room).connect()
        devices.append(device)
        return device

    client.make_device = make  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        for device in devices:
            await device.disconnect()
        await client.close()


async def link(a: Device, b: Device) -> None:
    """Both sides declare each other and see each other online."""
    assert await a.declare([b.key]) == "stored"
    assert await b.declare([a.key]) == "stored"
    await a.until(lambda: a.sees(b))
    await b.until(lambda: b.sees(a))


@pytest.mark.asyncio
async def test_two_rooms_reach_each_other_only_after_both_declared(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    assert not ana.sees(marco) and not marco.sees(ana)

    await ana.send(marco.key)
    assert (await ana.next_error())["code"] == "peer_offline"

    # One side alone: the relay learns a consent, not a link.
    assert await ana.declare([marco.key]) == "stored"
    await ana.send(marco.key)
    assert (await ana.next_error())["code"] == "peer_offline"
    await marco.send(ana.key)
    assert (await marco.next_error())["code"] == "peer_offline"
    assert marco.messages.empty() and ana.messages.empty()

    assert await marco.declare([ana.key]) == "stored"
    await ana.until(lambda: ana.sees(marco))
    await marco.until(lambda: marco.sees(ana))

    message_id = await ana.send(marco.key)
    frame = await marco.next_message()
    assert frame["from"] == ana.key and frame["id"] == message_id
    assert frame["session"] == ana.session
    reply_id = await marco.send(ana.key)
    frame = await ana.next_message()
    assert frame["from"] == marco.key and frame["id"] == reply_id
    assert ana.errors.empty() and marco.errors.empty()


@pytest.mark.asyncio
async def test_the_snapshot_carries_the_linked_session_and_nothing_else(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    await link(ana, marco)

    assert ana.peers == [{"key": marco.key, "session": marco.session}]
    assert marco.peers == [{"key": ana.key, "session": ana.session}]


@pytest.mark.asyncio
async def test_a_link_is_between_two_keys_not_a_membership(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    friend = await relay.make_device(room=marco.room)  # Marco's own network
    await link(ana, marco)

    # Marco's network member sees Marco, never Ana; Ana sees only Marco.
    await friend.until(lambda: friend.sees(marco))
    assert not friend.sees(ana) and not ana.sees(friend)
    await ana.send(friend.key)
    assert (await ana.next_error())["code"] == "peer_offline"
    await friend.send(ana.key)
    assert (await friend.next_error())["code"] == "peer_offline"
    assert friend.messages.empty() and ana.messages.empty()
    # and Marco still reaches his own member in the room
    await marco.send(friend.key)
    assert (await friend.next_message())["from"] == marco.key


@pytest.mark.asyncio
async def test_a_third_key_cannot_ride_a_link_or_pull_one_by_naming_it(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    eve = await relay.make_device()
    mallory = await relay.make_device()
    # Mallory declares everyone; nobody declares Mallory.
    await mallory.declare([ana.key, marco.key, eve.key])
    await link(ana, marco)
    await link(marco, eve)

    for target in (ana, marco, eve):
        await mallory.send(target.key)
        assert (await mallory.next_error())["code"] == "peer_offline"
    assert not mallory.sees(ana) and not ana.sees(mallory)

    # Links do not chain: Marco is linked to both, Ana and Eve are not linked.
    await ana.send(eve.key)
    assert (await ana.next_error())["code"] == "peer_offline"
    await eve.send(ana.key)
    assert (await eve.next_error())["code"] == "peer_offline"
    assert not ana.sees(eve) and not eve.sees(ana)
    assert all(
        d.messages.empty() for d in (ana, marco, eve, mallory)
    )


@pytest.mark.asyncio
async def test_same_room_routing_is_unchanged(relay):
    first = await relay.make_device()
    second = await relay.make_device(room=first.room)
    await first.until(lambda: first.sees(second))
    await second.until(lambda: second.sees(first))

    await first.send(second.key)
    assert (await second.next_message())["from"] == first.key
    await second.send(first.key)
    assert (await first.next_message())["from"] == second.key
    # no declaration was needed, and none is listed twice when both exist
    await first.declare([second.key])
    await second.declare([first.key])
    await first.send(second.key)
    assert (await second.next_message())["from"] == first.key
    assert first.peers == [{"key": second.key, "session": second.session}]


@pytest.mark.asyncio
async def test_withdrawing_a_declaration_cuts_delivery_at_once(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    await link(ana, marco)

    assert await marco.declare([]) == "stored"
    await ana.until(lambda: not ana.sees(marco))
    await marco.until(lambda: not marco.sees(ana))
    await ana.send(marco.key)
    assert (await ana.next_error())["code"] == "peer_offline"
    await marco.send(ana.key)
    assert (await marco.next_error())["code"] == "peer_offline"
    assert ana.messages.empty() and marco.messages.empty()

    # Declaring again restores it: consent is state, not an event.
    await marco.declare([ana.key])
    await ana.until(lambda: ana.sees(marco))
    await ana.send(marco.key)
    assert (await marco.next_message())["from"] == ana.key


@pytest.mark.asyncio
async def test_presence_of_a_linked_device_follows_its_connection(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    await link(ana, marco)

    await marco.disconnect()
    await ana.until(lambda: not ana.sees(marco))
    await ana.send(marco.key)
    assert (await ana.next_error())["code"] == "peer_offline"

    reborn = Device(relay, room=marco.room)
    reborn.signing, reborn.key = marco.signing, marco.key
    await reborn.connect()
    try:
        await ana.until(lambda: ana.sees(reborn))
        assert ana.peers == [{"key": marco.key, "session": reborn.session}]
        await ana.send(marco.key)
        assert (await reborn.next_message())["session"] == ana.session
        # the reconnecting side is told about the linked device at registration
        assert reborn.peers == [{"key": ana.key, "session": ana.session}]
    finally:
        await reborn.disconnect()


@pytest.mark.asyncio
async def test_a_device_that_connects_after_the_link_is_told_at_registration(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    assert await marco.declare([ana.key]) == "stored"
    await marco.disconnect()  # both consent; Marco is offline when Ana does
    assert await ana.declare([marco.key]) == "stored"
    assert not ana.sees(marco)

    reborn = Device(relay, room=marco.room)
    reborn.signing, reborn.key = marco.signing, marco.key
    await reborn.connect()
    try:
        assert reborn.peers == [{"key": ana.key, "session": ana.session}]
        await ana.until(lambda: ana.sees(reborn))
        assert ana.peers == [{"key": marco.key, "session": reborn.session}]
    finally:
        await reborn.disconnect()


@pytest.mark.asyncio
async def test_a_declaration_is_always_the_sessions_own_key(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    # The frame has no key: one it names, even another device's, is a field
    # this version does not know. It is ignored and the session's key counts.
    assert await marco.declare([ana.key], key=ana.key) == "stored"
    assert set(relay.app["relay_state"].backend.link_declarations) == {marco.key}


@pytest.mark.asyncio
async def test_malformed_declarations_are_refused(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    eve = SigningKey.generate().verify_key.encode().hex()
    now = int(time.time())

    # Stale or future timestamps.
    assert await marco.declare([ana.key], issued_at=now - 600) == "invalid_frame"
    assert await marco.declare([ana.key], issued_at=now + 600) == "invalid_frame"
    # Shape: self link, unsorted, duplicates, bad hex, too many. (A field this
    # version does not know is ignored, not refused: versioning.)
    assert await marco.declare([marco.key]) == "invalid_frame"
    low, high = sorted([ana.key, eve])
    await marco.ws.send_json({"type": "links", "peers": [high, low], "issued_at": now})
    assert await marco.answer("links_result") == "invalid_frame"
    await marco.ws.send_json({"type": "links", "peers": [ana.key, ana.key], "issued_at": now})
    assert await marco.answer("links_result") == "invalid_frame"
    assert await marco.declare(["z" * 64]) == "invalid_frame"
    too_many = [
        SigningKey.generate().verify_key.encode().hex()
        for _ in range(relay_backend.MAX_LINK_PEERS + 1)
    ]
    assert await marco.declare(too_many) == "invalid_frame"
    await marco.ws.send_json({"type": "links", "peers": [ana.key]})
    assert await marco.answer("links_result") == "invalid_frame"

    # Nothing above created a link: Ana never declared Marco, and nothing was stored.
    await marco.send(ana.key)
    assert (await marco.next_error())["code"] == "peer_offline"
    assert relay.app["relay_state"].backend.link_declarations == {}


@pytest.mark.asyncio
async def test_a_delayed_older_declaration_cannot_undo_a_newer_one(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    now = int(time.time())
    assert await marco.declare([ana.key], issued_at=now) == "stored"
    assert await marco.declare([], issued_at=now + 5) == "stored"  # the withdrawal
    assert await marco.declare([ana.key], issued_at=now + 1) == "stale"  # arrives late
    declared = relay.app["relay_state"].backend.link_declarations
    assert declared[marco.key][1] == frozenset()  # still withdrawn


@pytest.mark.asyncio
async def test_declarations_are_capacity_bounded(relay, monkeypatch):
    marco = await relay.make_device()
    other, first, second, third = [await relay.make_device() for _ in range(4)]
    assert await other.declare([marco.key]) == "stored"
    monkeypatch.setattr(relay_backend, "MAX_ACTIVE_LINK_DECLARATIONS", 3)
    # `other` is stored already; two more fit and the next one is refused
    assert await first.declare([marco.key]) == "stored"
    assert await second.declare([marco.key]) == "stored"
    assert await third.declare([marco.key]) == "capacity"
    # a stored key can still update, and withdrawing frees its slot
    assert await other.declare([marco.key]) == "stored"
    assert await other.declare([]) == "stored"
    assert await third.declare([marco.key]) == "stored"


@pytest.mark.asyncio
async def test_a_declaration_expires_unless_the_device_repeats_it(relay, monkeypatch):
    ana = await relay.make_device()
    marco = await relay.make_device()
    await link(ana, marco)

    # jump the relay's clock past the declaration lifetime
    real = time.monotonic
    monkeypatch.setattr(
        relay_backend.time,
        "monotonic",
        lambda: real() + relay_backend.LINK_TTL_SECONDS + 5,
    )
    await ana.send(marco.key)
    assert (await ana.next_error())["code"] == "peer_offline"


@pytest.mark.asyncio
async def test_linked_devices_only_fill_what_the_room_leaves(relay, monkeypatch):
    monkeypatch.setattr(service, "MAX_SNAPSHOT_PEERS", 1)
    ana = await relay.make_device()
    first = await relay.make_device()
    second = await relay.make_device()
    await ana.declare([first.key, second.key])
    await first.declare([ana.key])
    await second.declare([ana.key])
    await ana.until(lambda: len(ana.peers) == 1)
    assert len(ana.peers) == 1
    # both remain reachable even though only one fits the snapshot
    for target in (first, second):
        await ana.send(target.key)
        assert (await target.next_message())["from"] == ana.key
