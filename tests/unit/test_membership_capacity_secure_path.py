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

import pytest
import pytest_asyncio

from plugins.hub.network_members import MembershipSync
from plugins.hub.relay_client import MAX_PENDING, RelayClient, _ApplicationPending
from plugins.hub.relay_state import RelayError
from plugins.hub.secure_conversation import SecureConversationTransport


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
