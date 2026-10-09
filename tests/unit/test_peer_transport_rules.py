"""Mesh rules of PeerMeshRuntime: revocation, loopback, strangers and endpoint names.

Issue #123 items 5, 7, 3 and 2. All keys and addresses are synthetic.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import time

import pytest
import pytest_asyncio
from nacl.signing import SigningKey

from plugins.hub import peer_transport
from plugins.hub.peer_discovery import PeerLocatorCandidate
from plugins.hub.peer_records import peer_id_for_key
from plugins.hub.peer_router import PeerRouteError, TransientPeerDeliveryError
from plugins.hub.peer_transport import PeerMeshRuntime, SQLitePeerLocatorStore
from plugins.hub.relay_client import RelayClient
from plugins.hub.secure_conversation import SecureConversationTransport
from tests.unit.test_peer_transport import mesh_network  # noqa: F401


def key() -> str:
    return SigningKey.generate().verify_key.encode().hex()


def locator(
    relay_key: str,
    designation: str = "peridot",
    endpoint_key: str | None = None,
    *,
    revision: int = 1,
    endpoint: str = "kollab+tls://192.0.2.9:8801",
) -> PeerLocatorCandidate:
    now = int(time.time())
    return PeerLocatorCandidate(
        relay_public_key=relay_key,
        endpoint_designation=designation,
        endpoint_public_key=endpoint_key or key(),
        endpoint=endpoint,
        session_id=secrets.token_hex(16),
        revision=revision,
        issued_at=now,
        expires_at=now + 120,
        digest=secrets.token_hex(32),
    )


def deliver(mesh: PeerMeshRuntime, candidate: PeerLocatorCandidate) -> None:
    """What UDP discovery does with a verified datagram, minus the socket."""
    assert mesh.locator_store.accept_candidate(candidate) is True
    mesh._accept_locator_candidate(candidate)


# --- 5. a revoked peer's locator does not linger ---------------------------------


@pytest.mark.asyncio
async def test_revoking_a_peer_drops_its_locator_for_good(mesh_network):  # noqa: F811
    clients, states, *_ = mesh_network
    mesh, peer = states["origin"]["mesh"], clients["relay"].public_key
    deliver(mesh, locator(peer))
    assert mesh._locator_for_peer(peer) is not None

    clients["origin"].revoke(peer)

    assert mesh.record_store.is_revoked(peer_id_for_key(peer), scope=mesh.router.scope)
    assert peer not in mesh._locators
    assert mesh.locator_store.get(peer) is None
    # The tombstone is permanent: approved again, the device gets no direct link
    # and no new locator, and stays on the relay path.
    clients["origin"].approve(peer)
    assert mesh._locator_for_peer(peer) is None
    with pytest.raises(PeerRouteError, match="revoked"):
        mesh.locator_store.accept_candidate(locator(peer, revision=2))


@pytest.mark.asyncio
async def test_a_failing_locator_revoke_never_skips_the_router_revoke(mesh_network):  # noqa: F811
    clients, states, *_ = mesh_network
    mesh, peer = states["origin"]["mesh"], clients["relay"].public_key

    def full(_key):
        raise PeerRouteError("peer locator revocation capacity is full")

    mesh.locator_store.revoke = full
    clients["origin"].revoke(peer)

    assert mesh.record_store.is_revoked(peer_id_for_key(peer), scope=mesh.router.scope)


# --- 7. loopback only for a device on this host; link-local never ----------------


@pytest_asyncio.fixture
async def local_mesh(tmp_path):
    made = []

    def make(**options):
        client = RelayClient(tmp_path / "local", state_dir=tmp_path / "state")
        secure = SecureConversationTransport(client, client._store.key.encode())

        async def no_application(*_args):
            raise AssertionError("not reached")

        mesh = PeerMeshRuntime(
            client,
            secure,
            tmp_path / "mesh",
            no_application,
            forwarding_enabled=lambda: False,
            **options,
        )
        made.append((mesh, secure))
        return mesh

    yield make
    for mesh, secure in made:
        await mesh.close()
        secure.close()


@pytest.mark.asyncio
async def test_loopback_is_never_dialed(local_mesh):
    mesh = local_mesh(allow_private_network=True)
    for endpoint in (
        "kollab+tls://127.0.0.1:9443",
        "kollab+tls://127.8.9.10:9443",
        "kollab+tls://[::1]:9443",
        "kollab+tls://[::ffff:127.0.0.1]:9443",
    ):
        with pytest.raises(PeerRouteError, match="outside the allowed network"):
            await mesh._resolve_direct_endpoint(endpoint)


@pytest.mark.asyncio
async def test_a_name_that_resolves_to_loopback_is_loopback(local_mesh, monkeypatch):
    mesh = local_mesh(allow_private_network=True)

    async def rebound(host, port, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", rebound)
    with pytest.raises(PeerRouteError, match="outside the allowed network"):
        await mesh._resolve_direct_endpoint("kollab+tls://rebinding.example:9443")


@pytest.mark.asyncio
async def test_link_local_is_never_dialed(local_mesh):
    mesh = local_mesh(allow_private_network=True)
    for endpoint in (
        "kollab+tls://169.254.169.254:80",
        "kollab+tls://[fe80::1]:9443",
        "kollab+tls://[::ffff:169.254.169.254]:80",
    ):
        with pytest.raises(PeerRouteError, match="outside the allowed network"):
            await mesh._resolve_direct_endpoint(endpoint)
    # A LAN address is still a LAN address.
    assert (await mesh._resolve_direct_endpoint("kollab+tls://192.168.1.5:9443"))[2] == ("192.168.1.5",)


@pytest.mark.asyncio
async def test_a_members_loopback_locator_is_not_dialed(local_mesh):
    mesh = local_mesh(
        allow_private_network=True,
        direct_enabled=True,
        endpoint_identity_manager=object(),
        endpoint_designation="client-agent",
    )
    member = key()
    mesh.client.approve(member)
    deliver(mesh, locator(member, endpoint="kollab+tls://127.0.0.1:1"))

    with pytest.raises(PeerRouteError, match="outside the allowed network"):
        await mesh._send_direct_secure(member, "secure_packet", {}, timeout=1)


# --- 3. an accepted stranger is refused inside forwarding ------------------------


async def first_hop_frame(clients, states):
    """The frame the origin sends to the relay for a record to the destination."""
    origin_mesh = states["origin"]["mesh"]
    frames = []

    async def capture(_peer_key, frame, *, timeout):
        frames.append(frame)
        raise TransientPeerDeliveryError("captured")

    origin_mesh._send_forward_to_peer = capture
    with pytest.raises(Exception):
        await origin_mesh.request(
            clients["destination"].public_key, "secure_identity", {}, timeout=3
        )
    return frames[0]


@pytest.mark.asyncio
async def test_a_stranger_is_refused_at_forward_exchange_and_route(mesh_network):  # noqa: F811
    clients, states, *_ = mesh_network
    frame = await first_hop_frame(clients, states)
    relay_mesh, relay_client = states["relay"]["mesh"], clients["relay"]
    origin, destination = clients["origin"].public_key, clients["destination"].public_key
    assert relay_mesh._route_material(frame, origin)  # a member's route is read

    relay_client.state.links = [origin]  # the ingress is a stranger
    for call in (
        relay_mesh.handle_forward(origin, frame),
        relay_mesh.handle_exchange(origin, {}),
    ):
        with pytest.raises(PeerRouteError, match="accepted stranger"):
            await call
    with pytest.raises(PeerRouteError, match="accepted stranger"):
        relay_mesh._route_material(frame, origin)

    relay_client.state.links = [destination]  # a node further along the route is
    with pytest.raises(PeerRouteError, match="accepted stranger"):
        relay_mesh._route_material(frame, origin)

    relay_client.state.links = []
    assert relay_mesh._route_material(frame, origin)


# --- 2. the member approved first keeps an endpoint name -------------------------


def contested(mesh_network_value):
    """The origin's mesh, direct links on, and two members that will claim one name."""
    clients, states, *_ = mesh_network_value
    mesh = states["origin"]["mesh"]
    mesh.direct_enabled = True
    first, later = clients["relay"].public_key, clients["destination"].public_key
    assert mesh.client.members() == [first, later]  # approval order
    return mesh, first, later


@pytest.mark.asyncio
async def test_the_first_approved_member_keeps_the_name_while_it_is_live(mesh_network):  # noqa: F811
    mesh, first, later = contested(mesh_network)
    first_key, later_key = key(), key()
    deliver(mesh, locator(later, endpoint_key=later_key))  # the later claim arrives first
    deliver(mesh, locator(first, endpoint_key=first_key))

    assert mesh.endpoint_key_for("peridot") == first_key
    assert mesh._direct_caller("peridot", first_key) == first
    with pytest.raises(PeerRouteError, match="not bound"):
        mesh._direct_caller("peridot", later_key)
    assert mesh.endpoint_key_for("someone-else") == ""


@pytest.mark.asyncio
async def test_an_expired_owner_keeps_the_name_and_the_later_claimant_gets_nothing(
    mesh_network, monkeypatch  # noqa: F811
):
    mesh, first, later = contested(mesh_network)
    first_key, later_key = key(), key()
    deliver(mesh, locator(first, endpoint_key=first_key))
    real_time = time.time
    monkeypatch.setattr(peer_transport.time, "time", lambda: real_time() + 1000)
    assert mesh._locator_for_peer(first) is None  # its locator is gone...
    deliver(mesh, locator(later, endpoint_key=later_key))  # ...and the later member is live

    assert mesh.endpoint_key_for("peridot") == ""
    for endpoint_key in (first_key, later_key):
        with pytest.raises(PeerRouteError, match="not bound"):
            mesh._direct_caller("peridot", endpoint_key)

    deliver(mesh, locator(first, endpoint_key=first_key, revision=2))  # the owner is back
    assert mesh.endpoint_key_for("peridot") == first_key
    assert mesh._direct_caller("peridot", first_key) == first


@pytest.mark.asyncio
async def test_a_revoked_owner_frees_the_name_for_the_next_approved_claimant(mesh_network):  # noqa: F811
    mesh, first, later = contested(mesh_network)
    first_key, later_key = key(), key()
    deliver(mesh, locator(first, endpoint_key=first_key))
    deliver(mesh, locator(later, endpoint_key=later_key))
    assert mesh.endpoint_key_for("peridot") == first_key

    mesh.client.revoke(first)

    assert mesh.locator_store.claim(first) == ""
    assert mesh.endpoint_key_for("peridot") == later_key
    assert mesh._direct_caller("peridot", later_key) == later


@pytest.mark.asyncio
async def test_a_name_held_by_an_absent_owner_survives_a_restart(mesh_network):  # noqa: F811
    mesh, first, later = contested(mesh_network)
    deliver(mesh, locator(first))
    path = mesh.locator_store.path
    mesh.locator_store.close()

    reopened = SQLitePeerLocatorStore(path)
    try:
        assert reopened.claim(first) == "peridot"
        assert reopened.claim(later) == ""
    finally:
        reopened.close()


def test_claims_are_one_row_per_key_and_never_outgrow_the_approvals(tmp_path):
    store = SQLitePeerLocatorStore(tmp_path / "locators.sqlite3", max_peers=2)
    a, b, c = key(), key(), key()
    try:
        store.remember_claim(a, "alpha", approved={a, b, c})
        store.remember_claim(a, "alpha-two", approved={a, b, c})  # the same key renames, no new row
        store.remember_claim(b, "beta", approved={a, b, c})
        assert store.claim(a) == "alpha-two"
        with pytest.raises(PeerRouteError, match="capacity"):
            store.remember_claim(c, "gamma", approved={a, b, c})
        assert store.claim(c) == ""
        # b is no longer approved: its row makes room.
        store.remember_claim(c, "gamma", approved={a, c})
        assert (store.claim(a), store.claim(b), store.claim(c)) == ("alpha-two", "", "gamma")
        for bad in ("", "has space", "x" * 65):
            with pytest.raises(PeerRouteError):
                store.remember_claim(a, bad, approved={a})
    finally:
        store.close()


def test_approval_order_is_durable_and_a_reapproved_member_goes_last(tmp_path):
    first, second, third = key(), key(), key()
    client = RelayClient(tmp_path / "w", state_dir=tmp_path / "s")
    for member in (first, second, third):
        client.approve(member)
    client.revoke(second)
    client.approve(second)

    assert client.members() == [first, third, second]
    again = RelayClient(tmp_path / "w", state_dir=tmp_path / "s")
    assert again.members() == [first, third, second]
