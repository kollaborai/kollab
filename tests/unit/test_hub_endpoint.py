"""Tests for the off-box A2A endpoint (plugins/hub/dns/endpoint.py).

Covers the pure helpers (URI parsing, config, TLS context, well-known
import) plus an end-to-end integration test that drives the FULL off-box
path — TCP listener + Ed25519 handshake + message delivery — over a
plaintext loopback socket. TLS is an orthogonal ``ssl=`` wrapper on the
same code path and is exercised separately via the context builders.
"""

import asyncio
import os

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
from plugins.hub.messenger import AgentMessenger, AgentSocketServer
from plugins.hub.models import HubMessage

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


def test_register_well_known_imports_remote_coordinator(tmp_path):
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
    designation = register_well_known(payload, registry)
    assert designation == "obsidian"
    record = registry.resolve("obsidian")
    assert record is not None
    assert record.public_key == "ab" * 32
    assert record.endpoint_uri == "wss://remote.example.com:8765"
    # imported remote coordinators are trusted (approved) so the handshake works
    assert record.is_approved is True
    # resolve_address prefers the remote endpoint over an (absent) socket
    assert registry.resolve_address("obsidian") == "wss://remote.example.com:8765"


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
    server.set_dns_auth(registry, identity, require_auth=False)
    server.enable_endpoint("127.0.0.1", 0, None)  # plaintext, OS-assigned port
    await server.start()
    port = server._tcp_server.sockets[0].getsockname()[1]
    return server, identity, registry, port


def test_offbox_handshake_and_delivery(tmp_path):
    async def run():
        received = []
        server, identity, _registry, port = await _start_endpoint_server(
            tmp_path, received, socket_name=f"ep-ok-{os.getpid()}"
        )
        try:
            auth = {"identity_manager": identity, "designation": "client-agent"}
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                HubMessage(content="hello over the wire", from_identity="client-agent"),
                auth=auth,
            )
            assert ok is True
            assert len(received) == 1
            assert received[0].content == "hello over the wire"
        finally:
            await server.stop()

    asyncio.run(run())


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
            auth = {"identity_manager": identity, "designation": "intruder"}
            ok = await AgentMessenger.send_to_agent(
                f"ws://127.0.0.1:{port}",
                HubMessage(content="let me in", from_identity="intruder"),
                auth=auth,
            )
            assert ok is False
            assert received == []
        finally:
            await server.stop()

    asyncio.run(run())


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

    asyncio.run(run())


def test_offbox_all_dialers_route_through_open(tmp_path):
    """request_status / signal_shutdown / subscribe accept auth= and reach a
    remote endpoint through the same handshake path as send_to_agent."""

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
        server.set_dns_auth(registry, identity, require_auth=False)
        server.enable_endpoint("127.0.0.1", 0, None)
        await server.start()
        port = server._tcp_server.sockets[0].getsockname()[1]
        try:
            auth = {"identity_manager": identity, "designation": "client-agent"}
            target = f"ws://127.0.0.1:{port}"
            # status
            status = await AgentMessenger.request_status(target, auth=auth)
            assert status.get("type") == "status"
            received["status"] = True
            # shutdown signal — server acks, proving the handshake + round-trip.
            acked = await AgentMessenger.signal_shutdown(target, auth=auth)
            assert acked is True
            received["shutdown"] = True
            assert all(received.values())
        finally:
            await server.stop()

    asyncio.run(run())


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

    # Remote record (endpoint_uri) but no identity manager wired -> graceful
    # fallback to the local socket path (no crash, no half-built auth dict).
    p._dns_registry.register(
        AgentRecord(
            designation="remote-peer",
            socket_path="/tmp/should-not-use.sock",
            endpoint_uri="wss://mesh.example.com:8765",
        )
    )
    target, auth = p._resolve_dial_target("remote-peer", "/tmp/fallback.sock")
    assert target == "/tmp/fallback.sock"
    assert auth is None

    # Wire a real identity manager -> remote dial is upgraded with auth.
    p._dns_identity = IdentityManager(DNSStorage(tmp_path / "dns-id"))
    target, auth = p._resolve_dial_target("remote-peer", "/tmp/fallback.sock")
    assert target == "wss://mesh.example.com:8765"
    assert auth is not None
    assert auth["designation"] == "me"
    assert auth["identity_manager"] is p._dns_identity
