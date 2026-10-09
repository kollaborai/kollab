"""Opt-in, candidate-only IPv4 LAN discovery for signed peer locators.

The service transports a deliberately small signed locator. Cryptographic
signature, endpoint designation, current-registration and revocation checks are
delegated to an injected verifier. Accepted revision state is delegated to an
injected persistent store. Discovery only reports a candidate; it never grants
membership, creates a route, approves a peer, or wakes a model.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import ipaddress
import json
import logging
import re
import secrets
import socket
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import rfc8785

from .peer_locator import (
    PeerLocatorError,
    validate_peer_designation,
    validate_peer_locator_endpoint,
)

logger = logging.getLogger(__name__)

PEER_DISCOVERY_MAGIC = b"KPD\x01"
PEER_DISCOVERY_VERSION = 1
PEER_DISCOVERY_MAX_BYTES = 2048
PEER_DISCOVERY_TTL_MAX = 300
PEER_DISCOVERY_FUTURE_SKEW = 30
PEER_DISCOVERY_MAX_INFLIGHT = 8
PEER_DISCOVERY_MAX_SOURCES = 256
PEER_DISCOVERY_MAX_CANDIDATES = 256
# One member (relay key) keeps at most this many cached locators; past it the
# member's own oldest goes, so rotating endpoint keys cannot fill the cache
# against every other member.
PEER_DISCOVERY_MAX_CANDIDATES_PER_MEMBER = 8
PEER_DISCOVERY_SOURCE_RATE_PER_MINUTE = 20
PEER_DISCOVERY_TOTAL_RATE_PER_MINUTE = 300
PEER_DISCOVERY_CALLBACK_TIMEOUT = 5.0
PEER_DISCOVERY_ADVERTISE_INTERVAL = 20.0
PEER_DISCOVERY_MULTICAST_GROUP = "239.255.77.77"
PEER_DISCOVERY_PORT = 39531
_MAX_SAFE_INTEGER = (1 << 53) - 1
_HEX_32 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_16 = re.compile(r"[0-9a-f]{32}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
_PAYLOAD_FIELDS = frozenset(
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
_WIRE_FIELDS = _PAYLOAD_FIELDS | {"relay_signature", "endpoint_signature"}
_NORMALIZED_FIELDS = (_PAYLOAD_FIELDS - {"v"}) | {"digest"}

LocatorProvider = Callable[[str], Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None]
CandidateVerifier = Callable[[dict[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None]
RevisionAcceptor = Callable[["PeerLocatorCandidate"], bool | Awaitable[bool]]
CandidateCallback = Callable[["PeerLocatorCandidate"], Any | Awaitable[Any]]
SocketFactory = Callable[[int, int, int], socket.socket]
SourceAddressPolicy = Callable[[str], bool]


class PeerDiscoveryError(ValueError):
    """A discovery datagram, state transition or configuration was rejected."""


@dataclass(frozen=True)
class PeerLocatorCandidate:
    """Verified public locator metadata with no membership or tool authority."""

    relay_public_key: str
    endpoint_designation: str
    endpoint_public_key: str
    endpoint: str
    session_id: str
    revision: int
    issued_at: int
    expires_at: int
    digest: str

    def as_dict(self) -> dict[str, Any]:
        """Return a detached normalized value for a store adapter."""
        return {
            "relay_public_key": self.relay_public_key,
            "endpoint_designation": self.endpoint_designation,
            "endpoint_public_key": self.endpoint_public_key,
            "endpoint": self.endpoint,
            "session_id": self.session_id,
            "revision": self.revision,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "digest": self.digest,
        }


def _reject_constant(value: str) -> None:
    raise PeerDiscoveryError(f"invalid JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PeerDiscoveryError("duplicate locator field")
        result[key] = value
    return result


def _canonical_wire(wire: Mapping[str, Any]) -> bytes:
    try:
        encoded = rfc8785.dumps(dict(wire))
    except (TypeError, ValueError, RecursionError) as exc:
        raise PeerDiscoveryError("invalid locator JSON value") from exc
    if len(encoded) > PEER_DISCOVERY_MAX_BYTES - len(PEER_DISCOVERY_MAGIC):
        raise PeerDiscoveryError("locator exceeds its size limit")
    return encoded


def validate_locator_wire(wire: Any, *, now: float) -> dict[str, Any]:
    """Validate the exact public locator shape and its bounded signed lease.

    Cryptographic verification and endpoint-to-registration binding belong to
    the injected candidate verifier. This function rejects private/extra fields
    before those callbacks can observe a wire object.
    """
    if not isinstance(wire, dict) or set(wire) != _WIRE_FIELDS:
        raise PeerDiscoveryError("locator fields do not match the public schema")
    if type(wire["v"]) is not int or wire["v"] != PEER_DISCOVERY_VERSION:
        raise PeerDiscoveryError("unsupported locator version")
    for field in ("relay_public_key", "endpoint_public_key"):
        value = wire[field]
        if not isinstance(value, str) or not _HEX_32.fullmatch(value):
            raise PeerDiscoveryError("invalid locator identity key")
    if wire["relay_public_key"] == wire["endpoint_public_key"]:
        raise PeerDiscoveryError("locator identity keys must be distinct")
    try:
        validate_peer_designation(wire["endpoint_designation"])
    except PeerLocatorError as exc:
        raise PeerDiscoveryError("invalid endpoint designation") from exc
    try:
        validate_peer_locator_endpoint(wire["endpoint"], allow_private_network=None)
    except PeerLocatorError as exc:
        raise PeerDiscoveryError("invalid peer endpoint") from exc
    if not isinstance(wire["session_id"], str) or not _HEX_16.fullmatch(wire["session_id"]):
        raise PeerDiscoveryError("invalid locator session")
    if not isinstance(wire["relay_signature"], str) or not _SIGNATURE.fullmatch(
        wire["relay_signature"]
    ):
        raise PeerDiscoveryError("invalid relay signature encoding")
    if not isinstance(wire["endpoint_signature"], str) or not _SIGNATURE.fullmatch(
        wire["endpoint_signature"]
    ):
        raise PeerDiscoveryError("invalid endpoint signature encoding")

    for field in ("revision", "issued_at", "expires_at"):
        value = wire[field]
        if type(value) is not int or not 0 <= value <= _MAX_SAFE_INTEGER:
            raise PeerDiscoveryError(f"invalid locator {field}")
    issued_at = wire["issued_at"]
    expires_at = wire["expires_at"]
    if issued_at > now + PEER_DISCOVERY_FUTURE_SKEW:
        raise PeerDiscoveryError("locator issue time is too far in the future")
    if (
        wire["revision"] < 1
        or expires_at <= issued_at
        or expires_at - issued_at > PEER_DISCOVERY_TTL_MAX
    ):
        raise PeerDiscoveryError("locator is outside its signed lifetime")
    if expires_at <= now:
        raise PeerDiscoveryError("locator is expired")
    _canonical_wire(wire)
    return wire


def parse_locator_datagram(data: bytes, *, now: float) -> dict[str, Any]:
    """Parse a size-bounded canonical locator datagram."""
    if not isinstance(data, bytes) or not (
        len(PEER_DISCOVERY_MAGIC) < len(data) <= PEER_DISCOVERY_MAX_BYTES
    ):
        raise PeerDiscoveryError("invalid locator datagram size")
    if not data.startswith(PEER_DISCOVERY_MAGIC):
        raise PeerDiscoveryError("invalid locator datagram prefix")
    try:
        encoded = data[len(PEER_DISCOVERY_MAGIC) :]
        text = encoded.decode("utf-8", errors="strict")
        wire = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except PeerDiscoveryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PeerDiscoveryError("malformed locator JSON") from exc
    validated = validate_locator_wire(wire, now=now)
    if _canonical_wire(validated) != encoded:
        raise PeerDiscoveryError("locator JSON is not canonical")
    return validated


def _default_source_address_policy(source: str) -> bool:
    try:
        address = ipaddress.ip_address(source.split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        not address.is_loopback
        and not address.is_unspecified
        and not address.is_multicast
        and not address.is_reserved
        and (address.is_private or address.is_link_local)
    )


class _PeerDiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: "PeerDiscoveryService") -> None:
        self.service = service

    def datagram_received(self, data: bytes, addr: tuple[Any, ...]) -> None:
        self.service._queue_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        if not self.service._closed:
            logger.debug("peer discovery UDP error (%s)", type(exc).__name__)


class PeerDiscoveryService:
    """Opt-in LAN locator advertisement/scanning with injectable trust seams.

    Awaitable callbacks are cancelled after a bounded deadline. Synchronous
    callbacks execute on the event loop and therefore must be short and
    nonblocking; Python cannot preempt a synchronous callback safely.
    """

    def __init__(
        self,
        *,
        advertise_enabled: bool = False,
        scan_enabled: bool = False,
        locator_provider: LocatorProvider | None = None,
        candidate_verifier: CandidateVerifier | None = None,
        revision_acceptor: RevisionAcceptor | None = None,
        on_candidate: CandidateCallback | None = None,
        socket_factory: SocketFactory = socket.socket,
        source_address_policy: SourceAddressPolicy = _default_source_address_policy,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        multicast_group: str | None = PEER_DISCOVERY_MULTICAST_GROUP,
        port: int = PEER_DISCOVERY_PORT,
        bind_address: str = "0.0.0.0",
        advertise_target: tuple[str, int] | None = None,
        advertise_interval: float = PEER_DISCOVERY_ADVERTISE_INTERVAL,
        session_provider: Callable[[], str] | None = None,
    ) -> None:
        if type(advertise_enabled) is not bool or type(scan_enabled) is not bool:
            raise TypeError("peer discovery opt-ins must be bools")
        if session_provider is not None and not callable(session_provider):
            raise TypeError("session provider must be callable")
        if advertise_enabled and not callable(locator_provider):
            raise TypeError("advertising requires a signed locator provider")
        if scan_enabled and not all(
            callable(callback)
            for callback in (candidate_verifier, revision_acceptor, on_candidate)
        ):
            raise TypeError("scanning requires verifier, revision store and candidate callback")
        if not all(
            callable(callback)
            for callback in (socket_factory, source_address_policy, clock, monotonic_clock)
        ):
            raise TypeError("peer discovery dependencies must be callable")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("peer discovery port must be in 0..65535")
        if not isinstance(bind_address, str):
            raise TypeError("peer discovery bind address must be text")
        try:
            parsed_bind = ipaddress.ip_address(bind_address)
        except ValueError as exc:
            raise ValueError("peer discovery binds to a literal IPv4 address") from exc
        if parsed_bind.version != 4:
            raise ValueError("this peer discovery service currently uses IPv4")
        if multicast_group is not None:
            try:
                group_address = ipaddress.ip_address(multicast_group)
            except ValueError as exc:
                raise ValueError("multicast group must be a literal IPv4 address") from exc
            local_multicast = ipaddress.ip_network("239.255.0.0/16")
            if (
                group_address.version != 4
                or not group_address.is_multicast
                or group_address not in local_multicast
            ):
                raise ValueError("multicast group must be in the IPv4 local-scope range")
        if type(advertise_interval) not in (int, float) or not 1 <= advertise_interval <= 300:
            raise ValueError("advertise interval must be in 1..300 seconds")
        target = advertise_target or (multicast_group, port)
        if advertise_enabled:
            if (
                not isinstance(target, tuple)
                or len(target) != 2
                or not isinstance(target[0], str)
                or type(target[1]) is not int
                or not 1 <= target[1] <= 65535
            ):
                raise ValueError("advertisement target must be an IPv4 address and port")
            try:
                target_ip = ipaddress.ip_address(target[0])
            except ValueError as exc:
                raise ValueError("advertisement target must be a literal IPv4 address") from exc
            if target_ip.version != 4:
                raise ValueError("advertisement target must be IPv4")
            local_multicast = ipaddress.ip_network("239.255.0.0/16")
            if target_ip.is_multicast:
                if target_ip not in local_multicast:
                    raise ValueError("advertisement multicast target must be local-scope")
            elif (
                target_ip.is_unspecified
                or target_ip.is_reserved
                or not (target_ip.is_private or target_ip.is_link_local or target_ip.is_loopback)
            ):
                raise ValueError("advertisement target must remain on the local network")

        self.advertise_enabled = advertise_enabled
        self.scan_enabled = scan_enabled
        self.locator_provider = locator_provider
        self.candidate_verifier = candidate_verifier
        self.revision_acceptor = revision_acceptor
        self.on_candidate = on_candidate
        self._socket_factory = socket_factory
        self._source_address_policy = source_address_policy
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self.multicast_group = multicast_group
        self.port = port
        self.bind_address = bind_address
        self.advertise_target = target
        self.advertise_interval = float(advertise_interval)
        self._startup_session_id = secrets.token_hex(16)
        # A locator names the session its owner is running right now. A caller
        # whose session changes while the service runs (a relay reconnect)
        # supplies it here; otherwise the startup session stands.
        self._session_provider = session_provider
        self._transport: asyncio.DatagramTransport | None = None
        self._socket: socket.socket | None = None
        self._advertiser: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(PEER_DISCOVERY_MAX_INFLIGHT)
        self._accept_lock = asyncio.Lock()
        self._closed_event = asyncio.Event()
        self._started = False
        self._closed = False
        self._rate_minute: int | None = None
        self._rate_total = 0
        self._rate_sources: dict[str, int] = {}
        self._candidate_cache: OrderedDict[
            tuple[str, str], tuple[int, str, str, int]
        ] = OrderedDict()
        self._local_advertisement: tuple[int, str] | None = None
        self.datagrams_received = 0
        self.candidates_accepted = 0
        self.datagrams_rejected = 0

    @property
    def session_id(self) -> str:
        if self._session_provider is None:
            return self._startup_session_id
        session = self._session_provider()
        if not isinstance(session, str) or not _HEX_16.fullmatch(session):
            raise PeerDiscoveryError("session provider returned an invalid session")
        return session

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    async def start(self) -> None:
        """Start only explicitly enabled roles; disabled mode opens no socket."""
        if self._closed:
            raise PeerDiscoveryError("peer discovery service is closed")
        if self._started:
            return
        self._started = True
        if not (self.advertise_enabled or self.scan_enabled):
            return

        udp_socket: socket.socket | None = None
        try:
            udp_socket = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_socket.setblocking(False)
            if self.scan_enabled:
                udp_socket.bind((self.bind_address, self.port))
                if self.multicast_group is not None:
                    membership = socket.inet_aton(self.multicast_group) + socket.inet_aton(
                        self.bind_address if self.bind_address != "0.0.0.0" else "0.0.0.0"
                    )
                    udp_socket.setsockopt(
                        socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership
                    )
            if self.advertise_enabled and self.multicast_group is not None:
                udp_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                udp_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
                if self.bind_address != "0.0.0.0":
                    udp_socket.setsockopt(
                        socket.IPPROTO_IP,
                        socket.IP_MULTICAST_IF,
                        socket.inet_aton(self.bind_address),
                    )

            loop = asyncio.get_running_loop()
            transport, _protocol = await loop.create_datagram_endpoint(
                lambda: _PeerDiscoveryProtocol(self), sock=udp_socket
            )
            self._socket = udp_socket
            self._transport = transport
            if self.advertise_enabled:
                self._advertiser = asyncio.create_task(
                    self._advertise_loop(), name="peer-discovery-advertiser"
                )
        except Exception:
            self._started = False
            if udp_socket is not None:
                udp_socket.close()
            raise

    async def close(self) -> None:
        """Cancel owned tasks and close the UDP endpoint deterministically."""
        if self._closed:
            return
        self._closed = True
        self._closed_event.set()
        tasks = tuple(self._tasks)
        if self._advertiser is not None:
            self._advertiser.cancel()
            tasks += (self._advertiser,)
            self._advertiser = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        transport, self._transport = self._transport, None
        self._socket = None
        if transport is not None:
            transport.close()
            await asyncio.sleep(0)

    async def _advertise_loop(self) -> None:
        while not self._closed:
            try:
                await self._advertise_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("peer locator advertisement rejected (%s)", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._closed_event.wait(), timeout=self.advertise_interval
                )
            except TimeoutError:
                pass

    async def _advertise_once(self) -> None:
        if self._closed or self._transport is None or self.locator_provider is None:
            return
        session_id = self.session_id
        wire = await _invoke_bounded(
            self.locator_provider, session_id, timeout=PEER_DISCOVERY_CALLBACK_TIMEOUT
        )
        if wire is None:
            return
        if not isinstance(wire, Mapping):
            raise PeerDiscoveryError("locator provider returned an invalid value")
        wire_dict = dict(wire)
        validated = validate_locator_wire(wire_dict, now=self._clock())
        if validated["session_id"] != session_id:
            raise PeerDiscoveryError("locator provider returned a different session")
        encoded = _canonical_wire(validated)
        digest = hashlib.sha256(encoded).hexdigest()
        revision = validated["revision"]
        previous = self._local_advertisement
        if previous is not None and (
            revision < previous[0] or (revision == previous[0] and digest != previous[1])
        ):
            raise PeerDiscoveryError("local locator revision rollback or equivocation")
        self._local_advertisement = (revision, digest)
        transport = self._transport
        if transport is not None and not self._closed:
            transport.sendto(PEER_DISCOVERY_MAGIC + encoded, self.advertise_target)

    def _queue_datagram(self, data: bytes, addr: tuple[Any, ...]) -> None:
        if self._closed or not self.scan_enabled or len(self._tasks) >= PEER_DISCOVERY_MAX_INFLIGHT:
            self.datagrams_rejected += 1
            return
        if not isinstance(addr, tuple) or not addr or not isinstance(addr[0], str):
            self.datagrams_rejected += 1
            return
        source = addr[0].split("%", 1)[0]
        if not self._allow_source(source) or len(data) > PEER_DISCOVERY_MAX_BYTES:
            self.datagrams_rejected += 1
            return
        task = asyncio.create_task(self._process_datagram(data, source))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            try:
                task.exception()
            except Exception:
                pass

    def _allow_source(self, source: str) -> bool:
        try:
            address = ipaddress.ip_address(source)
        except ValueError:
            return False
        if (
            address.version != 4
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        ):
            return False
        try:
            if not self._source_address_policy(source):
                return False
        except Exception:
            return False

        minute = int(self._monotonic_clock() // 60)
        if minute != self._rate_minute:
            self._rate_minute = minute
            self._rate_total = 0
            self._rate_sources.clear()
        if self._rate_total >= PEER_DISCOVERY_TOTAL_RATE_PER_MINUTE:
            return False
        count = self._rate_sources.get(source)
        if count is None:
            if len(self._rate_sources) >= PEER_DISCOVERY_MAX_SOURCES:
                return False
            count = 0
        if count >= PEER_DISCOVERY_SOURCE_RATE_PER_MINUTE:
            return False
        self._rate_sources[source] = count + 1
        self._rate_total += 1
        return True

    async def _process_datagram(self, data: bytes, source: str) -> None:
        async with self._semaphore:
            if self._closed:
                return
            self.datagrams_received += 1
            try:
                wire = parse_locator_datagram(data, now=self._clock())
                verifier = self.candidate_verifier
                acceptor = self.revision_acceptor
                callback = self.on_candidate
                if verifier is None or acceptor is None or callback is None:
                    return
                normalized = await _invoke_bounded(
                    verifier, dict(wire), timeout=PEER_DISCOVERY_CALLBACK_TIMEOUT
                )
                if normalized is None:
                    raise PeerDiscoveryError("locator verification failed")
                candidate = _normalize_candidate(wire, normalized)
                async with self._accept_lock:
                    if self._closed:
                        return
                    _require_live_candidate(candidate, self._clock())
                    if self._candidate_cache_check(candidate):
                        return
                    accepted = await _invoke_bounded(
                        acceptor,
                        candidate,
                        timeout=PEER_DISCOVERY_CALLBACK_TIMEOUT,
                    )
                    if type(accepted) is not bool:
                        raise PeerDiscoveryError(
                            "revision store returned an invalid result"
                        )
                    if self._closed:
                        return
                    _require_live_candidate(candidate, self._clock())
                    self._candidate_cache_remember(candidate)
                    if not accepted:
                        return
                    _require_live_candidate(candidate, self._clock())
                    await _invoke_bounded(
                        callback,
                        candidate,
                        timeout=PEER_DISCOVERY_CALLBACK_TIMEOUT,
                    )
                    if not self._closed:
                        self.candidates_accepted += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.datagrams_rejected += 1
                logger.debug("peer locator candidate rejected (%s)", type(exc).__name__)

    def _candidate_cache_check(self, candidate: PeerLocatorCandidate) -> bool:
        now = int(self._clock())
        for key, (_revision, _session, _digest, expires_at) in tuple(
            self._candidate_cache.items()
        ):
            if expires_at <= now:
                del self._candidate_cache[key]
        key = (candidate.relay_public_key, candidate.endpoint_public_key)
        previous = self._candidate_cache.get(key)
        if previous is not None:
            revision, session_id, digest, _expires_at = previous
            if candidate.revision < revision:
                raise PeerDiscoveryError("locator revision rollback")
            if candidate.revision == revision:
                if candidate.session_id != session_id or candidate.digest != digest:
                    raise PeerDiscoveryError("locator revision equivocation")
                self._candidate_cache.move_to_end(key)
                return True
        elif (
            len(self._candidate_cache) >= PEER_DISCOVERY_MAX_CANDIDATES
            # A member at its share replaces its own oldest entry on remember,
            # so it adds nothing; only a member with room is turned away.
            and len(self._member_cache_keys(candidate.relay_public_key))
            < PEER_DISCOVERY_MAX_CANDIDATES_PER_MEMBER
        ):
            raise PeerDiscoveryError("peer locator cache is full")
        return False

    def _candidate_cache_remember(self, candidate: PeerLocatorCandidate) -> None:
        key = (candidate.relay_public_key, candidate.endpoint_public_key)
        self._candidate_cache[key] = (
            candidate.revision,
            candidate.session_id,
            candidate.digest,
            candidate.expires_at,
        )
        self._candidate_cache.move_to_end(key)
        own = self._member_cache_keys(candidate.relay_public_key)
        for oldest in own[: max(0, len(own) - PEER_DISCOVERY_MAX_CANDIDATES_PER_MEMBER)]:
            del self._candidate_cache[oldest]

    def _member_cache_keys(self, relay_public_key: str) -> list[tuple[str, str]]:
        """One member's cached locator keys, oldest first."""
        return [key for key in self._candidate_cache if key[0] == relay_public_key]


async def _invoke(callback: Callable[..., Any], *args: Any) -> Any:
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


async def _invoke_bounded(
    callback: Callable[..., Any], *args: Any, timeout: float
) -> Any:
    """Bound awaitable callback time; callers must keep sync callbacks short."""
    return await asyncio.wait_for(_invoke(callback, *args), timeout=timeout)


def _normalize_candidate(wire: dict[str, Any], normalized: Any) -> PeerLocatorCandidate:
    if not isinstance(normalized, Mapping) or set(normalized) != _NORMALIZED_FIELDS:
        raise PeerDiscoveryError("candidate verifier returned invalid metadata")
    expected_digest = hashlib.sha256(_canonical_wire(wire)).hexdigest()
    expected = {
        **{field: wire[field] for field in _PAYLOAD_FIELDS if field != "v"},
        "digest": expected_digest,
    }
    if dict(normalized) != expected or any(
        type(normalized[field]) is not type(value)
        for field, value in expected.items()
    ):
        raise PeerDiscoveryError("candidate verifier metadata does not match the signed locator")
    return PeerLocatorCandidate(**expected)


def _require_live_candidate(candidate: PeerLocatorCandidate, now: float) -> None:
    if (
        candidate.issued_at > now + PEER_DISCOVERY_FUTURE_SKEW
        or candidate.expires_at <= now
        or candidate.expires_at - candidate.issued_at > PEER_DISCOVERY_TTL_MAX
    ):
        raise PeerDiscoveryError("locator lease expired before acceptance")
