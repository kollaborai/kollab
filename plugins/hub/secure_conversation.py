"""Forward-secret application messages over an approved RelayClient peer.

The relay's existing Box envelope provides bounded direct peer transport. This
module adds a pinned, mutual TLS 1.3 session inside that transport so task text,
results, status details, and cancellation arguments stay opaque to the relay.
The TLS session is tied to both current RelayClient registrations and is thrown
away as soon as either registration or approval changes.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from .network_attach import ATTACH_METHODS
from .relay_client import PeerSessionEvent, RelayClient
from .relay_state import RelayError, failure_text, strict_json, validate_key
from .secure_session import (
    MAX_PLAINTEXT_BYTES,
    MAX_SEQUENCE,
    SecureSession,
    SecureSessionError,
    TLSRecordPacket,
    identity_certificate,
    identity_public_key,
)

logger = logging.getLogger(__name__)

MAX_SECURE_SESSIONS = 64
MAX_SECURE_SESSIONS_PER_PEER = 8
MAX_SECURE_IDLE_SECONDS = 300
MAX_SECURE_HANDSHAKE_SECONDS = 30
MAX_SECURE_CHUNK_BYTES = 4 * 1024
MAX_SECURE_MESSAGE_BYTES = MAX_PLAINTEXT_BYTES
MAX_WIRE_PACKET_BYTES = 12 * 1024
MAX_FRAMED_BYTES = MAX_SECURE_MESSAGE_BYTES + 4
_HEX_SESSION = re.compile(r"[0-9a-f]{32}\Z")
_SECURE_APP_METHODS = frozenset(
    {"message", "status", "cancel", "directory", "peer.exchange", "config_sync", "network_members"}
    | ATTACH_METHODS
)
SecureDispatch = Callable[[str, str, dict], Awaitable[dict]]
class TransportRequest(Protocol):
    async def __call__(
        self, peer_key: str, method: str, payload: dict, *, timeout: float
    ) -> dict: ...


BindingResolver = Callable[[str], tuple[str, str, str] | None]


@dataclass
class _SessionState:
    peer_key: str
    local_session: str
    peer_session: str
    tls: SecureSession
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    receive_buffer: bytearray = field(default_factory=bytearray)
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    link_session_id: str = ""


class SecureConversationTransport:
    """Serialize per-peer TLS traffic over RelayClient's private RPC."""

    def __init__(self, client: RelayClient, identity_seed: bytes):
        if identity_public_key(identity_seed).hex() != client.public_key:
            raise RelayError("secure identity does not match the relay client")
        self._client = client
        self._identity_seed = bytearray(identity_seed)
        self._certificate = identity_certificate(bytes(self._identity_seed))
        self._outbound: dict[tuple[str, str, str], _SessionState] = {}
        self._inbound: dict[tuple[str, str, str, str], _SessionState] = {}
        # The id of the session carrying the request being dispatched right now.
        self._carrying: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"secure-carrying-session-{id(self)}", default=None
        )
        self._request_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._remove_listener = client.add_peer_session_listener(
            self._on_peer_session_event
        )
        self._transport_request: TransportRequest = client.request
        self._binding_resolver: BindingResolver | None = None
        self._closed = False

    def set_peer_transport(
        self,
        request: TransportRequest,
        binding_resolver: BindingResolver,
    ) -> None:
        """Install the bounded peer carrier and its signed-session binding."""
        if not callable(request) or not callable(binding_resolver):
            raise TypeError("peer transport callbacks must be callable")
        self._transport_request = request
        self._binding_resolver = binding_resolver

    @property
    def certificate_b64(self) -> str:
        return base64.b64encode(self._certificate).decode("ascii")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._remove_listener()
        self._clear_sessions()
        for index in range(len(self._identity_seed)):
            self._identity_seed[index] = 0
        self._certificate = b""

    def link_session_id(self, peer_key: str) -> str | None:
        """Return a PeerLink-compatible ID bound to TLS and current relay sessions."""
        binding = self._current_binding(peer_key)
        candidates = [
            state.link_session_id
            for key, state in self._outbound.items()
            if key == binding and state.tls.established and state.link_session_id
        ]
        candidates.extend(
            state.link_session_id
            for key, state in self._inbound.items()
            if key[:3] == binding and state.tls.established and state.link_session_id
        )
        return min(candidates) if candidates else None

    def live_link_session_ids(self, peer_key: str) -> set[str]:
        """Ids of every established session with the peer, in either direction.

        A node's half of a session can outlive the peer's half (the peer drops
        its side after a failed request), so two nodes never agree on "the"
        session by looking at their own tables; a link is checked by asking
        whether the session it names is among these.
        """
        binding = self._current_binding(peer_key)
        ids = {
            state.link_session_id
            for key, state in self._outbound.items()
            if key == binding and state.tls.established and state.link_session_id
        }
        ids.update(
            state.link_session_id
            for key, state in self._inbound.items()
            if key[:3] == binding and state.tls.established and state.link_session_id
        )
        return ids

    def outbound_link_session_id(self, peer_key: str) -> str | None:
        """The id of this node's own established session to the peer, if any."""
        state = self._outbound.get(self._current_binding(peer_key))
        if state is None or not state.tls.established or not state.link_session_id:
            return None
        return state.link_session_id

    def carrying_link_session_id(self) -> str | None:
        """The id of the inbound session that carried the request being handled."""
        return self._carrying.get()

    def identity_response(self, peer_key: str, payload: dict) -> dict:
        self._current_binding(peer_key)
        if payload != {}:
            raise RelayError("invalid secure identity request")
        return {"certificate": self.certificate_b64}

    async def request(
        self,
        peer_key: str,
        method: str,
        payload: dict,
        *,
        timeout: float = 10.0,
    ) -> dict:
        """Send a framed application request only after mutual TLS completes."""
        if method not in _SECURE_APP_METHODS:
            raise RelayError("unsupported secure conversation operation")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 300
        ):
            raise RelayError("secure request timeout must be at most 300 seconds")
        binding = self._current_binding(peer_key)
        key = binding
        deadline = time.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout):
                self._prune_expired()
                request_lock = self._request_locks.get(key)
                if request_lock is None:
                    if len(self._request_locks) >= MAX_SECURE_SESSIONS:
                        raise RelayError("secure session capacity reached")
                    request_lock = self._request_locks[key] = asyncio.Lock()
                async with request_lock:
                    state = await self._open_locked(peer_key, key, binding, deadline)
                    raw = _encode_frame({"v": 1, "method": method, "payload": payload})
                    result = None
                    chunks = [
                        raw[offset : offset + MAX_SECURE_CHUNK_BYTES]
                        for offset in range(0, len(raw), MAX_SECURE_CHUNK_BYTES)
                    ]
                    for index, chunk in enumerate(chunks):
                        packet = state.tls.send(chunk)
                        state.last_used = time.monotonic()
                        response = await self._send_packet(
                            state,
                            packet,
                            deadline,
                            certificate="",
                        )
                        received = self._receive_packets(state, response)
                        if received is not None:
                            if index != len(chunks) - 1 or result is not None:
                                raise SecureSessionError(
                                    "peer returned an early secure response"
                                )
                            result = received
                    if result is None:
                        raise SecureSessionError(
                            "peer did not return a secure application response"
                        )
                    return result
        except asyncio.CancelledError:
            self._discard_outbound(key)
            raise
        except Exception as exc:
            self._discard_outbound(key)
            logger.warning(
                "secure %s request to %s failed: %s: %s",
                method,
                peer_key[:12],
                type(exc).__name__,
                str(exc)[:200],
            )
            raise RelayError("secure conversation transport failed") from None

    async def _open_locked(
        self,
        peer_key: str,
        key: tuple[str, str, str],
        binding: tuple[str, str, str],
        deadline: float,
    ) -> _SessionState:
        """The established outbound session for a binding; the request lock is held."""
        state = self._outbound.get(key)
        if state is None:
            self._make_room(peer_key, inbound=False)
            state = await self._new_client_session(binding)
            self._outbound[key] = state
        self._assert_current(state)
        if not state.tls.established:
            await self._handshake(state, deadline)
        return state

    async def ensure_session(self, peer_key: str, *, timeout: float = 10.0) -> None:
        """Open the outbound secure session to a peer when none is established.

        A peer-link proposal names the id of the TLS sessions it rides, and that
        id agrees on both ends only once this node's own outbound session exists
        beside any inbound one.
        """
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 300
        ):
            raise RelayError("secure request timeout must be at most 300 seconds")
        binding = self._current_binding(peer_key)
        key = binding
        deadline = time.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout):
                self._prune_expired()
                request_lock = self._request_locks.get(key)
                if request_lock is None:
                    if len(self._request_locks) >= MAX_SECURE_SESSIONS:
                        raise RelayError("secure session capacity reached")
                    request_lock = self._request_locks[key] = asyncio.Lock()
                async with request_lock:
                    await self._open_locked(peer_key, key, binding, deadline)
        except asyncio.CancelledError:
            self._discard_outbound(key)
            raise
        except Exception as exc:
            self._discard_outbound(key)
            logger.warning(
                "secure session to %s failed: %s: %s",
                peer_key[:12],
                type(exc).__name__,
                str(exc)[:200],
            )
            raise RelayError("secure conversation transport failed") from None

    async def handle_packet(
        self,
        peer_key: str,
        payload: dict,
        dispatch: SecureDispatch,
    ) -> dict:
        """Accept one opaque TLS packet and return only opaque TLS output."""
        binding = self._current_binding(peer_key)
        try:
            packet, certificate = _parse_wire_packet(payload)
            session_key = (*binding, packet.session_id.hex())
            self._prune_expired()
            state = self._inbound.get(session_key)
            if state is None:
                if not certificate:
                    raise SecureSessionError(
                        "new secure session omitted its pinned certificate"
                    )
                self._make_room(peer_key, inbound=True)
                peer_certificate = _decode_certificate(certificate)
                state = _SessionState(
                    peer_key=peer_key,
                    local_session=binding[1],
                    peer_session=binding[2],
                    tls=SecureSession(
                        bytes(self._identity_seed),
                        bytes.fromhex(peer_key),
                        peer_certificate,
                        role="server",
                    ),
                    created_at=time.monotonic(),
                )
                state.tls.start()
                self._inbound[session_key] = state
            elif certificate:
                raise SecureSessionError(
                    "existing secure session repeated its identity certificate"
                )

            async with state.lock:
                self._assert_current(state)
                update = state.tls.receive(packet)
                state.last_used = time.monotonic()
                if state.tls.transcript_id is not None:
                    # The secure peer-link exchange runs inside the first
                    # authenticated request. Publish its transcript binding
                    # before dispatch so the handler can sign that link.
                    state.link_session_id = _link_session_id(
                        state.tls,
                        peer_key,
                        self._client.public_key,
                        state.peer_session,
                        state.local_session,
                        self._room_scope(),
                    )
                packets = [update.packet] if update.packet is not None else []
                result = None
                if update.plaintext:
                    if not state.tls.established:
                        raise SecureSessionError(
                            "secure application data arrived before authentication"
                        )
                    request = _append_and_read_frame(state, update.plaintext)
                    if request is not None:
                        if update.packet is not None:
                            # Keep TLS control output ordered before the response.
                            packets = [update.packet]
                        carrying = self._carrying.set(state.link_session_id or None)
                        try:
                            result = await self._dispatch_secure_request(
                                peer_key, request, dispatch
                            )
                        finally:
                            self._carrying.reset(carrying)
                        response_raw = _encode_frame({"v": 1, "result": result})
                        if len(response_raw) > MAX_SECURE_CHUNK_BYTES:
                            raise SecureSessionError(
                                "secure application response exceeds its bound"
                            )
                        packets.append(state.tls.send(response_raw))
                if state.tls.transcript_id is not None:
                    state.link_session_id = _link_session_id(
                        state.tls,
                        peer_key,
                        self._client.public_key,
                        state.peer_session,
                        state.local_session,
                        self._room_scope(),
                    )
                return {
                    "packets": [_packet_to_wire(item) for item in packets],
                    "established": state.tls.established,
                }
        except asyncio.CancelledError:
            self._discard_inbound(peer_key, payload)
            raise
        except Exception as exc:
            logger.warning("secure conversation packet was rejected: %s", failure_text(exc))
            self._discard_inbound(peer_key, payload)
            raise RelayError("secure conversation packet was rejected") from None

    async def _dispatch_secure_request(
        self,
        peer_key: str,
        value: dict,
        dispatch: SecureDispatch,
    ) -> dict:
        if (
            not isinstance(value, dict) or not value.keys() >= {"v", "method", "payload"}
            or type(value["v"]) is not int
            or value["v"] != 1
            or value["method"] not in _SECURE_APP_METHODS
            or not isinstance(value["payload"], dict)
        ):
            raise SecureSessionError("invalid secure application request")
        return await dispatch(peer_key, value["method"], value["payload"])

    async def _new_client_session(self, binding: tuple[str, str, str]) -> _SessionState:
        peer_key, local_session, peer_session = binding
        response = await self._transport_request(
            peer_key, "secure_identity", {}, timeout=10
        )
        if not isinstance(response, dict) or not response.keys() >= {"certificate"}:
            raise RelayError("peer did not return a pinned identity certificate")
        certificate = _decode_certificate(response["certificate"])
        return _SessionState(
            peer_key=peer_key,
            local_session=local_session,
            peer_session=peer_session,
            tls=SecureSession(
                bytes(self._identity_seed),
                bytes.fromhex(peer_key),
                certificate,
                role="client",
            ),
        )

    async def _handshake(self, state: _SessionState, deadline: float) -> None:
        packet = state.tls.start()
        certificate = self.certificate_b64
        server_established = False
        for _ in range(8):
            if packet is None:
                raise SecureSessionError("TLS client handshake produced no packet")
            response = await self._send_packet(
                state,
                packet,
                deadline,
                certificate=certificate,
            )
            certificate = ""
            received = _parse_packet_response(response)
            server_established = response["established"]
            if not received:
                if state.tls.established:
                    break
                raise SecureSessionError("TLS peer omitted a handshake packet")
            update_packet = None
            for wire in received:
                update = state.tls.receive(wire)
                if update.plaintext:
                    raise SecureSessionError(
                        "TLS peer sent application data during the handshake"
                    )
                update_packet = update.packet
            packet = update_packet
            if state.tls.established and packet is None:
                break
        if not state.tls.established or state.tls.transcript_id is None:
            raise SecureSessionError("mutual TLS authentication did not complete")
        if not server_established:
            raise SecureSessionError("peer did not finish mutual TLS authentication")
        state.link_session_id = _link_session_id(
            state.tls,
            self._client.public_key,
            state.peer_key,
            state.local_session,
            state.peer_session,
            self._room_scope(),
        )

    async def _send_packet(
        self,
        state: _SessionState,
        packet: TLSRecordPacket,
        deadline: float,
        *,
        certificate: str,
    ) -> dict:
        self._assert_current(state)
        state.last_used = time.monotonic()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return await self._transport_request(
            state.peer_key,
            "secure_packet",
            {
                "v": 1,
                "session_id": packet.session_id.hex(),
                "sequence": packet.sequence,
                "data": base64.b64encode(packet.data).decode("ascii"),
                "certificate": certificate,
            },
            timeout=min(remaining, 300),
        )

    def _receive_packets(self, state: _SessionState, response: dict) -> dict | None:
        received = _parse_packet_response(response)
        if not response["established"]:
            raise SecureSessionError("peer secure session is not established")
        result = None
        for packet in received:
            update = state.tls.receive(packet)
            if update.packet is not None:
                raise SecureSessionError("unexpected TLS output while receiving")
            if update.plaintext:
                value = _append_and_read_frame(state, update.plaintext)
                if value is not None:
                    if result is not None:
                        raise SecureSessionError(
                            "peer returned multiple secure application responses"
                        )
                    if (
                        not isinstance(value, dict) or not value.keys() >= {"v", "result"}
                        or type(value["v"]) is not int
                        or value["v"] != 1
                        or not isinstance(value["result"], dict)
                    ):
                        raise SecureSessionError("invalid secure application response")
                    result = value["result"]
        return result

    def _current_binding(self, peer_key: str) -> tuple[str, str, str]:
        if self._closed:
            raise RelayError("secure conversation transport is closed")
        validate_key(peer_key)
        if peer_key not in self._client.state.approvals:
            raise RelayError("peer requires explicit local approval")
        # The peer carrier knows the sessions of peers reached without a relay
        # roster (by locator or by route), and its own when it has no relay
        # registration; it answers first and the relay roster is the fallback.
        if self._binding_resolver is not None:
            resolved = self._binding_resolver(peer_key)
            if resolved is not None:
                if (
                    not isinstance(resolved, tuple)
                    or len(resolved) != 3
                    or resolved[0] != peer_key
                    or any(not isinstance(item, str) or not item for item in resolved)
                ):
                    raise RelayError("peer session binding is invalid")
                return resolved
        status = self._client.status()
        if status.get("state") != "online" or not status.get("session"):
            raise RelayError("relay client is offline")
        peer = next(
            (item for item in self._client.peers() if item["key"] == peer_key),
            None,
        )
        if peer is None or not peer.get("session"):
            raise RelayError("peer is offline")
        return peer_key, status["session"], peer["session"]

    def _assert_current(self, state: _SessionState) -> None:
        if self._current_binding(state.peer_key) != (
            state.peer_key,
            state.local_session,
            state.peer_session,
        ):
            raise SecureSessionError("relay registration changed during TLS session")

    def _room_scope(self) -> bytes:
        room = self._client.state.room
        if not isinstance(room, str) or not re.fullmatch(r"[0-9a-f]{64}", room):
            raise RelayError("relay room identity is unavailable")
        return hashlib.sha256(bytes.fromhex(room)).digest()

    def _on_peer_session_event(self, event: PeerSessionEvent) -> None:
        if event.kind == "local_disconnected":
            self._clear_sessions()
        elif event.peer_key is not None:
            self._discard_peer(event.peer_key)

    def _make_room(self, peer_key: str, *, inbound: bool) -> None:
        self._prune_expired()
        sessions = self._inbound if inbound else self._outbound
        peer_count = sum(state.peer_key == peer_key for state in sessions.values())
        if peer_count >= MAX_SECURE_SESSIONS_PER_PEER:
            raise RelayError("secure session capacity reached for peer")
        if len(self._inbound) + len(self._outbound) >= MAX_SECURE_SESSIONS:
            raise RelayError("secure session capacity reached")

    def _prune_expired(self) -> None:
        now = time.monotonic()
        for key, state in tuple(self._outbound.items()):
            request_lock = self._request_locks.get(key)
            if (
                request_lock is None or not request_lock.locked()
            ) and now - state.last_used > MAX_SECURE_IDLE_SECONDS:
                self._discard_outbound(key)
        for key, state in tuple(self._inbound.items()):
            if not state.lock.locked() and (
                (
                    not state.tls.established
                    and now - state.created_at >= MAX_SECURE_HANDSHAKE_SECONDS
                )
                or now - state.last_used > MAX_SECURE_IDLE_SECONDS
            ):
                self._discard_inbound_key(key)

    def _discard_outbound(self, key: tuple[str, str, str]) -> None:
        state = self._outbound.pop(key, None)
        if state is not None:
            state.tls.close()
            state.receive_buffer.clear()

    def _discard_inbound(self, peer_key: str, payload: object) -> None:
        if isinstance(payload, dict):
            session_id = payload.get("session_id")
            if isinstance(session_id, str) and _HEX_SESSION.fullmatch(session_id):
                for key in tuple(self._inbound):
                    if key[0] == peer_key and key[3] == session_id:
                        self._discard_inbound_key(key)

    def _discard_inbound_key(self, key: tuple[str, str, str, str]) -> None:
        state = self._inbound.pop(key, None)
        if state is not None:
            state.tls.close()
            state.receive_buffer.clear()

    def _discard_peer(self, peer_key: str) -> None:
        for key in tuple(self._outbound):
            if key[0] == peer_key:
                self._discard_outbound(key)
        for key in tuple(self._inbound):
            if key[0] == peer_key:
                self._discard_inbound_key(key)
        for key in tuple(self._request_locks):
            if key[0] == peer_key:
                self._request_locks.pop(key, None)

    def _clear_sessions(self) -> None:
        for key in tuple(self._outbound):
            self._discard_outbound(key)
        for key in tuple(self._inbound):
            self._discard_inbound_key(key)
        self._request_locks.clear()


def _encode_frame(value: dict) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise RelayError("secure application payload is invalid") from None
    if not raw or len(raw) > MAX_SECURE_MESSAGE_BYTES:
        raise RelayError("secure application payload exceeds its bound")
    return len(raw).to_bytes(4, "big") + raw


def _append_and_read_frame(state: _SessionState, plaintext: bytes) -> dict | None:
    if len(state.receive_buffer) + len(plaintext) > MAX_FRAMED_BYTES:
        raise SecureSessionError("secure application frame exceeds its bound")
    state.receive_buffer.extend(plaintext)
    if len(state.receive_buffer) < 4:
        return None
    length = int.from_bytes(state.receive_buffer[:4], "big")
    if not 1 <= length <= MAX_SECURE_MESSAGE_BYTES:
        raise SecureSessionError("secure application frame length is invalid")
    if len(state.receive_buffer) < length + 4:
        return None
    if len(state.receive_buffer) != length + 4:
        raise SecureSessionError("secure session carried trailing application bytes")
    raw = bytes(state.receive_buffer[4:])
    state.receive_buffer.clear()
    return strict_json(raw, limit=MAX_SECURE_MESSAGE_BYTES)


def _packet_to_wire(packet: TLSRecordPacket) -> dict:
    if len(packet.data) > MAX_WIRE_PACKET_BYTES:
        raise SecureSessionError("TLS record exceeds relay packet limit")
    return {
        "session_id": packet.session_id.hex(),
        "sequence": packet.sequence,
        "data": base64.b64encode(packet.data).decode("ascii"),
    }


def _parse_packet(value: object) -> TLSRecordPacket:
    if not isinstance(value, dict) or not value.keys() >= {
        "session_id",
        "sequence",
        "data",
    }:
        raise SecureSessionError("invalid TLS packet fields")
    session_id = value["session_id"]
    sequence = value["sequence"]
    encoded = value["data"]
    if (
        not isinstance(session_id, str)
        or not _HEX_SESSION.fullmatch(session_id)
        or type(sequence) is not int
        or not 0 <= sequence <= MAX_SEQUENCE
        or not isinstance(encoded, str)
        or len(encoded) > (MAX_WIRE_PACKET_BYTES * 4 + 2) // 3
    ):
        raise SecureSessionError("invalid TLS packet metadata")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise SecureSessionError("invalid TLS packet encoding") from None
    if (
        not data
        or len(data) > MAX_WIRE_PACKET_BYTES
        or base64.b64encode(data).decode("ascii") != encoded
    ):
        raise SecureSessionError("invalid TLS packet size or encoding")
    return TLSRecordPacket(bytes.fromhex(session_id), sequence, data)


def _parse_wire_packet(payload: object) -> tuple[TLSRecordPacket, str]:
    if not isinstance(payload, dict) or not payload.keys() >= {
        "v",
        "session_id",
        "sequence",
        "data",
        "certificate",
    }:
        raise SecureSessionError("invalid secure packet fields")
    if type(payload["v"]) is not int or payload["v"] != 1:
        raise SecureSessionError("unsupported secure packet version")
    certificate = payload["certificate"]
    if not isinstance(certificate, str) or len(certificate) > 24 * 1024:
        raise SecureSessionError("invalid secure packet certificate")
    packet = _parse_packet(
        {
            "session_id": payload["session_id"],
            "sequence": payload["sequence"],
            "data": payload["data"],
        }
    )
    return packet, certificate


def _parse_packet_response(value: object) -> list[TLSRecordPacket]:
    if not isinstance(value, dict) or not value.keys() >= {"packets", "established"}:
        raise SecureSessionError("invalid secure packet response")
    packets = value["packets"]
    if (
        not isinstance(packets, list)
        or len(packets) > 2
        or type(value["established"]) is not bool
    ):
        raise SecureSessionError("invalid secure packet response bounds")
    return [_parse_packet(packet) for packet in packets]


def _decode_certificate(value: object) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value) <= 24 * 1024:
        raise SecureSessionError("invalid peer identity certificate")
    try:
        certificate = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise SecureSessionError("invalid peer identity certificate") from None
    if (
        not certificate
        or len(certificate) > 16 * 1024
        or base64.b64encode(certificate).decode("ascii") != value
    ):
        raise SecureSessionError("invalid peer identity certificate")
    return certificate


def _link_session_id(
    tls: SecureSession,
    first_key: str,
    second_key: str,
    first_session: str,
    second_session: str,
    room_scope: bytes,
) -> str:
    transcript = tls.transcript_id
    if transcript is None:
        raise SecureSessionError("authenticated TLS transcript is unavailable")
    peers = sorted(
        (
            (first_key, first_session),
            (second_key, second_session),
        )
    )
    digest = hashlib.sha256()
    digest.update(b"kollab-relay-peer-link-v1\0")
    digest.update(transcript)
    digest.update(room_scope)
    for key, session in peers:
        digest.update(bytes.fromhex(key))
        digest.update(session.encode("ascii"))
    return digest.hexdigest()
