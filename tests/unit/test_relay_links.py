"""Cross-room links: two keys that consented to each other reach each other.

Runs the real relay app (in-memory backend) and speaks its WebSocket protocol.
The relay must route between rooms only for two keys that each signed a
declaration naming the other, and must keep same-room routing as it was.
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
PATH = service.CONTACT_LINKS_PATH


def _ciphertext() -> str:
    return base64.b64encode(secrets.token_bytes(24)).decode("ascii")


def links_frame(
    signer: SigningKey,
    peers,
    *,
    key: str | None = None,
    issued_at: int | None = None,
    nonce: str | None = None,
    path: str = PATH,
    **extra,
) -> dict:
    """A signed declaration, built the way the client builds it."""
    body = {
        "v": 1,
        "key": key or signer.verify_key.encode().hex(),
        "peers": sorted(peers),
        "issued_at": int(time.time()) if issued_at is None else issued_at,
        "nonce": nonce or secrets.token_hex(16),
        **extra,
    }
    signature = signer.sign(
        service.contact_signature_message(ORIGIN, "POST", path, body)
    ).signature.hex()
    return {**body, "signature": signature}


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

    async def declare(self, peers, **kwargs) -> tuple[int, dict]:
        response = await self.client.post(
            PATH, json=links_frame(self.signing, peers, **kwargs)
        )
        return response.status, await response.json()

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
    assert (await a.declare([b.key]))[0] == 200
    assert (await b.declare([a.key]))[0] == 200
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
    status, body = await ana.declare([marco.key])
    assert (status, body) == (200, {"status": "stored"})
    await ana.send(marco.key)
    assert (await ana.next_error())["code"] == "peer_offline"
    await marco.send(ana.key)
    assert (await marco.next_error())["code"] == "peer_offline"
    assert marco.messages.empty() and ana.messages.empty()

    status, _ = await marco.declare([ana.key])
    assert status == 200
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

    status, _ = await marco.declare([])
    assert status == 200
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
    assert (await marco.declare([ana.key]))[0] == 200
    await marco.disconnect()  # both consent; Marco is offline when Ana does
    assert (await ana.declare([marco.key]))[0] == 200
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
async def test_only_a_registered_key_may_declare(relay):
    ana = await relay.make_device()
    stranger = Device(relay)  # a key that never registered

    status, body = await stranger.declare([ana.key])
    assert (status, body) == (403, {"error": "unauthorized"})
    assert relay.app["relay_state"].backend.link_declarations == {}

    await stranger.connect()
    assert (await stranger.declare([ana.key]))[0] == 200
    await stranger.disconnect()  # gone again: it can no longer declare or withdraw
    assert (await stranger.declare([]))[0] == 403


@pytest.mark.asyncio
async def test_forged_or_malformed_declarations_are_refused(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    eve = SigningKey.generate()

    async def post(frame):
        response = await relay.post(PATH, json=frame)
        return response.status, await response.json()

    # Eve signs a declaration in Marco's name.
    status, body = await post(links_frame(eve, [ana.key], key=marco.key))
    assert (status, body) == (401, {"error": "invalid_contact"})
    # A signed declaration cannot be edited afterwards.
    good = links_frame(marco.signing, [ana.key])
    edited = {**good, "peers": [eve.verify_key.encode().hex()]}
    assert (await post(edited))[0] == 401
    # A signature for another route does not count here.
    other = links_frame(marco.signing, [ana.key], path=service.CONTACT_DECISIONS_PATH)
    assert (await post(other))[0] == 401
    # Stale or future timestamps.
    assert (await post(links_frame(marco.signing, [ana.key], issued_at=int(time.time()) - 600)))[0] == 401
    assert (await post(links_frame(marco.signing, [ana.key], issued_at=int(time.time()) + 600)))[0] == 401
    # Replay of a valid frame.
    fresh = links_frame(marco.signing, [ana.key])
    assert (await post(fresh))[0] == 200
    assert await post(fresh) == (409, {"error": "replayed"})
    # Shape: self link, duplicates, unsorted, bad hex, too many, extra field.
    assert (await post(links_frame(marco.signing, [marco.key])))[0] == 400
    unsorted = links_frame(marco.signing, [])
    low, high = sorted([ana.key, eve.verify_key.encode().hex()])
    unsorted["peers"] = [high, low]
    body = {k: v for k, v in unsorted.items() if k != "signature"}
    signed = marco.signing.sign(
        service.contact_signature_message(ORIGIN, "POST", PATH, body)
    ).signature.hex()
    assert (await post({**body, "signature": signed}))[0] == 400
    duplicate = {**body, "peers": [ana.key, ana.key]}
    duplicate["signature"] = marco.signing.sign(
        service.contact_signature_message(ORIGIN, "POST", PATH, duplicate)
    ).signature.hex()
    assert (await post(duplicate))[0] == 400
    assert (await post(links_frame(marco.signing, ["z" * 64])))[0] == 400
    too_many = [SigningKey.generate().verify_key.encode().hex() for _ in range(relay_backend.MAX_LINK_PEERS + 1)]
    assert (await post(links_frame(marco.signing, too_many)))[0] == 400
    assert (await post(links_frame(marco.signing, [ana.key], surplus=1)))[0] == 400
    missing = links_frame(marco.signing, [ana.key])
    del missing["nonce"]
    assert (await post(missing))[0] == 400

    # Nothing above created a link: Ana never declared Marco.
    await marco.send(ana.key)
    assert (await marco.next_error())["code"] == "peer_offline"
    # Only the one accepted declaration is stored.
    backend = relay.app["relay_state"].backend
    assert set(backend.link_declarations) == {marco.key}
    assert backend.link_declarations[marco.key][1] == frozenset({ana.key})


@pytest.mark.asyncio
async def test_a_delayed_older_declaration_cannot_undo_a_newer_one(relay):
    ana = await relay.make_device()
    marco = await relay.make_device()
    now = int(time.time())
    assert (await marco.declare([ana.key], issued_at=now))[0] == 200
    assert (await marco.declare([], issued_at=now + 5))[0] == 200  # the withdrawal
    status, body = await marco.declare([ana.key], issued_at=now + 1)  # arrives late
    assert (status, body) == (409, {"error": "conflict"})
    declared = relay.app["relay_state"].backend.link_declarations
    assert declared[marco.key][1] == frozenset()  # still withdrawn


@pytest.mark.asyncio
async def test_declarations_are_rate_limited_and_capacity_bounded(relay, monkeypatch):
    marco = await relay.make_device()
    other, first, second, third = [await relay.make_device() for _ in range(4)]
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 2)
    statuses = [(await other.declare([marco.key]))[0] for _ in range(3)]
    assert statuses == [200, 200, 429]

    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 1000)
    monkeypatch.setattr(relay_backend, "MAX_ACTIVE_LINK_DECLARATIONS", 3)
    # `other` is stored already; two more fit and the next one is refused
    assert (await first.declare([marco.key]))[0] == 200
    assert (await second.declare([marco.key]))[0] == 200
    assert await third.declare([marco.key]) == (429, {"error": "capacity"})
    # a stored key can still update, and withdrawing frees its slot
    assert (await other.declare([marco.key]))[0] == 200
    assert (await other.declare([]))[0] == 200
    assert (await third.declare([marco.key]))[0] == 200


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
