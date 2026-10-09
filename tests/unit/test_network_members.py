"""Approval spreads to every device on a network (constitution sections 4 and 10).

Real bridges over an in-process relay wire. After a join by code each device
approves only whoever admitted it; the signed member lists must make every
member approve every other, and revocation must reach every member.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from plugins.hub import network_members, relay_client
from plugins.hub.device_names import NAME_RE
from plugins.hub.network_members import METHOD, MembershipSync, seal_members
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_state import RelayError, validate_public_key

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
    shapes = ({"v": 1}, {"v": 2}, {"v": 3}, {"v": True}, {"v": [1]}, "x", None)
    for payload in (forged, tampered, other_device, other_network, *shapes):
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


# --- dated lists: a replayed older list must not revoke members vouched since (#123) ---

# kollab 0.13 and 0.14 as released: reads "v": 1 exactly, ignores fields it does not
# know, remembers nothing. Frozen here so the v1 wire a v1-only device speaks cannot
# drift, and so the tests below talk to a real v1-only device, not to our own v1 path.
_V1_DOMAIN = b"kollab-network-members-v1\n"
_V1_FIELDS = frozenset({"v", "voucher", "to", "net", "members", "revoked", "sig"})


def _v1_signed(body):
    return _V1_DOMAIN + json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def v1_seal(*, key, to, net, members, revoked):
    body = {
        "v": 1,
        "voucher": key.verify_key.encode().hex(),
        "to": to,
        "net": net,
        "members": [{"key": k, "name": name} for k, name in members[:64]],
        "revoked": list(revoked[:64]),
    }
    return {**body, "sig": key.sign(_v1_signed(body)).signature.hex()}


def v1_open(payload, *, peer, own_key, net):
    if not isinstance(payload, dict) or not payload.keys() >= _V1_FIELDS or payload["v"] != 1:
        raise RelayError("invalid membership list")
    if payload["voucher"] != peer or payload["to"] != own_key or payload["net"] != net:
        raise RelayError("invalid membership list")
    members, revoked, sig = payload["members"], payload["revoked"], payload["sig"]
    if (
        not isinstance(members, list)
        or len(members) > 64
        or not isinstance(revoked, list)
        or len(revoked) > 64
        or not isinstance(sig, str)
    ):
        raise RelayError("invalid membership list")
    entries = []
    for item in members:
        if (
            not isinstance(item, dict)
            or not item.keys() >= {"key", "name"}
            or not isinstance(item["name"], str)
            or (item["name"] and not NAME_RE.fullmatch(item["name"]))
        ):
            raise RelayError("invalid membership list")
        validate_public_key(item["key"])
        entries.append((item["key"], item["name"]))
    for key in revoked:
        validate_public_key(key)
    body = {name: value for name, value in payload.items() if name != "sig"}
    try:
        VerifyKey(bytes.fromhex(peer)).verify(_v1_signed(body), bytes.fromhex(sig))
    except (BadSignatureError, ValueError):
        raise RelayError("invalid membership list") from None
    return entries, list(revoked)


def be_a_v1_device(node):
    """Make `node` answer lists as a 0.14 device does. Returns the versions it was sent."""
    versions = []

    async def receive(peer, payload):
        versions.append(payload.get("v") if isinstance(payload, dict) else None)
        client = node.client
        try:
            members, revoked = v1_open(
                payload, peer=peer, own_key=client.public_key, net=client.network_id()
            )
            client.accept_membership(peer, members, revoked)
        except RelayError:
            return {"error": "invalid"}
        return {"ok": True}

    node.bridge.membership_sync.receive = receive
    return versions


def list_from(sender, to, *members, issued_at=None, revoked=()):
    return seal_members(
        key=sender.client._store.key,
        to=to.key,
        net=sender.client.network_id(),
        members=[(m.key, m.name) for m in members],
        revoked=list(revoked),
        issued_at=issued_at,
    )


async def deliver(receiver, sender, payload):
    return await receiver.bridge._receive(sender.key, METHOD, payload, _secure=True)


@pytest.mark.asyncio
async def test_a_replayed_older_list_is_refused(chain):
    a, b, c, nodes, _wire = chain
    await sync(nodes)
    assert c.key in approved(a)  # B vouched for C
    mark = a.client.state.members_seen[b.key]

    # A genuine, validly signed list from B that names nobody: taken as news, it
    # would drop C at A, which only B vouched for. Older or equal, it is a replay.
    for stale in (mark - 1, mark):
        assert await deliver(a, b, list_from(b, a, issued_at=stale)) == {"error": "invalid"}
        assert c.key in approved(a)
    assert a.client.state.members_seen[b.key] == mark

    # The same list, newer, is news.
    assert await deliver(a, b, list_from(b, a, issued_at=mark + 1)) == {"ok": True}
    assert c.key not in approved(a)
    assert a.client.state.members_seen[b.key] == mark + 1


@pytest.mark.asyncio
async def test_a_voucher_that_sent_dated_lists_is_not_heard_undated_again(chain):
    a, b, c, nodes, _wire = chain
    await sync(nodes)
    assert a.client.state.members_seen[b.key]

    assert await deliver(a, b, list_from(b, a)) == {"error": "invalid"}  # v1, names nobody
    assert c.key in approved(a)


@pytest.mark.asyncio
@pytest.mark.parametrize("issued_at", [0, -5, True, 1.5, "1", 2**53])
async def test_a_list_with_a_malformed_date_is_refused(chain, issued_at):
    a, b, c, nodes, _wire = chain
    assert await deliver(a, b, list_from(b, a, issued_at=issued_at)) == {"error": "invalid"}
    assert b.key not in a.client.state.members_seen


@pytest.mark.asyncio
async def test_a_refused_list_does_not_move_the_mark(chain):
    a, b, c, nodes, _wire = chain
    forged = list_from(b, a, issued_at=5_000_000_000)
    forged["members"] = [{"key": fresh_key(), "name": "friend"}]  # altered after signing
    assert await deliver(a, b, forged) == {"error": "invalid"}
    assert b.key not in a.client.state.members_seen
    assert await deliver(a, b, list_from(b, a, issued_at=10)) == {"ok": True}


@pytest.mark.asyncio
async def test_the_high_water_survives_a_restart(chain):
    a, b, c, nodes, _wire = chain
    await sync(nodes)
    mark = a.client.state.members_seen[b.key]
    older = list_from(b, a, issued_at=mark - 1)

    restarted = RelayClient(a.client.workspace, state_dir=a.client.state_dir)
    assert restarted.state.members_seen[b.key] == mark
    after = MembershipSync(client=restarted, transport=None, online=dict)
    assert await after.receive(b.key, older) == {"error": "invalid"}
    assert c.key in restarted.state.approvals  # still vouched, still approved
    assert await after.receive(b.key, list_from(b, a, c, issued_at=mark + 1)) == {"ok": True}


@pytest.mark.asyncio
async def test_a_mark_is_kept_only_for_a_member_still_approved(chain):
    a, b, c, nodes, _wire = chain
    await sync(nodes)
    assert set(a.client.state.members_seen) == {b.key, c.key}  # both sent dated lists

    a.client.revoke(c.key)
    mark = a.client.state.members_seen[b.key]
    assert await deliver(a, b, list_from(b, a, issued_at=mark + 1)) == {"ok": True}
    assert set(a.client.state.members_seen) == {b.key}


@pytest.mark.asyncio
async def test_issued_at_never_goes_back_when_the_clock_does(chain, monkeypatch):
    a, b, c, nodes, _wire = chain
    clock = {"now": 5000.0}
    monkeypatch.setattr(
        network_members, "time", SimpleNamespace(**{**vars(time), "time": lambda: clock["now"]})
    )
    stamp = a.bridge.membership_sync._stamp
    assert stamp() == 5000
    assert stamp() == 5001  # the same second twice
    clock["now"] = 100.0  # the clock steps back
    assert stamp() == 5002
    clock["now"] = 9000.0
    assert stamp() == 9000

    # Saved before use: a restarted device with a clock still behind keeps counting up.
    clock["now"] = 100.0
    restarted = RelayClient(a.client.workspace, state_dir=a.client.state_dir)
    assert MembershipSync(client=restarted, transport=None, online=dict)._stamp() == 9001


@pytest.mark.asyncio
async def test_the_v1_wire_is_what_a_v1_device_signed_and_a_v1_device_refuses_v2(chain):
    a, b, c, nodes, _wire = chain
    key = b.client._store.key
    ours = seal_members(key=key, to=a.key, net="ab" * 32, members=[(c.key, "srv-c")], revoked=[])
    theirs = v1_seal(key=key, to=a.key, net="ab" * 32, members=[(c.key, "srv-c")], revoked=[])
    assert ours == theirs  # byte for byte: ed25519 signatures are deterministic

    dated = seal_members(
        key=key, to=a.key, net="ab" * 32, members=[(c.key, "srv-c")], revoked=[], issued_at=9
    )
    assert dated["v"] == 2 and dated["issued_at"] == 9 and dated["sig"] != ours["sig"]
    with pytest.raises(RelayError):
        v1_open(dated, peer=b.key, own_key=a.key, net="ab" * 32)
    # Stripping the date to pass for v1 does not verify: "v" is signed.
    undated = {name: value for name, value in dated.items() if name != "issued_at"}
    with pytest.raises(RelayError):
        v1_open({**undated, "v": 1}, peer=b.key, own_key=a.key, net="ab" * 32)


@pytest.mark.asyncio
async def test_a_device_that_reads_only_v1_still_interoperates(tmp_path):
    wire, nodes = await make_net(tmp_path, "mac", "srv-b", "srv-c")
    a, b, c = nodes  # A and C are new; B has not upgraded
    joined(a, b)
    joined(a, c)
    seen_by_b = be_a_v1_device(b)

    # New to old: A offers v2, B refuses it, A sends v1 at once and B takes it.
    await sync((a,), rounds=1)
    assert seen_by_b == [2, 1]
    assert c.key in approved(b) and c.key in b.client.state.vouched_by
    assert a.bridge.membership_sync._v1_only == {b.key: b.client._session_id}
    # New to new is v2 alone, and nothing falls back.
    assert b.key in approved(c) and a.client.state.members_seen == {}
    assert c.client.state.members_seen == {a.key: c.client.state.members_seen[a.key]}

    # Old to new: B's v1 lists are taken and set no mark, however often they come.
    for _ in range(2):
        assert await deliver(a, b, v1_seal(
            key=b.client._store.key, to=a.key, net=b.client.network_id(), members=[(c.key, "srv-c")], revoked=[]
        )) == {"ok": True}
    assert c.key in approved(a) and b.key not in a.client.state.members_seen

    # While B's session lasts A sends it v1 alone: a revocation reaches it.
    a.client.revoke(c.key, announce=True)
    await sync((a,), rounds=1)
    assert seen_by_b == [2, 1, 1]
    assert c.key not in approved(b)

    # B upgrades: its first dated list sets the mark, and its v1 lists stop being heard.
    assert await deliver(a, b, list_from(b, a, issued_at=50)) == {"ok": True}
    assert a.client.state.members_seen[b.key] == 50
    assert await deliver(a, b, list_from(b, a)) == {"error": "invalid"}
    await close_net(wire, nodes)


@pytest.mark.asyncio
async def test_a_refusal_that_is_not_about_the_version_costs_one_more_send_and_a_retry(chain):
    a, b, c, nodes, _wire = chain
    sent = []
    send = a.bridge.secure_transport.request

    async def refuse(key, method, payload, *, timeout):
        sent.append(payload["v"])
        return {"error": "invalid"}

    a.bridge.secure_transport.request = refuse
    try:
        await a.bridge.membership_sync.tick()
    finally:
        a.bridge.secure_transport.request = send
    assert sent == [2, 1]  # both offered, neither taken
    assert not a.bridge.membership_sync._delivered and not a.bridge.membership_sync._v1_only
    assert a.bridge.membership_sync._failures == {b.key: 1}
