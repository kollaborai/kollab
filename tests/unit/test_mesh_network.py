"""The mesh across three real bridges: A -> B -> C where only B can reach C.

A and B share a relay wire. C is not on any relay: it is reachable only over B's
direct TLS endpoint. Every node is a real RelayAgentBridge with its real peer
mesh; the relay wire and the UDP locator broadcast are the only stand-ins.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import time
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from nacl.signing import SigningKey

from kollabor_agent.runtime import AgentRuntime
from kollabor_events import EventBus, EventType, Hook
from plugins.hub import peer_transport
from plugins.hub.dns.endpoint import build_server_ssl_context
from plugins.hub.dns.identity import IdentityManager
from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.registry import AgentRegistry
from plugins.hub.dns.storage import DNSStorage
from plugins.hub.messenger import AgentSocketServer
from plugins.hub.peer_discovery import (
    PeerDiscoveryError,
    PeerDiscoveryService,
    _normalize_candidate,
)
from plugins.hub.peer_router import PeerRouteError
from plugins.hub.peer_transport import PeerMeshRuntime
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_conversations import RelayAddress
from plugins.hub.relay_state import RelayError

from .test_peer_transport import RelayWire, _mint_tls_cert
from .test_relay_agent_bridge import Directory, ModelRecorder

ROOM = "ab" * 32


@dataclass
class Node:
    name: str
    designation: str
    bridge: RelayAgentBridge
    hub: HubPlugin
    model: ModelRecorder
    server: AgentSocketServer | None

    @property
    def client(self):
        return self.bridge.commands.client

    @property
    def mesh(self):
        return self.bridge.peer_mesh

    @property
    def key(self) -> str:
        return self.client.public_key

    def address(self) -> str:
        return str(
            RelayAddress(
                self.key, self.client.state.workspace_id, self.bridge.identity.agent_id
            )
        )


async def make_node(
    tmp_path, name, *, designation, endpoint=None, settings=None, drop=(), dns_identity=True
):
    """endpoint: (cert, key, ca) to listen on a loopback TLS port, else None.

    drop: config names left unset. dns_identity=False: the device has no endpoint identity.
    """
    workspace = tmp_path / name
    workspace.mkdir()
    values = {
        "plugins.hub.peer_direct_enabled": True,
        "plugins.hub.peer_forward_enabled": True,
        "plugins.hub.peer_allow_private_network": True,
        **(settings or {}),
    }
    for option in drop:
        values.pop(f"plugins.hub.{option}", None)
    if endpoint:
        values["plugins.hub.endpoint_tls_ca"] = endpoint[2]
    config = SimpleNamespace(get=lambda key, default=None: values.get(key, default))
    bus = EventBus()
    model = ModelRecorder()
    bus.register_service("llm_service", model)
    hub = HubPlugin(event_bus=bus, config=config)
    hub._identity = AgentRuntime(
        identity=designation,
        agent_id=f"{name}-session",
        state="ready",
        socket_path=f"/tmp/kollab-mesh-{name}-{secrets.token_hex(3)}.sock",
    )
    hub._task_ledger = None
    hub._display_hub_message = MagicMock()
    hub._display_outgoing_message = MagicMock()
    hub._presence = MagicMock()
    storage = DNSStorage(tmp_path / f"dns-{name}")
    identity = IdentityManager(storage)
    registry = AgentRegistry(storage)
    _, public = identity.get_or_create_keypair(designation)
    registry.register(
        AgentRecord(designation=designation, public_key=public, approval_state="approved")
    )
    hub._dns_storage, hub._dns_identity, hub._dns_registry = storage, identity, registry
    if not dns_identity:
        hub._dns_identity = None
    server = None
    if endpoint:
        cert, key, _ca = endpoint

        async def refuse(_message):
            raise AssertionError("a peer carrier frame must not enter Hub dispatch")

        server = AgentSocketServer(
            f"{name}-agent", refuse, socket_name=f"kollab-mesh-{name}-{os.getpid()}"
        )
        server.set_dns_auth(registry, identity, require_auth=False, local_designation=designation)
        server.enable_endpoint("127.0.0.1", 0, build_server_ssl_context(cert, key))
        await server.start()
        port = server._tcp_server.sockets[0].getsockname()[1]
        hub._socket_server = server
        hub._endpoint_uri = f"wss://127.0.0.1:{port}"
    bridge = RelayAgentBridge(
        hub,
        workspace,
        state_dir=tmp_path / f"{name}-state",
        directory=Directory(workspace, hub._identity),
    )
    hub._relay_agent = bridge
    await bus.register_hook(
        Hook(
            name="continuation",
            plugin_name="test",
            event_type=EventType.TRIGGER_LLM_CONTINUE,
            callback=model.begin,
            priority=100,
        )
    )
    await bridge._ensure_owner()
    bridge._state()
    bridge.set_device_name(name)
    client = bridge.commands.client
    client.state.room = ROOM
    client.state.origin = "https://relay.example"
    client._adopt_bridge_fields()
    client._store.save()
    return Node(name, designation, bridge, hub, model, server)


def approve_all(*nodes):
    """Every pair approves each other and binds the device names."""
    for node in nodes:
        for other in nodes:
            if other is not node:
                node.client.approve(other.key)
                node.bridge.bind_peer_device(other.key, other.name)


def deliver_locator(receiver: Node, sender: Node) -> None:
    """What the UDP discovery service does with a datagram, minus the socket."""
    wire = sender.mesh._local_locator_wire(sender.mesh.local_session())
    assert wire is not None, "the sender produced no locator"
    normalized = receiver.mesh._verify_locator_wire(wire)
    assert normalized is not None, "the receiver rejected the locator"
    candidate = _normalize_candidate(wire, normalized)
    assert receiver.mesh.locator_store.accept_candidate(candidate) is True
    receiver.mesh._accept_locator_candidate(candidate)


async def build_mesh3(tmp_path, *, b_settings=None):
    cert, key = _mint_tls_cert(tmp_path)
    ca = cert
    wire = RelayWire()
    a = await make_node(tmp_path, "mac", designation="lapis")
    b = await make_node(
        tmp_path, "srv-b", designation="koordinator", endpoint=(cert, key, ca), settings=b_settings
    )
    c = await make_node(tmp_path, "srv-c", designation="peridot", endpoint=(cert, key, ca))
    nodes = (a, b, c)
    approve_all(*nodes)
    # A and B are on the relay together. C is on no relay at all.
    for node in (a, b):
        wire.add(node.client)
    a.client._peers[b.key] = b.client._session_id
    b.client._peers[a.key] = a.client._session_id
    return SimpleNamespace(a=a, b=b, c=c, wire=wire, cert=cert, tmp_path=tmp_path)


async def close_mesh3(net):
    for node in (net.a, net.b, net.c):
        await node.bridge.close()
        if node.server is not None:
            await node.server.stop()
    await net.wire.close()


@pytest_asyncio.fixture
async def mesh3(tmp_path):
    net = await build_mesh3(tmp_path)
    yield net
    await close_mesh3(net)


async def settle(*nodes, rounds=3):
    for _ in range(rounds):
        for node in nodes:
            await node.mesh.refresh()


async def link_everything(mesh3):
    """B and C find each other's locators and link; A learns the route by gossip."""
    a, b, c = mesh3.a, mesh3.b, mesh3.c
    deliver_locator(b, c)
    deliver_locator(c, b)
    await settle(b, c)
    await settle(a, b, c)


def set_trust(node, level):
    store = node.client._store
    store.state.trust = level
    store.save()


def wire_text(mesh3):
    return json.dumps(mesh3.wire.sent)


class Spy:
    """Everything one node's mesh was handed or passed on, as text."""

    def __init__(self, node):
        self.seen = []
        mesh = node.mesh
        handle_forward = mesh.handle_forward
        send_forward = mesh._send_forward_to_peer
        receive = node.bridge._receive

        async def spy_handle_forward(peer, frame):
            self.seen.append(json.dumps(frame))
            return await handle_forward(peer, frame)

        async def spy_send_forward(peer, frame, *, timeout):
            self.seen.append(json.dumps(frame))
            return await send_forward(peer, frame, timeout=timeout)

        async def spy_receive(peer, method, payload, **kwargs):
            self.seen.append(json.dumps([method, payload]))
            return await receive(peer, method, payload, **kwargs)

        mesh.handle_forward = spy_handle_forward
        mesh._send_forward_to_peer = spy_send_forward
        node.client.set_request_handler(spy_receive)
        mesh.application_handler = spy_receive

    def contains(self, text):
        return any(text in item for item in self.seen)


@pytest.mark.asyncio
async def test_a_relay_less_device_links_over_the_direct_endpoint_and_a_routes_through_b(
    mesh3,
):
    a, b, c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)

    b_id, c_id = b.mesh.local_peer_id, c.mesh.local_peer_id
    assert b.mesh.router.link_between(b_id, c_id) is not None
    assert c.mesh.router.link_between(b_id, c_id) is not None
    assert a.mesh.router.route_candidates(c.mesh.local_peer_id) == (
        (a.mesh.local_peer_id, b_id, c_id),
    )
    # C never had a relay session: it runs under its direct session.
    assert c.client.status()["state"] != "online"
    assert c.mesh.local_session() == c.mesh._direct_session
    assert c.key not in {peer["key"] for peer in a.client.peers()}


@pytest.mark.asyncio
async def test_names_are_the_same_whether_a_device_is_on_the_relay_or_behind_b(mesh3):
    a, _b, _c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)
    await a.bridge._rpc_directory({"peer": "", "cached": False})
    rows = {row["handle"]: row for row in await a.bridge.remote_agents()}

    assert set(rows) == {"koordinator@srv-b", "peridot@srv-c"}
    status = await a.bridge.commands.format_status()
    assert "koordinator@srv-b" in status and "peridot@srv-c" in status
    # Neither reads differently on the screen, and no key or address shows.
    import re

    assert not re.search(r"[0-9a-f]{64}", status)
    assert "relay:" not in status
    line_b = next(line for line in status.splitlines() if "koordinator@srv-b" in line)
    line_c = next(line for line in status.splitlines() if "peridot@srv-c" in line)
    assert line_b.split("@")[1].split(" ", 1)[1] == line_c.split("@")[1].split(" ", 1)[1]


@pytest.mark.asyncio
async def test_a_message_reaches_c_through_b_sealed_and_b_never_reads_it(mesh3):
    a, b, c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)
    spy = Spy(b)
    secret = "rotate the nginx logs, marker-8f3a"

    sent = await a.bridge.send(c.address(), secret)

    assert sent["state"] == "queued"
    await c.bridge._tick()
    assert len(c.model.contexts) == 1
    assert secret in c.model.conversation_history[-1].content
    assert "lapis@mac" in c.model.conversation_history[-1].content
    # B carried opaque frames only: the text is in nothing it was handed, sent
    # on, or received, and in nothing the relay carried.
    assert b.mesh.forwarded_frames > 0
    assert spy.seen
    assert not spy.contains(secret)
    assert secret not in wire_text(mesh3)
    assert b.model.contexts == [] and b.bridge.store.queued(b.bridge.identity.agent_id) == []


@pytest.mark.asyncio
async def test_c_answers_a_through_b_the_same_way(mesh3):
    a, _b, c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)

    await a.bridge.send(c.address(), "check the tunnel")
    await c.bridge._tick()
    reply = await c.bridge.send(a.address(), "tunnel is up, marker-51d2")

    assert reply["state"] == "queued"
    await a.bridge._tick()
    assert "marker-51d2" in a.model.conversation_history[-1].content
    assert "peridot@srv-c" in a.model.conversation_history[-1].content


@pytest.mark.asyncio
async def test_c_applies_its_own_trust_to_a_exactly_as_through_the_directory(mesh3):
    a, _b, c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)
    set_trust(a, "agents")
    set_trust(c, "agents")

    refused = await a.bridge.send(c.address(), "run it")

    # C lists no agent as reachable, so it turns A away with the same receipt a
    # directly reached device gives.
    assert refused["state"] == "rejected"
    assert refused["reason"] == "not_authorized"
    assert c.bridge.store.queued(c.bridge.identity.agent_id) == []

    c.bridge.store.grant(c.client.state.room, a.key, c.bridge.identity.identity)
    allowed = await a.bridge.send(c.address(), "run it now")
    assert allowed["state"] == "queued"
    await c.bridge._tick()
    assert len(c.model.contexts) == 1


@pytest.mark.asyncio
async def test_a_device_c_has_not_approved_is_turned_away_at_c(mesh3):
    a, _b, c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)
    c.client.revoke(a.key)

    with pytest.raises(Exception):
        await a.bridge.send(c.address(), "let me in")
    assert c.bridge.store.queued(c.bridge.identity.agent_id) == []


# ---------------------------------------------------------------------------
# The session invariant, the forwarding limits, the defaults and the pins.
# ---------------------------------------------------------------------------

DIRECT = "plugins.hub.peer_direct_enabled"
FORWARD = "plugins.hub.peer_forward_enabled"


class FrozenClock:
    """time.time() held still so a minute bucket cannot roll over mid-test."""

    def __init__(self, monkeypatch):
        self.now = time.time()
        monkeypatch.setattr(time, "time", lambda: self.now)


@pytest.mark.asyncio
async def test_ensure_session_makes_both_ends_name_the_same_link_session_id(mesh3):
    b, c = mesh3.b, mesh3.c
    deliver_locator(b, c)
    deliver_locator(c, b)
    tb, tc = b.mesh.secure_transport, c.mesh.secure_transport
    assert tb.link_session_id(c.key) is None and tc.link_session_id(b.key) is None

    await tb.ensure_session(c.key, timeout=3)
    proposed = tb.link_session_id(c.key)
    assert proposed is not None
    assert tc.link_session_id(b.key) == proposed  # C holds only the inbound half

    await tc.ensure_session(b.key, timeout=3)  # C's own outbound opens beside it
    settled = tb.link_session_id(c.key)
    assert settled == tc.link_session_id(b.key)

    await tb.ensure_session(c.key, timeout=3)  # already open: nothing new opens
    await tc.ensure_session(b.key, timeout=3)
    assert tb.link_session_id(c.key) == tc.link_session_id(b.key) == settled
    assert len(tb._outbound) == 1 and len(tc._outbound) == 1


@pytest.mark.asyncio
async def test_ensure_session_refuses_a_bad_timeout_and_an_unapproved_peer(mesh3):
    b, c = mesh3.b, mesh3.c
    deliver_locator(b, c)
    deliver_locator(c, b)
    transport = b.mesh.secure_transport
    for bad in (0, -1, 301, float("nan"), "3", True):
        with pytest.raises(RelayError):
            await transport.ensure_session(c.key, timeout=bad)
    assert transport.link_session_id(c.key) is None  # nothing was opened

    b.client.revoke(c.key)
    with pytest.raises(RelayError):
        await transport.ensure_session(c.key, timeout=3)


@pytest.mark.asyncio
async def test_a_link_proposal_is_made_only_after_the_outbound_session_is_open(mesh3):
    b, c = mesh3.b, mesh3.c
    deliver_locator(b, c)
    deliver_locator(c, b)
    order = []
    ensure = b.mesh.secure_transport.ensure_session
    propose = b.mesh._make_link_signature

    async def spy_ensure(peer_key, **kwargs):
        await ensure(peer_key, **kwargs)
        order.append(("session", peer_key))

    def spy_propose(local, remote, peer_key):
        order.append(("proposal", peer_key))
        return propose(local, remote, peer_key)

    b.mesh.secure_transport.ensure_session = spy_ensure
    b.mesh._make_link_signature = spy_propose
    await settle(b, c)

    assert ("proposal", c.key) in order
    for index, (event, peer) in enumerate(order):
        if event == "proposal":
            assert ("session", peer) in order[:index], "a proposal came before its session"
    assert b.mesh.router.link_between(b.mesh.local_peer_id, c.mesh.local_peer_id) is not None


def test_the_forwarding_limits_are_the_documented_ones():
    assert peer_transport.MAX_PEER_FORWARD_PER_PEER_PER_MINUTE == 120
    assert peer_transport.MAX_PEER_FORWARD_TOTAL_PER_MINUTE == 600
    assert peer_transport.MAX_PEER_FORWARD_CONCURRENCY == 16


@pytest.mark.asyncio
async def test_transit_is_capped_per_peer_and_a_new_minute_resets_it(mesh3, monkeypatch):
    clock = FrozenClock(monkeypatch)
    mesh = mesh3.b.mesh
    for _ in range(120):
        mesh._admit_transit("aa" * 32)
    with pytest.raises(PeerRouteError, match="over its limit"):
        mesh._admit_transit("aa" * 32)
    mesh._admit_transit("bb" * 32)  # the cap is per peer

    clock.now += 61
    mesh._admit_transit("aa" * 32)  # the next minute starts clean
    assert sum(mesh._forward_counts.values()) == 1
    assert sum(mesh._forward_total.values()) == 1


@pytest.mark.asyncio
async def test_transit_is_capped_in_total_across_peers(mesh3, monkeypatch):
    FrozenClock(monkeypatch)
    mesh = mesh3.b.mesh
    for peer in range(5):  # five peers at the per-peer cap fill the 600
        for _ in range(120):
            mesh._admit_transit(f"{peer:064x}")
    with pytest.raises(PeerRouteError, match="over its limit"):
        mesh._admit_transit(f"{99:064x}")
    assert sum(mesh._forward_total.values()) == 600


@pytest.mark.asyncio
async def test_transit_is_capped_at_sixteen_at_once(mesh3):
    mesh = mesh3.b.mesh
    for _ in range(16):
        await mesh._forward_semaphore.acquire()
    with pytest.raises(PeerRouteError, match="over its limit"):
        mesh._admit_transit(mesh3.a.key)
    mesh._forward_semaphore.release()
    mesh._admit_transit(mesh3.a.key)
    assert sum(mesh._forward_counts.values()) == 1  # the refused frame cost nothing


@pytest.mark.asyncio
async def test_only_frames_passing_through_count_against_the_limits(mesh3):
    a, b, c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)

    await a.bridge.send(c.address(), "count only the transit")
    await c.bridge._tick()

    assert len(c.model.contexts) == 1
    assert sum(b.mesh._forward_total.values()) > 0  # B carried frames for others
    assert sum(c.mesh._forward_total.values()) == 0  # delivery to C is not transit
    assert sum(a.mesh._forward_total.values()) == 0


@pytest.mark.asyncio
async def test_a_spent_transit_budget_turns_the_route_away_at_b_until_the_minute_turns(
    mesh3, monkeypatch
):
    clock = FrozenClock(monkeypatch)
    a, b, c = mesh3.a, mesh3.b, mesh3.c
    await link_everything(mesh3)
    b.mesh._forward_total[int(clock.now // 60)] = 600

    with pytest.raises(Exception):
        await a.bridge.send(c.address(), "over the budget")
    assert c.bridge.store.queued(c.bridge.identity.agent_id) == []

    clock.now += 61
    allowed = await a.bridge.send(c.address(), "next minute")
    assert allowed["state"] == "queued"


def test_the_hub_defaults_turn_direct_and_forwarding_on_and_leave_every_socket_off():
    hub = HubPlugin.get_default_config()["plugins"]["hub"]
    assert hub["peer_direct_enabled"] is True and hub["peer_forward_enabled"] is True
    for name in (
        "endpoint_enabled",
        "peer_allow_private_network",
        "peer_discovery_scan_enabled",
        "peer_discovery_advertise_enabled",
    ):
        assert hub[name] is False, name


@pytest.mark.asyncio
async def test_a_config_with_neither_key_runs_the_mesh_on_and_binds_nothing(tmp_path):
    node = await make_node(
        tmp_path,
        "plain",
        designation="lapis",
        drop=("peer_direct_enabled", "peer_forward_enabled"),
    )
    try:
        mesh = node.mesh
        assert mesh.direct_enabled is True and mesh._forwarding_enabled() is True
        assert not mesh.discovery.advertise_enabled and not mesh.discovery.scan_enabled
        await mesh.discovery.start()
        assert mesh.discovery._transport is None  # no LAN socket opened
        assert node.server is None
        assert mesh._local_locator_wire(mesh.local_session()) is None  # no endpoint, no locator
    finally:
        await node.bridge.close()


@pytest.mark.asyncio
async def test_a_device_without_an_endpoint_identity_keeps_direct_off_and_still_forwards(
    tmp_path,
):
    node = await make_node(tmp_path, "bare", designation="lapis", dns_identity=False)
    try:
        assert node.mesh is not None
        assert node.mesh.direct_enabled is False
        assert node.mesh._forwarding_enabled() is True
    finally:
        await node.bridge.close()


@pytest.mark.asyncio
async def test_the_forward_switch_stops_transit_and_nothing_else(tmp_path):
    net = await build_mesh3(tmp_path, b_settings={FORWARD: False})
    try:
        a, b, c = net.a, net.b, net.c
        await link_everything(net)

        assert b.mesh._forwarding_enabled() is False
        assert "forwarder" not in b.mesh._ensure_local_record().roles
        # B and C still link, and B still takes what is meant for B...
        assert b.mesh.router.link_between(b.mesh.local_peer_id, c.mesh.local_peer_id) is not None
        assert (await a.bridge.send(b.address(), "for you, B"))["state"] == "queued"
        # ...but it carries nothing for A to C.
        assert not a.mesh.router.route_candidates(c.mesh.local_peer_id)
        with pytest.raises(Exception):
            await a.bridge.send(c.address(), "through you")
        assert c.bridge.store.queued(c.bridge.identity.agent_id) == []
    finally:
        await close_mesh3(net)


@pytest.mark.asyncio
async def test_the_direct_switch_stops_locator_links_and_nothing_else(tmp_path):
    net = await build_mesh3(tmp_path, b_settings={DIRECT: False})
    try:
        a, b, c = net.a, net.b, net.c
        deliver_locator(b, c)
        deliver_locator(c, b)
        await settle(b, c)
        await settle(a, b, c)

        assert b.mesh.direct_enabled is False
        assert c.key not in b.mesh._live_peers()
        assert b.mesh._peer_session(c.key) is None
        assert b.mesh.endpoint_key_for("peridot") == ""
        assert b.mesh.router.link_between(b.mesh.local_peer_id, c.mesh.local_peer_id) is None
        with pytest.raises(Exception):
            await a.bridge.send(c.address(), "no way through")
        # The relay path is untouched.
        assert a.key in b.mesh._live_peers()
        assert (await a.bridge.send(b.address(), "still here"))["state"] == "queued"
    finally:
        await close_mesh3(net)


@pytest.mark.asyncio
async def test_local_session_is_the_relay_session_while_registered_and_the_direct_one_otherwise(
    mesh3, monkeypatch
):
    a, c = mesh3.a, mesh3.c
    online = a.client.status()
    assert online["state"] == "online"
    assert a.mesh.local_session() == online["session"] != a.mesh._direct_session
    # C never registered: it runs under its own per-process session.
    assert re.fullmatch(r"[0-9a-f]{32}", c.mesh._direct_session)
    assert c.mesh.local_session() == c.mesh._direct_session
    assert c.mesh._direct_session != a.mesh._direct_session
    # The moment A's relay session is gone or unusable it falls back to its own.
    for status in (
        {**online, "state": "connecting"},
        {**online, "session": "not a session"},
        {**online, "session": None},
    ):
        monkeypatch.setattr(a.client, "status", lambda status=status: status)
        assert a.mesh.local_session() == a.mesh._direct_session


@pytest.mark.asyncio
async def test_discovery_reads_the_session_from_the_mesh_every_time(mesh3, monkeypatch):
    a = mesh3.a
    online = a.client.status()
    assert a.mesh.discovery.session_id == online["session"]
    monkeypatch.setattr(a.client, "status", lambda: {**online, "state": "connecting"})
    assert a.mesh.discovery.session_id == a.mesh._direct_session


def test_discovery_accepts_only_a_callable_session_provider_that_returns_a_session():
    with pytest.raises(TypeError):
        PeerDiscoveryService(session_provider="abcd")
    fixed = "ab" * 16
    assert PeerDiscoveryService(session_provider=lambda: fixed).session_id == fixed
    for junk in ("zz", "ab" * 15, 7, None):
        with pytest.raises(PeerDiscoveryError):
            PeerDiscoveryService(session_provider=lambda junk=junk: junk).session_id


@pytest.mark.asyncio
async def test_a_locator_names_the_session_the_device_runs_under_and_peers_read_it_by_source(
    mesh3,
):
    a, b, c = mesh3.a, mesh3.b, mesh3.c
    wire_b = b.mesh._local_locator_wire(b.mesh.local_session())
    wire_c = c.mesh._local_locator_wire(c.mesh.local_session())
    assert wire_b["session_id"] == b.client.status()["session"]  # registered with the relay
    assert wire_c["session_id"] == c.mesh._direct_session  # on no relay at all
    assert wire_c["endpoint_designation"] == "peridot"
    assert c.mesh._local_locator_wire(b.mesh.local_session()) is None  # only the session in force
    assert a.mesh._local_locator_wire(a.mesh.local_session()) is None  # no endpoint, no locator

    await link_everything(mesh3)
    assert b.mesh._peer_session(a.key) == ("relay", a.client._session_id)
    assert b.mesh._peer_session(c.key) == ("locator", c.mesh._direct_session)
    assert a.mesh._peer_session(c.key) == ("record", c.mesh._direct_session)
    assert a.mesh._peer_session("cd" * 32) is None  # never approved


@pytest.mark.asyncio
async def test_discovery_takes_private_sources_and_the_hosts_own_address_and_no_other_public_one(
    mesh3, tmp_path, monkeypatch
):
    allowed = mesh3.b.mesh._discovery_source_allowed
    for source in ("10.1.2.3", "192.168.0.7", "172.16.4.4"):
        assert allowed(source) is True, source
    for source in ("127.0.0.1", "0.0.0.0", "239.255.77.77", "8.8.8.8", "::1", "nonsense", ""):
        assert allowed(source) is False, source

    # A cloud host announces from its own public address; only that one gets in.
    monkeypatch.setattr(
        PeerMeshRuntime, "_own_addresses", staticmethod(lambda: frozenset({"8.8.8.8"}))
    )
    assert allowed("8.8.8.8") is True
    assert allowed("8.8.4.4") is False

    closed = await make_node(
        tmp_path,
        "closed",
        designation="lapis",
        settings={"plugins.hub.peer_allow_private_network": False},
    )
    try:
        assert closed.mesh._discovery_source_allowed("10.1.2.3") is False
        assert closed.mesh._discovery_source_allowed("8.8.8.8") is False
    finally:
        await closed.bridge.close()


def test_the_hosts_own_addresses_are_ipv4_only_and_empty_when_interfaces_cannot_be_read(
    monkeypatch,
):
    import psutil

    interfaces = {
        "eth0": [
            SimpleNamespace(family=socket.AF_INET, address="203.0.113.9"),
            SimpleNamespace(family=socket.AF_INET6, address="fe80::1"),
        ]
    }
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: interfaces)
    assert PeerMeshRuntime._own_addresses() == frozenset({"203.0.113.9"})

    def unreadable():
        raise OSError("no interfaces")

    monkeypatch.setattr(psutil, "net_if_addrs", unreadable)
    assert PeerMeshRuntime._own_addresses() == frozenset()


@pytest.mark.asyncio
async def test_endpoint_key_for_names_one_approved_device_or_nobody(mesh3, monkeypatch):
    a, b, c = mesh3.a, mesh3.b, mesh3.c
    deliver_locator(b, c)
    c_key = c.mesh._local_locator_wire(c.mesh.local_session())["endpoint_public_key"]
    assert b.mesh.endpoint_key_for("peridot") == c_key
    assert b.mesh.endpoint_key_for("someone-else") == ""

    # Two approved devices naming one endpoint with different keys admit nobody;
    # naming it with the same key is one device's name, not a conflict.
    real = b.mesh._locator_for_peer
    other = SigningKey.generate().verify_key.encode().hex()
    for claimed_key, expected in ((other, ""), (c_key, c_key)):
        claim = {**real(c.key), "relay_public_key": a.key, "endpoint_public_key": claimed_key}
        with monkeypatch.context() as patch:
            patch.setattr(
                b.mesh,
                "_locator_for_peer",
                lambda key, claim=claim: claim if key == a.key else real(key),
            )
            assert b.mesh.endpoint_key_for("peridot") == expected

    # A device B no longer approves is not heard at all.
    b.client.revoke(c.key)
    assert b.mesh.endpoint_key_for("peridot") == ""


@pytest.mark.asyncio
async def test_the_registry_pin_can_only_deny_a_signed_locator(mesh3):
    b, c = mesh3.b, mesh3.c
    wire = c.mesh._local_locator_wire(c.mesh.local_session())
    key = wire["endpoint_public_key"]
    other = SigningKey.generate().verify_key.encode().hex()
    registry, server = b.hub._dns_registry, b.server
    deliver_locator(b, c)

    # Never seen here: the approved relay key that signed the locator vouches.
    assert b.mesh._verify_locator_wire(wire) is not None
    assert server._remote_peer_identity("peridot") == (key, True)

    # Known, approved, same key: the registry answers and the locator adds nothing.
    registry.register(
        AgentRecord(designation="peridot", public_key=key, approval_state="approved")
    )
    assert b.mesh._verify_locator_wire(wire) is not None
    assert server._remote_peer_identity("peridot") == (key, False)

    # Known under another key: the locator is refused and the registry key stays.
    record = registry.resolve("peridot")
    record.public_key = other
    assert b.mesh._verify_locator_wire(wire) is None
    assert server._remote_peer_identity("peridot") == (other, False)

    # Rejected: nothing admits it, locator or not.
    record.public_key, record.approval_state = key, "rejected"
    assert b.mesh._verify_locator_wire(wire) is None
    assert server._remote_peer_identity("peridot") == ("", False)

    # Only auto-approved (a same-host agent): still a locator peer.
    record.approval_state = "auto_approved"
    assert b.mesh._verify_locator_wire(wire) is not None
    assert server._remote_peer_identity("peridot") == (key, True)

    # A resolver that fails or answers junk admits nobody.
    def broken(_designation):
        raise RuntimeError("resolver failed")

    server.set_peer_identity_resolver(broken)
    assert server._remote_peer_identity("ghost") == ("", False)
    server.set_peer_identity_resolver(lambda _designation: "not-a-key")
    assert server._remote_peer_identity("ghost") == ("", False)
    with pytest.raises(TypeError):
        server.set_peer_identity_resolver("nope")
