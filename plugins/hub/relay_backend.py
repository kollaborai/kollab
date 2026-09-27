"""Shared lease, room-presence, and cross-node routing backends.

Redis/Valkey room mutations use room-hash Redis Cluster hash tags. Connection
quotas use expiring node and source-IP leases. Cluster Pub/Sub uses one sharded
inbox per relay worker; encrypted payloads and presence invalidations are
ephemeral and are never persisted as messages.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import secrets
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

LEASE_SECONDS = 35
LEASE_RENEW_SECONDS = 10
ROUTE_TIMEOUT_SECONDS = 3
MAX_BACKPLANE_MESSAGE_BYTES = 64 * 1024
MAX_ROOM_MEMBERS = 256


class RelayBackendError(RuntimeError):
    """The shared relay backend is unavailable or returned invalid data."""


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
    """Single-process backend, available only with explicit development mode."""

    def __init__(self, node_id: str, limits: RelayLimits):
        self.node_id = node_id
        self.limits = limits
        self.connections: dict[str, tuple[str, str]] = {}
        self.source_counts: Counter[str] = Counter()
        self.rooms: dict[str, dict[str, PeerRecord]] = {}
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

    async def reserve_connection(self, connection_id: str, source_ip: str) -> str | None:
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
            return True, []
        return True, list(room.values())

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
        self._work_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
            maxsize=max(32, limits.max_connections_per_node * 2)
        )
        self._ack_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max(32, limits.max_connections_per_node)
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
            raise RelayBackendError("cannot connect to configured Redis-compatible backend") from exc

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
            raise RelayBackendError("shared relay connection quota is unavailable") from exc

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
            return None, self._parse_hgetall(result[2:])
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("shared room registration is unavailable") from exc

    async def list_room(self, room_hash: str) -> tuple[list[PeerRecord], bool]:
        try:
            result = await self._redis.eval(
                _LIST_ROOM, 2, *self._room_keys(room_hash)
            )
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
            return int(result[0]) == 1, self._parse_hgetall(result[1:])
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("shared peer removal is unavailable") from exc

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
            return int(room_result) == 1
        except Exception as exc:
            if isinstance(exc, RelayBackendError):
                raise
            raise RelayBackendError("relay lease renewal failed") from exc

    async def notify_room_change(
        self, room_hash: str, node_ids: set[str]
    ) -> None:
        payload = {"type": "room_changed", "room_hash": room_hash}
        for node_id in sorted(node_ids):
            try:
                await self._publish_node(node_id, payload)
            except Exception as exc:
                raise RelayBackendError("could not publish room presence update") from exc

    async def forward(
        self, destination: PeerRecord, route: dict[str, str]
    ) -> bool:
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

    async def _publish_node(
        self, node_id: str, payload: dict[str, Any]
    ) -> int:
        encoded = _bounded_json(payload)
        channel = self._node_channel_for(node_id)
        if self.cluster:
            node = self._redis.get_node_from_key(channel)
            if node is None:
                raise RelayBackendError("cluster has no owner for destination worker inbox")
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
                if kind == "room_changed":
                    room_hash = payload["room_hash"]
                    if room_hash in self._queued_rooms:
                        continue
                    self._queued_rooms.add(room_hash)
                try:
                    self._work_queue.put_nowait((kind, payload))
                except asyncio.QueueFull:
                    if kind == "room_changed":
                        self._queued_rooms.discard(payload["room_hash"])
                    elif kind == "route":
                        try:
                            self._ack_queue.put_nowait({
                                "type": "ack",
                                "reply_node": payload["reply_node"],
                                "route_id": payload["route_id"],
                                "ok": False,
                            })
                        except asyncio.QueueFull:
                            # Sender's own bounded three-second deadline reports offline.
                            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            self._healthy = False
            if self._state is not None:
                await self._state.backend_failed()

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
            if (
                set(message) == {"type", "room_hash"}
                and _is_room_hash(message["room_hash"])
            ):
                return "room_changed", message
            return None
        if message["type"] != "route" or self._state is None:
            return None
        required = {
            "type", "route_id", "reply_node", "room_hash", "connection_id", "to",
            "to_session", "from", "session", "id", "ciphertext",
        }
        if set(message) != required:
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
            kind, payload = await self._work_queue.get()
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
                    try:
                        self._ack_queue.put_nowait(ack)
                    except asyncio.QueueFull:
                        pass
            except Exception:
                if kind == "route":
                    try:
                        self._ack_queue.put_nowait({
                            "type": "ack",
                            "reply_node": payload["reply_node"],
                            "route_id": payload["route_id"],
                            "ok": False,
                        })
                    except asyncio.QueueFull:
                        pass
            finally:
                if kind == "room_changed":
                    self._queued_rooms.discard(payload["room_hash"])
                self._work_queue.task_done()

    async def _ack_loop(self) -> None:
        while True:
            ack = await self._ack_queue.get()
            try:
                await self._publish_node(ack["reply_node"], {
                    "type": "ack",
                    "route_id": ack["route_id"],
                    "ok": ack["ok"],
                })
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
        return f"kollab:relay:owner:{{node-{node_id}}}"

    @staticmethod
    def _ip_key(source_hash: str) -> str:
        return f"kollab:relay:quota:{{ip-{source_hash}}}:leases"

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
            if cluster or not parsed.path.startswith("/") or parsed.hostname or parsed.port is not None:
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
                allowed = address.is_private or address.is_loopback or address.is_link_local
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


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(
        character in "0123456789abcdef" for character in value
    )


def _is_public_key(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _is_room_hash(value: Any) -> bool:
    return _is_public_key(value)
