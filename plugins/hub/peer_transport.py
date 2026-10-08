"""Hub carrier integration for signed peer routing.

Peer forwarding carries only the existing RelayAgent TLS records. Intermediate
peers authenticate route metadata, but never receive conversation plaintext or
Hub task/tool authority.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import socket
import sqlite3
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import rfc8785
from nacl.exceptions import BadSignatureError
from nacl.public import Box
from nacl.signing import SigningKey, VerifyKey

from .peer_discovery import PeerDiscoveryService, PeerLocatorCandidate
from .peer_locator import (
    PeerLocatorError,
    validate_peer_designation,
    validate_peer_locator_endpoint,
)
from .peer_records import (
    PEER_RECORD_TTL_MAX,
    PEER_STORE_MAX_RECORDS,
    PeerRecord,
    PeerRecordError,
    SQLitePeerRecordStore,
    peer_id_for_key,
)
from .peer_router import (
    PEER_ENVELOPE_MAX_BYTES,
    PEER_ROUTER_MAX_HOPS,
    DestinationReplayCache,
    ForwardEnvelope,
    PeerLink,
    PeerRouteError,
    PeerRouter,
    SQLitePeerLinkStore,
    TransientPeerDeliveryError,
    append_hop,
    trace_from_wire,
    verify_trace,
)
from .relay_client import MAX_APPLICATION_PAYLOAD, PeerSessionEvent, RelayClient
from .relay_state import RelayError, failure_text, validate_key
from .secure_conversation import SecureConversationTransport

MAX_PEER_FORWARD_CONCURRENCY = 16
# Seconds for one link exchange round trip (session open, then the request).
PEER_EXCHANGE_TIMEOUT = 3
MAX_PEER_FORWARD_PER_PEER_PER_MINUTE = 120
MAX_PEER_FORWARD_TOTAL_PER_MINUTE = 600
MAX_PEER_EXCHANGE_BYTES = 3500
MAX_PEER_EXCHANGE_RECORDS = 8
MAX_PEER_EXCHANGE_LINKS = 8
MAX_PEER_GOSSIP_RECORDS = 4
MAX_PEER_GOSSIP_LINKS = 4
MAX_PEER_FORWARD_TTL_SECONDS = 30
MAX_PEER_FORWARD_BYTES = MAX_APPLICATION_PAYLOAD - 128
MAX_PEER_FORWARD_ROUTE_NODES = PEER_ROUTER_MAX_HOPS + 2
MAX_PEER_LOCATOR_BYTES = 2048
MAX_PEER_LOCATOR_TTL_SECONDS = 300
MAX_PEER_LOCATORS = 256
MAX_PEER_FORWARD_RESPONSE_BYTES = MAX_APPLICATION_PAYLOAD - 256
MAX_DIRECT_PEER_CONNECT_ADDRESSES = 8
DIRECT_PEER_CONNECT_TIMEOUT = 5.0
# A direct attempt that ends this way leaves the relay path open. PeerRouteError is a ValueError:
# a handshake the peer refused (a timeout counts), a locator that no longer resolves and a garbled
# reply all land here.
_DIRECT_FAILED = (ValueError, TransientPeerDeliveryError, OSError, asyncio.TimeoutError)
_PEER_ID = re.compile(r"kollab-peer:ed25519:[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
_HEX_32 = re.compile(r"[0-9a-f]{64}\Z")
_SESSION_ID = re.compile(r"[0-9a-f]{32}\Z")
_SAFE_REVISION = (1 << 53) - 1
_LOCATOR_PAYLOAD_FIELDS = frozenset(
    {
        "v",
        "relay_public_key",
        "endpoint_designation",
        "endpoint_public_key",
        "endpoint",
        "session_id",
        "revision",
        "issued_at",
        "expires_at",
    }
)
_LOCATOR_WIRE_FIELDS = _LOCATOR_PAYLOAD_FIELDS | {
    "relay_signature",
    "endpoint_signature",
}
_FORWARD_FIELDS = frozenset(
    {"v", "envelope", "route", "records", "links", "trace"}
)
_RECEIPT_FIELDS = frozenset({"v", "message_id", "ciphertext"})

logger = logging.getLogger(__name__)

ApplicationHandler = Callable[[str, str, dict], Awaitable[dict]]


def _encoded_size(value: object) -> int:
    try:
        return len(rfc8785.dumps(value))
    except (TypeError, ValueError, RecursionError):
        raise PeerRouteError("invalid peer transport frame") from None


def _box_encrypt(signing_key: SigningKey, peer_public_key: str, value: bytes) -> bytes:
    """Seal a bounded application record to its ultimate Ed25519 recipient."""
    try:
        peer_key = VerifyKey(bytes.fromhex(peer_public_key)).to_curve25519_public_key()
        box = Box(signing_key.to_curve25519_private_key(), peer_key)
        return bytes(box.encrypt(value))
    except (ValueError, TypeError) as exc:
        raise PeerRouteError("peer application encryption failed") from exc


def _box_decrypt(signing_key: SigningKey, peer_public_key: str, value: bytes) -> bytes:
    """Open an opaque record only at its named ultimate recipient."""
    try:
        peer_key = VerifyKey(bytes.fromhex(peer_public_key)).to_curve25519_public_key()
        box = Box(signing_key.to_curve25519_private_key(), peer_key)
        return box.decrypt(value)
    except Exception as exc:
        raise PeerRouteError("peer application authentication failed") from exc


def _locator_endpoint(
    endpoint: object, *, allow_private_network: bool
) -> tuple[str, int]:
    try:
        return validate_peer_locator_endpoint(
            endpoint, allow_private_network=allow_private_network
        )
    except PeerLocatorError as exc:
        raise PeerRouteError(str(exc)) from exc


def verify_peer_locator(
    value: object,
    *,
    now: int | None = None,
    allow_private_network: bool = False,
    endpoint_registry: Any = None,
) -> dict[str, Any] | None:
    """Verify the two-key binding in a privacy-minimal signed locator."""
    current = int(time.time()) if now is None else now
    if not isinstance(value, dict) or not value.keys() >= _LOCATOR_WIRE_FIELDS:
        return None
    if type(value.get("v")) is not int or value["v"] != 1:
        return None
    relay_key = value.get("relay_public_key")
    endpoint_key = value.get("endpoint_public_key")
    designation = value.get("endpoint_designation")
    session_id = value.get("session_id")
    revision = value.get("revision")
    issued_at = value.get("issued_at")
    expires_at = value.get("expires_at")
    if (
        not isinstance(relay_key, str)
        or not _HEX_32.fullmatch(relay_key)
        or not isinstance(endpoint_key, str)
        or not _HEX_32.fullmatch(endpoint_key)
        or relay_key == endpoint_key
        or not isinstance(designation, str)
        or not isinstance(session_id, str)
        or not _SESSION_ID.fullmatch(session_id)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 1 <= revision <= _SAFE_REVISION
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= issued_at
        or expires_at - issued_at > MAX_PEER_LOCATOR_TTL_SECONDS
        or issued_at > current + 30
        or expires_at <= current
    ):
        return None
    try:
        validate_peer_designation(designation)
    except PeerLocatorError:
        return None
    try:
        _locator_endpoint(
            value.get("endpoint"), allow_private_network=allow_private_network
        )
    except PeerRouteError:
        return None
    relay_signature = value.get("relay_signature")
    endpoint_signature = value.get("endpoint_signature")
    if (
        not isinstance(relay_signature, str)
        or not _SIGNATURE.fullmatch(relay_signature)
        or not isinstance(endpoint_signature, str)
        or not _SIGNATURE.fullmatch(endpoint_signature)
    ):
        return None
    if endpoint_registry is not None:
        # The pin can only deny. A designation this device already knows must
        # keep its key and must not be rejected; one it has never seen is
        # vouched for by the approved relay key that signed the locator.
        try:
            known = endpoint_registry.resolve(designation)
        except Exception:
            return None
        if known is not None and (
            getattr(known, "approval_state", "") == "rejected"
            or str(getattr(known, "public_key", "")).lower() != endpoint_key
        ):
            return None
    payload = {name: value[name] for name in _LOCATOR_PAYLOAD_FIELDS}
    try:
        signed = rfc8785.dumps(payload)
        VerifyKey(bytes.fromhex(relay_key)).verify(
            signed, bytes.fromhex(relay_signature)
        )
        VerifyKey(bytes.fromhex(endpoint_key)).verify(
            signed, bytes.fromhex(endpoint_signature)
        )
        digest = hashlib.sha256(rfc8785.dumps(value)).hexdigest()
    except (BadSignatureError, TypeError, ValueError, RecursionError):
        return None
    return {**payload, "digest": digest}


class SQLitePeerLocatorStore:
    """Private durable high-water pins for signed peer locators."""

    def __init__(self, path: str | Path, *, max_peers: int = MAX_PEER_LOCATORS):
        if isinstance(max_peers, bool) or not 1 <= max_peers <= MAX_PEER_LOCATORS:
            raise ValueError("locator capacity exceeds protocol limit")
        self.path = Path(path)
        if self.path.is_symlink():
            raise PeerRouteError("peer locator state must not use symbolic links")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._max_peers = max_peers
        self._connection = sqlite3.connect(
            str(self.path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS locator_revision ("
            "relay_key TEXT PRIMARY KEY, revision INTEGER NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS locator_candidates ("
            "relay_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "session_id TEXT NOT NULL, digest TEXT NOT NULL, expires_at INTEGER NOT NULL, "
            "retain_until INTEGER NOT NULL, candidate_json TEXT NOT NULL, "
            "revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)))"
        )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        self._connection.close()

    def next_revision(self, relay_key: str) -> int:
        if not isinstance(relay_key, str) or not _HEX_32.fullmatch(relay_key):
            raise PeerRouteError("invalid local locator identity")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT revision FROM locator_revision WHERE relay_key = ?",
                (relay_key,),
            ).fetchone()
            revision = 1 if row is None else int(row[0]) + 1
            if revision > _SAFE_REVISION:
                raise PeerRouteError("peer locator revision capacity reached")
            connection.execute(
                "INSERT INTO locator_revision(relay_key, revision) VALUES (?, ?) "
                "ON CONFLICT(relay_key) DO UPDATE SET revision = excluded.revision",
                (relay_key, revision),
            )
            connection.execute("COMMIT")
            return revision
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    def accept_candidate(self, candidate: object, *, now: int | None = None) -> bool:
        value = _candidate_dict(candidate)
        current = int(time.time()) if now is None else now
        relay_key = value["relay_public_key"]
        revision = value["revision"]
        session_id = value["session_id"]
        digest = value["digest"]
        expires_at = value["expires_at"]
        encoded = rfc8785.dumps(value).decode("utf-8")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "DELETE FROM locator_candidates WHERE revoked = 0 AND retain_until <= ?",
                (current,),
            )
            previous = connection.execute(
                "SELECT revision, session_id, digest, retain_until, revoked "
                "FROM locator_candidates WHERE relay_key = ?",
                (relay_key,),
            ).fetchone()
            if previous is not None:
                old_revision, old_session, old_digest, retain_until, revoked = previous
                if revoked:
                    raise PeerRouteError("peer locator is locally revoked")
                if revision < old_revision:
                    raise PeerRouteError("peer locator revision rollback")
                if revision == old_revision:
                    if session_id != old_session or digest != old_digest:
                        raise PeerRouteError("peer locator revision equivocation")
                    connection.execute("COMMIT")
                    return False
                connection.execute(
                    "UPDATE locator_candidates SET revision = ?, session_id = ?, "
                    "digest = ?, expires_at = ?, retain_until = ?, candidate_json = ? "
                    "WHERE relay_key = ?",
                    (
                        revision,
                        session_id,
                        digest,
                        expires_at,
                        max(int(retain_until), expires_at),
                        encoded,
                        relay_key,
                    ),
                )
            else:
                count = connection.execute(
                    "SELECT COUNT(*) FROM locator_candidates"
                ).fetchone()[0]
                if count >= self._max_peers:
                    raise PeerRouteError("peer locator capacity is full")
                connection.execute(
                    "INSERT INTO locator_candidates "
                    "(relay_key, revision, session_id, digest, expires_at, retain_until, candidate_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (relay_key, revision, session_id, digest, expires_at, expires_at, encoded),
                )
            connection.execute("COMMIT")
            return True
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    def get(self, relay_key: str, *, now: int | None = None) -> dict[str, Any] | None:
        current = int(time.time()) if now is None else now
        row = self._connection.execute(
            "SELECT candidate_json FROM locator_candidates WHERE relay_key = ? "
            "AND revoked = 0 AND expires_at > ?",
            (relay_key, current),
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PeerRouteError("stored peer locator is corrupt") from exc
        return value if isinstance(value, dict) else None

    def revoke(self, relay_key: str, *, now: int | None = None) -> None:
        if not isinstance(relay_key, str) or not _HEX_32.fullmatch(relay_key):
            raise PeerRouteError("invalid revoked peer identity")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT 1 FROM locator_candidates WHERE relay_key = ?",
                (relay_key,),
            ).fetchone()
            if row is None:
                count = connection.execute(
                    "SELECT COUNT(*) FROM locator_candidates"
                ).fetchone()[0]
                if count >= self._max_peers:
                    raise PeerRouteError("peer locator revocation capacity is full")
                connection.execute(
                    "INSERT INTO locator_candidates "
                    "(relay_key, revision, session_id, digest, expires_at, retain_until, candidate_json, revoked) "
                    "VALUES (?, 0, '', '', 0, ?, '{}', 1)",
                    (relay_key, int(time.time()) if now is None else now),
                )
            else:
                connection.execute(
                    "UPDATE locator_candidates SET revoked = 1, candidate_json = '{}' "
                    "WHERE relay_key = ?",
                    (relay_key,),
                )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise


def _candidate_dict(candidate: object) -> dict[str, Any]:
    if hasattr(candidate, "as_dict"):
        try:
            value = candidate.as_dict()
        except Exception as exc:
            raise PeerRouteError("invalid peer locator candidate") from exc
    else:
        value = candidate
    fields = (_LOCATOR_PAYLOAD_FIELDS - {"v"}) | {"digest"}
    if not isinstance(value, dict) or not value.keys() >= fields:
        raise PeerRouteError("invalid peer locator candidate")
    if (
        not isinstance(value.get("digest"), str)
        or not _HEX_32.fullmatch(value["digest"])
        or not isinstance(value.get("relay_public_key"), str)
        or not _HEX_32.fullmatch(value["relay_public_key"])
        or not isinstance(value.get("endpoint_public_key"), str)
        or not _HEX_32.fullmatch(value["endpoint_public_key"])
        or value["relay_public_key"] == value["endpoint_public_key"]
        or not isinstance(value.get("endpoint_designation"), str)
        or not isinstance(value.get("session_id"), str)
        or not _SESSION_ID.fullmatch(value["session_id"])
        or isinstance(value.get("revision"), bool)
        or not isinstance(value.get("revision"), int)
        or not 1 <= value["revision"] <= _SAFE_REVISION
        or isinstance(value.get("issued_at"), bool)
        or not isinstance(value.get("issued_at"), int)
        or isinstance(value.get("expires_at"), bool)
        or not isinstance(value.get("expires_at"), int)
        or value["expires_at"] <= value["issued_at"]
        or value["expires_at"] - value["issued_at"] > MAX_PEER_LOCATOR_TTL_SECONDS
    ):
        raise PeerRouteError("invalid peer locator candidate")
    try:
        validate_peer_designation(value["endpoint_designation"])
        validate_peer_locator_endpoint(
            value["endpoint"], allow_private_network=None
        )
    except PeerLocatorError as exc:
        raise PeerRouteError("invalid peer locator candidate") from exc
    return {
        **{
            name: value[name]
            for name in _LOCATOR_PAYLOAD_FIELDS
            if name != "v"
        },
        "digest": value["digest"],
    }


class PeerMeshRuntime:
    """Connect signed PeerRouter state to the existing TLS and RelayClient paths."""

    def __init__(
        self,
        client: RelayClient,
        secure_transport: SecureConversationTransport,
        state_dir: Path,
        application_handler: ApplicationHandler,
        *,
        forwarding_enabled: Callable[[], bool],
        endpoint_identity_manager: Any = None,
        endpoint_registry: Any = None,
        endpoint_designation: str = "",
        direct_endpoint: str = "",
        endpoint_tls_ca: str = "",
        direct_enabled: bool = False,
        allow_private_network: bool = False,
        discovery_advertise_enabled: bool = False,
        discovery_scan_enabled: bool = False,
        discovery_bind_address: str = "0.0.0.0",
        discovery_multicast_group: str | None = "239.255.77.77",
        discovery_port: int = 39531,
        direct_sender: Callable[..., Awaitable[dict]] | None = None,
    ):
        if not callable(application_handler) or not callable(forwarding_enabled):
            raise TypeError("peer mesh callbacks must be callable")
        if any(
            type(value) is not bool
            for value in (
                direct_enabled,
                allow_private_network,
                discovery_advertise_enabled,
                discovery_scan_enabled,
            )
        ):
            raise TypeError("peer mesh opt-ins must be bools")
        if direct_sender is not None and not callable(direct_sender):
            raise TypeError("direct peer sender must be callable")
        if discovery_advertise_enabled and not direct_endpoint:
            raise PeerRouteError("peer advertisement requires a TLS endpoint")
        if discovery_advertise_enabled and endpoint_identity_manager is None:
            raise PeerRouteError("peer advertisement requires an endpoint identity")
        if direct_enabled and endpoint_identity_manager is None:
            raise PeerRouteError("direct peer transport requires an endpoint identity")
        if direct_endpoint:
            try:
                validate_peer_locator_endpoint(
                    direct_endpoint, allow_private_network=None
                )
            except PeerLocatorError as exc:
                raise PeerRouteError("invalid local direct peer endpoint") from exc
        if endpoint_designation:
            try:
                validate_peer_designation(endpoint_designation)
            except PeerLocatorError as exc:
                raise PeerRouteError("invalid local endpoint designation") from exc
        self.client = client
        self.secure_transport = secure_transport
        self.state_dir = Path(state_dir)
        self.application_handler = application_handler
        self._forwarding_enabled = forwarding_enabled
        self.endpoint_identity_manager = endpoint_identity_manager
        self.endpoint_registry = endpoint_registry
        self.endpoint_designation = endpoint_designation
        self.direct_endpoint = direct_endpoint
        self.endpoint_tls_ca = endpoint_tls_ca
        self.direct_enabled = direct_enabled
        self.allow_private_network = allow_private_network
        self._direct_sender = direct_sender
        self._signing_key: SigningKey = client._store.key
        self.local_peer_id = peer_id_for_key(client.public_key)
        if self.state_dir.is_symlink():
            raise RelayError("peer mesh state must not use symbolic links")
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.state_dir.is_symlink() or not self.state_dir.is_dir():
            raise RelayError("peer mesh state must be a private directory")
        for name in (
            "peer-records.sqlite3",
            "peer-links.sqlite3",
            "peer-replays.sqlite3",
            "peer-locators.sqlite3",
        ):
            if (self.state_dir / name).is_symlink():
                raise RelayError("peer mesh state must not use symbolic links")
        self.record_store = SQLitePeerRecordStore(
            self.state_dir / "peer-records.sqlite3", scope=None
        )
        self.link_store = SQLitePeerLinkStore(self.state_dir / "peer-links.sqlite3")
        self.replay_cache = DestinationReplayCache(
            path=self.state_dir / "peer-replays.sqlite3"
        )
        self.locator_store = SQLitePeerLocatorStore(
            self.state_dir / "peer-locators.sqlite3"
        )
        # The session this node runs under while it has no relay registration.
        self._direct_session = secrets.token_hex(16)
        self.discovery = PeerDiscoveryService(
            advertise_enabled=discovery_advertise_enabled,
            scan_enabled=discovery_scan_enabled,
            locator_provider=self._local_locator_wire
            if discovery_advertise_enabled
            else None,
            candidate_verifier=self._verify_locator_wire
            if discovery_scan_enabled
            else None,
            revision_acceptor=self.locator_store.accept_candidate
            if discovery_scan_enabled
            else None,
            on_candidate=self._accept_locator_candidate
            if discovery_scan_enabled
            else None,
            source_address_policy=self._discovery_source_allowed,
            multicast_group=discovery_multicast_group,
            port=discovery_port,
            bind_address=discovery_bind_address,
            session_provider=self.local_session,
        )
        self._local_locator_cache: tuple[str, int, dict[str, Any]] | None = None
        self._locators: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._scope = ""
        self.router: PeerRouter | None = None
        self._local_record: PeerRecord | None = None
        self._exchange_offsets: dict[str, tuple[int, int]] = {}
        # Highest link revision this node proposed per edge, replied to or not.
        self._proposed_revisions: dict[tuple[str, str, str], int] = {}
        self._exchange_lock = asyncio.Lock()
        self._peer_exchange_locks: dict[str, asyncio.Lock] = {}
        self._forward_semaphore = asyncio.Semaphore(MAX_PEER_FORWARD_CONCURRENCY)
        self._forward_counts: Counter[tuple[str, int]] = Counter()
        self._forward_total: Counter[int] = Counter()
        self._exchange_tasks: set[asyncio.Task] = set()
        self._closed = False
        self.forwarded_frames = 0
        self.received_frames = 0
        self._remove_peer_listener = client.add_peer_session_listener(
            self._on_peer_session_event
        )
        secure_transport.set_peer_transport(self.request, self.binding_for)
        self._ensure_router()

    async def start(self) -> None:
        """Start only explicitly enabled LAN discovery roles."""
        if self._closed:
            raise PeerRouteError("peer mesh is closed")
        await self.discovery.start()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.discovery.close()
        self._remove_peer_listener()
        for task in tuple(self._exchange_tasks):
            task.cancel()
        self._exchange_tasks.clear()
        self.secure_transport.set_peer_transport(
            self.client.request, lambda _key: None
        )
        self.replay_cache.close()
        self.link_store.close()
        self.record_store.close()
        self.locator_store.close()
        self.router = None
        self._local_record = None
        self._locators.clear()
        self._local_locator_cache = None

    def local_session(self) -> str:
        """The session this node runs under right now.

        While it is registered with the relay that is the relay session, which
        peers on the roster verify. Without a relay it is a per-process direct
        session, which peers learn from this node's signed locator or record.
        """
        status = self.client.status()
        session = status.get("session")
        if (
            status.get("state") == "online"
            and isinstance(session, str)
            and _SESSION_ID.fullmatch(session)
        ):
            return session
        return self._direct_session

    def _roster_peer(self, peer_key: str) -> dict | None:
        """The approved peer on the relay roster, if this node is registered."""
        if self.client.status().get("state") != "online":
            return None
        return next(
            (
                item
                for item in self.client.peers()
                if item.get("key") == peer_key and item.get("approved")
            ),
            None,
        )

    def _peer_session(self, peer_key: str) -> tuple[str, str] | None:
        """The session an approved peer runs under, and where it came from.

        The relay roster wins, then the peer's signed locator (only while
        direct links are on), then the signed record a route carried.
        """
        if peer_key not in self.client.state.approvals:
            return None
        roster = self._roster_peer(peer_key)
        if roster is not None:
            return "relay", roster["session"]
        if self.direct_enabled:
            locator = self._locator_for_peer(peer_key)
            if locator is not None:
                return "locator", locator["session_id"]
        router = self._ensure_router()
        record = self.record_store.get(peer_id_for_key(peer_key), scope=router.scope)
        if (
            record is None
            or not record.session_id
            or self.record_store.is_revoked(record.peer_id, scope=router.scope)
        ):
            return None
        return "record", record.session_id

    @staticmethod
    def _own_addresses() -> frozenset[str]:
        """The IPv4 addresses of this host's own interfaces."""
        try:
            import psutil

            return frozenset(
                item.address
                for items in psutil.net_if_addrs().values()
                for item in items
                if item.family == socket.AF_INET
            )
        except Exception:
            return frozenset()

    def _discovery_source_allowed(self, source: str) -> bool:
        try:
            address = ipaddress.ip_address(source)
        except ValueError:
            return False
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            return False
        if address.is_loopback or address.is_reserved:
            return False
        if self.allow_private_network is not True:
            return False
        # A second device on this host announces from the host's own address,
        # which is public on a cloud machine; every other public source stays
        # out.
        return not address.is_global or source in self._own_addresses()

    def _local_locator_wire(self, session_id: str) -> dict[str, Any] | None:
        """Create a short-lived locator for this device's own TLS endpoint.

        Only a device that belongs to a network advertises one, and it names
        the session the device runs under, relay-registered or not.
        """
        if (
            not self.direct_endpoint
            or self.endpoint_identity_manager is None
            or not self.endpoint_designation
            or not self.client.state.approvals
            or session_id != self.local_session()
        ):
            return None
        now = int(time.time())
        cached = self._local_locator_cache
        if cached and cached[0] == session_id and cached[1] - now > 60:
            return dict(cached[2])
        registration = (
            self.endpoint_registry.resolve(self.endpoint_designation)
            if self.endpoint_registry is not None
            else None
        )
        try:
            _private, endpoint_key = self.endpoint_identity_manager.get_or_create_keypair(
                self.endpoint_designation
            )
        except Exception:
            return None
        if (
            registration is None
            or str(getattr(registration, "public_key", "")).lower() != endpoint_key
            or endpoint_key == self.client.public_key
        ):
            return None
        try:
            validate_peer_locator_endpoint(
                self.direct_endpoint,
                allow_private_network=self.allow_private_network,
            )
        except PeerLocatorError:
            return None
        payload = {
            "v": 1,
            "relay_public_key": self.client.public_key,
            "endpoint_designation": self.endpoint_designation,
            "endpoint_public_key": endpoint_key,
            "endpoint": self.direct_endpoint,
            "session_id": session_id,
            "revision": self.locator_store.next_revision(self.client.public_key),
            "issued_at": now,
            "expires_at": now + 240,
        }
        signed = rfc8785.dumps(payload)
        wire = {
            **payload,
            "relay_signature": self._signing_key.sign(signed).signature.hex(),
            "endpoint_signature": self.endpoint_identity_manager.sign_message(
                self.endpoint_designation, signed
            ),
        }
        self._local_locator_cache = (session_id, payload["expires_at"], wire)
        return dict(wire)

    def _verify_locator_wire(self, wire: dict[str, Any]) -> dict[str, Any] | None:
        """Verify both signatures, the approved relay key and any known pin."""
        try:
            candidate = verify_peer_locator(
                wire,
                allow_private_network=self.allow_private_network,
                endpoint_registry=self.endpoint_registry,
            )
        except Exception:
            return None
        if candidate is None or candidate["relay_public_key"] not in self.client.state.approvals:
            return None
        if candidate["relay_public_key"] == self.client.public_key:
            return None
        # PeerDiscoveryService's normalized candidate deliberately omits the
        # wire-only version marker; it validates the remaining fields and
        # computes the canonical full-wire digest itself.
        return {key: value for key, value in candidate.items() if key != "v"}

    def _accept_locator_candidate(self, candidate: PeerLocatorCandidate) -> None:
        """Cache a verified candidate without creating a route or approval."""
        value = candidate.as_dict()
        relay_key = value["relay_public_key"]
        if (
            self._closed
            or relay_key not in self.client.state.approvals
            or value["expires_at"] <= int(time.time())
        ):
            return
        self._locators[relay_key] = value
        self._locators.move_to_end(relay_key)
        while len(self._locators) > MAX_PEER_LOCATORS:
            self._locators.popitem(last=False)

    def _locator_for_peer(self, peer_key: str) -> dict[str, Any] | None:
        value = self._locators.get(peer_key) or self.locator_store.get(peer_key)
        if (
            value is None
            or value.get("expires_at", 0) <= int(time.time())
            or peer_key not in self.client.state.approvals
        ):
            self._locators.pop(peer_key, None)
            return None
        return value

    async def _resolve_direct_endpoint(self, endpoint: str) -> tuple[str, int, tuple[str, ...]]:
        try:
            host, port = validate_peer_locator_endpoint(
                endpoint, allow_private_network=self.allow_private_network
            )
        except PeerLocatorError as exc:
            raise PeerRouteError("direct peer endpoint is invalid") from exc
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise TransientPeerDeliveryError("direct peer endpoint did not resolve") from exc
        addresses = sorted(
            {
                str(info[4][0]).split("%", 1)[0]
                for info in infos
                if len(info) > 4 and isinstance(info[4], tuple) and info[4]
            }
        )[:MAX_DIRECT_PEER_CONNECT_ADDRESSES]
        if not addresses:
            raise TransientPeerDeliveryError("direct peer endpoint has no addresses")
        for address_text in addresses:
            try:
                address = ipaddress.ip_address(address_text)
            except ValueError as exc:
                raise PeerRouteError("direct peer resolved to an invalid address") from exc
            if (
                address.is_unspecified
                or address.is_multicast
                or address.is_reserved
                or (not self.allow_private_network and not address.is_global)
            ):
                raise PeerRouteError("direct peer resolved outside the allowed network")
        return host, port, tuple(addresses)

    async def _send_direct_peer_forward(
        self,
        peer_key: str,
        frame: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Send one opaque carrier frame over the pinned direct TLS endpoint."""
        if not self.direct_enabled or self.endpoint_identity_manager is None:
            raise TransientPeerDeliveryError("direct peer transport is disabled")
        if self._direct_sender is not None:
            return await self._direct_sender(peer_key, frame, timeout=timeout)
        return await self._direct_request(
            peer_key,
            {"action": "peer_forward", "frame": frame},
            "peer_forward_result",
            timeout=timeout,
        )

    async def _send_direct_secure(
        self,
        peer_key: str,
        method: str,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Carry one end-to-end TLS record to a locator-authenticated peer.

        This bootstraps a link with a peer outside the relay room: the
        endpoint handshake authenticates the peer, its signed locator binds
        that endpoint to the approved relay key, and the payload stays the
        opaque TLS record the secure transport would send over the relay.
        """
        if not self.direct_enabled or self.endpoint_identity_manager is None:
            raise TransientPeerDeliveryError("direct peer transport is disabled")
        return await self._direct_request(
            peer_key,
            {"action": "peer_secure", "method": method, "payload": payload},
            "peer_secure_result",
            timeout=timeout,
        )

    async def _direct_request(
        self,
        peer_key: str,
        request: dict[str, Any],
        result_type: str,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """One authenticated request/response line over the peer's direct endpoint."""
        locator = self._locator_for_peer(peer_key)
        if locator is None:
            raise TransientPeerDeliveryError("direct peer locator is unavailable")
        if self.endpoint_identity_manager is None or not self.endpoint_designation:
            raise TransientPeerDeliveryError("local endpoint identity is unavailable")
        try:
            from .dns.endpoint import build_client_ssl_context
            from .messenger import (
                REMOTE_PEER_FORWARD_MAX_FRAME_BYTES,
                AgentMessenger,
            )
        except ImportError as exc:
            raise TransientPeerDeliveryError("direct peer transport is unavailable") from exc
        host, port, addresses = await self._resolve_direct_endpoint(locator["endpoint"])
        ssl_context = build_client_ssl_context(self.endpoint_tls_ca)
        request_line = rfc8785.dumps(request) + b"\n"
        if len(request_line) > REMOTE_PEER_FORWARD_MAX_FRAME_BYTES:
            raise PeerRouteError("direct peer frame exceeds its transport bound")
        last_error: Exception | None = None
        for address in addresses:
            reader = writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        address,
                        port,
                        ssl=ssl_context,
                        server_hostname=host,
                        limit=REMOTE_PEER_FORWARD_MAX_FRAME_BYTES,
                    ),
                    timeout=min(timeout, DIRECT_PEER_CONNECT_TIMEOUT),
                )
                if not await AgentMessenger.do_remote_client_handshake(
                    reader,
                    writer,
                    self.endpoint_identity_manager,
                    self.endpoint_designation,
                    locator["endpoint_designation"],
                    locator["endpoint_public_key"],
                    timeout=min(timeout, DIRECT_PEER_CONNECT_TIMEOUT),
                ):
                    raise PeerRouteError("direct peer endpoint identity was rejected")
                writer.write(request_line)
                await asyncio.wait_for(
                    writer.drain(), timeout=min(timeout, DIRECT_PEER_CONNECT_TIMEOUT)
                )
                response_line = await asyncio.wait_for(
                    reader.readline(), timeout=min(timeout, DIRECT_PEER_CONNECT_TIMEOUT)
                )
                if (
                    not response_line
                    or len(response_line) > REMOTE_PEER_FORWARD_MAX_FRAME_BYTES
                    or not response_line.endswith(b"\n")
                ):
                    raise TransientPeerDeliveryError("direct peer returned no bounded response")
                response = json.loads(response_line.decode("utf-8"))
                if (
                    not isinstance(response, dict)
                    or not response.keys() >= {"type", "response"}
                    or response.get("type") != result_type
                    or not isinstance(response.get("response"), dict)
                ):
                    raise TransientPeerDeliveryError("direct peer returned an invalid response")
                return response["response"]
            except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
                last_error = exc
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
                    except Exception:
                        pass
        raise TransientPeerDeliveryError("direct peer endpoint is unavailable") from last_error

    def _current_scope(self) -> str:
        room = self.client.state.room
        if not isinstance(room, str) or not _HEX_32.fullmatch(room):
            raise RelayError("peer mesh room scope is unavailable")
        return room

    def _ensure_router(self) -> PeerRouter:
        scope = self._current_scope()
        if self.router is not None and self._scope == scope:
            return self.router
        router = PeerRouter(
            self.local_peer_id,
            scope,
            record_store=self.record_store,
            link_state_store=self.link_store,
        )
        for link in self.link_store.list_links(scope):
            try:
                router.add_link(link)
            except (PeerRouteError, PeerRecordError):
                continue
        self._scope = scope
        self.router = router
        self._local_record = None
        self._refresh_online_neighbors()
        return router

    def _relay_endpoint(self) -> str:
        try:
            parsed = urlsplit(self.client.state.origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port else ""
            return f"wss://{host}{port}"
        except (TypeError, ValueError):
            raise RelayError("peer mesh relay origin is unavailable") from None

    def _ensure_local_record(self) -> PeerRecord:
        router = self._ensure_router()
        session = self.local_session()
        if session == self._direct_session and not self.direct_enabled:
            raise RelayError("peer mesh requires an active relay session")
        endpoint = self._relay_endpoint()
        roles = ("agent", "forwarder") if self._forwarding_enabled() is True else ("agent",)
        now = int(time.time())
        current = self.record_store.get(self.local_peer_id, scope=router.scope, now=now)
        if (
            current is not None
            and current.session_id == session
            and current.endpoints == (endpoint,)
            and current.roles == tuple(sorted(roles))
            and current.expires_at - now > 60
        ):
            self._local_record = current
            router.add_record(current)
            return current
        record = PeerRecord.issue(
            self._signing_key,
            scope=router.scope,
            session_id=session,
            revision=self.record_store.next_revision(
                self.local_peer_id, scope=router.scope, now=now
            ),
            endpoints=(endpoint,),
            roles=roles,
            issued_at=now,
            expires_at=now + PEER_RECORD_TTL_MAX,
        )
        self.record_store.accept(record, now=now)
        router.add_record(record, now=now)
        self._local_record = record
        return record

    def _live_peers(self) -> dict[str, str]:
        """Approved peers this node can reach right now, with their sessions.

        Roster peers while registered with the relay, plus peers reached by a
        live signed locator when direct links are on.
        """
        live: dict[str, str] = {}
        if self.client.status().get("state") == "online":
            live.update(
                {
                    peer["key"]: peer["session"]
                    for peer in self.client.peers()
                    if peer.get("approved")
                }
            )
        if self.direct_enabled:
            for key in tuple(self.client.state.approvals):
                if key in live:
                    continue
                locator = self._locator_for_peer(key)
                if locator is not None:
                    live[key] = locator["session_id"]
        return live

    def _refresh_online_neighbors(self) -> None:
        router = self.router
        if router is None:
            return
        live = self._live_peers()
        live_ids = {peer_id_for_key(key) for key in live}
        for peer_id in router.authenticated_neighbors:
            if peer_id not in live_ids:
                try:
                    router.set_authenticated_neighbor(peer_id, False)
                except PeerRouteError:
                    pass
        for peer_key, session in live.items():
            peer_id = peer_id_for_key(peer_key)
            record = self.record_store.get(peer_id, scope=router.scope)
            link = router.link_between(self.local_peer_id, peer_id)
            if (
                record is not None
                and record.session_id == session
                and link is not None
                and self._link_is_live(peer_key, link)
            ):
                try:
                    router.set_authenticated_neighbor(
                        peer_id, True, session_id=link.session_id
                    )
                except PeerRouteError:
                    pass

    def binding_for(self, peer_key: str) -> tuple[str, str, str] | None:
        """Bind TLS to the sessions both ends run under.

        A peer on the relay roster binds to its relay session. One reached only
        by locator or route binds to the session its signed locator or record
        names, and this node's side is its own local session, relay or direct.
        """
        validate_key(peer_key)
        found = self._peer_session(peer_key)
        if found is None:
            return None
        return peer_key, self.local_session(), found[1]

    def _on_peer_session_event(self, event: PeerSessionEvent) -> None:
        if self._closed:
            return
        try:
            router = self._ensure_router()
            if event.kind == "local_disconnected":
                for peer_id in router.authenticated_neighbors:
                    router.set_authenticated_neighbor(peer_id, False)
                self._local_record = None
                return
            if event.peer_key is None:
                return
            peer_id = peer_id_for_key(event.peer_key)
            if event.kind == "peer_revoked":
                router.revoke(peer_id)
                return
            if event.kind in {"peer_disappeared", "peer_session_changed"}:
                router.set_authenticated_neighbor(peer_id, False)
            if event.kind in {"peer_appeared", "peer_session_changed"}:
                task = asyncio.get_running_loop().create_task(
                    self.exchange_peer(event.peer_key), name="kollab-peer-exchange"
                )
                self._exchange_tasks.add(task)
                task.add_done_callback(self._exchange_tasks.discard)
        except (RuntimeError, PeerRouteError, PeerRecordError, RelayError):
            return

    async def refresh(self) -> None:
        if self._closed or self._exchange_lock.locked():
            return
        async with self._exchange_lock:
            try:
                self._ensure_router()
                self._ensure_local_record()
            except (RelayError, PeerRouteError, PeerRecordError):
                return
            strangers = getattr(self.client.state, "links", ())
            peers = [
                peer["key"]
                for peer in self.client.peers()
                if peer.get("approved") and peer["key"] not in strangers
            ]
            if self.direct_enabled:
                peers += [
                    key
                    for key in sorted(self.client.state.approvals)
                    if key not in peers
                    and key not in strangers
                    and self._locator_for_peer(key) is not None
                ]
            peers = peers[:MAX_PEER_EXCHANGE_RECORDS]
            if peers:
                await asyncio.gather(
                    *(self.exchange_peer(peer) for peer in peers),
                    return_exceptions=True,
                )
            self._refresh_online_neighbors()

    async def exchange_peer(self, peer_key: str) -> None:
        # An accepted stranger is not a mesh member: it never gets this
        # network's peer records.
        if (
            self._closed
            or peer_key not in self.client.state.approvals
            or peer_key in getattr(self.client.state, "links", ())
        ):
            return
        lock = self._peer_exchange_locks.get(peer_key)
        if lock is None:
            if len(self._peer_exchange_locks) >= MAX_PEER_EXCHANGE_RECORDS * 32:
                return
            lock = self._peer_exchange_locks[peer_key] = asyncio.Lock()
        if lock.locked():
            return
        async with lock:
            await self._exchange_peer_locked(peer_key)

    async def _exchange_peer_locked(self, peer_key: str) -> None:
        try:
            self._peer_session_source(peer_key)
            local_record = self._ensure_local_record()
            remote_record = self.record_store.get(
                peer_id_for_key(peer_key), scope=local_record.scope
            )
            for _attempt in range(2):
                known = remote_record is not None
                proposal = None
                # One node of a pair writes its link, the one with the lower peer
                # id. Two nodes proposing at once each name their own session and
                # revision, and end up holding two links they cannot reconcile.
                if known and local_record.peer_id < remote_record.peer_id:
                    # The link names this node's own outbound session: open it
                    # first, so the id proposed is the one the peer sees arrive.
                    await self.secure_transport.ensure_session(
                        peer_key, timeout=PEER_EXCHANGE_TIMEOUT
                    )
                    proposal = self._make_link_signature(local_record, remote_record, peer_key)
                request = self._exchange_request(peer_key, local_record, proposal)
                response = await self.secure_transport.request(
                    peer_key, "peer.exchange", request, timeout=PEER_EXCHANGE_TIMEOUT
                )
                remote_record, link = self._accept_exchange_response(
                    peer_key, response, proposal=proposal
                )
                if link is not None:
                    self._mark_neighbor(peer_key, link)
                if known:
                    return
        except (RelayError, PeerRouteError, PeerRecordError, OSError, TimeoutError):
            return

    def _make_link_signature(
        self, local: PeerRecord, remote: PeerRecord, peer_key: str
    ) -> dict | None:
        """Sign this node's proposal for the link, or None while the held link is good.

        The link names the outbound session this node opened. It is renewed with a
        fresh timestamp and a higher revision once the session changed or half its
        lifetime is gone; between renewals nothing is re-signed, because a restated
        statement carries a timestamp the peer rightly refuses after a minute.
        """
        tls_id = self.secure_transport.outbound_link_session_id(peer_key)
        if tls_id is None:
            raise PeerRouteError("peer exchange has no authenticated TLS session")
        current_link = self._ensure_router().link_between(
            local.peer_id, remote.peer_id
        )
        now = int(time.time())
        forwarding_allowed = bool(
            "forwarder" in local.roles and "forwarder" in remote.roles
        )
        if (
            current_link is not None
            and current_link.session_id == tls_id
            and current_link.forwarding_allowed == forwarding_allowed
            and current_link.expires_at - now > PEER_RECORD_TTL_MAX // 2
        ):
            return None
        edge = (local.scope, *sorted((local.peer_id, remote.peer_id)))
        # Revisions only move forward, past a proposal whose reply never came back.
        revision = max(
            self.link_store.next_revision(*edge),
            self._proposed_revisions.get(edge, 0) + 1,
        )
        self._proposed_revisions[edge] = revision
        issued_at = now
        expires_at = min(now + PEER_RECORD_TTL_MAX, local.expires_at, remote.expires_at)
        payload = PeerLink.signing_payload(
            scope=local.scope,
            peer_a=local.peer_id,
            peer_b=remote.peer_id,
            session_id=tls_id,
            revision=revision,
            issued_at=issued_at,
            expires_at=expires_at,
            forwarding_allowed=forwarding_allowed,
        )
        signer, signature = PeerLink.sign_statement(self._signing_key, payload)
        if signer != local.peer_id:
            raise PeerRouteError("peer link signer identity mismatch")
        return {"payload": payload, "signature": signature}

    def _exchange_request(self, peer_key: str, local: PeerRecord, proposal) -> dict:
        records, links = self._gossip_page(peer_key)
        value = {
            "v": 1,
            "record": local.to_wire(),
            "records": records,
            "links": links,
            "link_signature": proposal,
        }
        if _encoded_size(value) > MAX_PEER_EXCHANGE_BYTES:
            raise PeerRouteError("peer exchange exceeds its size limit")
        return value

    def _gossip_page(self, peer_key: str) -> tuple[list[dict], list[dict]]:
        router = self._ensure_router()
        peer_id = peer_id_for_key(peer_key)
        records = [
            record.to_wire()
            for record in router.record_snapshot().values()
            if record.peer_id not in {self.local_peer_id, peer_id}
        ]
        records.sort(key=lambda item: item["peer_id"])
        links = [
            link.to_wire()
            for link in router.link_snapshot().values()
            if {link.left, link.right} != {self.local_peer_id, peer_id}
        ]
        links.sort(key=lambda item: (item["left"], item["right"], item["revision"]))
        record_offset, link_offset = self._exchange_offsets.get(peer_key, (0, 0))
        record_page = records[record_offset : record_offset + MAX_PEER_GOSSIP_RECORDS]
        link_page = links[link_offset : link_offset + MAX_PEER_GOSSIP_LINKS]
        self._exchange_offsets[peer_key] = (
            0 if record_offset + len(record_page) >= len(records) else record_offset + len(record_page),
            0 if link_offset + len(link_page) >= len(links) else link_offset + len(link_page),
        )
        return record_page, link_page

    def _accept_exchange_response(
        self, peer_key: str, value, *, proposal: dict | None
    ) -> tuple[PeerRecord, PeerLink | None]:
        fields = {"v", "record", "records", "links", "link"}
        if (
            not isinstance(value, dict)
            or not value.keys() >= fields
            or type(value["v"]) is not int
            or value["v"] != 1
            or _encoded_size(value) > MAX_PEER_EXCHANGE_BYTES
        ):
            raise PeerRouteError("invalid peer exchange response")
        direct = self._direct_peer(peer_key)
        router = self._ensure_router()
        now = int(time.time())
        record = PeerRecord.from_wire(value["record"], now=now)
        if (
            record.scope != router.scope
            or record.peer_id != peer_id_for_key(peer_key)
            or record.public_key != peer_key
            or record.session_id != direct["session"]
        ):
            raise PeerRouteError("peer exchange identity or session mismatch")
        self.record_store.accept(record, now=now)
        router.add_record(record, now=now)
        self._accept_gossip(value["records"], value["links"], now=now)
        link = None
        if value["link"] is not None:
            if proposal is None:
                raise PeerRouteError("unsolicited peer link response")
            link = PeerLink.from_wire(value["link"])
            self._validate_pair_link(link, record, now=now, proposed=proposal["payload"])
            router.add_link(link, now=now)
        elif proposal is not None:
            raise PeerRouteError("peer did not acknowledge the signed link proposal")
        return record, link

    async def handle_exchange(self, peer_key: str, value: dict) -> dict:
        """Accept signed metadata only on a current, direct mutual-TLS session."""
        direct = self._direct_peer(peer_key)
        router = self._ensure_router()
        now = int(time.time())
        fields = {"v", "record", "records", "links", "link_signature"}
        if (
            not isinstance(value, dict)
            or not value.keys() >= fields
            or type(value["v"]) is not int
            or value["v"] != 1
            or _encoded_size(value) > MAX_PEER_EXCHANGE_BYTES
        ):
            raise PeerRouteError("invalid peer exchange request")
        record = PeerRecord.from_wire(value["record"], now=now)
        if (
            record.scope != router.scope
            or record.peer_id != peer_id_for_key(peer_key)
            or record.public_key != peer_key
            or record.session_id != direct["session"]
        ):
            raise PeerRouteError("peer exchange identity or session mismatch")
        self.record_store.accept(record, now=now)
        router.add_record(record, now=now)
        self._accept_gossip(value["records"], value["links"], now=now)
        local = self._ensure_local_record()
        link = None
        if value["link_signature"] is not None:
            link = self._accept_link_signature(
                peer_key, record, local, value["link_signature"], now=now
            )
            router.add_link(link, now=now)
            self._mark_neighbor(peer_key, link)
        response = self._exchange_response(peer_key, local, link)
        return response

    def _accept_link_signature(
        self,
        peer_key: str,
        remote: PeerRecord,
        local: PeerRecord,
        value,
        *,
        now: int,
    ) -> PeerLink:
        if not isinstance(value, dict) or not value.keys() >= {"payload", "signature"}:
            raise PeerRouteError("invalid peer link signature proposal")
        payload, signature = value["payload"], value["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
            raise PeerRouteError("invalid peer link signature proposal")
        # The link names the session the proposal arrived on: the proposer's
        # outbound session, which is this node's inbound one.
        expected_session = self.secure_transport.carrying_link_session_id()
        if expected_session is None:
            raise PeerRouteError("peer link has no authenticated TLS session")
        if remote.peer_id > local.peer_id:
            raise PeerRouteError("peer link proposal did not come from the proposing side")
        revision = payload.get("revision")
        expected = {
            "v": 1,
            "scope": local.scope,
            "left": min(local.peer_id, remote.peer_id),
            "right": max(local.peer_id, remote.peer_id),
            "session_id": expected_session,
            "revision": revision,
            "issued_at": payload.get("issued_at"),
            "expires_at": payload.get("expires_at"),
            "forwarding_allowed": bool("forwarder" in local.roles and "forwarder" in remote.roles),
        }
        if (
            payload != expected
            or type(revision) is not int
            or revision < self.link_store.next_revision(local.scope, local.peer_id, remote.peer_id)
        ):
            raise PeerRouteError("peer link proposal does not match the live session")
        issued_at, expires_at = payload["issued_at"], payload["expires_at"]
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or issued_at > now + 30
            or issued_at < now - 60
            or not issued_at < expires_at <= min(issued_at + PEER_RECORD_TTL_MAX, local.expires_at, remote.expires_at)
            or expires_at <= now
        ):
            raise PeerRouteError("peer link proposal has an invalid lifetime")
        try:
            VerifyKey(bytes.fromhex(peer_key)).verify(rfc8785.dumps(payload), bytes.fromhex(signature))
        except (BadSignatureError, ValueError) as exc:
            raise PeerRouteError("peer link signature verification failed") from exc
        own_peer_id, own_signature = PeerLink.sign_statement(self._signing_key, payload)
        if own_peer_id != local.peer_id:
            raise PeerRouteError("peer link signer identity mismatch")
        link = PeerLink.from_signatures(
            scope=local.scope,
            peer_a=local.peer_id,
            peer_b=remote.peer_id,
            session_id=expected_session,
            revision=revision,
            issued_at=issued_at,
            expires_at=expires_at,
            forwarding_allowed=expected["forwarding_allowed"],
            signatures={peer_id_for_key(peer_key): signature, own_peer_id: own_signature},
        )
        record_by_id = {local.peer_id: local, remote.peer_id: remote}
        link.verify(
            record_by_id[link.left],
            record_by_id[link.right],
            expected_scope=local.scope,
            expected_session_id=expected_session,
            now=now,
        )
        return link

    def _validate_pair_link(
        self,
        link: PeerLink,
        remote: PeerRecord,
        *,
        now: int,
        proposed: dict,
    ) -> None:
        router = self._ensure_router()
        local = self.record_store.get(self.local_peer_id, scope=router.scope, now=now)
        if local is None:
            raise PeerRouteError("local peer record is unavailable")
        if {link.left, link.right} != {self.local_peer_id, remote.peer_id}:
            raise PeerRouteError("peer exchange returned an unrelated link")
        tls_id = proposed["session_id"]
        if link.session_id != tls_id:
            raise PeerRouteError("peer exchange link is bound to another TLS session")
        if link.revision != proposed["revision"]:
            raise PeerRouteError("peer exchange link revision mismatch")
        if link.forwarding_allowed != bool("forwarder" in local.roles and "forwarder" in remote.roles):
            raise PeerRouteError("peer exchange link lacks bilateral forwarding consent")
        left = self.record_store.get(link.left, scope=router.scope, now=now)
        right = self.record_store.get(link.right, scope=router.scope, now=now)
        if left is None or right is None:
            raise PeerRouteError("peer link endpoint record is unavailable")
        link.verify(left, right, expected_scope=router.scope, expected_session_id=tls_id, now=now)

    def _exchange_response(self, peer_key: str, local: PeerRecord, link) -> dict:
        records, links = self._gossip_page(peer_key)
        value = {
            "v": 1,
            "record": local.to_wire(),
            "records": records,
            "links": links,
            "link": None if link is None else link.to_wire(),
        }
        if _encoded_size(value) > MAX_PEER_EXCHANGE_BYTES:
            raise PeerRouteError("peer exchange response exceeds its size limit")
        return value

    def _accept_gossip(self, records, links, *, now: int) -> None:
        router = self._ensure_router()
        if (
            not isinstance(records, list)
            or len(records) > MAX_PEER_GOSSIP_RECORDS
            or not isinstance(links, list)
            or len(links) > MAX_PEER_GOSSIP_LINKS
        ):
            raise PeerRouteError("peer exchange gossip exceeds its count limit")
        decoded_records = []
        for wire in records:
            record = PeerRecord.from_wire(wire, now=now)
            if record.scope != router.scope or self.record_store.is_revoked(record.peer_id, scope=router.scope):
                raise PeerRouteError("peer exchange contains an out-of-scope record")
            decoded_records.append(record)
        for record in decoded_records:
            if self._holds_newer_record(record, now):
                continue  # a copy older than the one held, passed on by a third party
            self.record_store.accept(record, now=now)
            router.add_record(record, now=now)
        for wire in links:
            link = PeerLink.from_wire(wire)
            if link.scope != router.scope:
                raise PeerRouteError("peer exchange contains an out-of-scope link")
            if (
                router.records.get(link.left, scope=router.scope, now=now) is None
                or router.records.get(link.right, scope=router.scope, now=now) is None
            ):
                continue
            if self._holds_newer(link):
                continue
            router.add_link(link, now=now)

    def _holds_newer_record(self, record: PeerRecord, now: int) -> bool:
        """This node already holds a later revision of the peer's record."""
        held = self.record_store.next_revision(record.peer_id, scope=record.scope, now=now) - 1
        return record.revision < held

    def _holds_newer(self, link: PeerLink) -> bool:
        """This node already holds a later revision of the link's edge."""
        held = self.link_store.next_revision(link.scope, link.left, link.right) - 1
        return link.revision < held

    def _link_is_live(self, peer_key: str, link: PeerLink) -> bool:
        """The TLS session the link names is open with the peer, in either direction.

        Each node holds its own half of a session and one half can outlive the
        other, so two nodes never agree on "the" session by reading their own
        tables. A link is checked by asking whether its session is among them.
        """
        return link.session_id in self.secure_transport.live_link_session_ids(peer_key)

    def _mark_neighbor(self, peer_key: str, link: PeerLink) -> None:
        router = self._ensure_router()
        peer_id = peer_id_for_key(peer_key)
        if not self._link_is_live(peer_key, link):
            raise PeerRouteError("peer link does not match the current TLS session")
        router.set_authenticated_neighbor(peer_id, True, session_id=link.session_id)

    def _direct_peer(self, peer_key: str) -> dict:
        """The approved peer a signed exchange may come from or go to.

        It is on the relay roster, or its live signed locator names the
        session its records must carry.
        """
        if peer_key not in self.client.state.approvals:
            raise PeerRouteError("peer exchange requires local approval")
        live = self._live_peers()
        if peer_key not in live:
            raise PeerRouteError("peer exchange requires a live direct session")
        return {"key": peer_key, "session": live[peer_key], "approved": True}

    def _forward_frame(
        self,
        path: tuple[str, ...],
        envelope: ForwardEnvelope,
        trace: tuple,
        links: dict[str, PeerLink] | None = None,
    ) -> dict[str, Any]:
        """The frame for a route. A transit node passes the links it was handed:
        its trace names those, and a copy of its own may be a revision apart."""
        router = self._ensure_router()
        records = router.record_snapshot()
        path_records = []
        path_links = []
        for peer_id in path:
            record = records.get(peer_id)
            if record is None:
                raise PeerRouteError("peer route record is unavailable")
            path_records.append(record.to_wire())
        for left, right in zip(path, path[1:]):
            link = (
                router.link_between(left, right)
                if links is None
                else next((item for item in links.values() if {item.left, item.right} == {left, right}), None)
            )
            if link is None:
                raise PeerRouteError("peer route link is unavailable")
            path_links.append(link.to_wire())
        frame = {
            "v": 1,
            "envelope": envelope.to_wire(),
            "route": list(path),
            "records": path_records,
            "links": path_links,
            "trace": [item.to_wire() for item in trace],
        }
        if _encoded_size(frame) > MAX_PEER_FORWARD_BYTES:
            raise PeerRouteError("peer forwarding frame exceeds its size limit")
        return frame

    async def _send_forward_to_peer(
        self, peer_key: str, frame: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        if self.direct_enabled and self._locator_for_peer(peer_key) is not None:
            try:
                return await self._send_direct_peer_forward(
                    peer_key, frame, timeout=timeout
                )
            except _DIRECT_FAILED:
                # The envelope's destination replay key makes relay fallback
                # safe if the direct peer committed but its receipt was lost.
                pass
        return await self.client.request(
            peer_key,
            "peer.forward",
            frame,
            timeout=min(timeout, MAX_PEER_FORWARD_TTL_SECONDS),
        )

    def _route_material(self, value: object, ingress_peer_key: str):
        if not isinstance(value, dict) or not value.keys() >= _FORWARD_FIELDS:
            raise PeerRouteError("invalid peer forwarding frame")
        if type(value.get("v")) is not int or value["v"] != 1:
            raise PeerRouteError("unsupported peer forwarding frame")
        if _encoded_size(value) > MAX_PEER_FORWARD_BYTES:
            raise PeerRouteError("peer forwarding frame exceeds its size limit")
        route = value.get("route")
        records_wire = value.get("records")
        links_wire = value.get("links")
        if (
            not isinstance(route, list)
            or not 2 <= len(route) <= MAX_PEER_FORWARD_ROUTE_NODES
            or any(not isinstance(peer_id, str) or not _PEER_ID.fullmatch(peer_id) for peer_id in route)
            or len(set(route)) != len(route)
            or not isinstance(records_wire, list)
            or len(records_wire) != len(route)
            or not isinstance(links_wire, list)
            or len(links_wire) != len(route) - 1
        ):
            raise PeerRouteError("invalid peer route metadata")
        router = self._ensure_router()
        now = int(time.time())
        records = {}
        for wire in records_wire:
            record = PeerRecord.from_wire(wire, now=now)
            if (
                record.scope != router.scope
                or record.peer_id not in route
                or record.peer_id in records
                or record.peer_id != peer_id_for_key(record.public_key)
                or (
                    record.peer_id != self.local_peer_id
                    and record.public_key not in self.client.state.approvals
                )
                or self.record_store.is_revoked(record.peer_id, scope=router.scope)
            ):
                raise PeerRouteError("peer route contains an unapproved identity")
            records[record.peer_id] = record
        if set(records) != set(route):
            raise PeerRouteError("peer route identity set does not match its path")
        links = {}
        for index, wire in enumerate(links_wire):
            link = PeerLink.from_wire(wire)
            left, right = sorted((route[index], route[index + 1]))
            if (
                link.scope != router.scope
                or (link.left, link.right) != (left, right)
                or link.link_id in links
            ):
                raise PeerRouteError("peer route link does not match its path")
            link.verify(
                records[link.left],
                records[link.right],
                expected_scope=router.scope,
                now=now,
            )
            if len(route) > 2 and not link.forwarding_allowed:
                raise PeerRouteError("peer route lacks forwarding consent")
            links[link.link_id] = link

        envelope_wire = value.get("envelope")
        if not isinstance(envelope_wire, dict):
            raise PeerRouteError("invalid peer forwarding envelope")
        origin = envelope_wire.get("origin")
        origin_record = records.get(origin)
        if origin_record is None:
            raise PeerRouteError("peer forwarding origin is unavailable")
        envelope = ForwardEnvelope.from_wire(
            envelope_wire,
            origin_public_key=origin_record.public_key,
            now=now,
        )
        if (
            route[0] != envelope.origin
            or route[-1] != envelope.destination
            or envelope.scope != router.scope
            or origin_record.public_key not in self.client.state.approvals
        ):
            raise PeerRouteError("peer forwarding route identity mismatch")
        trace = trace_from_wire(value.get("trace"))
        expected_local_index = len(trace) + 1
        if (
            expected_local_index >= len(route)
            or route[expected_local_index] != self.local_peer_id
            or route[len(trace)] != peer_id_for_key(ingress_peer_key)
        ):
            raise PeerRouteError("peer forwarding frame reached the wrong hop")
        verify_trace(
            envelope,
            trace,
            records,
            links=links,
            origin_public_key=origin_record.public_key,
            expected_next_hop=self.local_peer_id,
            now=now,
        )
        incoming_link = next(
            (
                link
                for link in links.values()
                if {link.left, link.right}
                == {route[len(trace)], self.local_peer_id}
            ),
            None,
        )
        if incoming_link is None:
            raise PeerRouteError("peer forwarding ingress link is unavailable")
        if (
            not self._link_is_live(ingress_peer_key, incoming_link)
            or incoming_link.expires_at <= now
        ):
            raise PeerRouteError("peer forwarding ingress session is stale")
        # Import only after the full signed route has been validated.
        for record in records.values():
            if self._holds_newer_record(record, now):
                continue
            self.record_store.accept(record, now=now)
            router.add_record(record, now=now)
        for link in links.values():
            if self._holds_newer(link):
                continue
            self.link_store.accept(link, now=now)
            router.add_link(link, now=now)
        return envelope, route, trace, records, links, expected_local_index

    def endpoint_key_for(self, designation: str) -> str:
        """The endpoint key a network member's live locator gives a designation.

        A claim belongs to the relay key that signed it, and `_direct_caller`
        maps a caller back to that key alone. When two members claim one name
        with different keys, the member approved first keeps it, so a later
        claim cannot block the first device. An accepted stranger is not a
        member and never claims a name.
        """
        if not self.direct_enabled:
            return ""
        for peer_key in self.client.members():
            locator = self._locator_for_peer(peer_key)
            if locator is not None and locator.get("endpoint_designation") == designation:
                return str(locator.get("endpoint_public_key", "")).lower()
        return ""

    def _direct_caller(self, designation: str, endpoint_public_key: str) -> str:
        """Map a current endpoint-authenticated caller to its approved relay key."""
        candidates = [
            self._locator_for_peer(peer_key)
            for peer_key in tuple(self.client.state.approvals)
        ]
        matches = [
            item
            for item in candidates
            if item is not None
            and item.get("endpoint_designation") == designation
            and item.get("endpoint_public_key") == endpoint_public_key.lower()
        ]
        if len(matches) != 1:
            raise PeerRouteError("direct peer identity is not bound to one approved relay")
        return matches[0]["relay_public_key"]

    async def handle_direct_forward(
        self, designation: str, endpoint_public_key: str, frame: dict[str, Any]
    ) -> dict[str, Any]:
        peer_key = self._direct_caller(designation, endpoint_public_key)
        if peer_key in getattr(self.client.state, "links", ()):
            raise PeerRouteError("an accepted stranger is not a mesh member")
        return await self.handle_forward(peer_key, frame)

    async def handle_direct_secure(
        self,
        designation: str,
        endpoint_public_key: str,
        method: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Deliver a direct TLS record exactly as a relay-carried one."""
        if self._closed or not self.direct_enabled:
            raise PeerRouteError("direct peer transport is disabled")
        if method not in {"secure_identity", "secure_packet"} or not isinstance(payload, dict):
            raise PeerRouteError("direct carrier accepts only secure conversation records")
        peer_key = self._direct_caller(designation, endpoint_public_key)
        result = await self.application_handler(peer_key, method, payload)
        if not isinstance(result, dict):
            raise PeerRouteError("secure record handler returned an invalid response")
        return result

    def _admit_transit(self, ingress_peer_key: str) -> None:
        """Bound what this node forwards for others: per peer, in total, at once."""
        minute = int(time.time() // 60)
        for stale in [key for key in self._forward_counts if key[1] != minute]:
            del self._forward_counts[stale]
        for stale in [key for key in self._forward_total if key != minute]:
            del self._forward_total[stale]
        if (
            self._forward_counts[(ingress_peer_key, minute)]
            >= MAX_PEER_FORWARD_PER_PEER_PER_MINUTE
            or self._forward_total[minute] >= MAX_PEER_FORWARD_TOTAL_PER_MINUTE
            or self._forward_semaphore.locked()
        ):
            raise PeerRouteError("peer forwarding is over its limit")
        self._forward_counts[(ingress_peer_key, minute)] += 1
        self._forward_total[minute] += 1

    async def handle_forward(
        self, ingress_peer_key: str, frame: dict[str, Any]
    ) -> dict[str, Any]:
        """Verify and forward one opaque end-to-end encrypted application frame."""
        if self._closed or ingress_peer_key not in self.client.state.approvals:
            raise PeerRouteError("peer forwarding is not approved")
        if ingress_peer_key not in self._live_peers():
            raise PeerRouteError("peer forwarding ingress is offline")
        try:
            envelope, route, trace, records, links, local_index = self._route_material(
                frame, ingress_peer_key
            )
            self.received_frames += 1
            if route[-1] == self.local_peer_id:
                return await self._deliver_envelope(envelope, records[envelope.origin])
            if self._forwarding_enabled() is not True:
                raise PeerRouteError("peer forwarding is disabled")
            self._admit_transit(ingress_peer_key)
            next_id = route[local_index + 1]
            next_record = records[next_id]
            ingress_link = next(
                link for link in links.values()
                if {link.left, link.right} == {route[local_index - 1], self.local_peer_id}
            )
            egress_link = next(
                link for link in links.values()
                if {link.left, link.right} == {self.local_peer_id, next_id}
            )
            if (
                not self._link_is_live(next_record.public_key, egress_link)
                or egress_link.expires_at <= int(time.time())
                or not egress_link.forwarding_allowed
            ):
                return {"v": 1, "error": "route_unavailable"}
            new_trace = append_hop(
                envelope,
                trace,
                signing_key=self._signing_key,
                next_hop=next_id,
                origin_public_key=records[envelope.origin].public_key,
                ingress_link_id=ingress_link.link_id,
                egress_link_id=egress_link.link_id,
                prior_records=records,
                peer_links=links,
            )
            next_frame = self._forward_frame(route, envelope, new_trace, links)
            async with self._forward_semaphore:
                response = await self._send_forward_to_peer(
                    next_record.public_key,
                    next_frame,
                    timeout=max(1.0, min(envelope.expires_at - int(time.time()), MAX_PEER_FORWARD_TTL_SECONDS)),
                )
            self.forwarded_frames += 1
            return response
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, RelayError):
                raise
            logger.warning("peer forwarding request was rejected: %s", failure_text(exc))
            raise RelayError("peer forwarding request was rejected") from None

    async def _deliver_envelope(
        self, envelope: ForwardEnvelope, origin_record: PeerRecord
    ) -> dict[str, Any]:
        now = int(time.time())
        claimed = self.replay_cache.claim(
            envelope, origin_public_key=origin_record.public_key, now=now
        )
        if not claimed:
            cached = self.replay_cache.cached_receipt(
                envelope, origin_public_key=origin_record.public_key, now=now
            )
            if cached is None:
                return {"v": 1, "error": "duplicate_pending"}
            return {
                "v": 1,
                "receipt": {
                    "v": 1,
                    "message_id": envelope.message_id,
                    "ciphertext": base64.urlsafe_b64encode(cached)
                    .decode("ascii")
                    .rstrip("="),
                },
            }
        try:
            plaintext = _box_decrypt(
                self._signing_key, origin_record.public_key, envelope.ciphertext
            )
            value = json.loads(plaintext.decode("utf-8"))
            if (
                not isinstance(value, dict)
                or not value.keys() >= {"v", "method", "payload"}
                or type(value["v"]) is not int
                or value["v"] != 1
                or value["method"] not in {"secure_identity", "secure_packet"}
                or not isinstance(value["payload"], dict)
            ):
                raise PeerRouteError("invalid end-to-end peer application record")
            result = await self.application_handler(
                origin_record.public_key, value["method"], value["payload"]
            )
            if not isinstance(result, dict):
                raise PeerRouteError("peer application handler returned an invalid result")
            response_bytes = rfc8785.dumps({"v": 1, "result": result})
            if len(response_bytes) > MAX_PEER_FORWARD_RESPONSE_BYTES:
                raise PeerRouteError("peer application receipt exceeds its bound")
            encrypted = _box_encrypt(
                self._signing_key, origin_record.public_key, response_bytes
            )
            self.replay_cache.complete(
                envelope,
                encrypted,
                origin_public_key=origin_record.public_key,
                now=int(time.time()),
            )
            return {
                "v": 1,
                "receipt": {
                    "v": 1,
                    "message_id": envelope.message_id,
                    "ciphertext": base64.urlsafe_b64encode(encrypted)
                    .decode("ascii")
                    .rstrip("="),
                },
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            # The durable claim remains to suppress a possible repeated side
            # effect; no exception content is exposed through the carrier.
            return {"v": 1, "error": "delivery_failed"}

    def _peer_session_source(self, peer_key: str) -> tuple[str, str]:
        """Return `relay`, `locator` or `record` plus the peer's session."""
        found = self._peer_session(peer_key)
        if found is None:
            raise PeerRouteError("peer has no current authenticated session")
        return found

    def known_peer_keys(self, requested: str = "") -> list[tuple[str, str]]:
        router = self._ensure_router()
        if requested:
            validate_key(requested)
            record = self.record_store.get(peer_id_for_key(requested), scope=router.scope)
            if requested not in self.client.state.approvals or record is None:
                return []
            return [(requested, record.session_id)] if router.route_candidates(record.peer_id) else []
        direct = {item["key"]: item["session"] for item in self.client.peers()}
        rows = []
        for record in router.record_snapshot().values():
            if record.peer_id == self.local_peer_id or record.public_key not in self.client.state.approvals:
                continue
            if router.route_candidates(record.peer_id):
                rows.append((record.public_key, record.session_id or direct.get(record.public_key, "")))
        rows.sort()
        return rows[:PEER_STORE_MAX_RECORDS]

    async def request(self, peer_key: str, method: str, payload: dict, *, timeout: float) -> dict:
        """Carry only secure identity/packet records over direct or mesh routes."""
        if self._closed:
            raise RelayError("peer mesh is closed")
        if method not in {"secure_identity", "secure_packet"}:
            raise RelayError("peer carrier accepts only secure conversation records")
        if peer_key not in self.client.state.approvals:
            raise RelayError("peer requires explicit local approval")
        direct = next((item for item in self.client.peers() if item["key"] == peer_key), None)
        router = self._ensure_router()
        remote_id = peer_id_for_key(peer_key)
        if direct is None and self.direct_enabled and self._locator_for_peer(peer_key):
            # A peer outside the relay room but reachable on its signed
            # locator (LAN or configured endpoint) needs no route or link:
            # this is how the first link to it is bootstrapped.
            try:
                return await self._send_direct_secure(
                    peer_key, method, payload, timeout=timeout
                )
            except _DIRECT_FAILED:
                pass
        if direct is None:
            record = self.record_store.get(remote_id, scope=router.scope)
            if record is None or not router.route_candidates(remote_id):
                raise RelayError("no authenticated peer route is available")
        else:
            # A peer already on the relay only benefits from the mesh when a
            # direct dial exists; otherwise the mesh would wrap the same packet
            # in peer.forward over the same relay, so keep the native path.
            if not self.direct_enabled or self._locator_for_peer(peer_key) is None:
                return await self.client.request(peer_key, method, payload, timeout=timeout)
            record = self.record_store.get(remote_id, scope=router.scope)
            link = router.link_between(self.local_peer_id, remote_id)
            # A link rides the TLS session it names. The handshake that opens a
            # session cannot depend on one, or a link that outlived its sessions
            # would refuse the very handshake that replaces it, on the relay too.
            if (
                link is None
                or not self._link_is_live(peer_key, link)
                or (method == "secure_packet" and record is None)
            ):
                return await self.client.request(peer_key, method, payload, timeout=timeout)
        inner = {"v": 1, "method": method, "payload": payload}
        if not isinstance(payload, dict) or _encoded_size(inner) > PEER_ENVELOPE_MAX_BYTES:
            raise RelayError("secure conversation record exceeds the peer envelope limit")
        ciphertext = _box_encrypt(
            self._signing_key, peer_key, rfc8785.dumps(inner)
        )
        envelope = ForwardEnvelope.create(
            self._signing_key,
            scope=router.scope,
            destination=remote_id,
            message_id=secrets.token_hex(16),
            ciphertext=ciphertext,
            expires_at=int(time.time()) + min(PEER_RECORD_TTL_MAX, max(1, int(timeout))),
        )
        result_holder: dict = {}

        async def send_path(path, same_envelope):
            frame = self._forward_frame(path, same_envelope, ())
            first_record = router.record_snapshot().get(path[1])
            if first_record is None:
                raise TransientPeerDeliveryError("first hop record is unavailable")
            response = await self._send_forward_to_peer(
                first_record.public_key,
                frame,
                timeout=min(float(timeout), MAX_PEER_FORWARD_TTL_SECONDS),
            )
            if (
                isinstance(response, dict)
                and set(response) == {"v", "error"}
                and response.get("v") == 1
                and response.get("error") in {
                    "route_unavailable",
                    "duplicate_pending",
                    "delivery_failed",
                }
            ):
                raise TransientPeerDeliveryError("peer route is unavailable")
            if (
                not isinstance(response, dict)
                or not response.keys() >= {"v", "receipt"}
                or response.get("v") != 1
                or not isinstance(response.get("receipt"), dict)
            ):
                raise PeerRouteError("peer route returned an invalid receipt")
            result_holder["receipt"] = response["receipt"]
            return True

        try:
            await router.send_with_failover(
                remote_id,
                envelope,
                send_path,
                timeout_per_path=min(float(timeout), MAX_PEER_FORWARD_TTL_SECONDS),
            )
        except PeerRouteError as exc:
            if direct is not None:
                # The peer is on the relay too: a stale mesh link costs one failed try.
                return await self.client.request(peer_key, method, payload, timeout=timeout)
            raise RelayError("peer route delivery failed") from exc
        receipt = result_holder.get("receipt")
        if not isinstance(receipt, dict):
            raise RelayError("peer route delivery did not return a secure response")
        if (
            not receipt.keys() >= {"v", "message_id", "ciphertext"}
            or type(receipt["v"]) is not int
            or receipt["v"] != 1
            or receipt["message_id"] != envelope.message_id
            or not isinstance(receipt["ciphertext"], str)
            or len(receipt["ciphertext"]) > 4 * ((MAX_PEER_FORWARD_RESPONSE_BYTES + 2) // 3)
        ):
            raise RelayError("peer route returned an invalid secure response")
        try:
            encoded = receipt["ciphertext"].encode("ascii")
            encrypted = base64.b64decode(
                encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
            if base64.urlsafe_b64encode(encrypted).decode("ascii").rstrip("=") != receipt["ciphertext"]:
                raise ValueError("noncanonical encoding")
            plaintext = _box_decrypt(self._signing_key, peer_key, encrypted)
            result = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise RelayError("peer route response authentication failed") from exc
        if (
            not isinstance(result, dict)
            or not result.keys() >= {"v", "result"}
            or type(result["v"]) is not int
            or result["v"] != 1
            or not isinstance(result["result"], dict)
        ):
            raise RelayError("peer route returned an invalid secure result")
        return result["result"]
