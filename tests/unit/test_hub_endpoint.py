"""Tests for the off-box A2A endpoint (plugins/hub/dns/endpoint.py).

Covers the pure helpers (URI parsing, config, TLS context, well-known
import) plus an end-to-end integration test that drives the FULL off-box
path — TCP listener + Ed25519 handshake + message delivery — over a
plaintext loopback socket. TLS is an orthogonal ``ssl=`` wrapper on the
same code path and is exercised separately via the context builders.
"""

import asyncio
import json
import os
import shutil
import subprocess

import pytest

from plugins.hub.dns.endpoint import (
    DEFAULT_ENDPOINT_PORT,
    EndpointConfig,
    build_client_ssl_context,
    build_server_ssl_context,
    is_remote_uri,
    normalize_well_known_url,
    parse_endpoint_uri,
    register_well_known,
    uri_is_tls,
)
from plugins.hub.dns.identity import IdentityManager
from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.registry import AgentRegistry
from plugins.hub.dns.storage import DNSStorage
from plugins.hub.messenger import (
    REMOTE_MESSAGE_AUTH_KEY,
    AgentMessenger,
    AgentSocketServer,
    PrivateDirectoryRemoteMessageAuthorizer,
    _remote_auth_bytes,
    remote_message_proof_body,
)
from plugins.hub.models import HubMessage


def _run(coro):
    """Run a coroutine on a fresh loop without replacing the policy loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        assert loop.is_closed()


# --- URI helpers -------------------------------------------------------------


def test_is_remote_uri_distinguishes_uris_from_socket_paths():
    assert is_remote_uri("wss://example.com:8765")
    assert is_remote_uri("https://example.com")
    assert is_remote_uri("ws://127.0.0.1:9000")
    assert is_remote_uri("a2a://peer:8765")
    assert not is_remote_uri("/tmp/kollabor-hub/peridot.sock")
    assert not is_remote_uri("")
    assert not is_remote_uri("peridot")


def test_uri_is_tls_only_for_secure_schemes():
    assert uri_is_tls("wss://example.com")
    assert uri_is_tls("https://example.com")
    assert not uri_is_tls("ws://example.com")
    assert not uri_is_tls("a2a://example.com")
    assert not uri_is_tls("/tmp/x.sock")


def test_parse_endpoint_uri_extracts_host_port():
    assert parse_endpoint_uri("wss://example.com:8765") == ("example.com", 8765)
    assert parse_endpoint_uri("ws://127.0.0.1:9000") == ("127.0.0.1", 9000)
    # TLS scheme without explicit port defaults to 443
    assert parse_endpoint_uri("https://example.com") == ("example.com", 443)
    # plaintext scheme without explicit port defaults to the agent port
    assert parse_endpoint_uri("a2a://peer") == ("peer", DEFAULT_ENDPOINT_PORT)
    # a unix socket path is not a remote URI
    assert parse_endpoint_uri("/tmp/x.sock") is None


def test_normalize_well_known_url():
    assert (
        normalize_well_known_url("example.com")
        == "https://example.com/.well-known/agent-keys.json"
    )
    assert (
        normalize_well_known_url("https://example.com/")
        == "https://example.com/.well-known/agent-keys.json"
    )
    # already-qualified well-known URL is preserved
    already = "https://example.com/.well-known/agent-keys.json"
    assert normalize_well_known_url(already) == already


# --- EndpointConfig ----------------------------------------------------------


class _FakeConfig:
    """Minimal dot-key config stand-in for EndpointConfig.from_config."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def test_endpoint_config_defaults_disabled():
    ep = EndpointConfig.from_config(None)
    assert ep.enabled is False
    assert ep.has_tls is False


def test_endpoint_config_reads_flat_keys_and_builds_uri():
    cfg = _FakeConfig(
        {
            "plugins.hub.endpoint_enabled": True,
            "plugins.hub.endpoint_port": 9443,
            "plugins.hub.endpoint_tls_cert": "/etc/cert.pem",
            "plugins.hub.endpoint_tls_key": "/etc/key.pem",
            "plugins.hub.endpoint_advertise_host": "mesh.example.com",
            "plugins.hub.authority": "example.com",
        }
    )
    ep = EndpointConfig.from_config(cfg)
    assert ep.enabled is True
    assert ep.port == 9443
    assert ep.has_tls is True
    assert ep.advertised_host == "mesh.example.com"
    assert ep.endpoint_uri == "wss://mesh.example.com:9443"


def test_endpoint_config_advertises_authority_when_no_host_and_ws_without_tls():
    cfg = _FakeConfig(
        {
            "plugins.hub.endpoint_enabled": True,
            "plugins.hub.endpoint_port": 8765,
            "plugins.hub.authority": "kollabor.ai",
        }
    )
    ep = EndpointConfig.from_config(cfg)
    assert ep.advertised_host == "kollabor.ai"
    # no cert/key -> plaintext ws scheme advertised
    assert ep.endpoint_uri == "ws://kollabor.ai:8765"


# --- TLS context builders ----------------------------------------------------


def test_build_server_ssl_context_none_when_cert_missing():
    assert build_server_ssl_context("", "") is None
    assert build_server_ssl_context("/nope/cert.pem", "") is None


def test_build_server_ssl_context_none_on_bad_paths():
    # cert+key supplied but unreadable -> None (fail-closed), not an exception
    assert build_server_ssl_context("/nope/cert.pem", "/nope/key.pem") is None


def test_build_client_ssl_context_returns_context():
    ctx = build_client_ssl_context()
    # System trust store context; verification on by default.
    assert ctx is not None
    assert ctx.check_hostname is True


# --- Federation import (well-known) ------------------------------------------


def test_register_well_known_does_not_admit_discovered_coordinator(tmp_path):
    storage = DNSStorage(tmp_path / "dns")
    registry = AgentRegistry(storage)
    payload = {
        "v": "aid1",
        "authority": "remote.example.com",
        "coordinator": {
            "designation": "obsidian",
            "aid": "agent:obsidian@remote.example.com",
            "public_key": "ab" * 32,
            "protocols": ["a2a", "socket"],
        },
        "endpoints": {"endpoint": "wss://remote.example.com:8765"},
    }
    # A domain descriptor is not an enrollment credential, even over HTTPS.
    assert register_well_known(payload, registry) is None
    assert registry.resolve("obsidian") is None
    assert registry.resolve_address("obsidian") is None


def test_register_well_known_rejects_missing_key(tmp_path):
    storage = DNSStorage(tmp_path / "dns")
    registry = AgentRegistry(storage)
    bad = {"coordinator": {"designation": "ghost"}}  # no public_key
    assert register_well_known(bad, registry) is None
    assert registry.resolve("ghost") is None


# --- End-to-end off-box path (TCP loopback) ----------------------------------


def _make_dns(tmp_path):
    storage = DNSStorage(tmp_path / "dns")
    identity = IdentityManager(storage)
    registry = AgentRegistry(storage)
    return storage, identity, registry


def _remote_auth(
    identity, registry, *, designation="client-agent", server="server-agent"
):
    server_record = registry.resolve(server)
    assert server_record is not None
    return {
        "identity_manager": identity,
        "designation": designation,
        "expected_server_designation": server,
        "expected_server_public_key": server_record.public_key,
    }


def _install_private_directory_grant(tmp_path, identity, message, *, target_uri):
    """Build a real owner-approved grant bound to the Hub message body."""
    from nacl.signing import SigningKey

    from plugins.hub.dns.private_directory import (
        PrivateDirectory,
        prove_pairing,
        sign_request,
    )

    device_key = identity._signing_keys["client-agent"]
    owner_key = SigningKey.generate()
    workspace_id = "endpoint-workspace"
    directory = PrivateDirectory(
        tmp_path / "private-directory.json",
        owner_public_key=bytes(owner_key.verify_key),
        workspace_id=workspace_id,
    )
    challenge = directory.begin_pairing(
        owner_key, expected_device_public_key=bytes(device_key.verify_key)
    )
    pairing_proof = prove_pairing(
        challenge, device_key, owner_public_key=bytes(owner_key.verify_key)
    )
    directory.record_pairing_proof(challenge, pairing_proof)
    credential = directory.approve_pairing(
        challenge,
        pairing_proof,
        owner_key,
        approved_by_human=True,
    )
    grant = directory.issue_conversation_grant(
        credential,
        owner_key,
        recipient_workspace_id=workspace_id,
        purpose="conversation",
        conversation_id=message.thread_id,
        approved_by_human=True,
    )
    body = remote_message_proof_body(message)
    request_proof = sign_request(
        device_key,
        credential_jws=credential.token,
        grant_jws=grant.token,
        body=body,
        method="POST",
        path="/hub/direct/message",
        target_uri=target_uri,
        recipient_workspace_id=workspace_id,
        purpose="conversation",
        conversation_id=message.thread_id,
        message_id=message.id,
    )
    message.metadata[REMOTE_MESSAGE_AUTH_KEY] = {
        "credential_jws": credential.token,
        "grant_jws": grant.token,
        "proof_jws": request_proof,
        "workspace_id": workspace_id,
        "purpose": "conversation",
        "conversation_id": message.thread_id,
    }
    return PrivateDirectoryRemoteMessageAuthorizer(directory, target_uri)


async def _start_endpoint_server(tmp_path, received, *, socket_name):
    """Spin up an AgentSocketServer with a plaintext TCP endpoint.

    Returns (server, identity, registry, port). Both 'server-agent' and
    'client-agent' keypairs are created and registered so the inbound
    handshake can verify the client's signature.
    """
    storage, identity, registry = _make_dns(tmp_path)
    _, server_pub = identity.get_or_create_keypair("server-agent")
    _, client_pub = identity.get_or_create_keypair("client-agent")
    registry.register(
        AgentRecord(
            designation="server-agent",
            public_key=server_pub,
            approval_state="approved",
        )
    )
    registry.register(
        AgentRecord(
            designation="client-agent",
            public_key=client_pub,
            approval_state="approved",
        )
    )

    async def on_message(msg):
        received.append(msg)

    server = AgentSocketServer("server-id", on_message, socket_name=socket_name)
    # Wire registry + identity (TCP listener forces the handshake regardless
    # of require_auth here).
    server.set_dns_auth(
        registry, identity, require_auth=False, local_designation="server-agent"
    )
    server.enable_endpoint("127.0.0.1", 0, None)  # plaintext, OS-assigned port
    await server.start()
    port = server._tcp_server.sockets[0].getsockname()[1]
    return server, identity, registry, port


def test_failed_endpoint_bind_captures_error_and_keeps_unix_alive(tmp_path):
    """A TCP bind failure is non-fatal: the unix socket stays up, the TCP
    server is None, and the bind error is captured for operability."""

    async def run():
        received = []

        async def on_message(msg):
            received.append(msg)

        # Bind a listener on an OS-assigned port, then steal that port so the
        # endpoint's bind to the same port collides and fails.
        import socket as _socket

        blocker = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        blocker.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 0)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        stolen_port = blocker.getsockname()[1]

        server = AgentSocketServer(
            "bind-fail-id", on_message, socket_name=f"ep-fail-{os.getpid()}"
        )
        server.enable_endpoint("127.0.0.1", stolen_port, None)
        try:
            sock_path = await server.start()
            # unix socket is up regardless of the TCP failure
            assert server._tcp_server is None
            assert server._endpoint_bind_error  # captured a reason
            # local delivery still works over the unix socket
            ok = await AgentMessenger.send_to_agent(
                sock_path,
                HubMessage(content="still works", from_identity="peer"),
            )
            assert ok is True
            assert len(received) == 1
        finally:
            await server.stop()
            blocker.close()

    _run(run())


def test_offbox_idle_connection_dropped_after_timeout(tmp_path):
    """An authenticated remote peer that sends nothing is dropped after the
    idle timeout, capping the per-connection resource hold."""

    async def run():
        storage, identity, registry = _make_dns(tmp_path)
        _, server_pub = identity.get_or_create_keypair("server-agent")
        _, client_pub = identity.get_or_create_keypair("client-agent")
        registry.register(
            AgentRecord(
                designation="server-agent",
                public_key=server_pub,
                approval_state="approved",
            )
        )
        registry.register(
            AgentRecord(
                designation="client-agent",
                public_key=client_pub,
                approval_state="approved",
            )
        )

        async def on_message(msg):
            pass

        server = AgentSocketServer(
            "idle-id", on_message, socket_name=f"ep-idle-{os.getpid()}"
        )
        server.set_dns_auth(
            registry, identity, require_auth=False, local_designation="server-agent"
        )
        server.enable_endpoint("127.0.0.1", 0, None)
        server._remote_idle_timeout = 0.3  # short for the test
        await server.start()
        port = server._tcp_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            server_record = registry.resolve("server-agent")
            assert server_record is not None
            ok = await AgentMessenger.do_remote_client_handshake(
                reader,
                writer,
                identity,
                "client-agent",
                "server-agent",
                server_record.public_key,
                timeout=5.0,
            )
            assert ok is True
            # Send nothing. The server should close within a few idle windows.
            # An EOF (empty bytes) proves it dropped the idle connection.
            data = await asyncio.wait_for(reader.readline(), timeout=3.0)
            assert data == b""  # server closed the connection
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            await server.stop()

    _run(run())


def test_offbox_handshake_and_delivery(tmp_path):
    async def run():
        received = []
        server, identity, _registry, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-ok-{os.getpid()}"
        )
        try:
            auth = _remote_auth(identity, _registry)
            message = HubMessage(
                content="hello over the wire",
                from_identity="client-agent",
                to="server-agent",
            )
            server.set_remote_message_authorizer(
                _install_private_directory_grant(
                    tmp_path,
                    identity,
                    message,
                    target_uri="https://127.0.0.1/hub/direct/message",
                )
            )
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                message,
                auth=auth,
            )
            assert ok is True
            assert len(received) == 1
            assert received[0].content == "hello over the wire"
            assert REMOTE_MESSAGE_AUTH_KEY not in received[0].metadata
        finally:
            await server.stop()

    _run(run())


def test_offbox_rejects_unregistered_client(tmp_path):
    async def run():
        received = []
        server, identity, _registry, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-bad-{os.getpid()}"
        )
        try:
            # 'intruder' has a keypair but is NOT in the server's registry,
            # so the server cannot resolve a public key -> auth_rejected.
            identity.get_or_create_keypair("intruder")
            auth = _remote_auth(identity, _registry, designation="intruder")
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                HubMessage(
                    content="let me in",
                    from_identity="intruder",
                    to="server-agent",
                ),
                auth=auth,
            )
            assert ok is False
            assert received == []
        finally:
            await server.stop()

    _run(run())


def test_offbox_rejects_unapproved_client(tmp_path):
    async def run():
        received = []
        server, identity, registry, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-pending-{os.getpid()}"
        )
        try:
            client_record = registry.resolve("client-agent")
            assert client_record is not None
            client_record.approval_state = "pending"
            message = HubMessage(
                content="pending peers cannot message",
                from_identity="client-agent",
                to="server-agent",
            )
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                message,
                auth=_remote_auth(identity, registry),
            )
            assert ok is False
            assert received == []
        finally:
            await server.stop()

    _run(run())


def test_offbox_connections_are_capped_before_handshake_tasks_are_created(tmp_path):
    """The off-box listener rejects excess streams and releases slots on close."""

    async def run():
        async def on_message(msg):
            pass

        server = AgentSocketServer(
            "connection-cap-id",
            on_message,
            socket_name=f"ep-cap-{os.getpid()}",
        )
        server.set_dns_auth(object(), object(), local_designation="server-agent")
        server._remote_connection_limit = 1

        async def hold_handshake(reader, writer):
            await reader.readline()
            return None

        server._do_remote_handshake = hold_handshake
        server.enable_endpoint("127.0.0.1", 0, None)
        await server.start()
        port = server._tcp_server.sockets[0].getsockname()[1]
        first_writer = second_writer = None
        try:
            _, first_writer = await asyncio.open_connection("127.0.0.1", port)
            for _ in range(100):
                if server._remote_connection_count == 1:
                    break
                await asyncio.sleep(0.01)
            assert server._remote_connection_count == 1

            second_reader, second_writer = await asyncio.open_connection(
                "127.0.0.1", port
            )
            assert await asyncio.wait_for(second_reader.read(1), timeout=1.0) == b""
            assert server._remote_connection_count == 1
            assert server._remote_connections_rejected == 1

            first_writer.close()
            await first_writer.wait_closed()
            for _ in range(100):
                if server._remote_connection_count == 0:
                    break
                await asyncio.sleep(0.01)
            assert server._remote_connection_count == 0
        finally:
            for writer in (first_writer, second_writer):
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
            await server.stop()

    _run(run())


def test_remote_endpoint_rejects_legacy_or_malformed_server_greeting(tmp_path):
    async def run():
        _, identity, registry = _make_dns(tmp_path)
        identity.get_or_create_keypair("client-agent")
        _, server_pub = identity.get_or_create_keypair("server-agent")
        registry.register(
            AgentRecord(
                designation="server-agent",
                public_key=server_pub,
                approval_state="approved",
            )
        )
        responses = []
        response_read = asyncio.Event()

        async def legacy_peer(reader, writer):
            writer.write(b'{"type":"ack"}\n')
            await writer.drain()
            responses.append(await reader.readline())
            response_read.set()
            writer.close()
            await writer.wait_closed()

        listener = await asyncio.start_server(legacy_peer, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                assert not await AgentMessenger.do_remote_client_handshake(
                    reader,
                    writer,
                    identity,
                    "client-agent",
                    "server-agent",
                    server_pub,
                    timeout=2,
                )
            finally:
                writer.close()
                await writer.wait_closed()
            await asyncio.wait_for(response_read.wait(), timeout=1)
            assert responses == [b""]
        finally:
            listener.close()
            await listener.wait_closed()

    _run(run())


def test_remote_client_rejects_impersonated_server_key(tmp_path):
    async def run():
        _, client_identity, registry = _make_dns(tmp_path / "client")
        client_identity.get_or_create_keypair("client-agent")
        _, pinned_server_key = client_identity.get_or_create_keypair("server-agent")
        registry.register(
            AgentRecord(
                designation="server-agent",
                public_key=pinned_server_key,
                approval_state="approved",
            )
        )
        _, attacker_identity, _ = _make_dns(tmp_path / "attacker")
        attacker_identity.get_or_create_keypair("server-agent")
        _, attacker_key = attacker_identity.get_or_create_keypair("server-agent")
        client_responses = []
        response_read = asyncio.Event()

        async def impersonator(reader, writer):
            nonce = "a" * 64
            challenge = {
                "type": "auth_challenge",
                "version": 2,
                "designation": "server-agent",
                "nonce": nonce,
                "signature": attacker_identity.sign_message(
                    "server-agent",
                    _remote_auth_bytes("server_challenge", "server-agent", nonce),
                ),
            }
            writer.write((json.dumps(challenge) + "\n").encode())
            await writer.drain()
            client_responses.append(await reader.readline())
            response_read.set()
            writer.close()
            await writer.wait_closed()

        listener = await asyncio.start_server(impersonator, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                assert not await AgentMessenger.do_remote_client_handshake(
                    reader,
                    writer,
                    client_identity,
                    "client-agent",
                    "server-agent",
                    pinned_server_key,
                    timeout=2,
                )
            finally:
                writer.close()
                await writer.wait_closed()
            await asyncio.wait_for(response_read.wait(), timeout=1)
            assert client_responses == [b""]
            assert attacker_key != pinned_server_key
        finally:
            listener.close()
            await listener.wait_closed()

    _run(run())


def test_offbox_message_requires_receiver_scoped_grant(tmp_path):
    async def run():
        received = []
        server, identity, registry, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-no-grant-{os.getpid()}"
        )
        try:
            message = HubMessage(
                content="no grant",
                from_identity="client-agent",
                to="server-agent",
            )
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                message,
                auth=_remote_auth(identity, registry),
            )
            assert ok is False
            assert received == []
        finally:
            await server.stop()

    _run(run())


def test_offbox_message_cannot_spoof_authenticated_designation(tmp_path):
    async def run():
        received = []
        server, identity, registry, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-spoof-{os.getpid()}"
        )
        try:
            message = HubMessage(
                content="spoofed identity",
                from_identity="server-agent",
                to="server-agent",
            )
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                message,
                auth=_remote_auth(identity, registry),
            )
            assert ok is False
            assert received == []
        finally:
            await server.stop()

    _run(run())


@pytest.mark.parametrize("forged_from_agent", ["human", "other-agent"])
def test_offbox_scoped_message_binds_from_agent_to_authenticated_peer(
    tmp_path, forged_from_agent
):
    async def run():
        received = []
        server, identity, registry, port = await _start_endpoint_server(
            tmp_path,
            received,
            socket_name=f"ep-agent-bind-{forged_from_agent}-{os.getpid()}",
        )
        try:
            message = HubMessage(
                content="ordinary authorized conversation",
                from_agent=forged_from_agent,
                from_identity="client-agent",
                to="server-agent",
            )
            server.set_remote_message_authorizer(
                _install_private_directory_grant(
                    tmp_path,
                    identity,
                    message,
                    target_uri="https://127.0.0.1/hub/direct/message",
                )
            )
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                message,
                auth=_remote_auth(identity, registry),
            )
            assert ok is True
            assert len(received) == 1
            assert received[0].from_agent == "client-agent"
            assert received[0].from_identity == "client-agent"
            assert received[0].metadata == {}
        finally:
            await server.stop()

    _run(run())


@pytest.mark.parametrize(
    ("metadata_key", "metadata_value"),
    [
        ("source_agent", "other-agent"),
        ("operator_message", True),
        ("task_assignment", True),
        ("task_complete", True),
        ("task_report", True),
        ("task_id", "task-123"),
        ("task_cron", True),
        ("task_cron_ack", True),
        ("manual_wake", True),
        ("relay_event", "message"),
        ("relay_kind", "message"),
        ("relay_grant_id", "grant-123"),
        ("relay_receipt", "receipt-123"),
        ("bridge_platform", "buzz"),
        ("lifecycle_event", "model_switch"),
    ],
)
def test_offbox_scoped_message_rejects_hub_control_metadata(
    tmp_path, metadata_key, metadata_value
):
    async def run():
        received = []
        server, identity, registry, port = await _start_endpoint_server(
            tmp_path,
            received,
            socket_name=f"ep-meta-{metadata_key}-{os.getpid()}",
        )
        try:
            message = HubMessage(
                content="authorized grant with forbidden control metadata",
                from_identity="client-agent",
                to="server-agent",
                metadata={metadata_key: metadata_value},
            )
            server.set_remote_message_authorizer(
                _install_private_directory_grant(
                    tmp_path,
                    identity,
                    message,
                    target_uri="https://127.0.0.1/hub/direct/message",
                )
            )
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                message,
                auth=_remote_auth(identity, registry),
            )
            assert ok is False
            assert received == []
        finally:
            await server.stop()

    _run(run())


@pytest.mark.parametrize("force_value", [True, 1, 0, "false", None])
def test_offbox_scoped_message_rejects_force_values_other_than_false(
    tmp_path, force_value
):
    async def run():
        received = []
        server, identity, registry, port = await _start_endpoint_server(
            tmp_path,
            received,
            socket_name=f"ep-force-{os.getpid()}-{str(force_value)}",
        )
        try:
            message = HubMessage(
                content="force bypass attempt",
                from_identity="client-agent",
                to="server-agent",
                force=force_value,
            )
            server.set_remote_message_authorizer(
                _install_private_directory_grant(
                    tmp_path,
                    identity,
                    message,
                    target_uri="https://127.0.0.1/hub/direct/message",
                )
            )
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                message,
                auth=_remote_auth(identity, registry),
            )
            assert ok is False
            assert received == []
        finally:
            await server.stop()

    _run(run())


def test_remote_open_requires_complete_pinned_auth(tmp_path):
    async def run():
        received = []
        server, _identity, _registry, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-no-pin-{os.getpid()}"
        )
        try:
            with pytest.raises(ConnectionError, match="pinned identity auth"):
                await AgentMessenger._open(
                    f"ws://127.0.0.1:{port}", timeout=2, auth=None
                )
            assert received == []
        finally:
            await server.stop()

    _run(run())


def _mint_self_signed_cert(tmp_path, cn="127.0.0.1"):
    """Mint a self-signed cert+key (IP SAN = cn) via the openssl CLI.

    Returns (cert_path, key_path) or (None, None) if openssl is unavailable.
    """
    if not shutil.which("openssl"):
        return None, None
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    san = f"IP:{cn}" if cn else "IP:127.0.0.1"
    cmd = [
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
        f"/CN={cn}",
        "-addext",
        f"subjectAltName={san}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=20)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None, None
    return str(cert), str(key)


def test_offbox_tls_round_trip_end_to_end(tmp_path):
    """Full TLS path: self-signed server cert, client trusts it as its own CA,
    completes the Ed25519 handshake over the TLS channel, and delivers a
    message. Skipped when the ``openssl`` CLI is unavailable."""

    cert, key = _mint_self_signed_cert(tmp_path)
    if cert is None:
        import pytest

        pytest.skip("openssl CLI unavailable — cannot mint a test cert")

    server_ssl = build_server_ssl_context(cert, key)
    assert server_ssl is not None  # cert mints + loads

    async def run():
        received = []
        storage, identity, registry = _make_dns(tmp_path / "tls-dns")
        _, server_pub = identity.get_or_create_keypair("server-agent")
        _, client_pub = identity.get_or_create_keypair("client-agent")
        registry.register(
            AgentRecord(
                designation="server-agent",
                public_key=server_pub,
                approval_state="approved",
            )
        )
        registry.register(
            AgentRecord(
                designation="client-agent",
                public_key=client_pub,
                approval_state="approved",
            )
        )

        async def on_message(msg):
            received.append(msg)

        server = AgentSocketServer(
            "tls-id", on_message, socket_name=f"ep-tls-{os.getpid()}"
        )
        server.set_dns_auth(
            registry, identity, require_auth=False, local_designation="server-agent"
        )
        server.enable_endpoint("127.0.0.1", 0, server_ssl)
        await server.start()
        port = server._tcp_server.sockets[0].getsockname()[1]
        try:
            # tls_ca in auth -> _open builds the client context from it; the
            # self-signed cert is its own CA, and its IP SAN lets hostname
            # verification pass for wss://127.0.0.1.
            auth = _remote_auth(identity, registry)
            auth["tls_ca"] = cert
            message = HubMessage(
                content="hello over TLS",
                from_identity="client-agent",
                to="server-agent",
            )
            server.set_remote_message_authorizer(
                _install_private_directory_grant(
                    tmp_path,
                    identity,
                    message,
                    target_uri=f"https://127.0.0.1:{port}/hub/direct/message",
                )
            )
            ok = await AgentMessenger.send_to_agent(
                f"wss://127.0.0.1:{port}",
                message,
                auth=auth,
            )
            assert ok is True
            assert len(received) == 1
            assert received[0].content == "hello over TLS"
        finally:
            await server.stop()

    _run(run())


def test_local_unix_delivery_unchanged_without_auth(tmp_path):
    """Regression: the local unix path with no auth behaves exactly as before."""

    async def run():
        received = []

        async def on_message(msg):
            received.append(msg)

        server = AgentSocketServer(
            "local-id", on_message, socket_name=f"ep-local-{os.getpid()}"
        )
        # No set_dns_auth, no enable_endpoint -> pure legacy unix behavior.
        sock_path = await server.start()
        try:
            assert server._tcp_server is None  # no TCP listener bound
            ok = await AgentMessenger.send_to_agent(
                sock_path,
                HubMessage(content="local hello", from_identity="peer"),
            )
            assert ok is True
            assert len(received) == 1
            assert received[0].content == "local hello"
        finally:
            await server.stop()

    _run(run())


def test_output_requests_coerce_missing_line_count(tmp_path):
    """A null native/socket line count must use the capture default."""

    async def run():
        seen = []

        def on_get_output(lines):
            seen.append(lines)
            return [f"requested:{lines}"]

        server = AgentSocketServer(
            "output-id",
            lambda _message: None,
            on_get_output=on_get_output,
            socket_name=f"ep-output-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(sock_path)
            writer.write(b'{"action":"get_output","lines":null}\n')
            await writer.drain()
            response = await reader.readline()
            writer.close()
            await writer.wait_closed()

            assert json.loads(response.decode())["lines"] == ["requested:100"]
            assert seen == [100]

            result = await AgentMessenger.request_output(sock_path, lines=None)
            assert result == ["requested:100"]
            assert seen == [100, 100]
        finally:
            await server.stop()

    _run(run())


def test_output_diagnostics_distinguish_empty_output_from_transport_failure():
    """Capture must expose transport failures instead of reporting idle output."""

    async def run():
        server = AgentSocketServer(
            "empty-output-id",
            lambda _message: None,
            on_get_output=lambda _lines: [],
            socket_name=f"ep-empty-output-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            result, error = await AgentMessenger.request_output_diagnostic(sock_path)
            assert result == []
            assert error is None
        finally:
            await server.stop()

        missing_socket = f"/tmp/kollab-missing-output-{os.getpid()}.sock"
        result, error = await AgentMessenger.request_output_diagnostic(
            missing_socket, timeout=0.1
        )
        assert result == []
        assert error
        assert any(
            kind in error
            for kind in ("FileNotFoundError", "ConnectionRefusedError", "OSError")
        )

    _run(run())


@pytest.mark.parametrize(
    "frame",
    [
        {
            "action": "rpc_request",
            "request_id": "remote",
            "method": "relay.command",
            "params": {"value": "allow all"},
        },
        {
            "action": "rpc_request",
            "request_id": "remote",
            "method": "state.set_config",
            "params": {},
        },
        {"action": "attach"},
        {"action": "subscribe"},
        {"action": "shutdown"},
        {"action": "status"},
    ],
)
def test_authenticated_offbox_cannot_invoke_operator_dispatch(tmp_path, frame):
    async def run():
        received = []
        server, identity, _, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-admin-{os.getpid()}"
        )
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                server_record = server._dns_registry.resolve("server-agent")
                assert server_record is not None
                assert await AgentMessenger.do_remote_client_handshake(
                    reader,
                    writer,
                    identity,
                    "client-agent",
                    "server-agent",
                    server_record.public_key,
                    timeout=2,
                )
                writer.write(json.dumps(frame).encode() + b"\n")
                await writer.drain()
                reply = json.loads(await asyncio.wait_for(reader.readline(), 2))
                assert "local operator authorization required" in json.dumps(reply)
                assert not received and not server._shutdown_requested
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await server.stop()

    _run(run())


def test_offbox_peer_auth_does_not_grant_local_operator_control(tmp_path):
    """A peer's valid key never authorizes status/attach/shutdown control."""

    async def run():
        received = {"status": False, "shutdown": False}

        async def on_message(msg):
            pass

        storage, identity, registry = _make_dns(tmp_path)
        _, server_pub = identity.get_or_create_keypair("server-agent")
        _, client_pub = identity.get_or_create_keypair("client-agent")
        registry.register(
            AgentRecord(
                designation="server-agent",
                public_key=server_pub,
                approval_state="approved",
            )
        )
        registry.register(
            AgentRecord(
                designation="client-agent",
                public_key=client_pub,
                approval_state="approved",
            )
        )

        server = AgentSocketServer(
            "server-id", on_message, socket_name=f"ep-all-{os.getpid()}"
        )
        server.set_dns_auth(
            registry, identity, require_auth=False, local_designation="server-agent"
        )
        server.enable_endpoint("127.0.0.1", 0, None)
        await server.start()
        port = server._tcp_server.sockets[0].getsockname()[1]
        try:
            auth = _remote_auth(identity, registry)
            target = f"ws://127.0.0.1:{port}"
            # status
            status = await AgentMessenger.request_status(target, auth=auth)
            assert status == {}
            received["status"] = True
            # Even authenticated peers cannot shut down the local daemon.
            acked = await AgentMessenger.signal_shutdown(target, auth=auth)
            assert acked is False
            received["shutdown"] = True
            assert all(received.values())
        finally:
            await server.stop()

    _run(run())


def test_resolve_dial_target_upgrades_remote(tmp_path):
    """The plugin helper upgrades a dial to remote when the registry has an
    endpoint_uri, and falls back to the local socket otherwise."""

    from plugins.hub.dns.models import AgentRecord
    from plugins.hub.plugin import HubPlugin

    class _Identity:
        identity = "me"

    class _FakePlugin:
        # Minimal stand-in exposing just the fields _resolve_dial_target reads.
        _dns_registry = AgentRegistry(DNSStorage(tmp_path / "dns"))
        _dns_identity = None
        _identity = _Identity()
        config = None
        # Borrow the real implementation.
        _resolve_dial_target = HubPlugin._resolve_dial_target

    p = _FakePlugin()

    # No record -> local socket path, no auth (legacy behavior preserved).
    target, auth = p._resolve_dial_target("unknown", "/tmp/x.sock")
    assert target == "/tmp/x.sock"
    assert auth is None

    # Local record (socket only) -> socket path, no auth.
    p._dns_registry.register(
        AgentRecord(designation="local-peer", socket_path="/tmp/local.sock")
    )
    target, auth = p._resolve_dial_target("local-peer", "/tmp/fallback.sock")
    assert target == "/tmp/local.sock"
    assert auth is None

    # A co-located agent that ALSO enabled its endpoint (socket_path AND
    # endpoint_uri both set) must STILL route over the local socket — never
    # its own public endpoint (avoids hairpin/NAT failure + needless hops).
    p._dns_registry.register(
        AgentRecord(
            designation="coloc-peer",
            socket_path="/tmp/coloc.sock",
            endpoint_uri="wss://mesh.example.com:8765",
        )
    )
    target, auth = p._resolve_dial_target("coloc-peer", "/tmp/fallback.sock")
    assert target == "/tmp/coloc.sock"
    assert auth is None

    # Remote-only record (endpoint_uri, NO socket_path — as produced by
    # register_well_known) with no identity manager -> graceful fallback.
    p._dns_registry.register(
        AgentRecord(
            designation="remote-peer",
            endpoint_uri="wss://mesh.example.com:8765",
        )
    )
    target, auth = p._resolve_dial_target("remote-peer", "/tmp/fallback.sock")
    assert target == "/tmp/fallback.sock"
    assert auth is None

    # A remote record with no public-key pin or explicit approval stays local.
    p._dns_identity = IdentityManager(DNSStorage(tmp_path / "dns-id"))
    p._dns_identity.get_or_create_keypair("me")
    target, auth = p._resolve_dial_target("remote-peer", "/tmp/fallback.sock")
    assert target == "/tmp/fallback.sock"
    assert auth is None

    remote_record = p._dns_registry.resolve("remote-peer")
    assert remote_record is not None
    _, remote_public_key = p._dns_identity.get_or_create_keypair("remote-peer")
    remote_record.public_key = remote_public_key
    remote_record.approval_state = "approved"

    # A pinned, explicitly approved remote peer upgrades with mutual auth.
    target, auth = p._resolve_dial_target("remote-peer", "/tmp/fallback.sock")
    assert target == "wss://mesh.example.com:8765"
    assert auth is not None
    assert auth["designation"] == "me"
    assert auth["identity_manager"] is p._dns_identity
    assert auth["expected_server_designation"] == "remote-peer"
    assert auth["expected_server_public_key"] == remote_public_key
