"""Link storage and presence lookup, the same behaviour on both backends.

The in-memory backend and the Redis backend (a real temporary redis-server,
skipped when the binary is missing) must answer identically: a link exists
only while both keys declare each other, a withdrawal keeps its timestamp, and
a linked device is found by key from any room.
"""

from __future__ import annotations

import secrets
import shutil
import socket
import subprocess
import tempfile
import time

import pytest
import pytest_asyncio

from plugins.hub import relay_backend
from plugins.hub.relay_backend import (
    InMemoryBackend,
    PeerRecord,
    RedisRelayBackend,
    RelayBackendError,
    RelayLimits,
)

NODE_ID = "a" * 32
TTL_MS = 60_000
A, B, C = "a1" * 32, "b2" * 32, "c3" * 32


def _peer(key: str) -> PeerRecord:
    return PeerRecord(
        key=key,
        session=secrets.token_hex(16),
        node_id=NODE_ID,
        connection_id=secrets.token_hex(16),
    )


@pytest.fixture(scope="module")
def redis_url():
    binary = shutil.which("redis-server")
    if binary is None:
        yield None
        return
    pytest.importorskip("redis.asyncio")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="relay-links-redis-") as directory:
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


@pytest_asyncio.fixture(params=["memory", "redis"])
async def backend(request, redis_url):
    if request.param == "memory":
        yield InMemoryBackend(NODE_ID, RelayLimits())
        return
    if redis_url is None:
        pytest.skip("redis-server is unavailable")
    import redis.asyncio as aioredis

    client = aioredis.Redis.from_url(redis_url, decode_responses=True)
    await client.flushall()
    real = RedisRelayBackend(redis_url, NODE_ID, cluster=False, limits=RelayLimits())
    real._redis = client
    try:
        yield real
    finally:
        await client.flushall()
        await client.aclose()


@pytest.mark.asyncio
async def test_a_link_needs_both_declarations(backend):
    now = int(time.time())
    assert await backend.sync_links(A, [B], issued_at=now, ttl_ms=TTL_MS) == ("stored", [B])
    assert await backend.is_linked(A, B) is False
    assert await backend.linked_keys(A) == []

    assert await backend.sync_links(B, [A, C], issued_at=now, ttl_ms=TTL_MS) == (
        "stored",
        sorted([A, C]),
    )
    assert await backend.is_linked(A, B) and await backend.is_linked(B, A)
    assert await backend.linked_keys(A) == [B]
    assert await backend.linked_keys(B) == [A]  # C never declared B
    assert await backend.is_linked(B, C) is False and await backend.is_linked(A, C) is False


@pytest.mark.asyncio
async def test_a_replacement_reports_only_what_changed(backend):
    now = int(time.time())
    await backend.sync_links(A, [B, C], issued_at=now, ttl_ms=TTL_MS)
    assert await backend.sync_links(A, [B, C], issued_at=now + 1, ttl_ms=TTL_MS) == (
        "stored",
        [],
    )
    assert await backend.sync_links(A, [B], issued_at=now + 2, ttl_ms=TTL_MS) == (
        "stored",
        [C],
    )
    assert await backend.sync_links(A, [], issued_at=now + 3, ttl_ms=TTL_MS) == (
        "stored",
        [B],
    )
    assert await backend.sync_links(A, [], issued_at=now + 4, ttl_ms=TTL_MS) == ("stored", [])


@pytest.mark.asyncio
async def test_a_withdrawal_keeps_its_timestamp_against_a_delayed_older_post(backend):
    now = int(time.time())
    await backend.sync_links(A, [B], issued_at=now, ttl_ms=TTL_MS)
    await backend.sync_links(B, [A], issued_at=now, ttl_ms=TTL_MS)
    await backend.sync_links(A, [], issued_at=now + 10, ttl_ms=TTL_MS)
    assert await backend.is_linked(A, B) is False

    assert await backend.sync_links(A, [B], issued_at=now + 5, ttl_ms=TTL_MS) == ("stale", [])
    assert await backend.is_linked(A, B) is False
    # a genuinely newer declaration works
    assert (await backend.sync_links(A, [B], issued_at=now + 11, ttl_ms=TTL_MS))[0] == "stored"
    assert await backend.is_linked(A, B) is True


@pytest.mark.asyncio
async def test_live_declarations_are_capped_and_a_withdrawal_frees_a_slot(backend, monkeypatch):
    monkeypatch.setattr(relay_backend, "MAX_ACTIVE_LINK_DECLARATIONS", 2)
    now = int(time.time())
    assert (await backend.sync_links(A, [C], issued_at=now, ttl_ms=TTL_MS))[0] == "stored"
    assert (await backend.sync_links(B, [C], issued_at=now, ttl_ms=TTL_MS))[0] == "stored"
    assert await backend.sync_links(C, [A], issued_at=now, ttl_ms=TTL_MS) == ("capacity", [])
    # updating a stored key never counts against the cap
    assert (await backend.sync_links(A, [B, C], issued_at=now + 1, ttl_ms=TTL_MS))[0] == "stored"
    await backend.sync_links(A, [], issued_at=now + 2, ttl_ms=TTL_MS)
    assert (await backend.sync_links(C, [A], issued_at=now, ttl_ms=TTL_MS))[0] == "stored"
    # a key that withdrew can come back only if a slot is free
    assert (await backend.sync_links(A, [C], issued_at=now + 3, ttl_ms=TTL_MS))[0] == "capacity"


@pytest.mark.asyncio
async def test_a_device_is_found_by_key_from_any_room_and_only_while_registered(backend):
    first, second = _peer(A), _peer(B)
    assert await backend.locate(A) is None

    assert (await backend.register_room("1" * 64, first))[0] is None
    assert (await backend.register_room("2" * 64, second))[0] is None
    assert await backend.locate(A) == (first, "1" * 64)
    assert await backend.locate(B) == (second, "2" * 64)

    assert (await backend.remove_peer("1" * 64, first))[0] is True
    assert await backend.locate(A) is None
    assert await backend.locate(B) == (second, "2" * 64)


@pytest.mark.asyncio
async def test_a_late_removal_does_not_unlist_a_device_that_moved_rooms(backend):
    old, new = _peer(A), _peer(A)
    await backend.register_room("1" * 64, old)
    await backend.register_room("2" * 64, new)  # the key moved to another room
    assert await backend.locate(A) == (new, "2" * 64)

    await backend.remove_peer("1" * 64, old)  # the old connection's cleanup arrives late
    assert await backend.locate(A) == (new, "2" * 64)


@pytest.mark.asyncio
async def test_renewal_keeps_a_device_findable(backend):
    member = _peer(A)
    if isinstance(backend, RedisRelayBackend):  # act as a started worker
        backend._healthy = True
        await backend._redis.set(backend._owner_key(NODE_ID), backend._boot_token)
    reservation = await backend.reserve_connection(member.connection_id, "127.0.0.1")
    assert reservation is not None
    await backend.register_room("1" * 64, member)
    if isinstance(backend, RedisRelayBackend):
        await backend._redis.delete(backend._presence_key(A))  # lost
        assert await backend.locate(A) is None
    assert await backend.renew(
        member.connection_id, "127.0.0.1", reservation, member, "1" * 64
    )
    assert await backend.locate(A) == (member, "1" * 64)


@pytest.mark.asyncio
async def test_a_malformed_stored_declaration_is_reported_not_trusted(backend):
    if isinstance(backend, InMemoryBackend):
        pytest.skip("only the Redis backend parses stored text")
    await backend._redis.set(backend._link_key(A), '{"at": 1, "peers": ["not-a-key"]}')
    with pytest.raises(RelayBackendError):
        await backend.linked_keys(A)
    await backend._redis.set(backend._link_key(A), "not json")
    with pytest.raises(RelayBackendError):
        await backend.is_linked(A, B)


@pytest.mark.asyncio
async def test_redis_keeps_every_link_key_on_the_mailbox_slot_with_an_expiry(backend):
    if isinstance(backend, InMemoryBackend):
        pytest.skip("Redis key layout")
    now = int(time.time())
    await backend.sync_links(A, [B], issued_at=now, ttl_ms=TTL_MS)
    await backend.register_room("1" * 64, _peer(A))
    for key in (backend._link_key(A), backend._link_index_key(), backend._presence_key(A)):
        assert "{mailbox}" in key
        assert await backend._redis.pttl(key) > 0
    # a withdrawal is a short-lived tombstone, not a permanent key
    await backend.sync_links(A, [], issued_at=now + 1, ttl_ms=TTL_MS)
    ttl = await backend._redis.pttl(backend._link_key(A))
    assert 0 < ttl <= relay_backend.LINK_WITHDRAWAL_SECONDS * 1000
    assert await backend._redis.zscore(backend._link_index_key(), A) is None


@pytest.mark.asyncio
async def test_memory_declarations_expire_when_not_repeated(monkeypatch):
    memory = InMemoryBackend(NODE_ID, RelayLimits())
    now = int(time.time())
    await memory.sync_links(A, [B], issued_at=now, ttl_ms=1000)
    await memory.sync_links(B, [A], issued_at=now, ttl_ms=1000)
    assert await memory.is_linked(A, B)
    real = time.monotonic
    monkeypatch.setattr(relay_backend.time, "monotonic", lambda: real() + 5)
    assert await memory.is_linked(A, B) is False
    assert memory.link_declarations == {}
