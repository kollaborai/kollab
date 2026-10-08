"""The relay's route index: which key is online under a contact route.

The `lookup` frame that reads it is covered in test_relay_knocks.py.
"""

from __future__ import annotations

import secrets
import shutil
import socket
import subprocess
import tempfile
import time

import pytest

from plugins.hub import relay_backend
from plugins.hub.device_names import contact_route_hex
from plugins.hub.relay_backend import InMemoryBackend, PeerRecord, RedisRelayBackend, RelayLimits

ORIGIN = "https://relay.example"
NODE_ID = "a" * 32


def _peer(key: str) -> PeerRecord:
    return PeerRecord(
        key=key,
        session=secrets.token_hex(16),
        node_id=NODE_ID,
        connection_id=secrets.token_hex(16),
    )


def test_in_memory_backend_route_index_tracks_register_and_remove():
    backend = InMemoryBackend(NODE_ID, RelayLimits())
    key = "c" * 64
    member = _peer(key)
    route = contact_route_hex(key)

    async def run():
        status, _ = await backend.register_room("room-1", member)
        assert status is None
        assert await backend.lookup_contact_route(route) == [key]

        removed, _ = await backend.remove_peer("room-1", member)
        assert removed is True
        assert await backend.lookup_contact_route(route) == []

    import asyncio

    asyncio.run(run())


class _FakeRedis:
    """Just enough of redis.asyncio for the route index.

    `eval` emulates _TOUCH_CONTACT_ROUTE as one atomic step: the member and its
    TTL land together or not at all.
    """

    def __init__(self):
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.evals = 0
        self.eval_error: Exception | None = None

    async def eval(self, script, numkeys, key, member, seconds):
        assert script is relay_backend._TOUCH_CONTACT_ROUTE and numkeys == 1
        self.evals += 1
        if self.eval_error is not None:
            raise self.eval_error
        self.sets.setdefault(key, set()).add(member)
        self.ttls[key] = int(seconds)

    async def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def hexists(self, key, member):
        return member in self.hashes.get(key, {})


def _redis_backend(fake):
    backend = RedisRelayBackend(
        "redis://127.0.0.1:6379/0", NODE_ID, cluster=False, limits=RelayLimits()
    )
    backend._redis = fake
    return backend


@pytest.mark.asyncio
async def test_redis_route_index_sets_the_member_and_its_ttl_in_one_step():
    fake = _FakeRedis()
    backend = _redis_backend(fake)
    key = "c" * 64
    route = contact_route_hex(key)

    await backend._touch_contact_route(key)

    assert fake.evals == 1  # one script, not a SADD then an EXPIRE
    assert await backend.lookup_contact_route(route) == [key]
    assert fake.ttls[backend._contact_route_key(route)] == relay_backend.LEASE_SECONDS


@pytest.mark.asyncio
async def test_redis_route_index_failures_are_logged_never_swallowed_and_leave_no_ttl_less_set(caplog):
    fake = _FakeRedis()
    fake.eval_error = ConnectionError("redis is down")
    backend = _redis_backend(fake)
    key = "c" * 64

    with caplog.at_level("WARNING", logger="plugins.hub.relay_backend"):
        await backend._touch_contact_route(key)  # must not raise into the room path

    assert "contact route index could not be updated" in caplog.text
    assert key not in caplog.text  # a log line never carries the key
    assert fake.sets == {} and fake.ttls == {}  # no set without its expiry


@pytest.mark.asyncio
async def test_redis_discard_unlists_a_key_that_left_its_room():
    fake = _FakeRedis()
    backend = _redis_backend(fake)
    key = "c" * 64
    route = contact_route_hex(key)
    await backend._touch_contact_route(key)

    await backend._discard_contact_route(key, "room-1")

    assert await backend.lookup_contact_route(route) == []


@pytest.mark.asyncio
async def test_redis_discard_keeps_a_key_that_reconnected_during_the_removal():
    fake = _FakeRedis()
    backend = _redis_backend(fake)
    key = "c" * 64
    route = contact_route_hex(key)
    await backend._touch_contact_route(key)
    # the new connection is already a member of the room when the old one's
    # discard arrives
    fake.hashes[backend._room_keys("room-1")[0]] = {key: "{}"}

    await backend._discard_contact_route(key, "room-1")

    assert await backend.lookup_contact_route(route) == [key]
    assert fake.ttls[backend._contact_route_key(route)] == relay_backend.LEASE_SECONDS


@pytest.mark.asyncio
async def test_in_memory_route_stays_listed_while_any_room_still_has_the_key():
    backend = InMemoryBackend(NODE_ID, RelayLimits())
    key = "c" * 64
    route = contact_route_hex(key)
    first, second = _peer(key), _peer(key)
    await backend.register_room("room-1", first)
    await backend.register_room("room-2", second)

    await backend.remove_peer("room-1", first)
    assert await backend.lookup_contact_route(route) == [key]  # still in room-2

    await backend.remove_peer("room-2", second)
    assert await backend.lookup_contact_route(route) == []


@pytest.fixture
def real_redis_url():
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("redis-server is unavailable")
    pytest.importorskip("redis.asyncio")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="relay-route-redis-") as directory:
        server = subprocess.Popen(
            [
                binary,
                *("--bind", "127.0.0.1", "--port", str(port)),
                *("--save", "", "--appendonly", "no", "--dir", directory),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        pytest.fail("temporary loopback Redis did not become ready")
                    time.sleep(0.025)
            yield f"redis://127.0.0.1:{port}/0"
        finally:
            server.terminate()
            server.wait(timeout=5)


@pytest.mark.asyncio
async def test_real_redis_route_index_follows_the_room_and_survives_a_late_discard(real_redis_url):
    import redis.asyncio as aioredis

    client = aioredis.Redis.from_url(real_redis_url, decode_responses=True)
    backend = RedisRelayBackend(real_redis_url, NODE_ID, cluster=False, limits=RelayLimits())
    backend._redis = client
    key = "c" * 64
    route = contact_route_hex(key)
    route_key = backend._contact_route_key(route)
    try:
        old, new = _peer(key), _peer(key)
        status, _ = await backend.register_room("room-1", old)
        assert status is None
        assert await backend.lookup_contact_route(route) == [key]
        ttl = await client.ttl(route_key)
        assert 0 < ttl <= relay_backend.LEASE_SECONDS  # the set never lives without its expiry

        removed, _ = await backend.remove_peer("room-1", old)
        assert removed is True
        assert await backend.lookup_contact_route(route) == []

        # reconnect: the new connection registers, then the OLD connection's
        # discard arrives late. The live key must stay listed.
        status, _ = await backend.register_room("room-1", new)
        assert status is None
        await backend._discard_contact_route(key, "room-1")
        assert await backend.lookup_contact_route(route) == [key]
        assert await client.ttl(route_key) > 0
    finally:
        await client.aclose()
