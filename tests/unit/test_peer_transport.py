"""Runtime integration tests for direct and relayed peer carriers.

All sockets in this module bind to loopback and all keys/messages are synthetic.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import rfc8785
from nacl.signing import SigningKey

from kollabor_agent.runtime import AgentRuntime
from kollabor_events import EventBus
from plugins.hub.dns.endpoint import build_client_ssl_context, build_server_ssl_context
from plugins.hub.dns.identity import IdentityManager
from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.registry import AgentRegistry
from plugins.hub.dns.storage import DNSStorage
from plugins.hub.messenger import AgentMessenger, AgentSocketServer
from plugins.hub.peer_discovery import PeerDiscoveryService
from plugins.hub.peer_records import peer_id_for_key
from plugins.hub.peer_router import TransientPeerDeliveryError
from plugins.hub.peer_transport import PeerMeshRuntime
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_state import RelayError
from plugins.hub.secure_conversation import SecureConversationTransport


class RelayWire:
    """An in-process encrypted RelayClient carrier, without a service process."""

    def __init__(self) -> None:
        self.clients: dict[str, RelayClient] = {}
        self.queues: dict[str, asyncio.Queue] = {}
        self.tasks: list[asyncio.Task] = []
        self.sent: list[tuple[str, dict]] = []

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
                wire.sent.append((client.public_key, frame))
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
async def mesh_network(tmp_path):
    names = ("origin", "relay", "destination")
    wire = RelayWire()
    clients = {
        name: RelayClient(tmp_path / name, state_dir=tmp_path / f"state-{name}")
        for name in names
    }
    room = clients["origin"].state.room
    for client in clients.values():
        client.state.room = room
        wire.add(client)
    # The origin has explicit peer approval for the final recipient. Relay
    # sessions remain only on the A-B and B-C edges.
    for client in clients.values():
        for other in clients.values():
            if client is not other:
                client.approve(other.public_key)
    for left, right in (("origin", "relay"), ("relay", "destination")):
        clients[left]._peers[clients[right].public_key] = clients[right]._session_id
        clients[right]._peers[clients[left].public_key] = clients[left]._session_id

    states = {}
    application_calls = {name: [] for name in names}
    dispatch_calls = {name: [] for name in names}
    for name, client in clients.items():
        secure = SecureConversationTransport(client, client._store.key.encode())

        async def dispatch(peer_key, method, payload, *, _name=name, _secure=secure):
            dispatch_calls[_name].append((peer_key, method, payload))
            if method == "directory":
                return {"agents": [{"name": f"{_name}-agent"}], "truncated": False}
            if method == "peer.exchange":
                return await states[_name]["mesh"].handle_exchange(peer_key, payload)
            raise RelayError("unexpected secure application in test")

        async def application(
            peer_key,
            method,
            payload,
            *,
            _name=name,
            _secure=secure,
            _dispatch=dispatch,
        ):
            if method == "peer.forward":
                return await states[_name]["mesh"].handle_forward(peer_key, payload)
            if method == "secure_identity":
                application_calls[_name].append((peer_key, method))
                return _secure.identity_response(peer_key, payload)
            if method == "secure_packet":
                application_calls[_name].append((peer_key, method))
                return await _secure.handle_packet(peer_key, payload, _dispatch)
            raise RelayError("unexpected relay application in test")

        mesh = PeerMeshRuntime(
            client,
            secure,
            tmp_path / f"mesh-{name}",
            application,
            forwarding_enabled=lambda: True,
        )
        states[name] = {"secure": secure, "mesh": mesh}
        client.set_request_handler(application)
        await mesh.start()

    try:
        # First establish B-C, then A-B. The second B-C exchange gossips A's
        # signed record/link to C so the end-to-end TLS binding works without
        # a relay registration between A and C.
        await states["relay"]["mesh"].exchange_peer(clients["destination"].public_key)
        await states["origin"]["mesh"].exchange_peer(clients["relay"].public_key)
        await states["relay"]["mesh"].exchange_peer(clients["destination"].public_key)
        yield clients, states, application_calls, dispatch_calls, wire
    finally:
        for state in states.values():
            await state["mesh"].close()
            state["secure"].close()
        await wire.close()


@pytest.mark.asyncio
async def test_link_refresh_reuses_statement_until_half_life(mesh_network, monkeypatch):
    # Re-signing an unchanged link with a new timestamp under the same revision
    # was rejected as equivocation on the periodic refresh, tearing down the
    # healthy session (the source of this file's intermittent failures).
    import plugins.hub.peer_transport as peer_transport

    clients, states, _, _, _ = mesh_network
    mesh = states["origin"]["mesh"]
    relay_key = clients["relay"].public_key
    records = mesh.router.record_snapshot()
    local = records[mesh.local_peer_id]
    remote = records[peer_id_for_key(relay_key)]
    link = mesh.router.link_between(mesh.local_peer_id, remote.peer_id)
    assert link is not None
    real_time = time.time

    monkeypatch.setattr(peer_transport.time, "time", lambda: real_time() + 1)
    again = mesh._make_link_signature(local, remote, relay_key)["payload"]
    assert (again["revision"], again["issued_at"], again["expires_at"]) == (
        link.revision,
        link.issued_at,
        link.expires_at,
    )

    monkeypatch.setattr(peer_transport.time, "time", lambda: link.expires_at - 10)
    renewed = mesh._make_link_signature(local, remote, relay_key)["payload"]
    assert renewed["revision"] > link.revision


@pytest.mark.asyncio
async def test_relay_peer_with_mesh_link_keeps_native_relay_path(mesh_network):
    # Live regression: once peers exchanged mesh records, secure requests to a
    # relay-connected peer went through peer.forward and failed; without a
    # direct dial the mesh adds nothing for a peer already on the relay.
    clients, states, application_calls, dispatch_calls, _ = mesh_network
    origin_mesh = states["origin"]["mesh"]
    relay_key = clients["relay"].public_key
    assert origin_mesh.direct_enabled is False
    assert origin_mesh.router.link_between(
        origin_mesh.local_peer_id, peer_id_for_key(relay_key)
    ) is not None
    forwarded = []

    async def record_forward(peer_key, frame, *, timeout):
        forwarded.append(peer_key)
        raise AssertionError("relay peer request used the mesh carrier")

    origin_mesh._send_forward_to_peer = record_forward
    result = await states["origin"]["secure"].request(relay_key, "directory", {}, timeout=10)

    assert result == {"agents": [{"name": "relay-agent"}], "truncated": False}
    assert forwarded == []
    assert any(method == "secure_packet" for _, method in application_calls["relay"])


@pytest.mark.asyncio
async def test_three_peer_carrier_delivers_secure_directory_and_replay_once(mesh_network):
    clients, states, application_calls, dispatch_calls, wire = mesh_network
    origin_mesh = states["origin"]["mesh"]
    relay_mesh = states["relay"]["mesh"]
    destination_mesh = states["destination"]["mesh"]
    destination_key = clients["destination"].public_key
    destination_id = peer_id_for_key(destination_key)
    assert origin_mesh.router is not None
    route = origin_mesh.router.route_candidates(destination_id)
    assert route == (
        (
            origin_mesh.local_peer_id,
            relay_mesh.local_peer_id,
            destination_mesh.local_peer_id,
        ),
    ), {
        name: {
            "peers": client.peers(),
            "records": tuple(state["mesh"].router.record_snapshot()),
            "links": tuple(state["mesh"].router.link_snapshot()),
            "neighbors": state["mesh"].router.authenticated_neighbors,
        }
        for name, (client, state) in (
            (name, (clients[name], states[name])) for name in clients
        )
    }

    forwarded = []
    receipts = []
    original_forward = relay_mesh._send_forward_to_peer

    async def capture_final_hop(peer_key, frame, *, timeout):
        forwarded.append((peer_key, frame))
        receipt = await original_forward(peer_key, frame, timeout=timeout)
        receipts.append(receipt)
        return receipt

    relay_mesh._send_forward_to_peer = capture_final_hop
    for name in application_calls:
        application_calls[name].clear()
        dispatch_calls[name].clear()

    result = await states["origin"]["secure"].request(
        destination_key, "directory", {}, timeout=10
    )

    assert result == {"agents": [{"name": "destination-agent"}], "truncated": False}
    assert forwarded
    assert all(peer_key == destination_key for peer_key, _ in forwarded)
    assert len(dispatch_calls["destination"]) == 1
    assert dispatch_calls["destination"][0][1] == "directory"
    assert application_calls["relay"] == []  # transit sees only opaque TLS records

    # A duplicate final-hop frame returns the encrypted cached receipt without
    # executing the destination application a second time.
    duplicate = await destination_mesh.handle_forward(
        clients["relay"].public_key, forwarded[-1][1]
    )
    assert duplicate == receipts[-1]
    assert len(dispatch_calls["destination"]) == 1
    assert "destination-agent" not in json.dumps(wire.sent)


@pytest.mark.asyncio
async def test_peer_mesh_discovery_is_opt_in_and_candidate_only(tmp_path):
    client = RelayClient(tmp_path / "local", state_dir=tmp_path / "state")
    secure = SecureConversationTransport(client, client._store.key.encode())
    relay_key = SigningKey.generate()
    endpoint_key = SigningKey.generate()
    relay_public_key = relay_key.verify_key.encode().hex()
    client.approve(relay_public_key)
    endpoint_registry = AgentRegistry(DNSStorage(tmp_path / "dns"))
    endpoint_registry.register(
        AgentRecord(
            designation="remote-agent",
            public_key=endpoint_key.verify_key.encode().hex(),
            approval_state="approved",
        )
    )
    mesh = PeerMeshRuntime(
        client,
        secure,
        tmp_path / "mesh",
        _no_application,
        forwarding_enabled=lambda: False,
        endpoint_registry=endpoint_registry,
        allow_private_network=True,
        discovery_scan_enabled=True,
        discovery_bind_address="127.0.0.1",
        discovery_multicast_group=None,
        discovery_port=0,
    )
    # Loopback is enabled here only for an isolated UDP test; production
    # candidate policy continues to reject loopback datagrams.
    mesh.discovery._source_address_policy = lambda source: source == "127.0.0.1"

    signed_locators = {}

    def signed_locator(session_id: str) -> dict:
        if session_id in signed_locators:
            return signed_locators[session_id]
        now = int(time.time())
        payload = {
            "v": 1,
            "relay_public_key": relay_public_key,
            "endpoint_designation": "remote-agent",
            "endpoint_public_key": endpoint_key.verify_key.encode().hex(),
            "endpoint": "kollab+tls://127.0.0.1:9443",
            "session_id": session_id,
            "revision": 1,
            "issued_at": now,
            "expires_at": now + 120,
        }
        signed = rfc8785.dumps(payload)
        signed = {
            **payload,
            "relay_signature": relay_key.sign(signed).signature.hex(),
            "endpoint_signature": endpoint_key.sign(signed).signature.hex(),
        }
        signed_locators[session_id] = signed
        return signed

    sender = PeerDiscoveryService(
        advertise_enabled=True,
        locator_provider=signed_locator,
        multicast_group=None,
        bind_address="127.0.0.1",
        port=0,
        advertise_target=("127.0.0.1", 9),
        advertise_interval=1,
    )
    try:
        assert mesh._verify_locator_wire(signed_locator(secrets.token_hex(16))) is not None
        await mesh.start()
        receive_port = mesh.discovery._socket.getsockname()[1]
        sender.advertise_target = ("127.0.0.1", receive_port)
        await sender.start()
        try:
            async with asyncio.timeout(2):
                while mesh._locator_for_peer(relay_public_key) is None:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pytest.fail(
                "locator not accepted: "
                f"received={mesh.discovery.datagrams_received}, "
                f"rejected={mesh.discovery.datagrams_rejected}, "
                f"accepted={mesh.discovery.candidates_accepted}"
            )

        assert mesh.locator_store.get(relay_public_key) is not None
        assert mesh.router is not None
        assert mesh.router.records.get(peer_id_for_key(relay_public_key)) is None
        assert mesh.router.route_candidates(peer_id_for_key(relay_public_key)) == ()
        assert mesh.received_frames == 0

        # A cryptographically valid locator with a different endpoint key for
        # the same designation cannot replace the DNS-pinned endpoint binding.
        wrong_key = SigningKey.generate()
        endpoint_registry.register(
            AgentRecord(
                designation="remote-agent",
                public_key=wrong_key.verify_key.encode().hex(),
                approval_state="approved",
            )
        )
        assert mesh._verify_locator_wire(signed_locator(secrets.token_hex(16))) is None
    finally:
        await sender.close()
        await mesh.close()
        secure.close()
        await client.close()


async def _no_application(_peer_key, _method, _payload):
    raise AssertionError("candidate discovery must not invoke peer application")


@pytest.mark.asyncio
async def test_bridge_starts_and_closes_peer_mesh_with_discovery_off(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(get=lambda _key, default=None: default)
    hub = HubPlugin(event_bus=EventBus(), config=config)
    hub._identity = AgentRuntime(
        identity="local-agent",
        agent_id="local-session",
        socket_path=f"/tmp/kollab-peer-test-{secrets.token_hex(4)}.sock",
    )
    bridge = RelayAgentBridge(hub, workspace, state_dir=tmp_path / "state")

    await bridge._ensure_owner()
    mesh = bridge.peer_mesh
    assert isinstance(mesh, PeerMeshRuntime)
    assert mesh.direct_enabled is False
    assert mesh.discovery.advertise_enabled is False
    assert mesh.discovery.scan_enabled is False
    assert mesh.discovery._transport is None

    await bridge.close()
    assert bridge.peer_mesh is None
    assert mesh._closed is True
    assert mesh.discovery._closed is True
    assert mesh.discovery._transport is None


def _mint_tls_cert(tmp_path: Path) -> tuple[str, str]:
    if not shutil.which("openssl"):
        pytest.skip("openssl CLI unavailable for loopback TLS test")
    cert = tmp_path / "peer-cert.pem"
    key = tmp_path / "peer-key.pem"
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("could not create isolated TLS certificate")
    return str(cert), str(key)


@pytest.mark.asyncio
async def test_direct_secure_record_uses_tls_identity_and_rejects_other_methods(
    tmp_path,
):
    cert, key = _mint_tls_cert(tmp_path)
    server_ssl = build_server_ssl_context(cert, key)
    dns_storage = DNSStorage(tmp_path / "dns")
    identity = IdentityManager(dns_storage)
    registry = AgentRegistry(dns_storage)
    _, server_key = identity.get_or_create_keypair("server-agent")
    _, client_key = identity.get_or_create_keypair("client-agent")
    for designation, public_key in (
        ("server-agent", server_key),
        ("client-agent", client_key),
    ):
        registry.register(
            AgentRecord(
                designation=designation,
                public_key=public_key,
                approval_state="approved",
            )
        )
    calls = []

    async def receive_message(_message):
        raise AssertionError("secure records must not enter Hub message dispatch")

    async def secure_handler(designation, public_key, method, payload):
        calls.append((designation, public_key, method, payload))
        return {"records": ["opaque-response"]}

    server = AgentSocketServer(
        "peer-secure-server", receive_message, socket_name=f"peer-secure-{os.getpid()}"
    )
    server.set_dns_auth(
        registry, identity, require_auth=False, local_designation="server-agent"
    )
    server.enable_endpoint("127.0.0.1", 0, server_ssl)
    server.set_peer_secure_handler(secure_handler)
    await server.start()

    local = RelayClient(tmp_path / "relay-client", state_dir=tmp_path / "relay-state")
    remote_relay_key = SigningKey.generate().verify_key.encode().hex()
    local.approve(remote_relay_key)
    secure = SecureConversationTransport(local, local._store.key.encode())
    mesh = PeerMeshRuntime(
        local,
        secure,
        tmp_path / "mesh",
        _no_application,
        forwarding_enabled=lambda: False,
        endpoint_identity_manager=identity,
        endpoint_designation="client-agent",
        endpoint_tls_ca=cert,
        direct_enabled=True,
        allow_private_network=True,
    )
    port = server._tcp_server.sockets[0].getsockname()[1]
    now = int(time.time())
    mesh._locators[remote_relay_key] = {
        "relay_public_key": remote_relay_key,
        "endpoint_designation": "server-agent",
        "endpoint_public_key": server_key,
        "endpoint": f"kollab+tls://127.0.0.1:{port}",
        "session_id": secrets.token_hex(16),
        "revision": 1,
        "issued_at": now,
        "expires_at": now + 120,
        "v": 1,
        "digest": "b" * 64,
    }
    try:
        result = await mesh._send_direct_secure(
            remote_relay_key, "secure_packet", {"records": ["opaque"]}, timeout=4
        )
        assert result == {"records": ["opaque-response"]}
        assert calls == [
            ("client-agent", client_key, "secure_packet", {"records": ["opaque"]})
        ]

        # Only the two secure record methods ride this carrier.
        with pytest.raises(TransientPeerDeliveryError):
            await mesh._direct_request(
                remote_relay_key,
                {"action": "peer_secure", "method": "message", "payload": {}},
                "peer_secure_result",
                timeout=4,
            )
        assert len(calls) == 1
    finally:
        await mesh.close()
        secure.close()
        await server.stop()


@pytest.mark.asyncio
async def test_locator_only_peer_is_reached_directly_and_refreshed(mesh_network):
    clients, states, application_calls, dispatch_calls, _wire = mesh_network
    origin_mesh = states["origin"]["mesh"]
    destination_key = clients["destination"].public_key
    origin_key = clients["origin"].public_key
    # The destination is outside the origin's relay room but announced a
    # signed locator on the origin's network.
    assert destination_key not in {peer["key"] for peer in clients["origin"].peers()}
    origin_mesh.direct_enabled = True
    origin_mesh.endpoint_identity_manager = object()
    destination_session = clients["destination"]._session_id
    origin_mesh._locator_for_peer = (
        lambda key: {"relay_public_key": key, "session_id": destination_session}
        if key == destination_key
        else None
    )
    carried = []

    async def direct_secure(peer_key, method, payload, *, timeout):
        carried.append((peer_key, method))
        return await states["destination"]["mesh"].application_handler(
            origin_key, method, payload
        )

    origin_mesh._send_direct_secure = direct_secure
    for name in application_calls:
        application_calls[name].clear()
        dispatch_calls[name].clear()

    result = await states["origin"]["secure"].request(
        destination_key, "directory", {}, timeout=10
    )

    assert result == {"agents": [{"name": "destination-agent"}], "truncated": False}
    assert carried and all(key == destination_key for key, _ in carried)
    assert application_calls["relay"] == []  # the middle node was not used
    assert [call[1] for call in dispatch_calls["destination"]] == ["directory"]

    exchanged = []

    async def record_exchange(peer_key):
        exchanged.append(peer_key)

    origin_mesh.exchange_peer = record_exchange
    await origin_mesh.refresh()
    assert destination_key in exchanged


@pytest.mark.asyncio
async def test_direct_peer_forward_uses_tls_identity_and_bounded_listener(
    tmp_path, monkeypatch
):
    cert, key = _mint_tls_cert(tmp_path)
    server_ssl = build_server_ssl_context(cert, key)
    assert server_ssl is not None

    dns_storage = DNSStorage(tmp_path / "dns")
    identity = IdentityManager(dns_storage)
    registry = AgentRegistry(dns_storage)
    _, server_key = identity.get_or_create_keypair("server-agent")
    _, client_key = identity.get_or_create_keypair("client-agent")
    for designation, public_key in (
        ("server-agent", server_key),
        ("client-agent", client_key),
    ):
        registry.register(
            AgentRecord(
                designation=designation,
                public_key=public_key,
                approval_state="approved",
            )
        )
    calls = []

    async def receive_message(_message):
        raise AssertionError("peer forwarding must not enter Hub message dispatch")

    async def forward_handler(designation, public_key, frame):
        calls.append((designation, public_key, frame))
        return {"accepted": True, "id": frame["id"]}

    server = AgentSocketServer(
        "peer-forward-server", receive_message, socket_name=f"peer-forward-{os.getpid()}"
    )
    server.set_dns_auth(
        registry, identity, require_auth=False, local_designation="server-agent"
    )
    server.enable_endpoint("127.0.0.1", 0, server_ssl)
    server.set_peer_forward_handler(forward_handler)
    await server.start()

    local = RelayClient(tmp_path / "relay-client", state_dir=tmp_path / "relay-state")
    remote_relay_key = SigningKey.generate().verify_key.encode().hex()
    local.approve(remote_relay_key)
    secure = SecureConversationTransport(local, local._store.key.encode())
    mesh = PeerMeshRuntime(
        local,
        secure,
        tmp_path / "mesh",
        _no_application,
        forwarding_enabled=lambda: False,
        endpoint_identity_manager=identity,
        endpoint_designation="client-agent",
        endpoint_tls_ca=cert,
        direct_enabled=True,
        allow_private_network=True,
    )
    port = server._tcp_server.sockets[0].getsockname()[1]
    now = int(time.time())
    mesh._locators[remote_relay_key] = {
        "relay_public_key": remote_relay_key,
        "endpoint_designation": "server-agent",
        "endpoint_public_key": server_key,
        "endpoint": f"kollab+tls://127.0.0.1:{port}",
        "session_id": secrets.token_hex(16),
        "revision": 1,
        "issued_at": now,
        "expires_at": now + 120,
        "v": 1,
        "digest": "b" * 64,
    }

    try:
        frame = {"id": "opaque-frame-1"}
        lines = []
        original_open_connection = asyncio.open_connection

        class ReaderProxy:
            def __init__(self, reader):
                self._reader = reader

            async def readline(self):
                line = await self._reader.readline()
                lines.append(line)
                return line

        async def open_connection(*args, **kwargs):
            reader, writer = await original_open_connection(*args, **kwargs)
            return ReaderProxy(reader), writer

        monkeypatch.setattr(asyncio, "open_connection", open_connection)
        try:
            result = await mesh._send_direct_peer_forward(
                remote_relay_key, frame, timeout=4
            )
        except Exception as exc:
            pytest.fail(f"direct send failed with {type(exc).__name__}; lines={lines!r}")
        assert result == {"accepted": True, "id": "opaque-frame-1"}
        assert calls == [("client-agent", client_key, frame)]

        # The authenticated listener only exposes the dedicated carrier hook.
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            port,
            ssl=build_client_ssl_context(cert),
            server_hostname="127.0.0.1",
        )
        try:
            assert await AgentMessenger.do_remote_client_handshake(
                reader,
                writer,
                identity,
                "client-agent",
                "server-agent",
                server_key,
                timeout=3,
            )
            writer.write(b'{"action":"status"}\n')
            await writer.drain()
            rejection = json.loads(await asyncio.wait_for(reader.readline(), 3))
            assert rejection["msg"] == "local operator authorization required"
            assert len(calls) == 1
        finally:
            writer.close()
            await writer.wait_closed()

        server.set_peer_forward_handler(None)
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            port,
            ssl=build_client_ssl_context(cert),
            server_hostname="127.0.0.1",
        )
        try:
            assert await AgentMessenger.do_remote_client_handshake(
                reader,
                writer,
                identity,
                "client-agent",
                "server-agent",
                server_key,
                timeout=3,
            )
            writer.write(b'{"action":"peer_forward","frame":{"id":"no-hook"}}\n')
            await writer.drain()
            rejection = json.loads(await asyncio.wait_for(reader.readline(), 3))
            assert rejection["msg"] == "peer forwarding unavailable"
            assert len(calls) == 1
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await mesh.close()
        secure.close()
        await local.close()
        await server.stop()


async def _carrier_server(tmp_path, *, register_client, resolver):
    """A TLS endpoint whose registry may or may not know the dialing designation."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    cert, key = _mint_tls_cert(tmp_path)
    server_storage = DNSStorage(tmp_path / "server-dns")
    server_identity = IdentityManager(server_storage)
    server_registry = AgentRegistry(server_storage)
    _, server_key = server_identity.get_or_create_keypair("server-agent")
    server_registry.register(
        AgentRecord(
            designation="server-agent", public_key=server_key, approval_state="approved"
        )
    )
    client_identity = IdentityManager(DNSStorage(tmp_path / "client-dns"))
    _, client_key = client_identity.get_or_create_keypair("client-agent")
    if register_client is not None:
        server_registry.register(
            AgentRecord(
                designation="client-agent",
                public_key=register_client.get("key", client_key),
                approval_state=register_client["state"],
            )
        )
    calls = []

    async def receive_message(_message):
        raise AssertionError("a peer carrier frame must not enter Hub dispatch")

    async def secure_handler(designation, public_key, method, payload):
        calls.append((designation, public_key, method))
        return {"records": []}

    server = AgentSocketServer(
        "carrier-server", receive_message, socket_name=f"carrier-{secrets.token_hex(3)}"
    )
    server.set_dns_auth(
        server_registry, server_identity, require_auth=False, local_designation="server-agent"
    )
    server.enable_endpoint("127.0.0.1", 0, build_server_ssl_context(cert, key))
    server.set_peer_secure_handler(secure_handler)
    if resolver:
        server.set_peer_identity_resolver(
            lambda designation: client_key if designation == "client-agent" else ""
        )
    await server.start()
    port = server._tcp_server.sockets[0].getsockname()[1]
    return SimpleNamespace(
        server=server,
        port=port,
        cert=cert,
        server_key=server_key,
        client_key=client_key,
        client_identity=client_identity,
        calls=calls,
    )


async def _dial(carrier):
    reader, writer = await asyncio.open_connection(
        "127.0.0.1",
        carrier.port,
        ssl=build_client_ssl_context(carrier.cert),
        server_hostname="127.0.0.1",
    )
    ok = await AgentMessenger.do_remote_client_handshake(
        reader,
        writer,
        carrier.client_identity,
        "client-agent",
        "server-agent",
        carrier.server_key,
        timeout=3,
    )
    return ok, reader, writer


async def _line(reader):
    return json.loads(await asyncio.wait_for(reader.readline(), 3))


@pytest.mark.asyncio
async def test_a_locator_named_endpoint_reaches_the_peer_carrier_and_nothing_else(tmp_path):
    carrier = await _carrier_server(tmp_path, register_client=None, resolver=True)
    try:
        ok, reader, writer = await _dial(carrier)
        assert ok
        try:
            writer.write(b'{"action":"ping"}\n')
            await writer.drain()
            assert (await _line(reader))["msg"] == "local operator authorization required"
        finally:
            writer.close()
        ok, reader, writer = await _dial(carrier)
        try:
            writer.write(
                b'{"action":"peer_secure","method":"secure_identity","payload":{}}\n'
            )
            await writer.drain()
            assert (await _line(reader))["type"] == "peer_secure_result"
        finally:
            writer.close()
        assert carrier.calls == [("client-agent", carrier.client_key, "secure_identity")]
    finally:
        await carrier.server.stop()


@pytest.mark.asyncio
async def test_a_locator_never_overrides_what_the_registry_records(tmp_path):
    # The registry holds this designation under another key, or rejected: the
    # locator's claim does not replace either, and the dial ends at the handshake.
    other = SigningKey.generate().verify_key.encode().hex()
    for name, record in (
        ("other-key", {"state": "approved", "key": other}),
        ("rejected", {"state": "rejected"}),
    ):
        carrier = await _carrier_server(tmp_path / name, register_client=record, resolver=True)
        try:
            ok, _reader, writer = await _dial(carrier)
            writer.close()
            assert not ok, name
        finally:
            await carrier.server.stop()


@pytest.mark.asyncio
async def test_an_unknown_endpoint_is_turned_away_without_a_locator(tmp_path):
    carrier = await _carrier_server(tmp_path, register_client=None, resolver=False)
    try:
        ok, _reader, writer = await _dial(carrier)
        writer.close()
        assert not ok
    finally:
        await carrier.server.stop()


@pytest.mark.asyncio
async def test_an_auto_approved_local_agent_is_a_carrier_peer_not_a_message_sender(tmp_path):
    # Runtime allowlists are not communication authorization: a same-host agent
    # the registry only auto-approved reaches the carrier through its locator.
    carrier = await _carrier_server(
        tmp_path, register_client={"state": "auto_approved"}, resolver=True
    )
    try:
        ok, reader, writer = await _dial(carrier)
        assert ok
        try:
            writer.write(b'{"action":"ping"}\n')
            await writer.drain()
            assert (await _line(reader))["msg"] == "local operator authorization required"
        finally:
            writer.close()
    finally:
        await carrier.server.stop()


@pytest.mark.asyncio
async def test_a_refused_direct_endpoint_falls_back_to_the_relay(mesh_network):
    # do_remote_client_handshake answers False for a refusal and for a timeout, and the
    # direct attempt turns that into PeerRouteError: delivery must still take the relay.
    import time

    from plugins.hub.peer_router import PeerRouteError

    clients, states, *_ = mesh_network
    mesh = states["origin"]["mesh"]
    peer_key = clients["relay"].public_key
    mesh.direct_enabled = True
    mesh._locators[peer_key] = {"expires_at": int(time.time()) + 60}

    async def refused(*_args, **_kwargs):
        raise PeerRouteError("direct peer endpoint identity was rejected")

    relayed = []

    async def relay_request(key, method, payload, *, timeout):
        relayed.append((key, method))
        return {"v": 1}

    mesh._send_direct_peer_forward = refused
    clients["origin"].request = relay_request
    assert await mesh._send_forward_to_peer(peer_key, {}, timeout=3) == {"v": 1}
    assert relayed == [(peer_key, "peer.forward")]


@pytest.mark.asyncio
async def test_a_refused_direct_secure_record_still_takes_the_routed_path(mesh_network):
    import time

    from plugins.hub.peer_router import PeerRouteError, TransientPeerDeliveryError

    clients, states, *_ = mesh_network
    mesh = states["origin"]["mesh"]
    destination = clients["destination"].public_key
    mesh.direct_enabled = True
    mesh._locators[destination] = {"expires_at": int(time.time()) + 60}
    attempts = []

    async def refused(*_args, **_kwargs):
        raise PeerRouteError("direct peer endpoint identity was rejected")

    async def routed(peer_key, frame, *, timeout):
        attempts.append(peer_key)
        raise TransientPeerDeliveryError("stop here")

    mesh._send_direct_secure = refused
    mesh._send_forward_to_peer = routed
    with pytest.raises(Exception):
        await mesh.request(destination, "secure_identity", {"x": 1}, timeout=3)
    assert attempts, "the direct refusal ended the request before the routed path was tried"


@pytest.mark.asyncio
async def test_the_direct_endpoint_turns_an_accepted_stranger_away_from_forwarding(mesh_network):
    # relay_agent refuses peer.forward from an accepted stranger; a locator-admitted
    # endpoint reaches handle_direct_forward without that dispatcher, so it checks too.
    import time

    from plugins.hub.peer_router import PeerRouteError

    clients, states, *_ = mesh_network
    mesh = states["relay"]["mesh"]
    stranger = clients["origin"].public_key
    clients["relay"].state.links.append(stranger)
    endpoint_key = "ab" * 32
    mesh._locators[stranger] = {
        "expires_at": int(time.time()) + 60,
        "relay_public_key": stranger,
        "endpoint_designation": "stranger-endpoint",
        "endpoint_public_key": endpoint_key,
    }
    with pytest.raises(PeerRouteError, match="stranger"):
        await mesh.handle_direct_forward("stranger-endpoint", endpoint_key, {})
