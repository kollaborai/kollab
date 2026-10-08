"""Bounded, signed peer metadata for the opt-in Kollab mesh.

Peer records are candidate discovery data only. A valid self-signature proves
control of a key; it does not grant communication, forwarding, workspace, or
tool permission. Local designation records remain in the existing DNS registry.
This module is a protocol foundation and is not yet wired to discovery or disk
store. An optional SQLite adapter preserves signed records, revision pins and
revocations on disk, but Hub does not yet construct it from application state.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import rfc8785
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

PEER_RECORD_VERSION = 1
PEER_RECORD_TTL_MAX = 300
PEER_RECORD_FUTURE_SKEW = 30
PEER_RECORD_MAX_BYTES = 4096
PEER_RECORD_MAX_ENDPOINTS = 8
PEER_RECORD_MAX_ROLES = 8
PEER_STORE_MAX_RECORDS = 256
_HEX_32 = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
_PEER_ID = re.compile(r"kollab-peer:ed25519:[0-9a-f]{64}\Z")
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ROLE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


class PeerRecordError(ValueError):
    """A peer record or link is malformed, stale, or conflicts with a pin."""


def peer_id_for_key(public_key: bytes | str) -> str:
    """Return the purpose-scoped stable id derived from the full Ed25519 key."""
    if isinstance(public_key, str):
        if not _HEX_32.fullmatch(public_key):
            raise PeerRecordError("invalid peer public key")
        try:
            key_bytes = bytes.fromhex(public_key)
        except ValueError as exc:
            raise PeerRecordError("invalid peer public key") from exc
    else:
        key_bytes = public_key
    if not isinstance(key_bytes, bytes) or len(key_bytes) != 32:
        raise PeerRecordError("invalid peer public key")
    digest = hashlib.sha256(key_bytes).hexdigest()
    return f"kollab-peer:ed25519:{digest}"


def _validate_scope(scope: str) -> None:
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise PeerRecordError("invalid network scope")


def _validate_endpoint(endpoint: str) -> None:
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or len(endpoint) > 1024
        or any(ord(char) < 33 for char in endpoint)
    ):
        raise PeerRecordError("invalid public endpoint")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        host = parsed.hostname
    except ValueError as exc:
        raise PeerRecordError("invalid public endpoint") from exc
    # Public peer advertisements are TLS-only. Local sockets and private
    # workspace paths stay in local state and never enter this record.
    if (
        parsed.scheme != "wss"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or port == 0
    ):
        raise PeerRecordError("endpoint must be a public wss origin")


@dataclass(frozen=True)
class ResolvedPeerEndpoint:
    """A validated host and the exact addresses a dialer must use.

    The caller must connect to one of ``addresses`` without resolving ``host``
    again, while using ``host`` for TLS SNI and certificate validation. This
    binds the policy check to the actual connection and prevents DNS rebinding
    between validation and dial.
    """

    host: str
    port: int
    addresses: tuple[str, ...]


async def resolve_peer_endpoint(
    endpoint: str,
    *,
    allow_private_network: bool = False,
    resolver: Callable[[str, int], Awaitable[list[tuple[Any, ...]]]] | None = None,
) -> ResolvedPeerEndpoint:
    """Resolve a peer origin and reject unsafe addresses before a socket dial.

    Private, loopback and link-local targets require an explicit private-network
    opt-in for LAN/self-hosted deployments. The returned IP set must be used
    directly; dialing the hostname afterward would reopen DNS rebinding.
    """
    if type(allow_private_network) is not bool:
        raise PeerRecordError("private network policy must be explicit")
    _validate_endpoint(endpoint)
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    if host is None:
        raise PeerRecordError("peer endpoint has no host")
    port = parsed.port or 443
    if resolver is None:
        loop = asyncio.get_running_loop()
        addresses_info = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    else:
        addresses_info = await resolver(host, port)
    addresses = sorted({str(item[4][0]) for item in addresses_info if len(item) > 4})
    if not addresses:
        raise PeerRecordError("peer endpoint resolved to no addresses")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value.split("%", 1)[0])
        except ValueError as exc:
            raise PeerRecordError(
                "peer endpoint resolved to an invalid address"
            ) from exc
        if not allow_private_network and not address.is_global:
            raise PeerRecordError("peer endpoint resolved to a non-public address")
        if address.is_unspecified or address.is_multicast:
            raise PeerRecordError("peer endpoint resolved to an unusable address")
    return ResolvedPeerEndpoint(host=host, port=port, addresses=tuple(addresses))


def _time_fields(issued_at: int, expires_at: int, now: int) -> None:
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= issued_at
        or expires_at - issued_at > PEER_RECORD_TTL_MAX
    ):
        raise PeerRecordError("invalid peer record lifetime")
    if issued_at > now + PEER_RECORD_FUTURE_SKEW:
        raise PeerRecordError("peer record issue time is in the future")
    if expires_at <= now:
        raise PeerRecordError("peer record expired")


def _record_payload(
    *,
    scope: str,
    peer_id: str,
    public_key: str,
    session_id: str,
    revision: int,
    issued_at: int,
    expires_at: int,
    endpoints: tuple[str, ...],
    roles: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "v": PEER_RECORD_VERSION,
        "scope": scope,
        "peer_id": peer_id,
        "public_key": public_key,
        "session_id": session_id,
        "revision": revision,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "endpoints": list(endpoints),
        "roles": list(roles),
    }


@dataclass(frozen=True)
class PeerRecord:
    scope: str
    peer_id: str
    public_key: str
    session_id: str
    revision: int
    issued_at: int
    expires_at: int
    endpoints: tuple[str, ...]
    roles: tuple[str, ...]
    signature: str

    @classmethod
    def issue(
        cls,
        signing_key: SigningKey,
        *,
        scope: str,
        revision: int,
        endpoints: tuple[str, ...] | list[str],
        roles: tuple[str, ...] | list[str],
        session_id: str = "",
        issued_at: int | None = None,
        expires_at: int | None = None,
    ) -> "PeerRecord":
        now = int(time.time()) if issued_at is None else issued_at
        expiry = now + PEER_RECORD_TTL_MAX if expires_at is None else expires_at
        public_key = signing_key.verify_key.encode().hex()
        peer_id = peer_id_for_key(public_key)
        record = cls(
            scope=scope,
            peer_id=peer_id,
            public_key=public_key,
            session_id=session_id,
            revision=revision,
            issued_at=now,
            expires_at=expiry,
            endpoints=tuple(sorted(endpoints)),
            roles=tuple(sorted(roles)),
            signature="",
        )
        record._validate(now=now)
        signature = signing_key.sign(rfc8785.dumps(record._payload())).signature.hex()
        signed = cls(**{**record.__dict__, "signature": signature})
        if len(rfc8785.dumps(signed.to_wire())) > PEER_RECORD_MAX_BYTES:
            raise PeerRecordError("peer record exceeds size limit")
        return signed

    @classmethod
    def from_wire(
        cls, value: dict[str, Any], *, now: int | None = None
    ) -> "PeerRecord":
        expected = {
            "v",
            "scope",
            "peer_id",
            "public_key",
            "session_id",
            "revision",
            "issued_at",
            "expires_at",
            "endpoints",
            "roles",
            "signature",
        }
        if not isinstance(value, dict) or not value.keys() >= expected:
            raise PeerRecordError("invalid peer record fields")
        if type(value["v"]) is not int or value["v"] != PEER_RECORD_VERSION:
            raise PeerRecordError("unsupported peer record version")
        endpoints = value["endpoints"]
        roles = value["roles"]
        if not isinstance(endpoints, list) or not all(
            isinstance(v, str) for v in endpoints
        ):
            raise PeerRecordError("invalid peer endpoints")
        if not isinstance(roles, list) or not all(isinstance(v, str) for v in roles):
            raise PeerRecordError("invalid peer roles")
        record = cls(
            scope=value["scope"],
            peer_id=value["peer_id"],
            public_key=value["public_key"],
            session_id=value["session_id"],
            revision=value["revision"],
            issued_at=value["issued_at"],
            expires_at=value["expires_at"],
            endpoints=tuple(endpoints),
            roles=tuple(roles),
            signature=value["signature"],
        )
        record.verify(now=now)
        if len(rfc8785.dumps(record.to_wire())) > PEER_RECORD_MAX_BYTES:
            raise PeerRecordError("peer record exceeds size limit")
        return record

    def _payload(self) -> dict[str, Any]:
        return _record_payload(
            scope=self.scope,
            peer_id=self.peer_id,
            public_key=self.public_key,
            session_id=self.session_id,
            revision=self.revision,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            endpoints=self.endpoints,
            roles=self.roles,
        )

    def _validate(self, *, now: int) -> None:
        _validate_scope(self.scope)
        if not isinstance(self.public_key, str) or not _HEX_32.fullmatch(
            self.public_key
        ):
            raise PeerRecordError("invalid peer public key")
        if self.peer_id != peer_id_for_key(self.public_key):
            raise PeerRecordError("peer id does not match public key")
        if self.session_id and not re.fullmatch(r"[0-9a-f]{32}", self.session_id):
            raise PeerRecordError("invalid peer registration session")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise PeerRecordError("invalid peer record revision")
        _time_fields(self.issued_at, self.expires_at, now)
        if (
            not self.endpoints
            or len(self.endpoints) > PEER_RECORD_MAX_ENDPOINTS
            or tuple(sorted(set(self.endpoints))) != self.endpoints
        ):
            raise PeerRecordError("invalid peer endpoints")
        for endpoint in self.endpoints:
            _validate_endpoint(endpoint)
        if (
            len(self.roles) > PEER_RECORD_MAX_ROLES
            or tuple(sorted(set(self.roles))) != self.roles
            or any(not _ROLE.fullmatch(role) for role in self.roles)
        ):
            raise PeerRecordError("invalid peer roles")

    def verify(self, *, now: int | None = None) -> None:
        current = int(time.time()) if now is None else now
        self._validate(now=current)
        if not isinstance(self.signature, str) or not _SIGNATURE.fullmatch(
            self.signature
        ):
            raise PeerRecordError("invalid peer signature")
        try:
            VerifyKey(bytes.fromhex(self.public_key)).verify(
                rfc8785.dumps(self._payload()), bytes.fromhex(self.signature)
            )
        except (BadSignatureError, ValueError) as exc:
            raise PeerRecordError("peer record signature verification failed") from exc

    def to_wire(self) -> dict[str, Any]:
        return {**self._payload(), "signature": self.signature}


class InMemoryPeerRecordStore:
    """Bounded candidate cache with revision pins and local revocation tombstones.

    Candidates are not trusted or routable until an authenticated bilateral
    link is installed. This process-local store is intentionally not presented
    as restart-safe trust state.
    """

    def __init__(
        self, *, max_records: int = PEER_STORE_MAX_RECORDS, scope: str | None = None
    ):
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or not 1 <= max_records <= PEER_STORE_MAX_RECORDS
        ):
            raise ValueError("max_records must be between 1 and the protocol limit")
        if scope is not None:
            _validate_scope(scope)
        self._max_records = max_records
        self._scope = scope
        self._records: dict[tuple[str, str], PeerRecord] = {}
        self._highest_revision: dict[tuple[str, str], int] = {}
        self._revision_retain_until: dict[tuple[str, str], int] = {}
        self._revoked: set[tuple[str, str]] = set()

    def _prune(self, now: int) -> None:
        for key, record in list(self._records.items()):
            if record.expires_at <= now:
                self._records.pop(key, None)
        for key, retain_until in list(self._revision_retain_until.items()):
            if retain_until <= now and key not in self._revoked:
                self._revision_retain_until.pop(key, None)
                self._highest_revision.pop(key, None)

    def accept(
        self, value: PeerRecord | dict[str, Any], *, now: int | None = None
    ) -> bool:
        record = (
            value
            if isinstance(value, PeerRecord)
            else PeerRecord.from_wire(value, now=now)
        )
        record.verify(now=now)
        current = int(time.time()) if now is None else now
        self._prune(current)
        if self._scope is not None and record.scope != self._scope:
            raise PeerRecordError("peer record scope mismatch")
        key = (record.scope, record.peer_id)
        if key in self._revoked:
            raise PeerRecordError("peer is locally revoked")
        previous_revision = self._highest_revision.get(key)
        if previous_revision is not None:
            if record.revision < previous_revision:
                raise PeerRecordError("peer record revision rollback")
            if record.revision == previous_revision:
                previous = self._records.get(key)
                if previous is None or previous.to_wire() != record.to_wire():
                    raise PeerRecordError("peer record revision equivocation")
                return False
        elif len(self._highest_revision) >= self._max_records:
            raise PeerRecordError("peer record capacity is full")
        self._highest_revision[key] = record.revision
        self._revision_retain_until[key] = max(
            self._revision_retain_until.get(key, 0), record.expires_at
        )
        self._records[key] = record
        return True

    def get(
        self, peer_id: str, *, scope: str | None = None, now: int | None = None
    ) -> PeerRecord | None:
        current = int(time.time()) if now is None else now
        self._prune(current)
        if scope is not None:
            return self._records.get((scope, peer_id))
        matches = [
            record
            for (record_scope, record_peer_id), record in self._records.items()
            if record_peer_id == peer_id
        ]
        if len(matches) != 1:
            return None
        return matches[0]

    def list(
        self, *, scope: str | None = None, now: int | None = None
    ) -> tuple[PeerRecord, ...]:
        current = int(time.time()) if now is None else now
        self._prune(current)
        return tuple(
            record
            for (record_scope, _), record in sorted(self._records.items())
            if (scope is None or record_scope == scope) and record.expires_at > current
        )

    def revoke(self, peer_id: str, *, scope: str | None = None) -> None:
        matching = [
            key
            for key in self._highest_revision
            if key[1] == peer_id and (scope is None or key[0] == scope)
        ]
        if not matching:
            selected_scope = scope or self._scope
            if selected_scope is None or not _PEER_ID.fullmatch(peer_id):
                raise PeerRecordError("cannot revoke an unknown peer without a scope")
            matching = [(selected_scope, peer_id)]
            if (
                len(self._revoked) >= self._max_records
                and matching[0] not in self._revoked
            ):
                raise PeerRecordError("peer revocation capacity is full")
        self._revoked.update(matching)
        for key in matching:
            self._records.pop(key, None)

    def is_revoked(self, peer_id: str, *, scope: str | None = None) -> bool:
        return any(
            key[1] == peer_id and (scope is None or key[0] == scope)
            for key in self._revoked
        )

    def __len__(self) -> int:
        return len(self._records)


class SQLitePeerRecordStore:
    """Durable, bounded peer candidate store with revision and revoke pins.

    The database contains public signed records only. Keep it in Kollab's
    private state directory; this store does not make self-signed records
    trusted or grant communication/tool permission.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = PEER_STORE_MAX_RECORDS,
        scope: str | None = None,
    ):
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or not 1 <= max_records <= PEER_STORE_MAX_RECORDS
        ):
            raise ValueError("max_records must be between 1 and the protocol limit")
        if scope is not None:
            _validate_scope(scope)
        self._scope = scope
        self._max_records = max_records
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS peer_records (
                scope TEXT NOT NULL,
                peer_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                retain_until INTEGER NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
                wire_json TEXT,
                PRIMARY KEY (scope, peer_id),
                CHECK ((revoked = 1 AND wire_json IS NULL) OR
                       (revoked = 0 AND wire_json IS NOT NULL))
            )
            """)
        try:
            Path(path).chmod(0o600)
        except (OSError, TypeError):
            # SQLite may use URI paths or platform-specific permission models.
            # Callers still own private-state directory permissions.
            pass

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _prune_locked(self, now: int) -> None:
        self._connection.execute(
            "DELETE FROM peer_records WHERE revoked = 0 AND retain_until <= ?",
            (now,),
        )

    def accept(
        self, value: PeerRecord | dict[str, Any], *, now: int | None = None
    ) -> bool:
        record = (
            value
            if isinstance(value, PeerRecord)
            else PeerRecord.from_wire(value, now=now)
        )
        record.verify(now=now)
        current = int(time.time()) if now is None else now
        if self._scope is not None and record.scope != self._scope:
            raise PeerRecordError("peer record scope mismatch")
        key = (record.scope, record.peer_id)
        encoded = rfc8785.dumps(record.to_wire()).decode("utf-8")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(current)
                previous = self._connection.execute(
                    "SELECT revision, retain_until, revoked, wire_json "
                    "FROM peer_records WHERE scope = ? AND peer_id = ?",
                    key,
                ).fetchone()
                if previous is not None:
                    previous_revision, retain_until, revoked, wire_json = previous
                    if revoked:
                        raise PeerRecordError("peer is locally revoked")
                    if record.revision < previous_revision:
                        raise PeerRecordError("peer record revision rollback")
                    if record.revision == previous_revision:
                        if wire_json != encoded:
                            raise PeerRecordError("peer record revision equivocation")
                        self._connection.execute("COMMIT")
                        return False
                    retained = max(retain_until, record.expires_at)
                    self._connection.execute(
                        "UPDATE peer_records SET revision = ?, expires_at = ?, "
                        "retain_until = ?, wire_json = ? "
                        "WHERE scope = ? AND peer_id = ?",
                        (
                            record.revision,
                            record.expires_at,
                            retained,
                            encoded,
                            *key,
                        ),
                    )
                else:
                    count = self._connection.execute(
                        "SELECT COUNT(*) FROM peer_records"
                    ).fetchone()[0]
                    if count >= self._max_records:
                        raise PeerRecordError("peer record capacity is full")
                    self._connection.execute(
                        "INSERT INTO peer_records "
                        "(scope, peer_id, revision, expires_at, retain_until, wire_json) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            *key,
                            record.revision,
                            record.expires_at,
                            record.expires_at,
                            encoded,
                        ),
                    )
                self._connection.execute("COMMIT")
                return True
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def get(
        self, peer_id: str, *, scope: str | None = None, now: int | None = None
    ) -> PeerRecord | None:
        current = int(time.time()) if now is None else now
        selected_scope = scope or self._scope
        if selected_scope is not None:
            keys = [(selected_scope, peer_id)]
        else:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT scope FROM peer_records WHERE peer_id = ? "
                    "AND revoked = 0 AND expires_at > ?",
                    (peer_id, current),
                ).fetchall()
            if len(rows) != 1:
                return None
            keys = [(rows[0][0], peer_id)]
        with self._lock:
            row = self._connection.execute(
                "SELECT wire_json FROM peer_records WHERE scope = ? AND peer_id = ? "
                "AND revoked = 0 AND expires_at > ?",
                (*keys[0], current),
            ).fetchone()
        if row is None:
            return None
        try:
            wire = json.loads(row[0])
            return PeerRecord.from_wire(wire, now=current)
        except (json.JSONDecodeError, PeerRecordError) as exc:
            raise PeerRecordError("stored peer record is corrupt") from exc

    def list(
        self, *, scope: str | None = None, now: int | None = None
    ) -> tuple[PeerRecord, ...]:
        current = int(time.time()) if now is None else now
        selected_scope = scope or self._scope
        with self._lock:
            self._prune_locked(current)
            if selected_scope is None:
                rows = self._connection.execute(
                    "SELECT wire_json FROM peer_records WHERE revoked = 0 "
                    "AND expires_at > ? ORDER BY scope, peer_id",
                    (current,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT wire_json FROM peer_records WHERE scope = ? "
                    "AND revoked = 0 AND expires_at > ? ORDER BY peer_id",
                    (selected_scope, current),
                ).fetchall()
        result = []
        for (encoded,) in rows:
            try:
                result.append(PeerRecord.from_wire(json.loads(encoded), now=current))
            except (json.JSONDecodeError, PeerRecordError) as exc:
                raise PeerRecordError("stored peer record is corrupt") from exc
        return tuple(result)

    def next_revision(
        self, peer_id: str, *, scope: str | None = None, now: int | None = None
    ) -> int:
        """Return the next durable revision for a local signed record."""
        if not _PEER_ID.fullmatch(peer_id):
            raise PeerRecordError("invalid peer identity")
        selected_scope = scope or self._scope
        if selected_scope is None:
            raise PeerRecordError("peer record revision requires a scope")
        _validate_scope(selected_scope)
        current = int(time.time()) if now is None else now
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(current)
                row = self._connection.execute(
                    "SELECT revision, revoked FROM peer_records "
                    "WHERE scope = ? AND peer_id = ?",
                    (selected_scope, peer_id),
                ).fetchone()
                if row is None:
                    result = 1
                elif row[1]:
                    raise PeerRecordError("peer is locally revoked")
                else:
                    result = int(row[0]) + 1
                self._connection.execute("COMMIT")
                return result
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def revoke(self, peer_id: str, *, scope: str | None = None) -> None:
        selected_scope = scope or self._scope
        if selected_scope is not None:
            _validate_scope(selected_scope)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if selected_scope is None:
                    rows = self._connection.execute(
                        "SELECT scope FROM peer_records WHERE peer_id = ?",
                        (peer_id,),
                    ).fetchall()
                    matches = [row[0] for row in rows]
                    if not matches:
                        raise PeerRecordError(
                            "cannot revoke an unknown peer without a scope"
                        )
                else:
                    matches = [selected_scope]
                for item_scope in matches:
                    if not _PEER_ID.fullmatch(peer_id):
                        raise PeerRecordError("invalid peer identity")
                    exists = self._connection.execute(
                        "SELECT 1 FROM peer_records WHERE scope = ? AND peer_id = ?",
                        (item_scope, peer_id),
                    ).fetchone()
                    if exists is None:
                        count = self._connection.execute(
                            "SELECT COUNT(*) FROM peer_records"
                        ).fetchone()[0]
                        if count >= self._max_records:
                            raise PeerRecordError("peer revocation capacity is full")
                        self._connection.execute(
                            "INSERT INTO peer_records "
                            "(scope, peer_id, revision, expires_at, retain_until, "
                            "revoked, wire_json) VALUES (?, ?, 0, 0, 0, 1, NULL)",
                            (item_scope, peer_id),
                        )
                    else:
                        self._connection.execute(
                            "UPDATE peer_records SET revoked = 1, wire_json = NULL "
                            "WHERE scope = ? AND peer_id = ?",
                            (item_scope, peer_id),
                        )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def is_revoked(self, peer_id: str, *, scope: str | None = None) -> bool:
        with self._lock:
            if scope is not None:
                row = self._connection.execute(
                    "SELECT 1 FROM peer_records WHERE scope = ? AND peer_id = ? "
                    "AND revoked = 1",
                    (scope, peer_id),
                ).fetchone()
            elif self._scope is not None:
                row = self._connection.execute(
                    "SELECT 1 FROM peer_records WHERE scope = ? AND peer_id = ? "
                    "AND revoked = 1",
                    (self._scope, peer_id),
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT 1 FROM peer_records WHERE peer_id = ? AND revoked = 1",
                    (peer_id,),
                ).fetchone()
        return row is not None

    def __len__(self) -> int:
        with self._lock:
            return self._connection.execute(
                "SELECT COUNT(*) FROM peer_records WHERE revoked = 0 "
                "AND expires_at > ?",
                (int(time.time()),),
            ).fetchone()[0]
