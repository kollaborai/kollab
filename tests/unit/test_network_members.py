"""Approval spreads to every device on a network (constitution sections 4 and 10).

Real bridges over an in-process relay wire. After a join by code each device
approves only whoever admitted it; the signed member lists must make every
member approve every other, and revocation must reach every member.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from nacl.signing import SigningKey

from plugins.hub import network_members, relay_client
from plugins.hub.network_members import METHOD, seal_members
from plugins.hub.relay_state import RelayError

from .test_mesh_network import deliver_locator, make_node
from .test_peer_transport import RelayWire, _mint_tls_cert

pytestmark = pytest.mark.usefixtures("unthrottled_relay")

NAMES = {"mac": "lapis", "srv-b": "koordinator", "srv-c": "peridot", "srv-d": "ruby", "odd": "sapphire"}


async def make_net(tmp_path, *names):
    wire = RelayWire()
    nodes = {n: await make_node(tmp_path, n, designation=NAMES[n]) for n in names}
    for node in nodes.values():
        wire.add(node.client)
        await node.bridge.membership_sync.close()  # the test drives every tick
        node.bridge.membership_sync._poll = 0.0  # no backoff between rounds
    for node in nodes.values():
        for other in nodes.values():
            if other is not node:
                node.client._peers[other.key] = other.client._session_id
    return wire, tuple(nodes.values())


async def close_net(wire, nodes):
    for node in nodes:
        await node.bridge.close()
    await wire.close()


def fresh_key() -> str:
    return SigningKey.generate().verify_key.encode().hex()


def joined(inviter, joiner):
    """What accepting a join code leaves behind: each approves the other, first-hand."""
    inviter.client.approve(joiner.key)
    inviter.client.add_config_recipient(joiner.key)
    inviter.bridge.bind_peer_device(joiner.key, joiner.name)
    joiner.client.approve(inviter.key)
    joiner.bridge.bind_peer_device(inviter.key, inviter.name)


async def sync(nodes, rounds=4):
    for _ in range(rounds):
        for node in nodes:
            await node.bridge.membership_sync.tick()


def approved(node):
    return set(node.client.state.approvals)


@pytest_asyncio.fixture
async def chain(tmp_path):
    """A invites B, B invites C: A and C have never met."""
    wire, nodes = await make_net(tmp_path, "mac", "srv-b", "srv-c")
    a, b, c = nodes
    joined(a, b)
    joined(b, c)
    yield a, b, c, nodes, wire
    await close_net(wire, nodes)


@pytest.mark.asyncio
async def test_a_three_device_network_approves_every_pair(chain):
    a, b, c, nodes, _wire = chain
    assert c.key not in approved(a) and a.key not in approved(c)

    await sync(nodes)

    for node in nodes:
        assert approved(node) == {n.key for n in nodes if n is not node}
    # Names travel with the vouch, so the roster reads agent@device on both ends.
    assert a.client.state.peer_devices[c.key] == "srv-c"
    assert c.client.state.peer_devices[a.key] == "mac"
    # Vouched, not first-hand: only B knew both.
    assert a.client.state.vouched_by == {c.key: [b.key]}
    assert c.client.state.vouched_by == {a.key: [b.key]}
    assert b.client.state.vouched_by == {}
    # Only a device a human accepted here gets the sealed config.
    assert c.key not in a.client.state.config_recipients


@pytest.mark.asyncio
async def test_the_relay_path_and_the_mesh_path_agree_on_who_is_a_member(chain):
    a, b, c, nodes, _wire = chain
    await sync(nodes)
    for node in nodes:
        others = {n.key for n in nodes if n is not node}
        assert set(node.client.members()) == others
        assert set(node.mesh._live_peers()) == others  # the mesh routes to members
        for key in others:
            # ...and the relay path lets each of them in.
            reply = await node.bridge._receive(key, "directory", {}, _secure=True)
            assert "agents" in reply
        with pytest.raises(RelayError, match="not approved"):
            await node.bridge._receive("ee" * 32, "directory", {}, _secure=True)


@pytest.mark.asyncio
async def test_membership_converges_through_a_starved_send_budget(chain, monkeypatch):
    """The production self-heal: a refused send is retried on a later tick, after backoff.

    Every device starts with an empty send bucket on a clock the test owns, so each
    first send is refused; the lists must still arrive once the bucket refills.
    """
    a, b, c, nodes, _wire = chain
    clock = {"now": 1000.0}
    fake_time = SimpleNamespace(**{**vars(time), "monotonic": lambda: clock["now"]})
    monkeypatch.setattr(network_members, "time", fake_time)
    monkeypatch.setattr(relay_client, "time", fake_time)
    monkeypatch.setattr(relay_client, "SEND_BURST", 20.0)
    monkeypatch.setattr(relay_client, "SEND_RATE_PER_SECOND", 2)
    for node in nodes:
        node.client._tokens = 0.0
        node.client._token_time = clock["now"]
        node.bridge.membership_sync._poll = 10.0

    await sync(nodes, rounds=1)
    assert all(not node.bridge.membership_sync._delivered for node in nodes)
    assert all(node.bridge.membership_sync._retry_at for node in nodes)
    assert c.key not in approved(a) and a.key not in approved(c)

    failures = [dict(node.bridge.membership_sync._failures) for node in nodes]
    await sync(nodes, rounds=1)  # still inside the backoff: nothing is sent
    assert failures == [dict(node.bridge.membership_sync._failures) for node in nodes]

    for _ in range(40):  # a bounded run of backoff windows; each one refills the bucket
        if all(approved(node) == {n.key for n in nodes if n is not node} for node in nodes):
            break
        clock["now"] += 10.0
        await sync(nodes, rounds=1)
    for node in nodes:
        assert approved(node) == {n.key for n in nodes if n is not node}


@pytest.mark.asyncio
async def test_a_vouch_is_an_approval_not_a_grant(chain):
    a, b, c, nodes, _wire = chain
    for node in nodes:
        node.client._store.state.trust = "manual"
        node.client._store.save()
    await sync(nodes)
    assert c.key in approved(a)
    assert list(a.bridge.store.grants(a.client.state.room)) == []
    assert c.key not in a.client.state.peer_trust


@pytest.mark.asyncio
async def test_revoking_a_device_drops_it_on_every_member(chain):
    a, b, c, nodes, _wire = chain
    await sync(nodes)

    a.client.revoke(c.key, announce=True)
    assert c.key not in approved(a)
    await sync(nodes)

    assert c.key not in approved(b)
    assert c.key in b.client.state.revoked and c.key in a.client.state.revoked
    assert a.key in approved(b)  # the rest of the network is untouched


@pytest.mark.asyncio
async def test_a_revoked_device_stays_out_until_a_member_accepts_it_again(chain):
    a, b, c, nodes, _wire = chain
    await sync(nodes)
    a.client.revoke(c.key, announce=True)
    await sync(nodes)

    # A stale vouch (B before it heard) does not bring C back...
    a.client.accept_membership(b.key, [(c.key, "srv-c")], [])
    assert c.key not in approved(a)
    # ...a human at A accepting C anew does.
    a.client.add_config_recipient(c.key)
    a.client.accept_membership(b.key, [(c.key, "srv-c")], [])
    assert c.key in approved(a) and c.key not in a.client.state.revoked


@pytest.mark.asyncio
async def test_revoking_a_device_also_drops_those_only_it_vouched_for(tmp_path):
    wire, nodes = await make_net(tmp_path, "mac", "srv-b", "srv-c", "srv-d")
    a, b, c, d = nodes
    joined(a, b)
    joined(a, d)
    joined(b, c)  # C is in through B alone
    await sync(nodes, rounds=5)
    for node in nodes:
        assert approved(node) == {n.key for n in nodes if n is not node}

    a.client.revoke(b.key, announce=True)
    assert approved(a) == {d.key}  # C went with B: its only vouch at A came from B
    await sync(nodes, rounds=5)

    assert approved(d) == {a.key}  # D dropped B, then C, whom only B and A had named
    assert b.key in d.client.state.revoked
    await close_net(wire, nodes)


@pytest.mark.asyncio
async def test_an_accepted_stranger_is_never_vouched_for_and_never_vouches(tmp_path):
    wire, nodes = await make_net(tmp_path, "mac", "srv-b", "srv-c", "odd")
    a, b, c, s = nodes
    joined(a, b)
    joined(b, c)
    # Story 5: A accepted a device from another network. It is a link, not a member.
    a.bridge.set_peer_link(s.key)
    a.bridge.bind_peer_device(s.key, s.name)
    a.client.approve(s.key)
    s.bridge.set_peer_link(a.key)
    s.client.approve(a.key)
    await sync(nodes)

    assert approved(b) == {a.key, c.key} and approved(c) == {a.key, b.key}
    assert s.key not in a.client.members() and s.key in approved(a)
    assert s.key not in a.bridge.membership_sync._delivered  # nothing sent to it
    # It cannot vouch: its list is turned away at the door.
    friend = fresh_key()
    payload = seal_members(
        key=s.client._store.key,
        to=a.key,
        net=s.client.network_id(),
        members=[(friend, "friend")],
        revoked=[],
    )
    with pytest.raises(RelayError, match="not part of this network"):
        await a.bridge._receive(s.key, METHOD, payload, _secure=True)
    assert friend not in approved(a)
    # A member naming it, or revoking it, changes nothing about a stranger.
    a.client.accept_membership(b.key, [(s.key, "odd")], [s.key])
    assert s.key in a.client.state.links and s.key in approved(a)
    assert s.key not in a.client.state.vouched_by and s.key not in a.client.state.revoked
    await close_net(wire, nodes)


@pytest.mark.asyncio
async def test_a_forged_vouch_is_refused(chain):
    a, b, c, nodes, _wire = chain
    outsider = fresh_key()

    def listing(signer, *, to, net=None, members=((outsider, "friend"),)):
        return seal_members(
            key=signer.client._store.key,
            to=to,
            net=net or signer.client.network_id(),
            members=list(members),
            revoked=[],
        )

    forged = listing(c, to=a.key)
    forged["voucher"] = b.key  # C's signature, B's name
    tampered = listing(b, to=a.key, members=())
    tampered["members"] = [{"key": outsider, "name": "friend"}]
    other_device = listing(b, to=c.key)  # a real list, meant for C
    other_network = listing(b, to=a.key, net="00" * 32)
    for payload in (forged, tampered, other_device, other_network, {"v": 1}, "x", None):
        assert await a.bridge.membership_sync.receive(b.key, payload) == {"error": "invalid"}
    assert outsider not in approved(a)
    # The genuine article is taken.
    assert await a.bridge.membership_sync.receive(b.key, listing(b, to=a.key)) == {"ok": True}
    assert outsider in approved(a)


@pytest.mark.asyncio
async def test_a_second_key_claiming_a_designation_cannot_block_the_first_device(tmp_path):
    cert, key = _mint_tls_cert(tmp_path)
    endpoint = (cert, key, cert)
    a = await make_node(tmp_path, "mac", designation="lapis")
    x = await make_node(tmp_path, "srv-x", designation="peridot", endpoint=endpoint)
    y = await make_node(tmp_path, "srv-y", designation="peridot", endpoint=endpoint)
    a.client.approve(x.key)  # x is the first device
    a.client.approve(y.key)
    x.client.approve(a.key)
    y.client.approve(a.key)
    deliver_locator(a, y)  # ...though y's claim arrives first
    deliver_locator(a, x)
    claim = lambda node: a.mesh._locator_for_peer(node.key)["endpoint_public_key"].lower()  # noqa: E731
    assert claim(x) != claim(y)

    assert a.mesh.endpoint_key_for("peridot") == claim(x)
    # An accepted stranger is no member and claims nothing.
    a.bridge.set_peer_link(x.key)
    assert a.mesh.endpoint_key_for("peridot") == claim(y)
    a.client.revoke(x.key)
    assert a.mesh.endpoint_key_for("peridot") == claim(y)
    for node in (a, x, y):
        await node.bridge.close()
        if node.server is not None:
            await node.server.stop()
