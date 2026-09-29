"""Route -> key lookup for `/connect knock`: the relay's route index."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from plugins.hub import relay_service as service
from plugins.hub.device_names import contact_route_hex
from plugins.hub.relay_backend import InMemoryBackend, PeerRecord, RelayLimits

ORIGIN = "https://relay.example"
NODE_ID = "a" * 32


def _peer(key: str) -> PeerRecord:
    return PeerRecord(
        key=key,
        session=secrets.token_hex(16),
        node_id=NODE_ID,
        connection_id=secrets.token_hex(16),
    )


async def _post(client: TestClient, path: str, frame: dict):
    response = await client.post(path, json=frame)
    return response.status, await response.json()


@pytest_asyncio.fixture
async def relay_client(monkeypatch):
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 100)
    config = service.RelayConfig(ORIGIN, NODE_ID, dev_in_memory=True)
    client = TestClient(TestServer(service.create_app(config)))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_lookup_resolves_a_key_registered_in_a_room(relay_client):
    key = "c" * 64
    backend = relay_client.app["relay_state"].backend
    status, rows = await backend.register_room("room-1", _peer(key))
    assert status is None

    route = contact_route_hex(key)
    status, body = await _post(relay_client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": route})
    assert status == 200
    assert body == {"key": key}


@pytest.mark.asyncio
async def test_lookup_of_an_unknown_route_is_a_clean_404(relay_client):
    status, body = await _post(
        relay_client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": "f" * 16}
    )
    assert status == 404
    assert body == {"error": "unknown_route"}


@pytest.mark.asyncio
async def test_lookup_rejects_a_malformed_route_shape(relay_client):
    status, body = await _post(
        relay_client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": "not-hex"}
    )
    assert status == 400
    assert body == {"error": "invalid_request"}


@pytest.mark.asyncio
async def test_lookup_stops_answering_once_the_peer_leaves_the_room(relay_client):
    key = "d" * 64
    backend = relay_client.app["relay_state"].backend
    member = _peer(key)
    await backend.register_room("room-2", member)
    route = contact_route_hex(key)

    status, _ = await _post(relay_client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": route})
    assert status == 200

    removed, _ = await backend.remove_peer("room-2", member)
    assert removed is True

    status, body = await _post(
        relay_client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": route}
    )
    assert status == 404
    assert body == {"error": "unknown_route"}


@pytest.mark.asyncio
async def test_lookup_reports_ambiguous_when_two_keys_share_a_route(relay_client):
    # A hash collision is practically impossible; force the index into that
    # state directly to prove the relay refuses to guess rather than
    # returning either key silently.
    backend = relay_client.app["relay_state"].backend
    backend.contact_routes["a" * 16] = {"1" * 64, "2" * 64}

    status, body = await _post(
        relay_client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": "a" * 16}
    )
    assert status == 409
    assert body == {"error": "ambiguous_route"}


@pytest.mark.asyncio
async def test_lookup_is_rate_limited_per_source(monkeypatch):
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 2)
    config = service.RelayConfig(ORIGIN, NODE_ID, dev_in_memory=True)
    client = TestClient(TestServer(service.create_app(config)))
    await client.start_server()
    try:
        route = "e" * 16
        for _ in range(2):
            status, _ = await _post(client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": route})
            assert status == 404
        status, body = await _post(client, service.CONTACT_LOOKUP_PATH, {"v": 1, "route": route})
        assert status == 429
        assert body == {"error": "rate_limited"}
    finally:
        await client.close()


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


class _FakeRedisSet:
    """Just enough of the redis.asyncio surface for the route index."""

    def __init__(self):
        self.sets: dict[str, set[str]] = {}

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    async def expire(self, key, seconds):
        return None

    async def smembers(self, key):
        return set(self.sets.get(key, set()))


def test_redis_backend_route_index_tracks_touch_and_discard():
    from plugins.hub.relay_backend import RedisRelayBackend

    backend = RedisRelayBackend(
        "redis://127.0.0.1:6379/0", NODE_ID, cluster=False, limits=RelayLimits()
    )
    backend._redis = _FakeRedisSet()
    key = "c" * 64
    route = contact_route_hex(key)

    async def run():
        await backend._touch_contact_route(key)
        assert await backend.lookup_contact_route(route) == [key]

        await backend._discard_contact_route(key)
        assert await backend.lookup_contact_route(route) == []

    import asyncio

    asyncio.run(run())
