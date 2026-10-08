"""Shared lease, room-presence, and cross-node routing backends.

Redis/Valkey room mutations use room-hash Redis Cluster hash tags. Connection
quotas use expiring node and source-IP leases. Cluster Pub/Sub uses one sharded
inbox per relay worker; encrypted payloads and presence invalidations are
ephemeral and are never persisted as messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .device_names import contact_route_hex

logger = logging.getLogger(__name__)

LEASE_SECONDS = 35
LEASE_RENEW_SECONDS = 10
ROUTE_TIMEOUT_SECONDS = 3
MAX_BACKPLANE_MESSAGE_BYTES = 64 * 1024
MAX_ROOM_MEMBERS = 256
MAX_WORK_QUEUE_ITEMS = 4096
MAX_WORK_QUEUE_BYTES = 16 * 1024 * 1024
MAX_ACK_QUEUE_ITEMS = 4096
MAX_ACTIVE_ENROLLMENT_OFFERS = 128
ENROLLMENT_CAPACITY_INDEX_TTL_MS = 20 * 60_000
ENROLLMENT_RATE_LIMIT = 10
ENROLLMENT_RATE_WINDOW_MS = 60_000
ENROLLMENT_MAX_RATE_SOURCES = 65_536
ENROLLMENT_INDEX_CLEANUP_BATCH = 256
ENROLLMENT_MAX_FAILED_CODES = 5
ENROLLMENT_MAX_NONCES_PER_PRINCIPAL = 4096
ENROLLMENT_MAX_NONCES = 131_072
LINK_INDEX_CLEANUP_BATCH = 256
# Cross-room links: each key declares which other keys it consents to reach;
# a link is active only when both sides declared each other.
MAX_LINK_PEERS = 64
MAX_ACTIVE_LINK_DECLARATIONS = 8192
LINK_TTL_SECONDS = 24 * 60 * 60
# A withdrawal keeps its timestamp this long (past the request clock skew), so a
# delayed older declaration cannot undo it.
LINK_WITHDRAWAL_SECONDS = 5 * 60


class RelayBackendError(RuntimeError):
    """The shared relay backend is unavailable or returned invalid data."""


def relay_owner_key(node_id: str) -> str:
    """Return the fenced shared owner key used by one stable relay worker."""
    return f"kollab:relay:owner:{{node-{node_id}}}"


@dataclass(frozen=True, slots=True)
class RelayLimits:
    max_connections_per_node: int = 512
    max_connections_per_room: int = 16
    max_connections_per_source: int = 16

    def __post_init__(self) -> None:
        if not 1 <= self.max_connections_per_node <= 100_000:
            raise ValueError("max_connections_per_node must be in 1..100000")
        if not 1 <= self.max_connections_per_room <= MAX_ROOM_MEMBERS:
            raise ValueError("max_connections_per_room must be in 1..256")
        if not 1 <= self.max_connections_per_source <= 100_000:
            raise ValueError("max_connections_per_source must be in 1..100000")


@dataclass(frozen=True, slots=True)
class PeerRecord:
    key: str
    session: str
    node_id: str
    connection_id: str

    def as_json(self) -> str:
        return json.dumps(
            {
                "key": self.key,
                "session": self.session,
                "node_id": self.node_id,
                "connection_id": self.connection_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class InMemoryBackend:
    """Single-process backend: explicit development mode, and `kollab relay serve --domain` (one worker)."""

    def __init__(self, node_id: str, limits: RelayLimits):
        self.node_id = node_id
        self.limits = limits
        self.connections: dict[str, tuple[str, str]] = {}
        self.source_counts: Counter[str] = Counter()
        self.rooms: dict[str, dict[str, PeerRecord]] = {}
        self.enrollment_offers: dict[str, dict[str, Any]] = {}
        self.enrollment_lookup_index: dict[str, str] = {}
        self.enrollment_rate: dict[str, tuple[int, float]] = {}
        self.enrollment_nonces: dict[str, dict[str, float]] = {}
        self.enrollment_nonce_count = 0
        # route hex -> keys currently registered in any room under it. A
        # normal key has exactly one entry; more than one is a (practically
        # impossible) hash collision, reported to callers as "ambiguous"
        # rather than silently picking one.
        self.contact_routes: dict[str, set[str]] = {}
        # key -> (issued_at, the keys it consents to link with, expiry). A
        # link between two keys exists only while each names the other.
        self.link_declarations: dict[str, tuple[int, frozenset[str], float]] = {}
        # key -> the room of its latest registration (the Redis presence index)
        self.presence: dict[str, str] = {}
        self.state: Any = None

    async def start(self, state: Any) -> None:
        self.state = state

    async def close(self) -> None:
        return None

    async def health(self) -> bool:
        return True

    @property
    def is_ready(self) -> bool:
        return True

    async def owner_valid(self) -> bool:
        return True

    async def reserve_connection(
        self, connection_id: str, source_ip: str
    ) -> str | None:
        if len(self.connections) >= self.limits.max_connections_per_node:
            return None
        if self.source_counts[source_ip] >= self.limits.max_connections_per_source:
            return None
        self.connections[connection_id] = (source_ip, connection_id)
        self.source_counts[source_ip] += 1
        return connection_id

    async def release_connection(
        self, connection_id: str, source_ip: str, reservation: str | None
    ) -> None:
        if self.connections.pop(connection_id, None) is not None:
            self.source_counts[source_ip] -= 1
            if self.source_counts[source_ip] <= 0:
                del self.source_counts[source_ip]

    async def register_room(
        self, room_hash: str, member: PeerRecord
    ) -> tuple[str | None, list[PeerRecord]]:
        room = self.rooms.setdefault(room_hash, {})
        if member.key in room:
            return "duplicate_identity", []
        if len(room) >= self.limits.max_connections_per_room:
            return "room_capacity", []
        room[member.key] = member
        self.presence[member.key] = room_hash
        self.contact_routes.setdefault(contact_route_hex(member.key), set()).add(
            member.key
        )
        return None, list(room.values())

    async def list_room(self, room_hash: str) -> tuple[list[PeerRecord], bool]:
        return list(self.rooms.get(room_hash, {}).values()), False

    async def remove_peer(
        self, room_hash: str, member: PeerRecord
    ) -> tuple[bool, list[PeerRecord]]:
        room = self.rooms.get(room_hash)
        if room is None or room.get(member.key) != member:
            return False, list(room.values()) if room else []
        del room[member.key]
        if not room:
            del self.rooms[room_hash]
        if self.presence.get(member.key) == room_hash:
            del self.presence[member.key]
        if not any(member.key in other for other in self.rooms.values()):
            self._discard_contact_route(member.key)
        return True, list(room.values())

    def _discard_contact_route(self, key: str) -> None:
        route = contact_route_hex(key)
        keys = self.contact_routes.get(route)
        if keys is None:
            return
        keys.discard(key)
        if not keys:
            del self.contact_routes[route]

    async def lookup_contact_route(self, route_hex: str) -> list[str]:
        """Keys currently registered under this route (0, 1, or >1 = ambiguous)."""
        return sorted(self.contact_routes.get(route_hex, ()))

    def _declared(self, key: str) -> frozenset[str]:
        now = time.monotonic()
        for other, (_, _, expires) in list(self.link_declarations.items()):
            if expires <= now:
                del self.link_declarations[other]
        row = self.link_declarations.get(key)
        return row[1] if row else frozenset()

    async def sync_links(
        self, key: str, peers: list[str], *, issued_at: int, ttl_ms: int
    ) -> tuple[str, list[str]]:
        """Replace what `key` consents to link with; return the peers that changed.

        A declaration older than the stored one is refused, so a delayed
        request cannot undo a later withdrawal.
        """
        before = self._declared(key)
        stored = self.link_declarations.get(key)
        if stored is not None and stored[0] > issued_at:
            return "stale", []
        if not peers:
            self.link_declarations[key] = (
                issued_at,
                frozenset(),
                time.monotonic() + LINK_WITHDRAWAL_SECONDS,
            )
            return "stored", sorted(before)
        live = sum(1 for _, declared, _ in self.link_declarations.values() if declared)
        if not before and live >= MAX_ACTIVE_LINK_DECLARATIONS:
            return "capacity", []
        self.link_declarations[key] = (
            issued_at,
            frozenset(peers),
            time.monotonic() + ttl_ms / 1000,
        )
        return "stored", sorted(before ^ frozenset(peers))

    async def linked_keys(self, key: str) -> list[str]:
        """Keys that mutually consented to link with `key`."""
        return sorted(other for other in self._declared(key) if key in self._declared(other))

    async def is_linked(self, first: str, second: str) -> bool:
        return second in self._declared(first) and first in self._declared(second)

    async def locate(self, key: str) -> tuple[PeerRecord, str] | None:
        """The live record and room of a key, wherever it is registered."""
        room_hash = self.presence.get(key)
        record = self.rooms.get(room_hash, {}).get(key) if room_hash else None
        return (record, room_hash) if record is not None else None

    async def renew(
        self,
        connection_id: str,
        source_ip: str,
        reservation: str | None,
        member: PeerRecord | None,
        room_hash: str | None,
    ) -> bool:
        if connection_id not in self.connections:
            return False
        return member is None or (
            room_hash is not None
            and self.rooms.get(room_hash, {}).get(member.key) == member
        )

    async def notify_room_change(self, room_hash: str, node_ids: set[str]) -> None:
        if self.state is not None and self.node_id in node_ids:
            await self.state.room_changed(room_hash)

    async def forward(self, destination: PeerRecord, route: dict[str, str]) -> bool:
        if self.state is None or destination.node_id != self.node_id:
            return False
        return await self.state.deliver_local(
            {
                **route,
                "connection_id": destination.connection_id,
                "to": destination.key,
                "to_session": destination.session,
            }
        )

    def _prune_enrollment_offers(self, now: float) -> None:
        expired = [
            offer_id
            for offer_id, offer in self.enrollment_offers.items()
            if float(offer["_expires_monotonic"]) <= now
        ]
        for offer_id in expired:
            self._delete_enrollment_offer(offer_id)

    def _delete_enrollment_offer(self, offer_id: str) -> None:
        offer = self.enrollment_offers.pop(offer_id, None)
        lookup_hash = offer.get("lookup_hash") if offer else None
        if lookup_hash:
            self.enrollment_lookup_index.pop(lookup_hash, None)

    def _prune_enrollment_nonces(
        self, now: float, principal_hash: str | None = None
    ) -> None:
        principals = (
            [(principal_hash, self.enrollment_nonces.get(principal_hash, {}))]
            if principal_hash is not None
            else list(self.enrollment_nonces.items())
        )
        for principal, entries in principals:
            expired = [nonce for nonce, expiry in entries.items() if expiry <= now]
            for nonce in expired:
                entries.pop(nonce, None)
            self.enrollment_nonce_count -= len(expired)
            if not entries:
                self.enrollment_nonces.pop(principal, None)

    async def enrollment_admission_usage(self) -> dict[str, int]:
        now = time.monotonic()
        for key, (_, expires_at) in list(self.enrollment_rate.items()):
            if expires_at <= now:
                self.enrollment_rate.pop(key, None)
        self._prune_enrollment_nonces(now)
        return {
            "rate_sources": len(self.enrollment_rate),
            "nonce_records": self.enrollment_nonce_count,
        }

    async def consume_enrollment_rate(
        self, source_hash: str, *, limit: int, window_ms: int
    ) -> bool:
        now = time.monotonic()
        slot = int(now * 1000) // window_ms
        bucket = f"{source_hash}:{slot}"
        for key, (_, expires_at) in list(self.enrollment_rate.items()):
            if expires_at <= now:
                self.enrollment_rate.pop(key, None)
        current, _ = self.enrollment_rate.get(bucket, (0, now + window_ms / 1000))
        if current >= limit:
            return False
        if (
            bucket not in self.enrollment_rate
            and len(self.enrollment_rate) >= ENROLLMENT_MAX_RATE_SOURCES
        ):
            return False
        self.enrollment_rate[bucket] = (current + 1, now + window_ms / 1000)
        return True

    async def consume_enrollment_nonce(
        self,
        principal_hash: str,
        nonce_hash: str,
        *,
        ttl_ms: int,
        capacity: int,
    ) -> bool:
        now = time.monotonic()
        self._prune_enrollment_nonces(now, principal_hash)
        entries = self.enrollment_nonces.get(principal_hash)
        if entries is not None and nonce_hash in entries:
            return False
        if entries is not None and len(entries) >= capacity:
            raise RelayBackendError("enrollment nonce capacity is full")
        if self.enrollment_nonce_count >= ENROLLMENT_MAX_NONCES:
            self._prune_enrollment_nonces(now)
            entries = self.enrollment_nonces.get(principal_hash)
            if entries is not None and nonce_hash in entries:
                return False
            if entries is not None and len(entries) >= capacity:
                raise RelayBackendError("enrollment nonce capacity is full")
            if self.enrollment_nonce_count >= ENROLLMENT_MAX_NONCES:
                raise RelayBackendError("enrollment nonce capacity is full")
        if entries is None:
            if len(self.enrollment_nonces) >= 8192:
                self._prune_enrollment_nonces(now)
                if len(self.enrollment_nonces) >= 8192:
                    raise RelayBackendError("enrollment nonce capacity is full")
            entries = self.enrollment_nonces.setdefault(principal_hash, {})
        entries[nonce_hash] = now + ttl_ms / 1000
        self.enrollment_nonce_count += 1
        return True

    async def create_enrollment_offer(
        self, offer_id: str, fields: dict[str, str], *, ttl_ms: int, capacity: int
    ) -> str:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        existing = self.enrollment_offers.get(offer_id)
        if existing is not None:
            return (
                "duplicate"
                if existing["create_digest"] == fields["create_digest"]
                else "conflict"
            )
        if len(self.enrollment_offers) >= capacity:
            return "capacity"
        record = dict(fields)
        record.update(
            {
                "state": "open",
                "failed_codes": "0",
                "device_name": "",
                "_expires_monotonic": now + ttl_ms / 1000,
            }
        )
        self.enrollment_offers[offer_id] = record
        lookup_hash = fields.get("lookup_hash")
        if lookup_hash:
            self.enrollment_lookup_index[lookup_hash] = offer_id
        return "created"

    async def find_enrollment_offer_by_lookup(self, lookup_hash: str) -> str | None:
        """Resolve a short code's lookup hash to an unexpired, unused offer id."""
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer_id = self.enrollment_lookup_index.get(lookup_hash)
        if offer_id is None:
            return None
        offer = self.enrollment_offers.get(offer_id)
        if offer is None or offer.get("state") != "open":
            return None
        return offer_id

    async def submit_enrollment_request(
        self,
        offer_id: str,
        *,
        round_id: str,
        destination_key: str,
        candidate_hash: str,
        envelope: str,
        content_digest: str,
        device_name: str = "",
    ) -> str:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if offer is None:
            return "unavailable"
        existing_round = offer.get("request_round_id")
        if existing_round:
            if (
                existing_round == round_id
                and offer.get("request_digest") == content_digest
                and offer.get("destination_key") == destination_key
            ):
                return "duplicate"
            return "bound"
        if offer.get("state") != "open":
            return "unavailable"
        failures = int(offer.get("failed_codes", "0"))
        if failures >= ENROLLMENT_MAX_FAILED_CODES:
            self._delete_enrollment_offer(offer_id)
            return "unavailable"
        if not hmac.compare_digest(offer["code_verifier_hash"], candidate_hash):
            failures += 1
            offer["failed_codes"] = str(failures)
            if failures >= ENROLLMENT_MAX_FAILED_CODES:
                self._delete_enrollment_offer(offer_id)
                return "unavailable"
            return "invalid_code"
        offer.update(
            {
                "state": "request_pending",
                "destination_key": destination_key,
                "request_round_id": round_id,
                "request_digest": content_digest,
                "request_envelope": envelope,
                "request_claim_id": "",
                "device_name": device_name,
            }
        )
        return "accepted"

    async def claim_enrollment_round(
        self, offer_id: str, *, issuer_key: str, claim_id: str
    ) -> dict[str, str]:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if offer is None or offer.get("issuer_key") != issuer_key:
            return {"status": "unavailable"}
        if offer.get("state") in ("request_pending", "request_claimed"):
            phase = "request"
        elif offer.get("state") in ("proof_pending", "proof_claimed"):
            phase = "proof"
        else:
            return {"status": "empty"}
        state = offer["state"]
        stored_claim_id = offer.get(f"{phase}_claim_id", "")
        if state.endswith("_pending"):
            offer["state"] = f"{phase}_claimed"
            offer[f"{phase}_claim_id"] = claim_id
        elif stored_claim_id != claim_id:
            return {"status": "claimed"}
        return {
            "status": "claimed",
            "phase": phase,
            "round_id": offer[f"{phase}_round_id"],
            "destination_key": offer["destination_key"],
            "envelope": offer[f"{phase}_envelope"],
            "device_name": offer.get("device_name", ""),
        }

    async def publish_enrollment_challenge(
        self,
        offer_id: str,
        *,
        issuer_key: str,
        destination_key: str,
        round_id: str,
        envelope: str,
        content_digest: str,
    ) -> str:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if offer is None or offer.get("issuer_key") != issuer_key:
            return "unavailable"
        if offer.get("destination_key") != destination_key:
            return "unavailable"
        if offer.get("challenge_round_id"):
            return (
                "duplicate"
                if offer["challenge_round_id"] == round_id
                and offer.get("challenge_digest") == content_digest
                else "conflict"
            )
        if offer.get("state") != "request_claimed":
            return "not_ready"
        offer.update(
            {
                "state": "challenge_ready",
                "challenge_round_id": round_id,
                "challenge_digest": content_digest,
                "challenge_envelope": envelope,
            }
        )
        return "stored"

    async def submit_enrollment_proof(
        self,
        offer_id: str,
        *,
        round_id: str,
        destination_key: str,
        envelope: str,
        content_digest: str,
    ) -> str:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if offer is None or offer.get("destination_key") != destination_key:
            return "unavailable"
        if offer.get("proof_round_id"):
            return (
                "duplicate"
                if offer["proof_round_id"] == round_id
                and offer.get("proof_digest") == content_digest
                else "conflict"
            )
        if offer.get("state") != "challenge_ready":
            return "not_ready"
        offer.update(
            {
                "state": "proof_pending",
                "proof_round_id": round_id,
                "proof_digest": content_digest,
                "proof_envelope": envelope,
                "proof_claim_id": "",
            }
        )
        return "stored"

    async def publish_enrollment_decision(
        self,
        offer_id: str,
        *,
        issuer_key: str,
        destination_key: str,
        round_id: str,
        envelope: str,
        content_digest: str,
    ) -> str:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if offer is None or offer.get("issuer_key") != issuer_key:
            return "unavailable"
        if offer.get("destination_key") != destination_key:
            return "unavailable"
        if offer.get("decision_round_id"):
            return (
                "duplicate"
                if offer["decision_round_id"] == round_id
                and offer.get("decision_digest") == content_digest
                else "conflict"
            )
        if offer.get("state") != "proof_claimed":
            return "not_ready"
        offer.update(
            {
                "state": "closed",
                "decision_round_id": round_id,
                "decision_digest": content_digest,
                "decision_envelope": envelope,
            }
        )
        return "stored"

    async def poll_enrollment_reply(
        self, offer_id: str, *, destination_key: str
    ) -> dict[str, str]:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if offer is None or offer.get("destination_key") != destination_key:
            return {"status": "unavailable"}
        if offer.get("decision_round_id"):
            phase = "decision"
        elif offer.get("challenge_round_id"):
            phase = "challenge"
        else:
            return {"status": "pending"}
        return {
            "status": "ready",
            "phase": phase,
            "round_id": offer[f"{phase}_round_id"],
            "envelope": offer[f"{phase}_envelope"],
        }

    async def submit_enrollment_install_ack(
        self,
        offer_id: str,
        *,
        round_id: str,
        destination_key: str,
        frame: dict[str, Any],
        content_digest: str,
    ) -> str:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if (
            offer is None
            or offer.get("destination_key") != destination_key
            or offer.get("decision_round_id") != round_id
        ):
            return "unavailable"
        previous_digest = offer.get("install_ack_digest")
        if previous_digest is not None:
            return "duplicate" if previous_digest == content_digest else "conflict"
        offer["install_ack_frame"] = dict(frame)
        offer["install_ack_digest"] = content_digest
        return "stored"

    async def poll_enrollment_install_ack(
        self, offer_id: str, *, issuer_key: str
    ) -> dict[str, Any]:
        now = time.monotonic()
        self._prune_enrollment_offers(now)
        offer = self.enrollment_offers.get(offer_id)
        if offer is None or offer.get("issuer_key") != issuer_key:
            return {"status": "unavailable"}
        frame = offer.get("install_ack_frame")
        if frame is None:
            return {"status": "pending"}
        return {"status": "ready", "frame": dict(frame)}


_RESERVE_LEASE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
return 1
"""

_RENEW_LEASE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score or tonumber(score) <= now then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
return 1
"""

_RELEASE_LEASE = "return redis.call('ZREM', KEYS[1], ARGV[1])"

_RENEW_OWNER = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""

_RELEASE_OWNER = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""

_JOIN_ROOM = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now)
for _, peer in ipairs(expired) do redis.call('HDEL', KEYS[1], peer) end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('HGET', KEYS[1], ARGV[1]) then return {'duplicate_identity', #expired} end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then return {'room_capacity', #expired} end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[3]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[3]) * 2)
local roster = redis.call('HGETALL', KEYS[1])
local result = {'ok', #expired}
for _, value in ipairs(roster) do table.insert(result, value) end
return result
"""

_LIST_ROOM = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now)
for _, peer in ipairs(expired) do redis.call('HDEL', KEYS[1], peer) end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local roster = redis.call('HGETALL', KEYS[1])
local result = {#expired}
for _, value in ipairs(roster) do table.insert(result, value) end
if redis.call('HLEN', KEYS[1]) == 0 then redis.call('DEL', KEYS[1], KEYS[2]) end
return result
"""

_REMOVE_PEER = """
if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then
  local roster = redis.call('HGETALL', KEYS[1])
  return {0, unpack(roster)}
end
redis.call('HDEL', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
if redis.call('HLEN', KEYS[1]) == 0 then redis.call('DEL', KEYS[1], KEYS[2]) end
local roster = redis.call('HGETALL', KEYS[1])
local result = {1}
for _, value in ipairs(roster) do table.insert(result, value) end
return result
"""

# One key, one script: the route set can never exist without its TTL.
_TOUCH_CONTACT_ROUTE = """
redis.call('SADD', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""

# Which room a key is registered in, so a linked device can be found from
# another room. Only a hint: the room's own member hash stays authoritative.
_DISCARD_PRESENCE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""

# Replace one key's consent set. An older request than the stored one is
# refused, so a delayed post cannot undo a later withdrawal (a withdrawal is
# stored as an empty set for a while). Returns the status and the previous peers.
_SYNC_LINKS = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local ttl = tonumber(ARGV[1])
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, tonumber(ARGV[5]))
for _, member in ipairs(expired) do redis.call('ZREM', KEYS[2], member) end
local previous = redis.call('GET', KEYS[1])
local before = {}
if previous then
  local decoded = cjson.decode(previous)
  if tonumber(decoded['at']) > tonumber(ARGV[2]) then return {'stale'} end
  before = decoded['peers']
end
if tonumber(ARGV[7]) == 0 then
  redis.call('SET', KEYS[1], ARGV[6], 'PX', tonumber(ARGV[8]))
  redis.call('ZREM', KEYS[2], ARGV[3])
  return {'stored', unpack(before)}
end
if #before == 0 and redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then return {'capacity'} end
redis.call('SET', KEYS[1], ARGV[6], 'PX', ttl)
redis.call('ZADD', KEYS[2], now + ttl, ARGV[3])
redis.call('PEXPIRE', KEYS[2], ttl * 2)
return {'stored', unpack(before)}
"""

_RENEW_ROOM = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return 0 end
local score = redis.call('ZSCORE', KEYS[2], ARGV[1])
if not score or tonumber(score) <= now then return 0 end
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[3]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[3]) * 2)
return 1
"""

_CONSUME_ENROLLMENT_RATE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, tonumber(ARGV[5]))
for _, source in ipairs(expired) do redis.call('ZREM', KEYS[2], source) end
local counter_ttl = redis.call('PTTL', KEYS[1])
local indexed = redis.call('ZSCORE', KEYS[2], ARGV[3])
if not indexed then
  if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then return -1 end
end
local expires_at = now + tonumber(ARGV[2])
if counter_ttl > 0 then expires_at = now + counter_ttl end
redis.call('ZADD', KEYS[2], expires_at, ARGV[3])
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[2]) * 2)
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2])) end
if count > tonumber(ARGV[1]) then return 0 end
return 1
"""

_CONSUME_ENROLLMENT_NONCE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local ttl = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - ttl)
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, tonumber(ARGV[6]))
for _, member in ipairs(expired) do redis.call('ZREM', KEYS[2], member) end
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 0 end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return -1 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then return -1 end
redis.call('ZADD', KEYS[1], now, ARGV[1])
redis.call('PEXPIRE', KEYS[1], ttl)
redis.call('ZADD', KEYS[2], now + ttl, ARGV[5] .. ':' .. ARGV[1])
redis.call('PEXPIRE', KEYS[2], ttl * 2)
return 1
"""

_CREATE_ENROLLMENT_OFFER = """
local existing = redis.call('HGET', KEYS[1], 'create_digest')
if existing then
  if existing == ARGV[4] then return 2 end
  return -1
end
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
for i = 7, #ARGV, 2 do
  redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
end
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[1]))
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[1]), ARGV[2])
-- Keep the active index alive longer than every service-enforced offer TTL.
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[5]))
if ARGV[6] ~= '' then
  redis.call('SET', KEYS[3], ARGV[2])
  redis.call('PEXPIRE', KEYS[3], tonumber(ARGV[1]))
end
return 1
"""

_SUBMIT_ENROLLMENT_REQUEST = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 'unavailable' end
local existing = redis.call('HGET', KEYS[1], 'request_round_id')
if existing then
  if existing == ARGV[1]
      and redis.call('HGET', KEYS[1], 'request_digest') == ARGV[5]
      and redis.call('HGET', KEYS[1], 'destination_key') == ARGV[2] then
    return 'duplicate'
  end
  return 'bound'
end
if redis.call('HGET', KEYS[1], 'state') ~= 'open' then return 'unavailable' end
local function burn()
  local lookup_hash = redis.call('HGET', KEYS[1], 'lookup_hash')
  redis.call('DEL', KEYS[1])
  if lookup_hash and lookup_hash ~= '' then
    redis.call('DEL', 'kollab:relay:enrollment:{mailbox}:lookup:' .. lookup_hash)
  end
end
local failed = tonumber(redis.call('HGET', KEYS[1], 'failed_codes') or '0')
if failed >= tonumber(ARGV[6]) then
  burn()
  return 'unavailable'
end
local stored = redis.call('HGET', KEYS[1], 'code_verifier_hash') or ''
local difference = 0
for i = 1, 64 do
  difference = difference + math.abs((string.byte(stored, i) or 0) - (string.byte(ARGV[3], i) or 0))
end
if #stored ~= 64 or #ARGV[3] ~= 64 or difference ~= 0 then
  failed = redis.call('HINCRBY', KEYS[1], 'failed_codes', 1)
  if failed >= tonumber(ARGV[6]) then
    burn()
    return 'unavailable'
  end
  return 'invalid_code'
end
redis.call('HSET', KEYS[1],
  'state', 'request_pending',
  'destination_key', ARGV[2],
  'request_round_id', ARGV[1],
  'request_digest', ARGV[5],
  'request_envelope', ARGV[4],
  'request_claim_id', '',
  'device_name', ARGV[7])
return 'accepted'
"""

_FIND_ENROLLMENT_OFFER_BY_LOOKUP = """
local offer_id = redis.call('GET', KEYS[1])
if not offer_id then return false end
local offer_key = 'kollab:relay:enrollment:{mailbox}:offer:' .. offer_id
if redis.call('HGET', offer_key, 'state') ~= 'open' then return false end
return offer_id
"""

_CLAIM_ENROLLMENT_ROUND = """
if redis.call('EXISTS', KEYS[1]) == 0
    or redis.call('HGET', KEYS[1], 'issuer_key') ~= ARGV[1] then
  return {'unavailable'}
end
local state = redis.call('HGET', KEYS[1], 'state')
local phase
if state == 'request_pending' or state == 'request_claimed' then
  phase = 'request'
elseif state == 'proof_pending' or state == 'proof_claimed' then
  phase = 'proof'
else
  return {'empty'}
end
local claim_field = phase .. '_claim_id'
if state == phase .. '_pending' then
  redis.call('HSET', KEYS[1], 'state', phase .. '_claimed', claim_field, ARGV[2])
elseif redis.call('HGET', KEYS[1], claim_field) ~= ARGV[2] then
  return {'claimed'}
end
return {
  'claimed', phase,
  redis.call('HGET', KEYS[1], phase .. '_round_id') or '',
  redis.call('HGET', KEYS[1], 'destination_key') or '',
  redis.call('HGET', KEYS[1], phase .. '_envelope') or '',
  redis.call('HGET', KEYS[1], 'device_name') or ''
}
"""

_PUBLISH_ENROLLMENT_CHALLENGE = """
if redis.call('EXISTS', KEYS[1]) == 0
    or redis.call('HGET', KEYS[1], 'issuer_key') ~= ARGV[1]
    or redis.call('HGET', KEYS[1], 'destination_key') ~= ARGV[2] then
  return 'unavailable'
end
local existing = redis.call('HGET', KEYS[1], 'challenge_round_id')
if existing then
  if existing == ARGV[3]
      and redis.call('HGET', KEYS[1], 'challenge_digest') == ARGV[5] then
    return 'duplicate'
  end
  return 'conflict'
end
if redis.call('HGET', KEYS[1], 'state') ~= 'request_claimed' then return 'not_ready' end
redis.call('HSET', KEYS[1],
  'state', 'challenge_ready',
  'challenge_round_id', ARGV[3],
  'challenge_envelope', ARGV[4],
  'challenge_digest', ARGV[5])
return 'stored'
"""

_SUBMIT_ENROLLMENT_PROOF = """
if redis.call('EXISTS', KEYS[1]) == 0
    or redis.call('HGET', KEYS[1], 'destination_key') ~= ARGV[2] then
  return 'unavailable'
end
local existing = redis.call('HGET', KEYS[1], 'proof_round_id')
if existing then
  if existing == ARGV[1]
      and redis.call('HGET', KEYS[1], 'proof_digest') == ARGV[4] then
    return 'duplicate'
  end
  return 'conflict'
end
if redis.call('HGET', KEYS[1], 'state') ~= 'challenge_ready' then return 'not_ready' end
redis.call('HSET', KEYS[1],
  'state', 'proof_pending',
  'proof_round_id', ARGV[1],
  'proof_envelope', ARGV[3],
  'proof_digest', ARGV[4],
  'proof_claim_id', '')
return 'stored'
"""

_PUBLISH_ENROLLMENT_DECISION = """
if redis.call('EXISTS', KEYS[1]) == 0
    or redis.call('HGET', KEYS[1], 'issuer_key') ~= ARGV[1]
    or redis.call('HGET', KEYS[1], 'destination_key') ~= ARGV[2] then
  return 'unavailable'
end
local existing = redis.call('HGET', KEYS[1], 'decision_round_id')
if existing then
  if existing == ARGV[3]
      and redis.call('HGET', KEYS[1], 'decision_digest') == ARGV[5] then
    return 'duplicate'
  end
  return 'conflict'
end
if redis.call('HGET', KEYS[1], 'state') ~= 'proof_claimed' then return 'not_ready' end
redis.call('HSET', KEYS[1],
  'state', 'closed',
  'decision_round_id', ARGV[3],
  'decision_envelope', ARGV[4],
  'decision_digest', ARGV[5])
return 'stored'
"""

_POLL_ENROLLMENT_REPLY = """
if redis.call('EXISTS', KEYS[1]) == 0
    or redis.call('HGET', KEYS[1], 'destination_key') ~= ARGV[1] then
  return {'unavailable'}
end
local phase
if redis.call('HGET', KEYS[1], 'decision_round_id') then
  phase = 'decision'
elseif redis.call('HGET', KEYS[1], 'challenge_round_id') then
  phase = 'challenge'
else
  return {'pending'}
end
return {
  'ready', phase,
  redis.call('HGET', KEYS[1], phase .. '_round_id') or '',
  redis.call('HGET', KEYS[1], phase .. '_envelope') or ''
}
"""

_SUBMIT_ENROLLMENT_INSTALL_ACK = """
if redis.call('EXISTS', KEYS[1]) == 0
    or redis.call('HGET', KEYS[1], 'destination_key') ~= ARGV[2]
    or redis.call('HGET', KEYS[1], 'decision_round_id') ~= ARGV[1] then
  return 'unavailable'
end
local existing = redis.call('HGET', KEYS[1], 'install_ack_digest')
if existing then
  if existing == ARGV[3] then return 'duplicate' end
  return 'conflict'
end
redis.call('HSET', KEYS[1],
  'install_ack_frame', ARGV[4],
  'install_ack_digest', ARGV[3])
return 'stored'
"""

_POLL_ENROLLMENT_INSTALL_ACK = """
if redis.call('EXISTS', KEYS[1]) == 0
    or redis.call('HGET', KEYS[1], 'issuer_key') ~= ARGV[1] then
  return {'unavailable'}
end
local frame = redis.call('HGET', KEYS[1], 'install_ack_frame')
if not frame then return {'pending'} end
return {'ready', frame}
"""


class _ShardedPubSub:
    """Adapter issuing Redis 7 SSUBSCRIBE with redis-py async PubSub."""

    def __init__(self, redis_client: Any):
        from redis.asyncio.client import PubSub

        class PubSubWithShards(PubSub):
            PUBLISH_MESSAGE_TYPES = tuple(PubSub.PUBLISH_MESSAGE_TYPES) + ("smessage",)

            async def ssubscribe(self, channel: str) -> None:
                await self.execute_command("SSUBSCRIBE", channel)
                self.channels.update(self._normalize_keys({channel: None}))

            async def on_connect(self, connection: Any) -> None:
                self.pending_unsubscribe_channels.clear()
                if self.channels:
                    channels = [
                        self.encoder.decode(value, force=True)
                        for value in self.channels
                    ]
                    await self.execute_command("SSUBSCRIBE", *channels)

        self.pubsub = PubSubWithShards(
            redis_client.connection_pool, ignore_subscribe_messages=False
        )

    async def subscribe(self, channel: str) -> None:
        await self.pubsub.ssubscribe(channel)

    async def listen(self):
        async for message in self.pubsub.listen():
            yield message

    async def ping(self, payload: str) -> None:
        await self.pubsub.ping(payload)

    async def close(self) -> None:
        await self.pubsub.aclose()


class RedisRelayBackend:
    """Redis/Valkey standalone or cluster backend with leased presence."""

    def __init__(
        self,
        url: str,
        node_id: str,
        *,
        cluster: bool,
        limits: RelayLimits,
    ):
        self.url = url
        self.node_id = node_id
        self.cluster = cluster
        self.limits = limits
        self._redis: Any = None
        self._pubsub_redis: Any = None
        self._pubsub: Any = None
        self._listener: asyncio.Task | None = None
        self._state: Any = None
        self._healthy = False
        self._subscription_ready = asyncio.Event()
        self._last_pong = 0.0
        self._last_probe_success = 0.0
        self._subscription_lock = asyncio.Lock()
        self._pending_routes: dict[str, asyncio.Future[bool]] = {}
        self._pending_probes: dict[str, asyncio.Future[bool]] = {}
        self._queued_rooms: set[str] = set()
        self._processing_rooms: set[str] = set()
        self._room_change_again: set[str] = set()
        work_queue_items = min(
            MAX_WORK_QUEUE_ITEMS, max(32, limits.max_connections_per_node * 2)
        )
        self._work_queue: asyncio.Queue[tuple[str, dict[str, Any], int]] = (
            asyncio.Queue(maxsize=work_queue_items)
        )
        # Count queued and currently processed messages. This keeps the memory
        # budget in force while workers hold payloads after Queue.get().
        self._work_queue_items = 0
        self._work_queue_bytes = 0
        self._ack_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=min(MAX_ACK_QUEUE_ITEMS, max(32, limits.max_connections_per_node))
        )
        self._workers: list[asyncio.Task[None]] = []
        self._ack_worker: asyncio.Task[None] | None = None
        self._node_channel = self._node_channel_for(node_id)
        self._boot_token = secrets.token_hex(32)
        self._owner_acquired = False

    async def start(self, state: Any) -> None:
        try:
            if self.cluster:
                from redis.asyncio.cluster import RedisCluster

                self._redis = RedisCluster.from_url(
                    self.url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=5,
                    health_check_interval=15,
                    max_connections=128,
                )
                await self._redis.initialize()
                owner = self._redis.get_node_from_key(self._node_channel)
                if owner is None:
                    raise RelayBackendError("cluster has no owner for the worker inbox")
                self._pubsub_redis = self._redis_for_cluster_node(owner)
                self._pubsub = _ShardedPubSub(self._pubsub_redis)
                await self._pubsub.subscribe(self._node_channel)
            else:
                from redis.asyncio import Redis

                self._redis = Redis.from_url(
                    self.url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=5,
                    health_check_interval=15,
                    max_connections=128,
                )
                self._pubsub_redis = Redis.from_url(
                    self.url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=None,
                    health_check_interval=15,
                    max_connections=2,
                )
                self._pubsub = self._pubsub_redis.pubsub(
                    ignore_subscribe_messages=False
                )
                await self._pubsub.subscribe(self._node_channel)

            self._state = state
            acquired = await self._redis.set(
                self._owner_key(self.node_id),
                self._boot_token,
                nx=True,
                px=LEASE_SECONDS * 1000,
            )
            if not acquired:
                raise RelayBackendError(
                    "worker node id is already active or its prior lease has not expired"
                )
            self._owner_acquired = True
            # The exclusive owner lease proves any prior worker incarnation is
            # gone; stale per-node reservations can now be discarded safely.
            await self._redis.delete(self._node_quota_key(self.node_id))
            self._healthy = True
            self._last_pong = time.monotonic()
            self._listener = asyncio.create_task(
                self._listen(), name=f"kollab-relay-backplane-{self.node_id}"
            )
            worker_count = min(8, max(1, self.limits.max_connections_per_node))
            self._workers = [
                asyncio.create_task(
                    self._work_loop(), name=f"kollab-relay-work-{self.node_id}-{index}"
                )
                for index in range(worker_count)
            ]
            self._ack_worker = asyncio.create_task(
                self._ack_loop(), name=f"kollab-relay-ack-{self.node_id}"
            )
            await asyncio.wait_for(self._subscription_ready.wait(), timeout=5)
            if not await self.health():
                raise RelayBackendError("relay backplane health check failed")
        except Exception as exc:
            await self.close()
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError(
                "cannot connect to configured Redis-compatible backend"
            ) from exc

    async def close(self) -> None:
        self._healthy = False
        if self._listener is not None:
            self._listener.cancel()
            await asyncio.gather(self._listener, return_exceptions=True)
            self._listener = None
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()
        if self._ack_worker is not None:
            self._ack_worker.cancel()
            await asyncio.gather(self._ack_worker, return_exceptions=True)
            self._ack_worker = None
        if self._pubsub is not None:
            try:
                closer = getattr(self._pubsub, "aclose", None)
                if closer is None:
                    closer = self._pubsub.close
                await closer()
            except Exception:
                pass
            self._pubsub = None
        if self._pubsub_redis is not None:
            try:
                await self._pubsub_redis.aclose()
            except Exception:
                pass
            self._pubsub_redis = None
        if self._redis is not None:
            if self._owner_acquired:
                try:
                    await self._redis.eval(
                        _RELEASE_OWNER,
                        1,
                        self._owner_key(self.node_id),
                        self._boot_token,
                    )
                except Exception:
                    pass
                self._owner_acquired = False
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    async def health(self) -> bool:
        if (
            not self._healthy
            or self._redis is None
            or self._listener is None
            or self._listener.done()
            or not self._subscription_ready.is_set()
        ):
            return False
        try:
            result = await self._redis.ping()
            if not result:
                return False
            owner_ok = await self._redis.eval(
                _RENEW_OWNER,
                1,
                self._owner_key(self.node_id),
                self._boot_token,
                LEASE_SECONDS * 1000,
            )
            if int(owner_ok) != 1:
                self._healthy = False
                return False
            probe_id = secrets.token_hex(16)
            future = asyncio.get_running_loop().create_future()
            self._pending_probes[probe_id] = future
            await self._publish_node(
                self.node_id, {"type": "probe", "probe_id": probe_id}
            )
            try:
                alive = await asyncio.wait_for(
                    asyncio.shield(future), timeout=ROUTE_TIMEOUT_SECONDS
                )
            finally:
                self._pending_probes.pop(probe_id, None)
                if not future.done():
                    future.cancel()
            if alive:
                self._last_probe_success = time.monotonic()
            return alive
        except Exception:
            self._healthy = False
            return False

    @property
    def is_ready(self) -> bool:
        return bool(
            self._healthy
            and self._listener is not None
            and not self._listener.done()
            and self._subscription_ready.is_set()
            and self._last_probe_success
            and time.monotonic() - self._last_probe_success
            <= LEASE_RENEW_SECONDS * 2 + ROUTE_TIMEOUT_SECONDS
        )

    async def owner_valid(self) -> bool:
        if not self._healthy or self._redis is None:
            return False
        try:
            valid = await self._redis.get(self._owner_key(self.node_id))
            if valid != self._boot_token:
                self._healthy = False
                return False
            return True
        except Exception:
            self._healthy = False
            return False

    async def reserve_connection(
        self, connection_id: str, source_ip: str
    ) -> str | None:
        if not self._healthy:
            raise RelayBackendError("relay backend is not ready")
        if not await self.owner_valid():
            raise RelayBackendError("relay worker no longer owns its node identity")
        source_hash = hashlib.sha256(source_ip.encode("ascii")).hexdigest()
        lease_ms = LEASE_SECONDS * 1000
        node_key = self._node_quota_key(self.node_id)
        ip_key = self._ip_key(source_hash)
        try:
            reserved_node = await self._redis.eval(
                _RESERVE_LEASE,
                1,
                node_key,
                connection_id,
                lease_ms,
                self.limits.max_connections_per_node,
            )
            if int(reserved_node) != 1:
                return None
            try:
                reserved_ip = await self._redis.eval(
                    _RESERVE_LEASE,
                    1,
                    ip_key,
                    connection_id,
                    lease_ms,
                    self.limits.max_connections_per_source,
                )
            except Exception:
                await asyncio.gather(
                    self._redis.eval(_RELEASE_LEASE, 1, node_key, connection_id),
                    self._redis.eval(_RELEASE_LEASE, 1, ip_key, connection_id),
                    return_exceptions=True,
                )
                raise
            if int(reserved_ip) != 1:
                await asyncio.gather(
                    self._redis.eval(_RELEASE_LEASE, 1, node_key, connection_id),
                    self._redis.eval(_RELEASE_LEASE, 1, ip_key, connection_id),
                    return_exceptions=True,
                )
                return None
            return source_hash
        except Exception as exc:
            await asyncio.gather(
                self._redis.eval(_RELEASE_LEASE, 1, node_key, connection_id),
                self._redis.eval(_RELEASE_LEASE, 1, ip_key, connection_id),
                return_exceptions=True,
            )
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError(
                "shared relay connection quota is unavailable"
            ) from exc

    async def release_connection(
        self, connection_id: str, source_ip: str, reservation: str | None
    ) -> None:
        if self._redis is None or reservation is None:
            return
        try:
            await asyncio.gather(
                self._redis.eval(
                    _RELEASE_LEASE,
                    1,
                    self._node_quota_key(self.node_id),
                    connection_id,
                ),
                self._redis.eval(
                    _RELEASE_LEASE,
                    1,
                    self._ip_key(reservation),
                    connection_id,
                ),
            )
        except Exception as exc:
            raise RelayBackendError("could not release relay connection quota") from exc

    async def register_room(
        self, room_hash: str, member: PeerRecord
    ) -> tuple[str | None, list[PeerRecord]]:
        try:
            result = await self._redis.eval(
                _JOIN_ROOM,
                2,
                *self._room_keys(room_hash),
                member.key,
                member.as_json(),
                LEASE_SECONDS * 1000,
                self.limits.max_connections_per_room,
            )
            status = str(result[0])
            if status != "ok":
                return status, []
            await self._touch_contact_route(member.key)
            await self._touch_presence(member.key, room_hash)
            return None, self._parse_hgetall(result[2:])
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("shared room registration is unavailable") from exc

    async def list_room(self, room_hash: str) -> tuple[list[PeerRecord], bool]:
        try:
            result = await self._redis.eval(_LIST_ROOM, 2, *self._room_keys(room_hash))
            pruned = int(result[0]) > 0
            records = self._parse_hgetall(result[1:])
            return records, pruned
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("shared room roster is unavailable") from exc

    async def remove_peer(
        self, room_hash: str, member: PeerRecord
    ) -> tuple[bool, list[PeerRecord]]:
        try:
            result = await self._redis.eval(
                _REMOVE_PEER,
                2,
                *self._room_keys(room_hash),
                member.key,
                member.as_json(),
            )
            removed = int(result[0]) == 1
            if removed:
                await self._discard_contact_route(member.key, room_hash)
                await self._discard_presence(member.key, room_hash)
            return removed, self._parse_hgetall(result[1:])
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("shared peer removal is unavailable") from exc

    async def _touch_contact_route(self, key: str) -> None:
        # The route index sits beside the atomic room scripts, not inside
        # them (its key lives in another hash slot), so a knock lookup can be
        # briefly stale after a room change. A failure is logged, never
        # raised: it only ages the entry out within LEASE_SECONDS.
        try:
            await self._redis.eval(
                _TOUCH_CONTACT_ROUTE,
                1,
                self._contact_route_key(contact_route_hex(key)),
                key,
                LEASE_SECONDS,
            )
        except Exception:
            logger.warning("contact route index could not be updated")

    async def _discard_contact_route(self, key: str, room_hash: str) -> None:
        """Unlist a key whose room membership just ended, unless it is back.

        A reconnect can register the key between the room removal and this
        call; the membership check after the SREM lists it again instead of
        leaving a live device unreachable until its next renewal.
        """
        try:
            await self._redis.srem(self._contact_route_key(contact_route_hex(key)), key)
            if await self._redis.hexists(self._room_keys(room_hash)[0], key):
                await self._touch_contact_route(key)
        except Exception:
            logger.warning("contact route index could not be updated")

    async def lookup_contact_route(self, route_hex: str) -> list[str]:
        """Keys currently registered under this route (0, 1, or >1 = ambiguous)."""
        try:
            members = await self._redis.smembers(self._contact_route_key(route_hex))
        except Exception as exc:
            raise RelayBackendError("contact route lookup is unavailable") from exc
        return sorted(members)

    async def _touch_presence(self, key: str, room_hash: str) -> None:
        # Best effort like the route index: a miss only hides the device from
        # its linked peers until the next lease renewal.
        try:
            await self._redis.set(
                self._presence_key(key), room_hash, px=LEASE_SECONDS * 1000
            )
        except Exception:
            logger.warning("link presence index could not be updated")

    async def _discard_presence(self, key: str, room_hash: str) -> None:
        try:
            await self._redis.eval(
                _DISCARD_PRESENCE, 1, self._presence_key(key), room_hash
            )
        except Exception:
            logger.warning("link presence index could not be updated")

    async def sync_links(
        self, key: str, peers: list[str], *, issued_at: int, ttl_ms: int
    ) -> tuple[str, list[str]]:
        """Replace what `key` consents to link with; return the peers that changed."""
        try:
            encoded = json.dumps(
                {"at": issued_at, "peers": peers}, sort_keys=True, separators=(",", ":")
            )
            result = await self._redis.eval(
                _SYNC_LINKS,
                2,
                self._link_key(key),
                self._link_index_key(),
                ttl_ms,
                issued_at,
                key,
                MAX_ACTIVE_LINK_DECLARATIONS,
                LINK_INDEX_CLEANUP_BATCH,
                encoded,
                len(peers),
                LINK_WITHDRAWAL_SECONDS * 1000,
            )
            status = str(result[0])
            if status != "stored":
                return status, []
            before = {str(item) for item in result[1:]}
            return "stored", sorted(before ^ set(peers))
        except Exception as exc:
            raise RelayBackendError("link storage is unavailable") from exc

    async def _declared(self, keys: list[str]) -> list[frozenset[str]]:
        try:
            rows = await self._redis.mget([self._link_key(key) for key in keys])
            return [self._parse_link_row(row) for row in rows]
        except RelayBackendError:
            raise
        except Exception as exc:
            raise RelayBackendError("link lookup is unavailable") from exc

    @staticmethod
    def _parse_link_row(raw: Any) -> frozenset[str]:
        if raw is None:
            return frozenset()
        try:
            value = json.loads(raw, object_pairs_hook=_unique_pairs)
        except (TypeError, ValueError):
            raise RelayBackendError("stored link declaration is malformed") from None
        if (
            not isinstance(value, dict)
            or set(value) != {"at", "peers"}
            or not isinstance(value["peers"], list)
            or not all(_is_public_key(peer) for peer in value["peers"])
        ):
            raise RelayBackendError("stored link declaration is malformed")
        return frozenset(value["peers"])

    async def linked_keys(self, key: str) -> list[str]:
        """Keys that mutually consented to link with `key`."""
        (mine,) = await self._declared([key])
        if not mine:
            return []
        candidates = sorted(mine)
        theirs = await self._declared(candidates)
        return [
            other for other, declared in zip(candidates, theirs) if key in declared
        ]

    async def is_linked(self, first: str, second: str) -> bool:
        mine, theirs = await self._declared([first, second])
        return second in mine and first in theirs

    async def locate(self, key: str) -> tuple[PeerRecord, str] | None:
        """The live record and room of a key, wherever it is registered."""
        try:
            room_hash = await self._redis.get(self._presence_key(key))
        except Exception as exc:
            raise RelayBackendError("link presence lookup is unavailable") from exc
        if not _is_room_hash(room_hash):
            return None
        records, _ = await self.list_room(room_hash)
        record = next((item for item in records if item.key == key), None)
        return (record, room_hash) if record is not None else None

    async def renew(
        self,
        connection_id: str,
        source_ip: str,
        reservation: str | None,
        member: PeerRecord | None,
        room_hash: str | None,
    ) -> bool:
        if reservation is None:
            return False
        try:
            node_result, ip_result = await asyncio.gather(
                self._redis.eval(
                    _RENEW_LEASE,
                    1,
                    self._node_quota_key(self.node_id),
                    connection_id,
                    LEASE_SECONDS * 1000,
                ),
                self._redis.eval(
                    _RENEW_LEASE,
                    1,
                    self._ip_key(reservation),
                    connection_id,
                    LEASE_SECONDS * 1000,
                ),
            )
            if int(node_result) != 1 or int(ip_result) != 1:
                return False
            if member is None:
                return True
            if room_hash is None:
                return False
            room_result = await self._redis.eval(
                _RENEW_ROOM,
                2,
                *self._room_keys(room_hash),
                member.key,
                member.as_json(),
                LEASE_SECONDS * 1000,
            )
            renewed = int(room_result) == 1
            if renewed:
                await self._touch_contact_route(member.key)
                await self._touch_presence(member.key, room_hash)
            return renewed
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("relay lease renewal failed") from exc

    async def consume_enrollment_rate(
        self, source_hash: str, *, limit: int, window_ms: int
    ) -> bool:
        try:
            result = await self._redis.eval(
                _CONSUME_ENROLLMENT_RATE,
                2,
                self._enrollment_rate_key(source_hash),
                self._enrollment_rate_index_key(),
                limit,
                window_ms,
                source_hash,
                ENROLLMENT_MAX_RATE_SOURCES,
                ENROLLMENT_INDEX_CLEANUP_BATCH,
            )
            return int(result) == 1
        except Exception as exc:
            raise RelayBackendError("enrollment rate limiter is unavailable") from exc

    async def consume_enrollment_nonce(
        self,
        principal_hash: str,
        nonce_hash: str,
        *,
        ttl_ms: int,
        capacity: int,
    ) -> bool:
        try:
            result = int(
                await self._redis.eval(
                    _CONSUME_ENROLLMENT_NONCE,
                    2,
                    self._enrollment_nonce_key(principal_hash),
                    self._enrollment_nonce_index_key(),
                    nonce_hash,
                    ttl_ms,
                    capacity,
                    ENROLLMENT_MAX_NONCES,
                    principal_hash,
                    ENROLLMENT_INDEX_CLEANUP_BATCH,
                )
            )
            if result < 0:
                raise RelayBackendError("enrollment nonce capacity is full")
            return result == 1
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("enrollment nonce check is unavailable") from exc

    async def enrollment_admission_usage(self) -> dict[str, int]:
        try:
            pipeline = self._redis.pipeline(transaction=False)
            pipeline.zcard(self._enrollment_rate_index_key())
            pipeline.zcard(self._enrollment_nonce_index_key())
            rate_sources, nonce_records = await pipeline.execute()
            return {
                "rate_sources": int(rate_sources),
                "nonce_records": int(nonce_records),
            }
        except Exception as exc:
            raise RelayBackendError(
                "enrollment admission usage is unavailable"
            ) from exc

    async def create_enrollment_offer(
        self, offer_id: str, fields: dict[str, str], *, ttl_ms: int, capacity: int
    ) -> str:
        try:
            lookup_hash = fields.get("lookup_hash", "")
            stored_fields = {
                "state": "open",
                "failed_codes": "0",
                "device_name": "",
                **fields,
            }
            args: list[str | int] = [
                ttl_ms,
                offer_id,
                capacity,
                fields["create_digest"],
                ENROLLMENT_CAPACITY_INDEX_TTL_MS,
                lookup_hash,
            ]
            for key, value in sorted(stored_fields.items()):
                args.extend((key, value))
            lookup_key = (
                self._enrollment_lookup_key(lookup_hash)
                if lookup_hash
                else self._enrollment_key(offer_id)
            )
            result = int(
                await self._redis.eval(
                    _CREATE_ENROLLMENT_OFFER,
                    3,
                    self._enrollment_key(offer_id),
                    self._enrollment_capacity_key(),
                    lookup_key,
                    *args,
                )
            )
            if result == 0:
                return "capacity"
            if result == -1:
                return "conflict"
            return (
                "created" if result == 1 else "duplicate" if result == 2 else "conflict"
            )
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("enrollment offer storage is unavailable") from exc

    async def find_enrollment_offer_by_lookup(self, lookup_hash: str) -> str | None:
        try:
            result = await self._redis.eval(
                _FIND_ENROLLMENT_OFFER_BY_LOOKUP,
                1,
                self._enrollment_lookup_key(lookup_hash),
            )
            return str(result) if result else None
        except Exception as exc:
            raise RelayBackendError("enrollment lookup storage is unavailable") from exc

    async def submit_enrollment_request(
        self,
        offer_id: str,
        *,
        round_id: str,
        destination_key: str,
        candidate_hash: str,
        envelope: str,
        content_digest: str,
        device_name: str = "",
    ) -> str:
        try:
            result = await self._redis.eval(
                _SUBMIT_ENROLLMENT_REQUEST,
                1,
                self._enrollment_key(offer_id),
                round_id,
                destination_key,
                candidate_hash,
                envelope,
                content_digest,
                ENROLLMENT_MAX_FAILED_CODES,
                device_name,
            )
            return str(result)
        except Exception as exc:
            raise RelayBackendError(
                "enrollment request storage is unavailable"
            ) from exc

    async def claim_enrollment_round(
        self, offer_id: str, *, issuer_key: str, claim_id: str
    ) -> dict[str, str]:
        try:
            result = await self._redis.eval(
                _CLAIM_ENROLLMENT_ROUND,
                1,
                self._enrollment_key(offer_id),
                issuer_key,
                claim_id,
            )
            values = [str(value) for value in result]
            if values[0] != "claimed" or len(values) != 6:
                return {"status": values[0]}
            return {
                "status": values[0],
                "phase": values[1],
                "round_id": values[2],
                "destination_key": values[3],
                "envelope": values[4],
                "device_name": values[5],
            }
        except Exception as exc:
            raise RelayBackendError("enrollment claim is unavailable") from exc

    async def publish_enrollment_challenge(
        self,
        offer_id: str,
        *,
        issuer_key: str,
        destination_key: str,
        round_id: str,
        envelope: str,
        content_digest: str,
    ) -> str:
        try:
            result = await self._redis.eval(
                _PUBLISH_ENROLLMENT_CHALLENGE,
                1,
                self._enrollment_key(offer_id),
                issuer_key,
                destination_key,
                round_id,
                envelope,
                content_digest,
            )
            return str(result)
        except Exception as exc:
            raise RelayBackendError(
                "enrollment challenge storage is unavailable"
            ) from exc

    async def submit_enrollment_proof(
        self,
        offer_id: str,
        *,
        round_id: str,
        destination_key: str,
        envelope: str,
        content_digest: str,
    ) -> str:
        try:
            result = await self._redis.eval(
                _SUBMIT_ENROLLMENT_PROOF,
                1,
                self._enrollment_key(offer_id),
                round_id,
                destination_key,
                envelope,
                content_digest,
            )
            return str(result)
        except Exception as exc:
            raise RelayBackendError("enrollment proof storage is unavailable") from exc

    async def publish_enrollment_decision(
        self,
        offer_id: str,
        *,
        issuer_key: str,
        destination_key: str,
        round_id: str,
        envelope: str,
        content_digest: str,
    ) -> str:
        try:
            result = await self._redis.eval(
                _PUBLISH_ENROLLMENT_DECISION,
                1,
                self._enrollment_key(offer_id),
                issuer_key,
                destination_key,
                round_id,
                envelope,
                content_digest,
            )
            return str(result)
        except Exception as exc:
            raise RelayBackendError(
                "enrollment decision storage is unavailable"
            ) from exc

    async def poll_enrollment_reply(
        self, offer_id: str, *, destination_key: str
    ) -> dict[str, str]:
        try:
            result = await self._redis.eval(
                _POLL_ENROLLMENT_REPLY,
                1,
                self._enrollment_key(offer_id),
                destination_key,
            )
            values = [str(value) for value in result]
            if values[0] != "ready" or len(values) != 4:
                return {"status": values[0]}
            return {
                "status": values[0],
                "phase": values[1],
                "round_id": values[2],
                "envelope": values[3],
            }
        except Exception as exc:
            raise RelayBackendError("enrollment reply poll is unavailable") from exc

    async def submit_enrollment_install_ack(
        self,
        offer_id: str,
        *,
        round_id: str,
        destination_key: str,
        frame: dict[str, Any],
        content_digest: str,
    ) -> str:
        try:
            encoded_frame = json.dumps(
                frame, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            result = await self._redis.eval(
                _SUBMIT_ENROLLMENT_INSTALL_ACK,
                1,
                self._enrollment_key(offer_id),
                round_id,
                destination_key,
                content_digest,
                encoded_frame,
            )
            return str(result)
        except Exception as exc:
            raise RelayBackendError(
                "enrollment installation receipt storage is unavailable"
            ) from exc

    async def poll_enrollment_install_ack(
        self, offer_id: str, *, issuer_key: str
    ) -> dict[str, Any]:
        try:
            result = await self._redis.eval(
                _POLL_ENROLLMENT_INSTALL_ACK,
                1,
                self._enrollment_key(offer_id),
                issuer_key,
            )
            values = [item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in result]
            if values[0] != "ready" or len(values) != 2:
                return {"status": values[0]}
            try:
                frame = json.loads(values[1])
            except (json.JSONDecodeError, TypeError) as exc:
                raise RelayBackendError(
                    "enrollment installation receipt is malformed"
                ) from exc
            if not isinstance(frame, dict):
                raise RelayBackendError(
                    "enrollment installation receipt is malformed"
                )
            return {"status": "ready", "frame": frame}
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError(
                "enrollment installation receipt poll is unavailable"
            ) from exc

    async def notify_room_change(self, room_hash: str, node_ids: set[str]) -> None:
        payload = {"type": "room_changed", "room_hash": room_hash}
        for node_id in sorted(node_ids):
            try:
                await self._publish_node(node_id, payload)
            except Exception as exc:
                raise RelayBackendError(
                    "could not publish room presence update"
                ) from exc

    async def forward(self, destination: PeerRecord, route: dict[str, str]) -> bool:
        if destination.node_id == self.node_id:
            if not await self.owner_valid():
                return False
            return await self._state.deliver_local(
                {
                    **route,
                    "connection_id": destination.connection_id,
                    "to": destination.key,
                    "to_session": destination.session,
                }
            )
        if len(self._pending_routes) >= self.limits.max_connections_per_node:
            return False
        route_id = secrets.token_hex(16)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending_routes[route_id] = future
        payload = {
            "type": "route",
            "route_id": route_id,
            "reply_node": self.node_id,
            "room_hash": route["room_hash"],
            "connection_id": destination.connection_id,
            "to": destination.key,
            "to_session": destination.session,
            "from": route["from"],
            "session": route["session"],
            "id": route["id"],
            "ciphertext": route["ciphertext"],
            "kind": route.get("kind", "message"),
            "ticket": route.get("ticket", ""),
        }
        try:
            await self._publish_node(destination.node_id, payload)
            return await asyncio.wait_for(
                asyncio.shield(future), timeout=ROUTE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            return False
        except RelayBackendError:
            raise
        except Exception as exc:
            raise RelayBackendError("cross-node relay route failed") from exc
        finally:
            self._pending_routes.pop(route_id, None)
            if not future.done():
                future.cancel()

    async def _publish_node(self, node_id: str, payload: dict[str, Any]) -> int:
        encoded = _bounded_json(payload)
        channel = self._node_channel_for(node_id)
        if self.cluster:
            node = self._redis.get_node_from_key(channel)
            if node is None:
                raise RelayBackendError(
                    "cluster has no owner for destination worker inbox"
                )
            value = await self._redis.execute_command(
                "SPUBLISH", channel, encoded, target_nodes=node
            )
        else:
            value = await self._redis.publish(channel, encoded)
        return int(value)

    async def _listen(self) -> None:
        try:
            async for message in self._pubsub.listen():
                message_type = str(message.get("type", ""))
                if message_type in {"ssubscribe", "subscribe"}:
                    self._subscription_ready.set()
                    continue
                if message_type == "pong":
                    self._last_pong = time.monotonic()
                    continue
                if message_type not in {"message", "smessage"}:
                    continue
                channel = message.get("channel")
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8", errors="strict")
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="strict")
                if channel != self._node_channel or not isinstance(data, str):
                    continue
                if len(data.encode("utf-8")) > MAX_BACKPLANE_MESSAGE_BYTES:
                    continue
                parsed = self._parse_node_message(data)
                if parsed is None:
                    continue
                kind, payload = parsed
                if kind in {"probe", "ack"}:
                    self._handle_control_message(kind, payload)
                    continue
                negative_ack = self._enqueue_work(kind, payload)
                if negative_ack is not None:
                    try:
                        self._ack_queue.put_nowait(negative_ack)
                    except asyncio.QueueFull:
                        # Preserve an explicit overload result when the normal
                        # ack queue is saturated. Awaiting here applies backpressure
                        # to the Pub/Sub reader instead of allocating more tasks.
                        await self._publish_node(
                            negative_ack["reply_node"],
                            {
                                "type": "ack",
                                "route_id": negative_ack["route_id"],
                                "ok": False,
                            },
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._healthy = False
            if self._state is not None:
                await self._state.backend_failed()

    def _enqueue_work(
        self, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Queue validated backplane work within item and Python-payload budgets.

        A returned acknowledgement means a route was rejected and must be
        answered with ``ok=False``. Presence invalidations are coalesced by room;
        a dropped invalidation is reconciled by the periodic room scan.
        """
        room_hash = payload["room_hash"] if kind == "room_changed" else None
        if room_hash is not None and room_hash in self._queued_rooms:
            if room_hash in self._processing_rooms:
                self._room_change_again.add(room_hash)
            return None

        cost = _queued_work_cost(kind, payload)
        over_capacity = (
            self._work_queue_items >= self._work_queue.maxsize
            or self._work_queue_bytes + cost > MAX_WORK_QUEUE_BYTES
        )
        if over_capacity:
            if kind == "route":
                return _negative_route_ack(payload)
            return None

        if room_hash is not None:
            self._queued_rooms.add(room_hash)
        try:
            self._work_queue.put_nowait((kind, payload, cost))
        except asyncio.QueueFull:
            if room_hash is not None:
                self._queued_rooms.discard(room_hash)
            if kind == "route":
                return _negative_route_ack(payload)
            return None

        self._work_queue_items += 1
        self._work_queue_bytes += cost
        return None

    def _parse_node_message(self, raw: str) -> tuple[str, dict[str, Any]] | None:
        try:
            message = json.loads(raw, object_pairs_hook=_unique_pairs)
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            return None
        if message["type"] == "probe":
            if set(message) == {"type", "probe_id"} and _is_id(message["probe_id"]):
                return "probe", message
            return None
        if message["type"] == "ack":
            if (
                set(message) == {"type", "route_id", "ok"}
                and _is_id(message["route_id"])
                and isinstance(message["ok"], bool)
            ):
                return "ack", message
            return None
        if message["type"] == "room_changed":
            if set(message) == {"type", "room_hash"} and _is_room_hash(
                message["room_hash"]
            ):
                return "room_changed", message
            return None
        if message["type"] != "route" or self._state is None:
            return None
        required = {
            "type",
            "route_id",
            "reply_node",
            "room_hash",
            "connection_id",
            "to",
            "to_session",
            "from",
            "session",
            "id",
            "ciphertext",
            "kind",
            "ticket",
        }
        if set(message) != required:
            return None
        kind, ticket = message["kind"], message["ticket"]
        if (
            kind not in ("message", "knock", "knock_answer")
            or not isinstance(ticket, str)
            or len(ticket) > 2048
            or (kind == "knock") != bool(ticket)
        ):
            return None
        if (
            not _is_id(message["route_id"])
            or not _is_id(message["reply_node"])
            or not _is_room_hash(message["room_hash"])
            or not _is_id(message["connection_id"])
            or not _is_public_key(message["to"])
            or not _is_id(message["to_session"])
            or not _is_public_key(message["from"])
            or not _is_id(message["session"])
            or not _is_id(message["id"])
            or not isinstance(message["ciphertext"], str)
            or not message["ciphertext"]
            or len(message["ciphertext"]) > 48 * 1024
        ):
            return None
        return "route", message

    def _handle_control_message(self, kind: str, message: dict[str, Any]) -> None:
        if kind == "probe":
            future = self._pending_probes.get(message["probe_id"])
        else:
            future = self._pending_routes.get(message["route_id"])
        if future is not None and not future.done():
            future.set_result(True if kind == "probe" else message["ok"] is True)

    async def _work_loop(self) -> None:
        while True:
            kind, payload, cost = await self._work_queue.get()
            if kind == "room_changed":
                self._processing_rooms.add(payload["room_hash"])
            try:
                if kind == "room_changed":
                    await self._state.room_changed(payload["room_hash"])
                elif kind == "route":
                    try:
                        delivered = await self._state.deliver_local(payload)
                    except Exception:
                        delivered = False
                    ack = {
                        "type": "ack",
                        "reply_node": payload["reply_node"],
                        "route_id": payload["route_id"],
                        "ok": delivered,
                    }
                    await self._ack_queue.put(ack)
            except Exception:
                if kind == "route":
                    await self._ack_queue.put(_negative_route_ack(payload))
            finally:
                rerun_room = None
                if kind == "room_changed":
                    room_hash = payload["room_hash"]
                    self._processing_rooms.discard(room_hash)
                    if room_hash in self._room_change_again:
                        self._room_change_again.discard(room_hash)
                        rerun_room = room_hash
                    self._queued_rooms.discard(room_hash)
                self._work_queue_items -= 1
                self._work_queue_bytes -= cost
                self._work_queue.task_done()
                if rerun_room is not None:
                    self._enqueue_work(
                        "room_changed",
                        {"type": "room_changed", "room_hash": rerun_room},
                    )

    async def _ack_loop(self) -> None:
        while True:
            ack = await self._ack_queue.get()
            try:
                await self._publish_node(
                    ack["reply_node"],
                    {
                        "type": "ack",
                        "route_id": ack["route_id"],
                        "ok": ack["ok"],
                    },
                )
            except Exception:
                self._healthy = False
                if self._state is not None:
                    await self._state.backend_failed()
            finally:
                self._ack_queue.task_done()

    def _redis_for_cluster_node(self, node: Any) -> Any:
        from redis.asyncio import Redis
        from redis.asyncio.connection import ConnectionPool

        options = dict(self._redis.get_connection_kwargs())
        connection_class = options.pop("connection_class")
        options.pop("max_connections", None)
        options.pop("response_callbacks", None)
        options.pop("credential_provider", None)
        options["decode_responses"] = True
        # The subscriber is idle between relay events. Command-pool timeouts
        # would otherwise disconnect it before the periodic sharded self-probe.
        options["socket_timeout"] = None
        pool = ConnectionPool(
            connection_class=connection_class,
            max_connections=2,
            host=node.host,
            port=int(node.port),
            **options,
        )
        return Redis(connection_pool=pool)

    @staticmethod
    def _room_keys(room_hash: str) -> tuple[str, str]:
        tag = f"{{{room_hash}}}"
        return (
            f"kollab:relay:room:{tag}:members",
            f"kollab:relay:room:{tag}:leases",
        )

    @staticmethod
    def _node_quota_key(node_id: str) -> str:
        return f"kollab:relay:quota:{{node-{node_id}}}:leases"

    @staticmethod
    def _owner_key(node_id: str) -> str:
        return relay_owner_key(node_id)

    @staticmethod
    def _ip_key(source_hash: str) -> str:
        return f"kollab:relay:quota:{{ip-{source_hash}}}:leases"

    @staticmethod
    def _enrollment_key(offer_id: str) -> str:
        return f"kollab:relay:enrollment:{{mailbox}}:offer:{offer_id}"

    @staticmethod
    def _enrollment_lookup_key(lookup_hash: str) -> str:
        return f"kollab:relay:enrollment:{{mailbox}}:lookup:{lookup_hash}"

    @staticmethod
    def _enrollment_capacity_key() -> str:
        return "kollab:relay:enrollment:{mailbox}:active-offers"

    @staticmethod
    def _enrollment_rate_key(source_hash: str) -> str:
        return f"kollab:relay:enrollment:{{mailbox}}:rate:{source_hash}"

    @staticmethod
    def _enrollment_rate_index_key() -> str:
        return "kollab:relay:enrollment:{mailbox}:rate-sources"

    @staticmethod
    def _enrollment_nonce_key(principal_hash: str) -> str:
        return f"kollab:relay:enrollment:{{mailbox}}:nonces:principal:{principal_hash}"

    @staticmethod
    def _enrollment_nonce_index_key() -> str:
        return "kollab:relay:enrollment:{mailbox}:nonce-index"

    @staticmethod
    def _contact_route_key(route_hex: str) -> str:
        return f"kollab:relay:contact:{{mailbox}}:route:{route_hex}"

    @staticmethod
    def _link_key(key: str) -> str:
        return f"kollab:relay:link:{{mailbox}}:declared:{key}"

    @staticmethod
    def _link_index_key() -> str:
        return "kollab:relay:link:{mailbox}:declarers"

    @staticmethod
    def _presence_key(key: str) -> str:
        return f"kollab:relay:link:{{mailbox}}:presence:{key}"

    @staticmethod
    def _node_channel_for(node_id: str) -> str:
        return f"kollab:relay:node:{{{node_id}}}:inbox"

    @staticmethod
    def _parse_record(raw: str) -> PeerRecord:
        value = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (
            not isinstance(value, dict)
            or set(value) != {"key", "session", "node_id", "connection_id"}
            or not _is_public_key(value["key"])
            or not _is_id(value["session"])
            or not _is_id(value["node_id"])
            or not _is_id(value["connection_id"])
        ):
            raise RelayBackendError("shared peer record is malformed")
        return PeerRecord(**value)

    @classmethod
    def _parse_hgetall(cls, values: list[Any]) -> list[PeerRecord]:
        if len(values) % 2:
            raise RelayBackendError("shared room roster has malformed fields")
        result = []
        for index in range(0, len(values), 2):
            key = str(values[index])
            record = cls._parse_record(str(values[index + 1]))
            if key != record.key:
                raise RelayBackendError("shared room roster key mismatch")
            result.append(record)
        if len(result) > MAX_ROOM_MEMBERS:
            raise RelayBackendError("shared room exceeds safe snapshot capacity")
        return result


def validate_backend_url(url: str, *, cluster: bool) -> None:
    """Reject URL option overrides, ambiguous endpoints and public plaintext."""
    try:
        if (
            not isinstance(url, str)
            or not url
            or len(url) > 4096
            or any(ord(character) < 33 or ord(character) == 127 for character in url)
            or "?" in url
            or "#" in url
        ):
            raise ValueError
        parsed = urlsplit(url)
        if parsed.scheme == "unix":
            if (
                cluster
                or not parsed.path.startswith("/")
                or parsed.hostname
                or parsed.port is not None
            ):
                raise ValueError
            return
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError
        host = parsed.hostname.lower()
        if not host.isascii() or "%" in host or "\\" in host:
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        if parsed.netloc.rsplit("@", 1)[-1].endswith(":"):
            raise ValueError
        if parsed.path not in {"", "/"} and (
            not parsed.path.startswith("/")
            or not parsed.path[1:].isascii()
            or not parsed.path[1:].isdigit()
        ):
            raise ValueError
        if cluster and parsed.path not in {"", "/", "/0"}:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            # Redis decodes hostnames before dialing; numeric aliases and
            # encoded separators must not bypass the plaintext IP policy.
            if host.isdigit() or host.startswith("0x"):
                raise ValueError
        if parsed.scheme == "redis":
            if address is not None:
                allowed = (
                    address.is_private or address.is_loopback or address.is_link_local
                )
            else:
                allowed = (
                    host == "localhost"
                    or "." not in host
                    or host.endswith((".local", ".internal", ".svc", ".cluster.local"))
                )
            if not allowed:
                raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError(
            "backend URL requires a valid endpoint and nonnegative database number, no query or fragment; "
            "public hosts require TLS and cluster URLs require database 0"
        ) from None


def _bounded_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_BACKPLANE_MESSAGE_BYTES:
        raise RelayBackendError("internal route exceeds bounded backplane frame")
    return encoded


def _negative_route_ack(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "ack",
        "reply_node": payload["reply_node"],
        "route_id": payload["route_id"],
        "ok": False,
    }


def _queued_work_cost(kind: str, payload: dict[str, Any]) -> int:
    """Estimate retained Python payload bytes, including the queue tuple.

    Backplane payloads are flat dicts of scalar values after validation. Counting
    their container, keys, values and tuple bounds retained payload memory; the
    separate item cap bounds deque and task bookkeeping overhead.
    """
    item = (kind, payload, 0)
    return sys.getsizeof(item) + sys.getsizeof(kind) + sys.getsizeof(payload) + sum(
        sys.getsizeof(key) + sys.getsizeof(value) for key, value in payload.items()
    )


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _is_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_public_key(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_room_hash(value: Any) -> bool:
    return _is_public_key(value)
