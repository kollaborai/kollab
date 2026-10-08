"""Bounded relay backplane queues and room invalidation coalescing."""

from __future__ import annotations

import asyncio
import json

import pytest

from plugins.hub import relay_backend as backend_module
from plugins.hub.relay_backend import RedisRelayBackend, RelayLimits

NODE_ID = "a" * 32
ROOM_HASH = "b" * 64


def _route(route_id: str = "c" * 32) -> dict[str, str]:
    return {
        "type": "route",
        "route_id": route_id,
        "reply_node": "d" * 32,
        "room_hash": ROOM_HASH,
        "connection_id": "e" * 32,
        "to": "f" * 64,
        "to_session": "1" * 32,
        "from": "2" * 64,
        "session": "3" * 32,
        "id": "4" * 32,
        "ciphertext": "A" * 128,
        "kind": "message",
        "ticket": "",
    }


class _OneMessagePubSub:
    def __init__(self, backend: RedisRelayBackend, payload: dict[str, str]):
        self.backend = backend
        self.payload = payload

    async def listen(self):
        yield {
            "type": "message",
            "channel": self.backend._node_channel,
            "data": json.dumps(self.payload, separators=(",", ":")),
        }


def _backend(*, max_connections: int = 512) -> RedisRelayBackend:
    backend = RedisRelayBackend(
        "redis://127.0.0.1:6379/0",
        NODE_ID,
        cluster=False,
        limits=RelayLimits(max_connections_per_node=max_connections),
    )
    backend._state = object()
    return backend


@pytest.mark.asyncio
async def test_route_byte_overflow_returns_negative_ack(monkeypatch):
    backend = _backend()
    monkeypatch.setattr(backend_module, "MAX_WORK_QUEUE_BYTES", 64)
    backend._pubsub = _OneMessagePubSub(backend, _route())

    await backend._listen()

    assert backend._work_queue.empty()
    assert backend._work_queue_items == 0
    assert backend._work_queue_bytes == 0
    acknowledgement = backend._ack_queue.get_nowait()
    assert acknowledgement == {
        "type": "ack",
        "reply_node": "d" * 32,
        "route_id": "c" * 32,
        "ok": False,
    }


@pytest.mark.asyncio
async def test_route_byte_overflow_publishes_negative_ack_if_ack_queue_is_full(
    monkeypatch,
):
    backend = _backend()
    monkeypatch.setattr(backend_module, "MAX_WORK_QUEUE_BYTES", 64)
    backend._ack_queue = asyncio.Queue(maxsize=1)
    backend._ack_queue.put_nowait({"sentinel": True})
    backend._pubsub = _OneMessagePubSub(backend, _route())
    published = []

    async def publish(node_id, payload):
        published.append((node_id, payload))
        return 1

    backend._publish_node = publish

    await backend._listen()

    assert backend._ack_queue.qsize() == 1
    assert published == [
        (
            "d" * 32,
            {"type": "ack", "route_id": "c" * 32, "ok": False},
        )
    ]


@pytest.mark.asyncio
async def test_route_item_overflow_returns_negative_ack():
    backend = _backend(max_connections=1)
    assert backend._work_queue.maxsize == 32
    for index in range(32):
        room_hash = f"{index:064x}"
        assert (
            backend._enqueue_work(
                "room_changed", {"type": "room_changed", "room_hash": room_hash}
            )
            is None
        )
    backend._pubsub = _OneMessagePubSub(backend, _route())

    await backend._listen()

    assert backend._work_queue_items == 32
    acknowledgement = backend._ack_queue.get_nowait()
    assert acknowledgement["route_id"] == "c" * 32
    assert acknowledgement["ok"] is False


@pytest.mark.asyncio
async def test_queued_room_changes_coalesce_by_room():
    backend = _backend()
    payload = {"type": "room_changed", "room_hash": ROOM_HASH}

    assert backend._enqueue_work("room_changed", payload) is None
    assert backend._enqueue_work("room_changed", payload) is None

    assert backend._work_queue_items == 1
    assert backend._work_queue.qsize() == 1
    assert backend._queued_rooms == {ROOM_HASH}


@pytest.mark.asyncio
async def test_room_change_during_active_refresh_is_requeued_once():
    backend = _backend()

    class State:
        def __init__(self):
            self.calls = 0
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def room_changed(self, room_hash):
            assert room_hash == ROOM_HASH
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.release_first.wait()

    state = State()
    backend._state = state
    worker = asyncio.create_task(backend._work_loop())
    payload = {"type": "room_changed", "room_hash": ROOM_HASH}
    backend._enqueue_work("room_changed", payload)
    try:
        await asyncio.wait_for(state.first_started.wait(), timeout=1)
        backend._enqueue_work("room_changed", payload)
        backend._enqueue_work("room_changed", payload)
        assert backend._room_change_again == {ROOM_HASH}

        state.release_first.set()
        await asyncio.wait_for(backend._work_queue.join(), timeout=1)

        assert state.calls == 2
        assert backend._work_queue_items == 0
        assert backend._work_queue_bytes == 0
        assert not backend._queued_rooms
        assert not backend._room_change_again
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
