"""Regression for the zircon #606 gate finding at 4cdc8ee5: a relay capacity
rejection must reach MembershipSync as backpressure, not a peer failure.

RelayClient.request raises RelayError("pending application request capacity
reached") when the process-wide pending budget is full. On the live membership
path that raise happens inside SecureConversationTransport, whose broad failure
rewrites (request() and ensure_session()) used to flatten every error to
"secure conversation transport failed", so the capacity branch in
network_members._send_one never matched on the real wiring and healthy peers
were parked on backoff. The fix types the raise ("capacity" code at the
source) and lets both secure-boundary rewrites carry the typed signal across.

Real wiring, no stub overrides of the send path: two RelayClients on an
in-process encrypted wire, a real SecureConversationTransport at each end,
and MembershipSync built exactly like relay_agent.make_membership_sync.
"""

import asyncio
import json
import secrets
import time

import pytest
import pytest_asyncio

from plugins.hub.network_members import MembershipSync
from plugins.hub.relay_client import MAX_PENDING, RelayClient, _ApplicationPending
from plugins.hub.relay_state import RelayError
from plugins.hub.secure_conversation import (
    MAX_SECURE_IDLE_SECONDS,
    SecureConversationTransport,
)


class RelayWire:
    """An in-process encrypted RelayClient carrier, without a service process."""

    def __init__(self) -> None:
        self.clients: dict[str, RelayClient] = {}
        self.queues: dict[str, asyncio.Queue] = {}
        self.tasks: list[asyncio.Task] = []

    def add(self, client: RelayClient) -> None:
        queue = asyncio.Queue()
        self.clients[client.public_key] = client
        self.queues[client.public_key] = queue
        client._session_id = secrets.token_hex(16)
        client._state = "online"
        client._closed = False
        client.state.origin = "https://relay.example"
        wire = self

        class Socket:
            closed = False

            async def send_str(self, raw: str) -> None:
                frame = json.loads(raw)
                wire.queues[frame["to"]].put_nowait(
                    {
                        "type": "message",
                        "from": client.public_key,
                        "session": client._session_id,
                        "id": frame["id"],
                        "ciphertext": frame["ciphertext"],
                    }
                )

        client._ws = Socket()

        async def receive() -> None:
            while True:
                await client._handle_frame(await queue.get())

        self.tasks.append(asyncio.create_task(receive()))

    async def close(self) -> None:
        for client in self.clients.values():
            await client.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


@pytest_asyncio.fixture
async def pair(tmp_path):
    """Origin and destination joined by the in-process wire, secure ends real."""
    wire = RelayWire()
    origin = RelayClient(tmp_path / "origin", state_dir=tmp_path / "state-origin")
    dest = RelayClient(tmp_path / "dest", state_dir=tmp_path / "state-dest")
    room = origin.state.room
    dest.state.room = room
    wire.add(origin)
    wire.add(dest)
    origin.approve(dest.public_key)
    dest.approve(origin.public_key)
    origin._peers[dest.public_key] = dest._session_id
    dest._peers[origin.public_key] = origin._session_id
    origin_secure = SecureConversationTransport(origin, origin._store.key.encode())
    dest_secure = SecureConversationTransport(dest, dest._store.key.encode())

    async def destination_dispatch(peer_key, method, payload):
        return {}

    async def destination_application(peer_key, method, payload):
        if method == "secure_identity":
            return dest_secure.identity_response(peer_key, payload)
        if method == "secure_packet":
            return await dest_secure.handle_packet(
                peer_key, payload, destination_dispatch
            )
        raise RelayError("unexpected relay application in test")

    dest.set_request_handler(destination_application)
    yield wire, origin, dest, origin_secure
    await wire.close()


def _fill_budget(client: RelayClient) -> None:
    """Occupy the real process-wide pending budget so client.request rejects."""
    loop = asyncio.get_running_loop()
    for index in range(MAX_PENDING):
        future = loop.create_future()
        # Fillers are never awaited; retrieve whatever settles them so
        # teardown logs stay free of "exception was never retrieved" noise.
        future.add_done_callback(lambda done: done.exception())
        client._application_pending[f"filler-{index}"] = _ApplicationPending(
            peer=f"filler-{index}",
            local_session="filler-local",
            peer_session="filler-peer",
            method="network_members",
            future=future,
        )


def _membership(client: RelayClient, transport, dest_key: str) -> MembershipSync:
    return MembershipSync(
        client=client,
        transport=transport,
        online=lambda: {dest_key: "sess-1"},
    )


def _assert_backpressure(membership: MembershipSync, dest_key: str) -> None:
    """Capacity is backpressure: no failure bookkeeping, the peer stays
    eligible for the next tick."""
    assert dest_key not in membership._delivered
    assert dest_key not in membership._failures
    assert dest_key not in membership._retry_at


@pytest.mark.asyncio
async def test_capacity_rejection_before_handshake_is_backpressure(pair):
    """Budget full on the very first send: the handshake never starts, and the
    typed signal must survive ensure_session's rewrite."""
    wire, origin, dest, origin_secure = pair
    membership = _membership(origin, origin_secure, dest.public_key)
    _fill_budget(origin)
    await membership.tick()
    _assert_backpressure(membership, dest.public_key)


@pytest.mark.asyncio
async def test_capacity_rejection_after_handshake_is_backpressure(pair):
    """Budget full mid-conversation: the session exists, the new request is
    rejected at the relay budget, and the peer stays eligible."""
    wire, origin, dest, origin_secure = pair
    membership = _membership(origin, origin_secure, dest.public_key)
    # One successful secure roundtrip first, so the outbound session is fully
    # established before the budget fills.
    await origin_secure.request(dest.public_key, "directory", {"probe": True})
    _fill_budget(origin)
    await membership.tick()
    _assert_backpressure(membership, dest.public_key)


@pytest_asyncio.fixture
async def multi_peer_mesh(tmp_path):
    """Real relay and secure-transport wiring for several approved peers."""
    wire = RelayWire()
    origin = RelayClient(tmp_path / "origin", state_dir=tmp_path / "state-origin")
    wire.add(origin)
    origin_secure = SecureConversationTransport(origin, origin._store.key.encode())
    peers = []
    peer_count = 5

    for index in range(peer_count):
        peer = RelayClient(
            tmp_path / f"peer-{index}", state_dir=tmp_path / f"state-peer-{index}"
        )
        peer.state.room = origin.state.room
        wire.add(peer)
        origin.approve(peer.public_key)
        peer.approve(origin.public_key)
        origin._peers[peer.public_key] = peer._session_id
        peer._peers[origin.public_key] = origin._session_id

        if index == peer_count - 1:
            peer_secure = SecureConversationTransport(peer, peer._store.key.encode())

            async def destination_dispatch(peer_key, method, payload):
                return {}

            async def destination_application(peer_key, method, payload):
                if method == "secure_identity":
                    return peer_secure.identity_response(peer_key, payload)
                if method == "secure_packet":
                    return await peer_secure.handle_packet(
                        peer_key, payload, destination_dispatch
                    )
                raise RelayError("unexpected relay application in test")

            peer.set_request_handler(destination_application)

        peers.append(peer)

    yield wire, origin, origin_secure, peers
    await wire.close()


@pytest.mark.asyncio
async def test_many_capacity_rejections_release_request_locks(
    multi_peer_mesh, monkeypatch
):
    """Rejected bindings do not exhaust the secure-lock budget permanently."""
    import plugins.hub.secure_conversation as secure_conversation_module

    _, origin, origin_secure, peers = multi_peer_mesh
    monkeypatch.setattr(secure_conversation_module, "MAX_SECURE_SESSIONS", 2)
    membership = MembershipSync(
        client=origin,
        transport=origin_secure,
        online=lambda: {peer.public_key: peer._session_id for peer in peers},
    )
    _fill_budget(origin)

    await membership.tick()

    for peer in peers:
        _assert_backpressure(membership, peer.public_key)
    assert origin_secure._request_locks == {}

    # Once the relay budget is available, a later peer can complete a real TLS
    # handshake rather than being rejected by stale per-binding lock entries.
    for pending in tuple(origin._application_pending.values()):
        pending.future.set_result({})
    origin._application_pending.clear()
    await origin_secure.ensure_session(peers[-1].public_key)
    assert len(origin_secure._outbound) == 1
    assert origin_secure._request_locks == {}


@pytest.mark.asyncio
async def test_peer_cleanup_preserves_request_lock_for_active_waiter(pair):
    """Peer/session cleanup must not split concurrent calls across locks."""
    _, _, dest, origin_secure = pair
    key = origin_secure._current_binding(dest.public_key)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    shared_locks = []

    async def first_request():
        async with origin_secure._request_guard(key):
            shared_locks.append(origin_secure._request_locks[key])
            first_started.set()
            await release_first.wait()

    async def second_request():
        await first_started.wait()
        async with origin_secure._request_guard(key):
            assert origin_secure._request_locks[key] is shared_locks[0]
            second_started.set()

    first = asyncio.create_task(first_request())
    await first_started.wait()
    second = asyncio.create_task(second_request())
    await asyncio.sleep(0)
    assert origin_secure._request_lock_users[key] == 2

    origin_secure._discard_peer(dest.public_key)
    assert origin_secure._request_locks[key] is shared_locks[0]
    origin_secure._clear_sessions()
    assert origin_secure._request_locks[key] is shared_locks[0]

    release_first.set()
    await asyncio.gather(first, second)
    assert second_started.is_set()
    assert key not in origin_secure._request_locks
    assert key not in origin_secure._request_lock_users


@pytest.mark.asyncio
async def test_idle_session_pruning_discards_idle_request_lock(pair):
    """Pruning keeps active locks safe, then clears idle session and lock state."""
    _, _, dest, origin_secure = pair
    await origin_secure.ensure_session(dest.public_key)
    key = origin_secure._current_binding(dest.public_key)
    state = origin_secure._outbound[key]
    state.last_used = time.monotonic() - MAX_SECURE_IDLE_SECONDS - 1

    request_started = asyncio.Event()
    release_request = asyncio.Event()

    async def hold_request_lock():
        async with origin_secure._request_guard(key):
            request_started.set()
            await release_request.wait()

    active_request = asyncio.create_task(hold_request_lock())
    await request_started.wait()
    request_lock = origin_secure._request_locks[key]

    origin_secure._prune_expired()

    assert origin_secure._outbound[key] is state
    assert origin_secure._request_locks[key] is request_lock
    assert origin_secure._request_lock_users[key] == 1

    release_request.set()
    await active_request
    assert key not in origin_secure._request_locks
    assert key not in origin_secure._request_lock_users

    # Model an idle lock retained with the session; pruning must remove both.
    origin_secure._request_locks[key] = asyncio.Lock()
    origin_secure._prune_expired()

    assert key not in origin_secure._outbound
    assert key not in origin_secure._request_locks
    assert key not in origin_secure._request_lock_users
