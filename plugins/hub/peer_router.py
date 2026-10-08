"""Authenticated peer links, end-to-end envelopes, and bounded route choice.

This protocol layer is transport-neutral and not yet connected to Hub runtime
or discovery. Callers must install a link only after the corresponding live
peer session authenticated both endpoints and the human's forwarding policy
allows it. Routing never grants permission to start a conversation or use tools.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Awaitable, Callable

import rfc8785
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .peer_records import (
    PEER_RECORD_TTL_MAX,
    InMemoryPeerRecordStore,
    PeerRecord,
    PeerRecordError,
    SQLitePeerRecordStore,
    peer_id_for_key,
)

PEER_LINK_TTL_MAX = 300
PEER_LINK_MAX_BYTES = 2048
PEER_ROUTER_MAX_LINKS = 1024
PEER_ROUTER_MAX_HOPS = 8
PEER_ROUTER_MAX_PATHS = 3
PEER_ROUTER_SEARCH_BUDGET = 4096
PEER_ENVELOPE_MAX_BYTES = 40 * 1024
PEER_REPLAY_CACHE_MAX = 4096
PEER_LINK_REVOCATION_MAX = 1024
PEER_ROUTE_ATTEMPT_TIMEOUT_MAX = 30.0
PEER_WIRE_FRAME_MAX_BYTES = 64 * 1024
_HEX_32 = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
_MESSAGE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PEER_ID = re.compile(r"kollab-peer:ed25519:[0-9a-f]{64}\Z")


class PeerRouteError(ValueError):
    """No valid route or the mesh frame failed its bounds or signature."""


class TransientPeerDeliveryError(Exception):
    """A route may be retried after a transient delivery failure."""


def _validate_lifetime(issued_at: int, expires_at: int, now: int, maximum: int) -> None:
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= issued_at
        or expires_at - issued_at > maximum
    ):
        raise PeerRouteError("invalid signed frame lifetime")
    if issued_at > now + 30:
        raise PeerRouteError("signed frame issue time is in the future")
    if expires_at <= now:
        raise PeerRouteError("signed frame expired")


def _link_payload(
    scope: str,
    left: str,
    right: str,
    session_id: str,
    revision: int,
    issued_at: int,
    expires_at: int,
    forwarding_allowed: bool,
) -> dict[str, Any]:
    return {
        "v": 1,
        "scope": scope,
        "left": left,
        "right": right,
        "session_id": session_id,
        "revision": revision,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "forwarding_allowed": forwarding_allowed,
    }


@dataclass(frozen=True)
class PeerLink:
    """A short-lived pair-signed assertion that two peers shared a session."""

    scope: str
    left: str
    right: str
    session_id: str
    revision: int
    issued_at: int
    expires_at: int
    forwarding_allowed: bool
    left_signature: str
    right_signature: str

    @staticmethod
    def signing_payload(
        *,
        scope: str,
        peer_a: str,
        peer_b: str,
        session_id: str,
        revision: int = 1,
        issued_at: int,
        expires_at: int,
        forwarding_allowed: bool = False,
    ) -> dict[str, Any]:
        if peer_a == peer_b:
            raise PeerRouteError("peer link must have two distinct peers")
        if type(forwarding_allowed) is not bool:
            raise PeerRouteError("forwarding consent must be explicit")
        left, right = sorted((peer_a, peer_b))
        return _link_payload(
            scope,
            left,
            right,
            session_id,
            revision,
            issued_at,
            expires_at,
            forwarding_allowed,
        )

    @classmethod
    def from_signatures(
        cls,
        *,
        scope: str,
        peer_a: str,
        peer_b: str,
        session_id: str,
        revision: int = 1,
        issued_at: int,
        expires_at: int,
        signatures: dict[str, str],
        forwarding_allowed: bool = False,
    ) -> "PeerLink":
        payload = cls.signing_payload(
            scope=scope,
            peer_a=peer_a,
            peer_b=peer_b,
            session_id=session_id,
            revision=revision,
            issued_at=issued_at,
            expires_at=expires_at,
            forwarding_allowed=forwarding_allowed,
        )
        left, right = payload["left"], payload["right"]
        if set(signatures) != {left, right}:
            raise PeerRouteError("peer link requires both endpoint signatures")
        return cls(
            scope=scope,
            left=left,
            right=right,
            session_id=session_id,
            revision=revision,
            issued_at=issued_at,
            expires_at=expires_at,
            forwarding_allowed=forwarding_allowed,
            left_signature=signatures[left],
            right_signature=signatures[right],
        )

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> "PeerLink":
        expected = {
            "v",
            "scope",
            "left",
            "right",
            "session_id",
            "revision",
            "issued_at",
            "expires_at",
            "forwarding_allowed",
            "left_signature",
            "right_signature",
        }
        if not isinstance(value, dict) or not value.keys() >= expected:
            raise PeerRouteError("invalid peer link fields")
        if type(value["v"]) is not int or value["v"] != 1:
            raise PeerRouteError("unsupported peer link version")
        if type(value["forwarding_allowed"]) is not bool:
            raise PeerRouteError("forwarding consent must be explicit")
        link = cls(
            scope=value["scope"],
            left=value["left"],
            right=value["right"],
            session_id=value["session_id"],
            revision=value["revision"],
            issued_at=value["issued_at"],
            expires_at=value["expires_at"],
            forwarding_allowed=value["forwarding_allowed"],
            left_signature=value["left_signature"],
            right_signature=value["right_signature"],
        )
        if len(rfc8785.dumps(link.to_wire())) > PEER_LINK_MAX_BYTES:
            raise PeerRouteError("peer link exceeds size limit")
        return link

    @staticmethod
    def sign_statement(
        signing_key: SigningKey, payload: dict[str, Any]
    ) -> tuple[str, str]:
        peer_id = peer_id_for_key(signing_key.verify_key.encode())
        return peer_id, signing_key.sign(rfc8785.dumps(payload)).signature.hex()

    @property
    def link_id(self) -> str:
        return hashlib.sha256(rfc8785.dumps(self._payload())).hexdigest()

    def _payload(self) -> dict[str, Any]:
        return _link_payload(
            self.scope,
            self.left,
            self.right,
            self.session_id,
            self.revision,
            self.issued_at,
            self.expires_at,
            self.forwarding_allowed,
        )

    def verify(
        self,
        left_record: PeerRecord,
        right_record: PeerRecord,
        *,
        expected_scope: str,
        expected_session_id: str | None = None,
        now: int | None = None,
    ) -> None:
        current = int(time.time()) if now is None else now
        if (
            self.scope != expected_scope
            or left_record.scope != self.scope
            or right_record.scope != self.scope
        ):
            raise PeerRouteError("peer link scope mismatch")
        if (
            not isinstance(self.scope, str)
            or not _SCOPE.fullmatch(self.scope)
            or not isinstance(self.left, str)
            or not _PEER_ID.fullmatch(self.left)
            or not isinstance(self.right, str)
            or not _PEER_ID.fullmatch(self.right)
        ):
            raise PeerRouteError("invalid peer link identity or scope")
        if (left_record.peer_id, right_record.peer_id) != (self.left, self.right):
            raise PeerRouteError("peer link identities do not match records")
        if self.left >= self.right:
            raise PeerRouteError("peer link identity order is not canonical")
        if not isinstance(self.session_id, str) or not _HEX_32.fullmatch(
            self.session_id
        ):
            raise PeerRouteError("invalid authenticated session transcript id")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise PeerRouteError("invalid peer link revision")
        if type(self.forwarding_allowed) is not bool:
            raise PeerRouteError("forwarding consent must be explicit")
        if expected_session_id is not None and self.session_id != expected_session_id:
            raise PeerRouteError("peer link does not bind the authenticated session")
        _validate_lifetime(self.issued_at, self.expires_at, current, PEER_LINK_TTL_MAX)
        left_record.verify(now=current)
        right_record.verify(now=current)
        payload = rfc8785.dumps(self._payload())
        for record, signature in (
            (left_record, self.left_signature),
            (right_record, self.right_signature),
        ):
            if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
                raise PeerRouteError("invalid peer link signature")
            try:
                VerifyKey(bytes.fromhex(record.public_key)).verify(
                    payload, bytes.fromhex(signature)
                )
            except (BadSignatureError, ValueError) as exc:
                raise PeerRouteError("peer link signature verification failed") from exc

    def to_wire(self) -> dict[str, Any]:
        return {
            **self._payload(),
            "left_signature": self.left_signature,
            "right_signature": self.right_signature,
        }


class SQLitePeerLinkStore:
    """Persist per-edge link revisions so consent withdrawals survive restart."""

    def __init__(self, path: str | Path, *, max_links: int = PEER_ROUTER_MAX_LINKS):
        if isinstance(max_links, bool) or not 1 <= max_links <= PEER_ROUTER_MAX_LINKS:
            raise ValueError("max_links exceeds protocol limit")
        self._max_links = max_links
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS peer_link_state (
                scope TEXT NOT NULL,
                left_peer TEXT NOT NULL,
                right_peer TEXT NOT NULL,
                revision INTEGER NOT NULL,
                digest TEXT NOT NULL,
                retain_until INTEGER NOT NULL,
                PRIMARY KEY (scope, left_peer, right_peer)
            )
            """)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS peer_link_revocations (
                scope TEXT NOT NULL,
                peer_id TEXT NOT NULL,
                revoked_at INTEGER NOT NULL,
                PRIMARY KEY (scope, peer_id)
            )
            """)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS peer_link_wire (
                scope TEXT NOT NULL,
                left_peer TEXT NOT NULL,
                right_peer TEXT NOT NULL,
                revision INTEGER NOT NULL,
                wire_json TEXT NOT NULL,
                PRIMARY KEY (scope, left_peer, right_peer)
            )
            """)
        try:
            Path(path).chmod(0o600)
        except (OSError, TypeError):
            pass

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def prune(self, now: int) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM peer_link_state WHERE retain_until <= ?", (now,)
            )
            self._connection.execute(
                "DELETE FROM peer_link_wire WHERE NOT EXISTS ("
                "SELECT 1 FROM peer_link_state AS state WHERE "
                "state.scope = peer_link_wire.scope AND "
                "state.left_peer = peer_link_wire.left_peer AND "
                "state.right_peer = peer_link_wire.right_peer)"
            )

    def next_revision(self, scope: str, peer_a: str, peer_b: str) -> int:
        edge = (scope, *sorted((peer_a, peer_b)))
        with self._lock:
            row = self._connection.execute(
                "SELECT revision FROM peer_link_state WHERE scope = ? "
                "AND left_peer = ? AND right_peer = ?",
                edge,
            ).fetchone()
        return 1 if row is None else int(row[0]) + 1

    def list_links(self, scope: str, *, now: int | None = None) -> tuple[PeerLink, ...]:
        current = int(time.time()) if now is None else now
        self.prune(current)
        with self._lock:
            rows = self._connection.execute(
                "SELECT wire_json FROM peer_link_wire WHERE scope = ? "
                "ORDER BY left_peer, right_peer",
                (scope,),
            ).fetchall()
        links = []
        for (encoded,) in rows:
            try:
                link = PeerLink.from_wire(json.loads(encoded))
            except (json.JSONDecodeError, PeerRouteError) as exc:
                raise PeerRouteError("stored peer link is corrupt") from exc
            if link.expires_at > current:
                links.append(link)
        return tuple(links)

    def accept(
        self,
        link: PeerLink,
        *,
        now: int | None = None,
        max_links: int | None = None,
    ) -> bool:
        current = int(time.time()) if now is None else now
        if max_links is not None and (
            isinstance(max_links, bool)
            or not isinstance(max_links, int)
            or max_links < 1
        ):
            raise ValueError("max_links must be positive")
        capacity = (
            self._max_links if max_links is None else min(self._max_links, max_links)
        )
        edge = (link.scope, link.left, link.right)
        digest = link.link_id
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM peer_link_state WHERE retain_until <= ?", (current,)
                )
                revoked = connection.execute(
                    "SELECT peer_id FROM peer_link_revocations WHERE scope = ? "
                    "AND peer_id IN (?, ?)",
                    (link.scope, link.left, link.right),
                ).fetchone()
                if revoked is not None:
                    raise PeerRouteError("peer is locally revoked")
                previous = connection.execute(
                    "SELECT revision, digest, retain_until FROM peer_link_state "
                    "WHERE scope = ? AND left_peer = ? AND right_peer = ?",
                    edge,
                ).fetchone()
                if previous is not None:
                    previous_revision, previous_digest, retain_until = previous
                    if link.revision < previous_revision:
                        raise PeerRouteError("peer link revision rollback")
                    if link.revision == previous_revision:
                        if digest != previous_digest:
                            raise PeerRouteError("peer link revision equivocation")
                        connection.execute(
                            "INSERT OR REPLACE INTO peer_link_wire "
                            "(scope, left_peer, right_peer, revision, wire_json) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (*edge, link.revision, rfc8785.dumps(link.to_wire()).decode()),
                        )
                        connection.execute("COMMIT")
                        return False
                    retained = max(retain_until, link.expires_at)
                    connection.execute(
                        "UPDATE peer_link_state SET revision = ?, digest = ?, "
                        "retain_until = ? WHERE scope = ? AND left_peer = ? "
                        "AND right_peer = ?",
                        (link.revision, digest, retained, *edge),
                    )
                else:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM peer_link_state"
                    ).fetchone()[0]
                    if count >= capacity:
                        raise PeerRouteError("peer link capacity is full")
                    connection.execute(
                        "INSERT INTO peer_link_state "
                        "(scope, left_peer, right_peer, revision, digest, retain_until) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (*edge, link.revision, digest, link.expires_at),
                    )
                connection.execute(
                    "INSERT OR REPLACE INTO peer_link_wire "
                    "(scope, left_peer, right_peer, revision, wire_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (*edge, link.revision, rfc8785.dumps(link.to_wire()).decode()),
                )
                connection.execute("COMMIT")
                return True
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def revoke_peer(self, scope: str, peer_id: str, *, now: int | None = None) -> None:
        if not _PEER_ID.fullmatch(peer_id):
            raise PeerRouteError("invalid peer identity")
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                exists = connection.execute(
                    "SELECT 1 FROM peer_link_revocations WHERE scope = ? "
                    "AND peer_id = ?",
                    (scope, peer_id),
                ).fetchone()
                if exists is None:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM peer_link_revocations"
                    ).fetchone()[0]
                    if count >= PEER_LINK_REVOCATION_MAX:
                        raise PeerRouteError("peer link revocation capacity is full")
                    connection.execute(
                        "INSERT INTO peer_link_revocations (scope, peer_id, revoked_at) "
                        "VALUES (?, ?, ?)",
                        (scope, peer_id, int(time.time()) if now is None else now),
                    )
                connection.execute(
                    "DELETE FROM peer_link_state WHERE scope = ? "
                    "AND (left_peer = ? OR right_peer = ?)",
                    (scope, peer_id, peer_id),
                )
                connection.execute(
                    "DELETE FROM peer_link_wire WHERE scope = ? "
                    "AND (left_peer = ? OR right_peer = ?)",
                    (scope, peer_id, peer_id),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise


@dataclass(frozen=True)
class ForwardEnvelope:
    """Immutable origin-signed header plus opaque recipient ciphertext."""

    scope: str
    origin: str
    destination: str
    message_id: str
    issued_at: int
    expires_at: int
    ciphertext: bytes
    ciphertext_digest: str
    signature: str

    @classmethod
    def create(
        cls,
        signing_key: SigningKey,
        *,
        scope: str,
        destination: str,
        message_id: str,
        ciphertext: bytes,
        issued_at: int | None = None,
        expires_at: int | None = None,
    ) -> "ForwardEnvelope":
        current = int(time.time()) if issued_at is None else issued_at
        expires = current + PEER_RECORD_TTL_MAX if expires_at is None else expires_at
        if (
            not isinstance(ciphertext, bytes)
            or not 1 <= len(ciphertext) <= PEER_ENVELOPE_MAX_BYTES
        ):
            raise PeerRouteError("invalid encrypted payload size")
        origin = peer_id_for_key(signing_key.verify_key.encode())
        payload = {
            "v": 1,
            "scope": scope,
            "origin": origin,
            "destination": destination,
            "message_id": message_id,
            "issued_at": current,
            "expires_at": expires,
            "ciphertext_digest": hashlib.sha256(ciphertext).hexdigest(),
        }
        envelope = cls(
            scope=scope,
            origin=origin,
            destination=destination,
            message_id=message_id,
            issued_at=current,
            expires_at=expires,
            ciphertext=ciphertext,
            ciphertext_digest=payload["ciphertext_digest"],
            signature=signing_key.sign(rfc8785.dumps(payload)).signature.hex(),
        )
        envelope.verify(signing_key.verify_key.encode().hex(), now=current)
        return envelope

    @classmethod
    def from_wire(
        cls,
        value: dict[str, Any],
        *,
        origin_public_key: str,
        now: int | None = None,
    ) -> "ForwardEnvelope":
        expected = {
            "v",
            "scope",
            "origin",
            "destination",
            "message_id",
            "issued_at",
            "expires_at",
            "ciphertext_digest",
            "ciphertext",
            "signature",
        }
        if not isinstance(value, dict) or not value.keys() >= expected:
            raise PeerRouteError("invalid forwarding envelope fields")
        if len(rfc8785.dumps(value)) > PEER_WIRE_FRAME_MAX_BYTES:
            raise PeerRouteError("forwarding envelope exceeds frame limit")
        if type(value["v"]) is not int or value["v"] != 1:
            raise PeerRouteError("unsupported forwarding envelope version")
        encoded = value["ciphertext"]
        if not isinstance(encoded, str) or len(encoded) > 4 * (
            (PEER_ENVELOPE_MAX_BYTES + 2) // 3
        ):
            raise PeerRouteError("invalid encrypted payload encoding")
        try:
            raw = encoded.encode("ascii")
            ciphertext = base64.b64decode(
                raw + b"=" * (-len(raw) % 4), altchars=b"-_", validate=True
            )
            if (
                base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("=")
                != encoded
            ):
                raise ValueError("non-canonical base64")
        except (ValueError, UnicodeError) as exc:
            raise PeerRouteError("invalid encrypted payload encoding") from exc
        envelope = cls(
            scope=value["scope"],
            origin=value["origin"],
            destination=value["destination"],
            message_id=value["message_id"],
            issued_at=value["issued_at"],
            expires_at=value["expires_at"],
            ciphertext=ciphertext,
            ciphertext_digest=value["ciphertext_digest"],
            signature=value["signature"],
        )
        envelope.verify(origin_public_key, now=now)
        if len(rfc8785.dumps(envelope.to_wire())) > PEER_WIRE_FRAME_MAX_BYTES:
            raise PeerRouteError("forwarding envelope exceeds frame limit")
        return envelope

    def _payload(self) -> dict[str, Any]:
        return {
            "v": 1,
            "scope": self.scope,
            "origin": self.origin,
            "destination": self.destination,
            "message_id": self.message_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "ciphertext_digest": self.ciphertext_digest,
        }

    def verify(self, origin_public_key: str, *, now: int | None = None) -> None:
        current = int(time.time()) if now is None else now
        try:
            key = bytes.fromhex(origin_public_key)
        except (TypeError, ValueError) as exc:
            raise PeerRouteError("invalid envelope origin key") from exc
        if len(key) != 32 or self.origin != peer_id_for_key(key):
            raise PeerRouteError("envelope origin identity mismatch")
        if not isinstance(self.scope, str) or not _SCOPE.fullmatch(self.scope):
            raise PeerRouteError("invalid envelope scope")
        if not isinstance(self.destination, str) or not _PEER_ID.fullmatch(
            self.destination
        ):
            raise PeerRouteError("invalid envelope destination")
        if (
            not isinstance(self.message_id, str)
            or len(self.message_id) < 16
            or not _MESSAGE_ID.fullmatch(self.message_id)
        ):
            raise PeerRouteError("invalid envelope message id")
        if (
            not isinstance(self.ciphertext, bytes)
            or not 1 <= len(self.ciphertext) <= PEER_ENVELOPE_MAX_BYTES
        ):
            raise PeerRouteError("invalid encrypted payload size")
        _validate_lifetime(
            self.issued_at, self.expires_at, current, PEER_RECORD_TTL_MAX
        )
        digest = hashlib.sha256(self.ciphertext).hexdigest()
        if self.ciphertext_digest != digest or not _HEX_32.fullmatch(
            self.ciphertext_digest
        ):
            raise PeerRouteError("encrypted payload digest mismatch")
        if not isinstance(self.signature, str) or not _SIGNATURE.fullmatch(
            self.signature
        ):
            raise PeerRouteError("invalid envelope signature")
        try:
            VerifyKey(key).verify(
                rfc8785.dumps(self._payload()), bytes.fromhex(self.signature)
            )
        except (BadSignatureError, ValueError) as exc:
            raise PeerRouteError("envelope signature verification failed") from exc

    def to_wire(self) -> dict[str, Any]:
        wire = {
            **self._payload(),
            "ciphertext": base64.urlsafe_b64encode(self.ciphertext)
            .decode("ascii")
            .rstrip("="),
            "signature": self.signature,
        }
        if len(rfc8785.dumps(wire)) > PEER_WIRE_FRAME_MAX_BYTES:
            raise PeerRouteError("forwarding envelope exceeds frame limit")
        return wire


@dataclass(frozen=True)
class HopAttestation:
    peer_id: str
    next_hop: str
    ingress_link_id: str
    egress_link_id: str
    message_id: str
    prior_trace_hash: str
    issued_at: int
    signature: str

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> "HopAttestation":
        expected = {
            "peer_id",
            "next_hop",
            "ingress_link_id",
            "egress_link_id",
            "message_id",
            "prior_trace_hash",
            "issued_at",
            "signature",
        }
        if not isinstance(value, dict) or not value.keys() >= expected:
            raise PeerRouteError("invalid forwarding hop fields")
        if any(not isinstance(value[field], str) for field in expected - {"issued_at"}):
            raise PeerRouteError("invalid forwarding hop value")
        if isinstance(value["issued_at"], bool) or not isinstance(
            value["issued_at"], int
        ):
            raise PeerRouteError("invalid forwarding hop time")
        return cls(**{field: value[field] for field in expected})

    def _payload(self, *, scope: str) -> dict[str, Any]:
        return {
            "v": 1,
            "scope": scope,
            "peer_id": self.peer_id,
            "next_hop": self.next_hop,
            "ingress_link_id": self.ingress_link_id,
            "egress_link_id": self.egress_link_id,
            "message_id": self.message_id,
            "prior_trace_hash": self.prior_trace_hash,
            "issued_at": self.issued_at,
        }

    def to_wire(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "next_hop": self.next_hop,
            "ingress_link_id": self.ingress_link_id,
            "egress_link_id": self.egress_link_id,
            "message_id": self.message_id,
            "prior_trace_hash": self.prior_trace_hash,
            "issued_at": self.issued_at,
            "signature": self.signature,
        }


def append_hop(
    envelope: ForwardEnvelope,
    trace: tuple[HopAttestation, ...] | list[HopAttestation],
    *,
    signing_key: SigningKey,
    next_hop: str,
    origin_public_key: str,
    ingress_link_id: str,
    egress_link_id: str,
    prior_records: dict[str, PeerRecord],
    peer_links: dict[str, PeerLink],
    now: int | None = None,
) -> tuple[HopAttestation, ...]:
    """Append one signed transit hop without changing or decrypting the body."""
    current = int(time.time()) if now is None else now
    envelope.verify(origin_public_key, now=current)
    if envelope.expires_at <= current:
        raise PeerRouteError("cannot forward an expired envelope")
    peer_id = peer_id_for_key(signing_key.verify_key.encode())
    if len(trace) >= PEER_ROUTER_MAX_HOPS:
        raise PeerRouteError("peer forwarding hop limit reached")
    local_record = prior_records.get(peer_id)
    if (
        local_record is None
        or local_record.public_key != signing_key.verify_key.encode().hex()
    ):
        raise PeerRouteError("forwarding signer does not match its peer record")
    if peer_id in {envelope.origin, envelope.destination} or any(
        h.peer_id == peer_id for h in trace
    ):
        raise PeerRouteError("peer forwarding loop detected")
    if trace:
        verify_trace(
            envelope,
            trace,
            prior_records,
            links=peer_links,
            origin_public_key=origin_public_key,
            expected_next_hop=peer_id,
            now=current,
        )
    if any(h.message_id != envelope.message_id for h in trace):
        raise PeerRouteError("trace message id mismatch")
    if next_hop in {peer_id, envelope.origin} or any(
        h.peer_id == next_hop for h in trace
    ):
        raise PeerRouteError("invalid next hop or forwarding loop")
    if trace and trace[-1].next_hop != peer_id:
        raise PeerRouteError("incoming route does not target this forwarding peer")
    ingress_peer = envelope.origin if not trace else trace[-1].peer_id
    _verify_hop_link(
        ingress_link_id,
        ingress_peer,
        peer_id,
        prior_records,
        peer_links,
        envelope.scope,
        now=current,
        require_forwarding=True,
    )
    _verify_hop_link(
        egress_link_id,
        peer_id,
        next_hop,
        prior_records,
        peer_links,
        envelope.scope,
        now=current,
        # A transit node consumes forwarding consent on every edge it uses,
        # including the final edge to the ultimate destination.
        require_forwarding=True,
    )
    prior = _trace_hash(envelope, trace)
    hop = HopAttestation(
        peer_id=peer_id,
        next_hop=next_hop,
        ingress_link_id=ingress_link_id,
        egress_link_id=egress_link_id,
        message_id=envelope.message_id,
        prior_trace_hash=prior,
        issued_at=current,
        signature="",
    )
    signature = signing_key.sign(
        rfc8785.dumps(hop._payload(scope=envelope.scope))
    ).signature.hex()
    result = (*trace, HopAttestation(**{**hop.__dict__, "signature": signature}))
    _validate_forward_frame_size(envelope, result)
    return result


def verify_trace(
    envelope: ForwardEnvelope,
    trace: tuple[HopAttestation, ...] | list[HopAttestation],
    records: dict[str, PeerRecord],
    *,
    links: dict[str, PeerLink],
    origin_public_key: str,
    expected_next_hop: str | None = None,
    now: int | None = None,
) -> None:
    current = int(time.time()) if now is None else now
    envelope.verify(origin_public_key, now=current)
    _validate_forward_frame_size(envelope, trace)
    if len(trace) > PEER_ROUTER_MAX_HOPS:
        raise PeerRouteError("peer forwarding hop limit exceeded")
    seen = {envelope.origin}
    previous = _trace_hash(envelope, ())
    for index, hop in enumerate(trace):
        if (
            hop.peer_id in seen
            or hop.peer_id == envelope.destination
            or hop.next_hop in seen
        ):
            raise PeerRouteError("peer forwarding loop detected")
        seen.add(hop.peer_id)
        if hop.message_id != envelope.message_id or hop.prior_trace_hash != previous:
            raise PeerRouteError("peer forwarding trace chain mismatch")
        if index > 0 and trace[index - 1].egress_link_id != hop.ingress_link_id:
            raise PeerRouteError("adjacent forwarding hops reference different links")
        if (
            isinstance(hop.issued_at, bool)
            or not isinstance(hop.issued_at, int)
            or hop.issued_at > current + 30
        ):
            raise PeerRouteError("invalid hop issue time")
        if hop.issued_at + PEER_RECORD_TTL_MAX <= current:
            raise PeerRouteError("expired forwarding hop")
        if hop.issued_at > envelope.expires_at:
            raise PeerRouteError("forwarding hop outlives the envelope")
        record = records.get(hop.peer_id)
        if record is None:
            raise PeerRouteError("unknown forwarding peer")
        record.verify(now=current)
        if record.scope != envelope.scope:
            raise PeerRouteError("forwarding peer scope mismatch")
        if not ({"forwarder", "relay"} & set(record.roles)):
            raise PeerRouteError("peer record does not opt in to forwarding")
        if not _SIGNATURE.fullmatch(hop.signature):
            raise PeerRouteError("invalid hop signature")
        ingress_peer = envelope.origin if index == 0 else trace[index - 1].peer_id
        _verify_hop_link(
            hop.ingress_link_id,
            ingress_peer,
            hop.peer_id,
            records,
            links,
            envelope.scope,
            now=current,
            require_forwarding=True,
        )
        _verify_hop_link(
            hop.egress_link_id,
            hop.peer_id,
            hop.next_hop,
            records,
            links,
            envelope.scope,
            now=current,
            require_forwarding=True,
        )
        try:
            VerifyKey(bytes.fromhex(record.public_key)).verify(
                rfc8785.dumps(hop._payload(scope=envelope.scope)),
                bytes.fromhex(hop.signature),
            )
        except (BadSignatureError, ValueError) as exc:
            raise PeerRouteError("hop signature verification failed") from exc
        if index + 1 < len(trace) and hop.next_hop != trace[index + 1].peer_id:
            raise PeerRouteError("forwarding next hop does not match trace")
        previous = _trace_hash(envelope, trace[: index + 1])
    if trace:
        endpoint = (
            envelope.destination if expected_next_hop is None else expected_next_hop
        )
        if trace[-1].next_hop != endpoint:
            raise PeerRouteError(
                "forwarding trace does not end at the expected next hop"
            )


def _verify_hop_link(
    link_id: str,
    peer_a: str,
    peer_b: str,
    records: dict[str, PeerRecord],
    links: dict[str, PeerLink],
    scope: str,
    *,
    now: int,
    require_forwarding: bool,
) -> None:
    if not isinstance(link_id, str) or not _HEX_32.fullmatch(link_id):
        raise PeerRouteError("invalid forwarding link reference")
    link = links.get(link_id)
    if link is None:
        raise PeerRouteError("forwarding trace references an unknown link")
    expected_pair = tuple(sorted((peer_a, peer_b)))
    if (link.left, link.right) != expected_pair:
        raise PeerRouteError("forwarding trace link peers do not match")
    left_record = records.get(link.left)
    right_record = records.get(link.right)
    if left_record is None or right_record is None:
        raise PeerRouteError("forwarding trace link records are unavailable")
    link.verify(left_record, right_record, expected_scope=scope, now=now)
    if link.link_id != link_id:
        raise PeerRouteError("forwarding trace link id mismatch")
    if require_forwarding and not link.forwarding_allowed:
        raise PeerRouteError("forwarding trace uses a link without forwarding consent")


def trace_from_wire(values: list[dict[str, Any]]) -> tuple[HopAttestation, ...]:
    if not isinstance(values, list) or len(values) > PEER_ROUTER_MAX_HOPS:
        raise PeerRouteError("invalid forwarding trace length")
    if len(rfc8785.dumps(values)) > PEER_WIRE_FRAME_MAX_BYTES:
        raise PeerRouteError("forwarding trace exceeds frame limit")
    return tuple(HopAttestation.from_wire(value) for value in values)


def _validate_forward_frame_size(
    envelope: ForwardEnvelope,
    trace: tuple[HopAttestation, ...] | list[HopAttestation],
) -> None:
    frame = {
        "envelope": envelope.to_wire(),
        "trace": [hop.to_wire() for hop in trace],
    }
    if len(rfc8785.dumps(frame)) > PEER_WIRE_FRAME_MAX_BYTES:
        raise PeerRouteError("forwarding envelope and trace exceed frame limit")


def _trace_hash(
    envelope: ForwardEnvelope,
    trace: tuple[HopAttestation, ...] | list[HopAttestation],
) -> str:
    if trace:
        value: Any = [hop.to_wire() for hop in trace]
    else:
        value = {"header": envelope._payload(), "origin_signature": envelope.signature}
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


class DestinationReplayCache:
    """Bounded dedupe store; an optional SQLite path preserves claims on restart.

    Call ``complete`` with the opaque encrypted reply receipt after processing.
    A restarted destination suppresses a claimed message and can resend a
    completed receipt without decrypting it or executing the request again.
    A crash after a side effect but before completion remains at-most-once with
    a missing-reply recovery case; callers must report that state truthfully.
    """

    def __init__(
        self,
        *,
        capacity: int = PEER_REPLAY_CACHE_MAX,
        path: str | Path | None = None,
    ):
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or not 1 <= capacity <= PEER_REPLAY_CACHE_MAX
        ):
            raise ValueError("capacity must be between 1 and the protocol limit")
        self._capacity = capacity
        self._seen: dict[tuple[str, str, str, str], tuple[int, str, bytes | None]] = {}
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        if path is not None:
            self._connection = sqlite3.connect(
                str(path), timeout=30, isolation_level=None, check_same_thread=False
            )
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS destination_replays (
                    scope TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    envelope_digest TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    encrypted_receipt BLOB,
                    PRIMARY KEY (scope, origin, destination, message_id)
                )
                """)
            try:
                Path(path).chmod(0o600)
            except (OSError, TypeError):
                pass

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()

    @staticmethod
    def _key(envelope: ForwardEnvelope) -> tuple[str, str, str, str]:
        return (
            envelope.scope,
            envelope.origin,
            envelope.destination,
            envelope.message_id,
        )

    @staticmethod
    def _digest(envelope: ForwardEnvelope) -> str:
        return hashlib.sha256(rfc8785.dumps(envelope.to_wire())).hexdigest()

    def claim(
        self,
        envelope: ForwardEnvelope,
        *,
        origin_public_key: str,
        now: int | None = None,
    ) -> bool:
        current = int(time.time()) if now is None else now
        envelope.verify(origin_public_key, now=current)
        if envelope.expires_at <= current:
            raise PeerRouteError("cannot claim an expired envelope")
        key = self._key(envelope)
        digest = self._digest(envelope)
        with self._lock:
            if self._connection is None:
                for old_key, (expiry, _old_digest, _receipt) in list(
                    self._seen.items()
                ):
                    if expiry <= current:
                        self._seen.pop(old_key, None)
                previous = self._seen.get(key)
                if previous is not None:
                    if previous[1] != digest:
                        raise PeerRouteError(
                            "replayed message id has different envelope"
                        )
                    return False
                if len(self._seen) >= self._capacity:
                    raise PeerRouteError("destination replay cache is full")
                self._seen[key] = (envelope.expires_at, digest, None)
                return True

            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM destination_replays WHERE expires_at <= ?",
                    (current,),
                )
                previous = connection.execute(
                    "SELECT envelope_digest FROM destination_replays WHERE "
                    "scope = ? AND origin = ? AND destination = ? AND message_id = ?",
                    key,
                ).fetchone()
                if previous is not None:
                    if previous[0] != digest:
                        raise PeerRouteError(
                            "replayed message id has different envelope"
                        )
                    connection.execute("COMMIT")
                    return False
                count = connection.execute(
                    "SELECT COUNT(*) FROM destination_replays"
                ).fetchone()[0]
                if count >= self._capacity:
                    raise PeerRouteError("destination replay cache is full")
                connection.execute(
                    "INSERT INTO destination_replays "
                    "(scope, origin, destination, message_id, envelope_digest, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (*key, digest, envelope.expires_at),
                )
                connection.execute("COMMIT")
                return True
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def complete(
        self,
        envelope: ForwardEnvelope,
        encrypted_receipt: bytes,
        *,
        origin_public_key: str,
        now: int | None = None,
    ) -> None:
        """Persist an opaque reply encrypted to the origin for duplicate resend."""
        current = int(time.time()) if now is None else now
        envelope.verify(origin_public_key, now=current)
        if (
            not isinstance(encrypted_receipt, bytes)
            or not encrypted_receipt
            or len(encrypted_receipt) > PEER_WIRE_FRAME_MAX_BYTES
        ):
            raise PeerRouteError("invalid encrypted receipt size")
        key = self._key(envelope)
        digest = self._digest(envelope)
        with self._lock:
            if self._connection is None:
                previous = self._seen.get(key)
                if previous is None or previous[0] <= current:
                    raise PeerRouteError("message has no active replay claim")
                if previous[1] != digest:
                    raise PeerRouteError("replay claim envelope mismatch")
                if previous[2] is not None and previous[2] != encrypted_receipt:
                    raise PeerRouteError("message completion is immutable")
                self._seen[key] = (previous[0], digest, encrypted_receipt)
                return

            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT envelope_digest, expires_at, encrypted_receipt "
                    "FROM destination_replays WHERE scope = ? AND origin = ? "
                    "AND destination = ? AND message_id = ?",
                    key,
                ).fetchone()
                if previous is None or previous[1] <= current:
                    raise PeerRouteError("message has no active replay claim")
                if previous[0] != digest:
                    raise PeerRouteError("replay claim envelope mismatch")
                if previous[2] is not None and bytes(previous[2]) != encrypted_receipt:
                    raise PeerRouteError("message completion is immutable")
                connection.execute(
                    "UPDATE destination_replays SET encrypted_receipt = ? "
                    "WHERE scope = ? AND origin = ? AND destination = ? AND message_id = ?",
                    (encrypted_receipt, *key),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def cached_receipt(
        self,
        envelope: ForwardEnvelope,
        *,
        origin_public_key: str,
        now: int | None = None,
    ) -> bytes | None:
        """Return a previously stored opaque receipt without exposing its body."""
        current = int(time.time()) if now is None else now
        envelope.verify(origin_public_key, now=current)
        key = self._key(envelope)
        digest = self._digest(envelope)
        with self._lock:
            if self._connection is None:
                previous = self._seen.get(key)
                if previous is None or previous[0] <= current:
                    return None
                if previous[1] != digest:
                    raise PeerRouteError("replay claim envelope mismatch")
                return previous[2]
            row = self._connection.execute(
                "SELECT envelope_digest, encrypted_receipt, expires_at "
                "FROM destination_replays WHERE scope = ? AND origin = ? "
                "AND destination = ? AND message_id = ?",
                key,
            ).fetchone()
        if row is None or row[2] <= current:
            return None
        if row[0] != digest:
            raise PeerRouteError("replay claim envelope mismatch")
        return None if row[1] is None else bytes(row[1])


class PeerRouter:
    """Build a bounded graph from pair-signed links and try distinct paths.

    Only the first hop must be locally online. Further edges are recent signed
    link attestations and can still fail in transit; callers should invoke
    ``send_with_failover`` with the same idempotent envelope on each path.
    """

    def __init__(
        self,
        local_peer_id: str,
        scope: str,
        *,
        max_paths: int = PEER_ROUTER_MAX_PATHS,
        max_hops: int = PEER_ROUTER_MAX_HOPS,
        max_search_states: int = PEER_ROUTER_SEARCH_BUDGET,
        max_links: int = PEER_ROUTER_MAX_LINKS,
        record_store: InMemoryPeerRecordStore | SQLitePeerRecordStore | None = None,
        link_state_store: SQLitePeerLinkStore | None = None,
    ):
        if not _PEER_ID.fullmatch(local_peer_id) or not _SCOPE.fullmatch(scope):
            raise ValueError("local peer and scope are required")
        if isinstance(max_paths, bool) or not 1 <= max_paths <= PEER_ROUTER_MAX_PATHS:
            raise ValueError("max_paths exceeds protocol limit")
        if isinstance(max_hops, bool) or not 1 <= max_hops <= PEER_ROUTER_MAX_HOPS:
            raise ValueError("max_hops exceeds protocol limit")
        if (
            isinstance(max_search_states, bool)
            or not 1 <= max_search_states <= PEER_ROUTER_SEARCH_BUDGET
        ):
            raise ValueError("max_search_states exceeds protocol limit")
        if isinstance(max_links, bool) or not 1 <= max_links <= PEER_ROUTER_MAX_LINKS:
            raise ValueError("max_links exceeds protocol limit")
        self.local_peer_id = local_peer_id
        self.scope = scope
        self.max_paths = max_paths
        self.max_hops = max_hops
        self.max_search_states = max_search_states
        self.max_links = max_links
        self.records = (
            record_store
            if record_store is not None
            else InMemoryPeerRecordStore(scope=scope)
        )
        self.link_state_store = link_state_store
        self._links: dict[tuple[str, str], PeerLink] = {}
        self._link_highwater: dict[tuple[str, str], tuple[int, str, int]] = {}
        self._online_neighbor_sessions: dict[str, str] = {}

    def record_snapshot(self, *, now: int | None = None) -> dict[str, PeerRecord]:
        current = int(time.time()) if now is None else now
        return {
            record.peer_id: record
            for record in self.records.list(scope=self.scope, now=current)
        }

    def link_snapshot(self, *, now: int | None = None) -> dict[str, PeerLink]:
        current = int(time.time()) if now is None else now
        self._prune_links(current)
        return {link.link_id: link for link in self._links.values()}

    def link_between(self, peer_a: str, peer_b: str, *, now: int | None = None) -> PeerLink | None:
        current = int(time.time()) if now is None else now
        self._prune_links(current)
        return self._links.get(tuple(sorted((peer_a, peer_b))))

    @property
    def authenticated_neighbors(self) -> dict[str, str]:
        return dict(self._online_neighbor_sessions)

    def add_record(self, record: PeerRecord, *, now: int | None = None) -> None:
        if record.scope != self.scope:
            raise PeerRouteError("peer record scope mismatch")
        self.records.accept(record, now=now)

    def add_link(self, link: PeerLink, *, now: int | None = None) -> None:
        current = int(time.time()) if now is None else now
        self._prune_links(current)
        left = self.records.get(link.left, scope=self.scope, now=current)
        right = self.records.get(link.right, scope=self.scope, now=current)
        if left is None or right is None:
            raise PeerRouteError("peer link references an unknown or expired record")
        link.verify(left, right, expected_scope=self.scope, now=current)
        edge = (link.left, link.right)
        if self.link_state_store is not None:
            self.link_state_store.accept(link, now=current, max_links=self.max_links)
        else:
            previous = self._link_highwater.get(edge)
            if previous is not None:
                previous_revision, previous_digest, retain_until = previous
                if link.revision < previous_revision:
                    raise PeerRouteError("peer link revision rollback")
                if link.revision == previous_revision:
                    if link.link_id != previous_digest:
                        raise PeerRouteError("peer link revision equivocation")
                else:
                    retained_until = max(retain_until, link.expires_at)
                    self._link_highwater[edge] = (
                        link.revision,
                        link.link_id,
                        retained_until,
                    )
            else:
                if len(self._link_highwater) >= self.max_links:
                    raise PeerRouteError("peer link capacity is full")
                self._link_highwater[edge] = (
                    link.revision,
                    link.link_id,
                    link.expires_at,
                )
        self._links[edge] = link

    def _prune_links(self, now: int) -> None:
        if self.link_state_store is not None:
            self.link_state_store.prune(now)
        for edge, link in list(self._links.items()):
            if link.expires_at <= now:
                self._links.pop(edge, None)
        if self.link_state_store is None:
            for edge, (_revision, _digest, retain_until) in list(
                self._link_highwater.items()
            ):
                if retain_until <= now:
                    self._link_highwater.pop(edge, None)
        for peer_id, session_id in list(self._online_neighbor_sessions.items()):
            if not any(
                {link.left, link.right} == {self.local_peer_id, peer_id}
                and link.session_id == session_id
                and link.expires_at > now
                for link in self._links.values()
            ):
                self._online_neighbor_sessions.pop(peer_id, None)

    def set_authenticated_neighbor(
        self,
        peer_id: str,
        online: bool,
        *,
        session_id: str | None = None,
        now: int | None = None,
    ) -> None:
        """Mark only a live bilateral session; caller owns handshake verification."""
        if type(online) is not bool:
            raise PeerRouteError("neighbor state must be explicit")
        if peer_id == self.local_peer_id:
            raise PeerRouteError("local peer cannot be its own neighbor")
        current = int(time.time()) if now is None else now
        self._prune_links(current)
        record = self.records.get(peer_id, scope=self.scope, now=current)
        if record is None:
            raise PeerRouteError("authenticated neighbor has no fresh peer record")
        if online:
            if not isinstance(session_id, str) or not _HEX_32.fullmatch(session_id):
                raise PeerRouteError("authenticated session transcript id required")
            if not any(
                {link.left, link.right} == {self.local_peer_id, peer_id}
                and link.session_id == session_id
                and link.expires_at > current
                for link in self._links.values()
            ):
                raise PeerRouteError(
                    "authenticated neighbor has no matching pair-signed session link"
                )
            self._online_neighbor_sessions[peer_id] = session_id
        else:
            self._online_neighbor_sessions.pop(peer_id, None)

    def revoke(self, peer_id: str) -> None:
        self.records.revoke(peer_id, scope=self.scope)
        if self.link_state_store is not None:
            self.link_state_store.revoke_peer(self.scope, peer_id)
        self._online_neighbor_sessions.pop(peer_id, None)
        self._links = {
            edge: link
            for edge, link in self._links.items()
            if peer_id not in (link.left, link.right)
        }
        if self.link_state_store is None:
            self._link_highwater = {
                edge: state
                for edge, state in self._link_highwater.items()
                if peer_id not in edge
            }

    def route_candidates(
        self, destination: str, *, now: int | None = None
    ) -> tuple[tuple[str, ...], ...]:
        current = int(time.time()) if now is None else now
        self._prune_links(current)
        if destination == self.local_peer_id:
            return ((self.local_peer_id,),)
        if self.records.get(destination, scope=self.scope, now=current) is None:
            return ()
        adjacency: dict[str, dict[str, PeerLink]] = {}
        for link in self._links.values():
            if link.expires_at <= current:
                continue
            left = self.records.get(link.left, scope=self.scope, now=current)
            right = self.records.get(link.right, scope=self.scope, now=current)
            if left is None or right is None:
                continue
            try:
                link.verify(left, right, expected_scope=self.scope, now=current)
            except (PeerRouteError, PeerRecordError):
                continue
            for source, neighbor in ((link.left, link.right), (link.right, link.left)):
                adjacency.setdefault(source, {})[neighbor] = link

        queue = deque([(self.local_peer_id, (self.local_peer_id,))])
        routes: list[tuple[str, ...]] = []
        examined = 0
        while (
            queue and len(routes) < self.max_paths and examined < self.max_search_states
        ):
            node, path = queue.popleft()
            examined += 1
            if len(path) - 1 >= self.max_hops:
                continue
            for neighbor in sorted(adjacency.get(node, {})):
                if neighbor in path:
                    continue
                link = adjacency[node][neighbor]
                if len(path) == 1:
                    live_session = self._online_neighbor_sessions.get(neighbor)
                    if live_session != link.session_id:
                        continue
                if link.expires_at <= current:
                    continue
                forwarding_allowed = link.forwarding_allowed
                # The local origin may directly contact its destination
                # without transit consent. Once a remote node is relaying a
                # message, every following edge requires bilateral consent.
                requires_forwarding = neighbor != destination or len(path) > 1
                if requires_forwarding and not forwarding_allowed:
                    continue
                if neighbor != destination:
                    peer_record = self.records.get(
                        neighbor, scope=self.scope, now=current
                    )
                    if peer_record is None or not (
                        {"forwarder", "relay"} & set(peer_record.roles)
                    ):
                        continue
                candidate = (*path, neighbor)
                if neighbor == destination:
                    routes.append(candidate)
                    if len(routes) >= self.max_paths:
                        break
                else:
                    if examined + len(queue) >= self.max_search_states:
                        continue
                    queue.append((neighbor, candidate))
        return tuple(routes)

    async def send_with_failover(
        self,
        destination: str,
        envelope: ForwardEnvelope,
        send_path: Callable[[tuple[str, ...], ForwardEnvelope], Awaitable[bool]],
        *,
        now: int | None = None,
        timeout_per_path: float = 10.0,
    ) -> tuple[str, ...]:
        """Try at most three refreshed paths before the signed envelope expires."""
        if (
            isinstance(timeout_per_path, bool)
            or not isinstance(timeout_per_path, (int, float))
            or not math.isfinite(timeout_per_path)
            or not 0 < timeout_per_path <= PEER_ROUTE_ATTEMPT_TIMEOUT_MAX
        ):
            raise ValueError("timeout_per_path exceeds the protocol limit")
        loop = asyncio.get_running_loop()
        started = loop.time()
        current = int(time.time()) if now is None else now
        if envelope.scope != self.scope or envelope.destination != destination:
            raise PeerRouteError("envelope destination or scope mismatch")
        origin_record = self.records.get(envelope.origin, scope=self.scope, now=current)
        if origin_record is None:
            raise PeerRouteError("envelope origin record is unavailable")
        envelope.verify(origin_record.public_key, now=current)
        if envelope.expires_at <= current:
            raise PeerRouteError("cannot route an expired envelope")
        deadline = started + (envelope.expires_at - current)
        if not self.route_candidates(destination, now=current):
            raise PeerRouteError("no route to destination")
        attempted: set[tuple[str, ...]] = set()
        for _attempt in range(self.max_paths):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PeerRouteError("envelope expired during route failover")
            logical_now = current + int(loop.time() - started)
            origin_record = self.records.get(
                envelope.origin, scope=self.scope, now=logical_now
            )
            if origin_record is None:
                raise PeerRouteError(
                    "envelope origin was revoked during route failover"
                )
            envelope.verify(origin_record.public_key, now=logical_now)
            fresh_paths = self.route_candidates(destination, now=logical_now)
            path = next(
                (candidate for candidate in fresh_paths if candidate not in attempted),
                None,
            )
            if path is None:
                break
            attempted.add(path)
            try:
                delivered = await asyncio.wait_for(
                    send_path(path, envelope),
                    timeout=min(float(timeout_per_path), remaining),
                )
                if delivered:
                    return path
            except (asyncio.TimeoutError, TransientPeerDeliveryError):
                continue
        raise PeerRouteError("all available peer routes failed")
