"""The mesh across three real bridges: A -> B -> C where only B can reach C.

A and B share a relay wire. C is not on any relay: it is reachable only over B's
direct TLS endpoint. Every node is a real RelayAgentBridge with its real peer
mesh; the relay wire and the UDP locator broadcast are the only stand-ins.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from kollabor_agent.runtime import AgentRuntime
from kollabor_events import EventBus, EventType, Hook
from plugins.hub.dns.endpoint import build_server_ssl_context
from plugins.hub.dns.identity import IdentityManager
from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.registry import AgentRegistry
from plugins.hub.dns.storage import DNSStorage
from plugins.hub.messenger import AgentSocketServer
from plugins.hub.peer_discovery import _normalize_candidate
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_conversations import RelayAddress

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


async def make_node(tmp_path, name, *, designation, endpoint=None, settings=None):
    """endpoint: (cert, key, ca) to listen on a loopback TLS port, else None."""
    workspace = tmp_path / name
    workspace.mkdir()
    values = {
        "plugins.hub.peer_direct_enabled": True,
        "plugins.hub.peer_forward_enabled": True,
        "plugins.hub.peer_allow_private_network": True,
        **(settings or {}),
    }
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


@pytest_asyncio.fixture
async def mesh3(tmp_path):
    cert, key = _mint_tls_cert(tmp_path)
    ca = cert
    wire = RelayWire()
    a = await make_node(tmp_path, "mac", designation="lapis")
    b = await make_node(tmp_path, "srv-b", designation="koordinator", endpoint=(cert, key, ca))
    c = await make_node(tmp_path, "srv-c", designation="peridot", endpoint=(cert, key, ca))
    nodes = (a, b, c)
    approve_all(*nodes)
    # A and B are on the relay together. C is on no relay at all.
    for node in (a, b):
        wire.add(node.client)
    a.client._peers[b.key] = b.client._session_id
    b.client._peers[a.key] = a.client._session_id
    yield SimpleNamespace(a=a, b=b, c=c, wire=wire, cert=cert, tmp_path=tmp_path)
    for node in nodes:
        await node.bridge.close()
        if node.server is not None:
            await node.server.stop()
    await wire.close()


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
