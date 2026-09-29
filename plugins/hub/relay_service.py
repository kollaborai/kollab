"""Shared-backend WSS rendezvous and opaque ciphertext relay for Kollab.

The service authenticates room possession with an Ed25519 challenge, exposes
only pseudonymous online peer keys/sessions, and routes bounded ciphertext to
an online peer in the same room. It never decrypts messages, fetches caller
URLs, executes agent tools, or stores an offline queue.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import signal
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from aiohttp import WSCloseCode, WSMsgType, web
from nacl.exceptions import BadSignatureError, CryptoError
from nacl.signing import VerifyKey

from .device_names import NAME_RE
from .relay_backend import (
    CONTACT_REQUEST_TTL_MS,
    ENROLLMENT_MAX_NONCES,
    ENROLLMENT_MAX_NONCES_PER_PRINCIPAL,
    ENROLLMENT_MAX_RATE_SOURCES,
    ENROLLMENT_RATE_LIMIT,
    ENROLLMENT_RATE_WINDOW_MS,
    MAX_ACTIVE_ENROLLMENT_OFFERS,
    MAX_CONTACT_REQUESTS_PER_RECIPIENT,
    InMemoryBackend,
    PeerRecord,
    RedisRelayBackend,
    RelayBackendError,
    RelayLimits,
    validate_backend_url,
)

PROTOCOL = "kollab-relay/1"
HEALTH_PATH = "/relay/v1/health"
WEBSOCKET_PATH = "/relay/v1/ws"
MAX_FRAME_BYTES = 64 * 1024
MAX_CIPHERTEXT_CHARS = 48 * 1024
MAX_CONNECTIONS_PER_NODE = 512
MAX_CONNECTIONS_PER_ROOM = 16
MAX_CONNECTIONS_PER_SOURCE = 16
REGISTRATION_TIMEOUT_SECONDS = 10
SEND_DEADLINE_SECONDS = 3
HEARTBEAT_SECONDS = 20
ENROLLMENT_POLL_RATE_LIMIT = 60
TOKEN_BUCKET_RATE = 10.0
TOKEN_BUCKET_BURST = 20.0
_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_128 = re.compile(r"[0-9a-f]{128}\Z")
_B64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_SHORT_ENROLLMENT_CODE = re.compile(
    r"([0-9A-HJKMNP-TV-Z]{4})-?([0-9A-HJKMNP-TV-Z]{4})\Z",
    re.IGNORECASE,
)

ENROLLMENT_OFFERS_PATH = "/relay/v1/enrollment/offers"
ENROLLMENT_LOOKUP_PATH = "/relay/v1/enrollment/lookup"
ENROLLMENT_REQUEST_PATH = "/relay/v1/enrollment/offers/{offer_id}/request"
ENROLLMENT_ISSUER_POLL_PATH = "/relay/v1/enrollment/offers/{offer_id}/poll"
ENROLLMENT_CHALLENGE_PATH = "/relay/v1/enrollment/offers/{offer_id}/challenge"
ENROLLMENT_PROOF_PATH = "/relay/v1/enrollment/offers/{offer_id}/proof"
ENROLLMENT_DECISION_PATH = "/relay/v1/enrollment/offers/{offer_id}/decision"
ENROLLMENT_REPLY_POLL_PATH = "/relay/v1/enrollment/offers/{offer_id}/reply/poll"
ENROLLMENT_ACK_PATH = "/relay/v1/enrollment/offers/{offer_id}/ack"
ENROLLMENT_ACK_POLL_PATH = "/relay/v1/enrollment/offers/{offer_id}/ack/poll"
ENROLLMENT_SIGNATURE_DOMAIN = b"kollab-relay-enrollment-http/1\x00"
ENROLLMENT_CODE_KDF_DOMAIN = b"kollab-relay-enrollment-code-v1\x00"
ENROLLMENT_VERIFIER_DOMAIN = b"kollab-relay-enrollment-verifier-v1\x00"
ENROLLMENT_LOOKUP_DOMAIN = b"kollab-relay-enrollment-lookup-v1\x00"
ENROLLMENT_CODE_SCRYPT_N = 1 << 14
ENROLLMENT_CODE_SCRYPT_R = 8
ENROLLMENT_CODE_SCRYPT_P = 1
ENROLLMENT_MAX_TTL_SECONDS = 10 * 60
ENROLLMENT_MIN_TTL_SECONDS = 30
ENROLLMENT_MAX_ENVELOPE_BYTES = 24 * 1024
ENROLLMENT_MAX_ENVELOPE_CHARS = 4 * ENROLLMENT_MAX_ENVELOPE_BYTES // 3
ENROLLMENT_TIMESTAMP_SKEW_SECONDS = 120
ENROLLMENT_NONCE_TTL_MS = 2 * ENROLLMENT_TIMESTAMP_SKEW_SECONDS * 1000
ENROLLMENT_MAX_ROUNDS = 4
CONTACT_SIGNATURE_DOMAIN = b"kollab-relay-contact-http/1\x00"
CONTACT_MAX_ENVELOPE_BYTES = 6 * 1024
CONTACT_MAX_ENVELOPE_CHARS = (4 * CONTACT_MAX_ENVELOPE_BYTES + 2) // 3
CONTACT_TIMESTAMP_SKEW_SECONDS = ENROLLMENT_TIMESTAMP_SKEW_SECONDS

# Client-facing schema: each endpoint enforces these exact properties and types.
# The code is 100 random Crockford Base32 bits, grouped 4-4-4-4-4. The HTTP API
# never accepts it; clients send only the 32-byte scrypt-derived verifier. That
# verifier is bearer-equivalent at the relay and must stay out of logs/history.
_STRING = {"type": "string"}
_HEX32 = {"type": "string", "pattern": "^[0-9a-f]{32}$"}
_HEX64 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_HEX128 = {"type": "string", "pattern": "^[0-9a-f]{128}$"}
_INTEGER = {"type": "integer", "minimum": 0}
_BASE64URL_32 = {"type": "string", "pattern": "^[A-Za-z0-9_-]{43}$"}
_DEVICE_NAME = {"type": "string", "pattern": f"^{NAME_RE.pattern}$", "maxLength": 63}
_ENVELOPE_SCHEMA = {
    "type": "string",
    "pattern": "^[A-Za-z0-9_-]+$",
    "maxLength": ENROLLMENT_MAX_ENVELOPE_CHARS,
}


def _schema(
    properties: dict[str, dict[str, Any]], *, optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [key for key in properties if key not in optional],
        "optional": list(optional),
        "properties": properties,
    }


_ISSUER_PROPERTIES = {
    "v": {"const": 1},
    "offer_id": _HEX32,
    "issuer_key": _HEX64,
    "room_capability": _HEX64,
    "session": _HEX32,
    "issued_at": _INTEGER,
    "nonce": _HEX32,
    "signature": _HEX128,
}
_DESTINATION_PROPERTIES = {
    "v": {"const": 1},
    "offer_id": _HEX32,
    "destination_key": _HEX64,
    "issued_at": _INTEGER,
    "nonce": _HEX32,
    "signature": _HEX128,
}

_LOOKUP_PROPERTIES = {
    "v": {"const": 1},
    "destination_key": _HEX64,
    "lookup": _BASE64URL_32,
    "issued_at": _INTEGER,
    "nonce": _HEX32,
    "signature": _HEX128,
}

ENROLLMENT_JSON_SCHEMAS = {
    "create_offer": _schema(
        {
            **_ISSUER_PROPERTIES,
            "expires_at": _INTEGER,
            "code_verifier_hash": _HEX64,
            "lookup_hash": _HEX64,
        },
        optional=("lookup_hash",),
    ),
    "submit_request": _schema(
        {
            **_DESTINATION_PROPERTIES,
            "round_id": _HEX32,
            "code_verifier": _BASE64URL_32,
            "envelope": _ENVELOPE_SCHEMA,
            "device_name": _DEVICE_NAME,
        },
        optional=("device_name",),
    ),
    "lookup_offer": _schema(_LOOKUP_PROPERTIES),
    "issuer_poll": _schema({**_ISSUER_PROPERTIES, "claim_id": _HEX32}),
    "publish_challenge": _schema(
        {
            **_ISSUER_PROPERTIES,
            "round_id": _HEX32,
            "destination_key": _HEX64,
            "envelope": _ENVELOPE_SCHEMA,
        }
    ),
    "submit_proof": _schema(
        {
            **_DESTINATION_PROPERTIES,
            "round_id": _HEX32,
            "envelope": _ENVELOPE_SCHEMA,
        }
    ),
    "publish_decision": _schema(
        {
            **_ISSUER_PROPERTIES,
            "round_id": _HEX32,
            "destination_key": _HEX64,
            "envelope": _ENVELOPE_SCHEMA,
        }
    ),
    "reply_poll": _schema(_DESTINATION_PROPERTIES),
    "submit_install_ack": _schema(
        {
            **_DESTINATION_PROPERTIES,
            "round_id": _HEX32,
            "envelope": _ENVELOPE_SCHEMA,
        }
    ),
    "poll_install_ack": _schema(_ISSUER_PROPERTIES),
}

_CONTACT_ENVELOPE_SCHEMA = {
    "type": "string",
    "pattern": "^[A-Za-z0-9_-]+$",
    "maxLength": CONTACT_MAX_ENVELOPE_CHARS,
}
_CONTACT_RECIPIENT_PROPERTIES = {
    "recipient_identity": {
        "type": "string",
        "pattern": "^ed25519:[0-9a-f]{64}$",
    },
    "recipient_key": _HEX64,
}
_CONTACT_SENDER_PROPERTIES = {
    "sender_key": _HEX64,
    "request_id": _HEX32,
    "issued_at": _INTEGER,
    "expires_at": _INTEGER,
    "nonce": _HEX32,
    "signature": _HEX128,
}
CONTACT_JSON_SCHEMAS = {
    "submit": _schema(
        {
            "v": {"const": 1},
            **_CONTACT_RECIPIENT_PROPERTIES,
            **_CONTACT_SENDER_PROPERTIES,
            "envelope": _CONTACT_ENVELOPE_SCHEMA,
        }
    ),
    "inbox": _schema(
        {
            "v": {"const": 1},
            **_CONTACT_RECIPIENT_PROPERTIES,
            "issued_at": _INTEGER,
            "nonce": _HEX32,
            "signature": _HEX128,
        }
    ),
    "decision": _schema(
        {
            "v": {"const": 1},
            **_CONTACT_RECIPIENT_PROPERTIES,
            "request_id": _HEX32,
            "decision": {"type": "string", "pattern": "^(accept|reject)$"},
            "issued_at": _INTEGER,
            "nonce": _HEX32,
            "signature": _HEX128,
        }
    ),
}

CONTACT_REQUESTS_PATH = "/relay/v1/contact/requests"
CONTACT_INBOX_PATH = "/relay/v1/contact/inbox"
CONTACT_DECISIONS_PATH = "/relay/v1/contact/decisions"


def generate_enrollment_code(offer_id: str) -> str:
    """Generate the local-only 40-bit short join code; the offer id is looked up.

    Kept validating ``offer_id`` even though it is no longer embedded, since
    every call site already has it in hand and a bad id here is a caller bug.
    """
    if not isinstance(offer_id, str) or not _HEX_32.fullmatch(offer_id):
        raise ValueError("invalid enrollment offer id")
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    secret = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{secret[:4]}-{secret[4:]}"


def _normalize_enrollment_code(code: str) -> str:
    """Return the normalized secret for a short join code.

    A short code carries no offer id; the caller must resolve one first
    through the lookup route.
    """
    if not isinstance(code, str):
        raise ValueError("invalid enrollment code")
    stripped = code.strip()
    match = _SHORT_ENROLLMENT_CODE.fullmatch(stripped)
    if match is not None:
        return (match.group(1) + match.group(2)).upper()
    raise ValueError("invalid enrollment code")


def derive_enrollment_code_verifier(code: str, offer_id: str | None = None) -> tuple[str, str]:
    """Derive `(offer_id, verifier)` locally; never call this with an HTTP body.

    A short code has no offer id embedded, so the resolved id from the
    lookup route must be passed in.
    """
    secret = _normalize_enrollment_code(code)
    resolved_offer_id = offer_id
    if not isinstance(resolved_offer_id, str) or not _HEX_32.fullmatch(resolved_offer_id):
        raise ValueError("invalid enrollment offer id")
    salt = hashlib.sha256(
        ENROLLMENT_CODE_KDF_DOMAIN + bytes.fromhex(resolved_offer_id)
    ).digest()
    verifier = hashlib.scrypt(
        secret.encode("ascii"),
        salt=salt,
        n=ENROLLMENT_CODE_SCRYPT_N,
        r=ENROLLMENT_CODE_SCRYPT_R,
        p=ENROLLMENT_CODE_SCRYPT_P,
        dklen=32,
    )
    return resolved_offer_id, _base64url_encode(verifier)


def derive_enrollment_lookup_tag(code: str, origin: str) -> str:
    """Derive the base64url lookup tag a joiner sends to find its offer."""
    secret = _normalize_enrollment_code(code)
    if not isinstance(origin, str) or not origin:
        raise ValueError("invalid origin")
    key = ENROLLMENT_LOOKUP_DOMAIN + origin.encode("ascii")
    tag = hmac.new(key, secret.encode("ascii"), hashlib.sha256).digest()
    return _base64url_encode(tag)


def enrollment_lookup_hash(lookup: str) -> str:
    """Return the relay-storable hash of a lookup tag; the tag is bearer-equivalent."""
    tag_bytes = _base64url_decode(lookup, expected_bytes=32)
    return hashlib.sha256(tag_bytes).hexdigest()


def enrollment_verifier_hash(offer_id: str, verifier: str) -> str:
    """Return the relay-storable verifier hash; the verifier is bearer-equivalent."""
    if not isinstance(offer_id, str) or not _HEX_32.fullmatch(offer_id):
        raise ValueError("invalid enrollment offer id")
    verifier_bytes = _base64url_decode(verifier, expected_bytes=32)
    key = ENROLLMENT_VERIFIER_DOMAIN + bytes.fromhex(offer_id)
    return hmac.new(key, verifier_bytes, hashlib.sha256).hexdigest()


def enrollment_signature_message(
    origin: str, method: str, path: str, body: dict[str, Any]
) -> bytes:
    """Canonical relay signature bytes; `signature` itself is omitted."""
    if not isinstance(body, dict):
        raise ValueError("signed request must be a JSON object")
    signed_body = {key: value for key, value in body.items() if key != "signature"}
    canonical_json = json.dumps(
        signed_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return (
        ENROLLMENT_SIGNATURE_DOMAIN
        + method.upper().encode("ascii")
        + b"\n"
        + origin.encode("ascii")
        + b"\n"
        + path.encode("ascii")
        + b"\n"
        + canonical_json
    )


def sign_enrollment_request(
    signing_key: Any, origin: str, method: str, path: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sign a protocol body for an enrollment HTTP request."""
    if "signature" in body:
        raise ValueError("request already has a signature")
    message = enrollment_signature_message(origin, method, path, body)
    signed = signing_key.sign(message)
    return {**body, "signature": signed.signature.hex()}


def verify_enrollment_request_signature(
    public_key: str,
    origin: str,
    method: str,
    path: str,
    body: dict[str, Any],
) -> bool:
    """Verify the canonical protocol signature without exposing exceptions."""
    signature = body.get("signature")
    if (
        not isinstance(public_key, str)
        or not _HEX_64.fullmatch(public_key)
        or not isinstance(signature, str)
        or not _HEX_128.fullmatch(signature)
    ):
        return False
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            enrollment_signature_message(origin, method, path, body),
            bytes.fromhex(signature),
        )
        return True
    except (BadSignatureError, ValueError, TypeError, UnicodeEncodeError):
        return False


def contact_signature_message(
    origin: str, method: str, path: str, body: dict[str, Any]
) -> bytes:
    if not isinstance(body, dict):
        raise ValueError("signed request must be a JSON object")
    signed_body = {key: value for key, value in body.items() if key != "signature"}
    canonical_json = json.dumps(
        signed_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return (
        CONTACT_SIGNATURE_DOMAIN
        + method.upper().encode("ascii")
        + b"\n"
        + origin.encode("ascii")
        + b"\n"
        + path.encode("ascii")
        + b"\n"
        + canonical_json
    )


def verify_contact_request_signature(
    public_key: str,
    origin: str,
    method: str,
    path: str,
    body: dict[str, Any],
) -> bool:
    signature = body.get("signature")
    if (
        not isinstance(public_key, str)
        or not _HEX_64.fullmatch(public_key)
        or not isinstance(signature, str)
        or not _HEX_128.fullmatch(signature)
    ):
        return False
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            contact_signature_message(origin, method, path, body),
            bytes.fromhex(signature),
        )
        return True
    except (BadSignatureError, CryptoError, ValueError, TypeError, UnicodeEncodeError):
        return False


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str, *, expected_bytes: int | None = None) -> bytes:
    if not isinstance(value, str) or not _B64URL.fullmatch(value):
        raise ValueError("invalid base64url value")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64url value") from exc
    if _base64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url value")
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise ValueError("invalid base64url length")
    return decoded


def _validate_enrollment_schema(name: str, frame: dict[str, Any]) -> None:
    _validate_json_schema(ENROLLMENT_JSON_SCHEMAS[name], frame)
    if "envelope" in frame:
        envelope = _base64url_decode(frame["envelope"])
        if not envelope or len(envelope) > ENROLLMENT_MAX_ENVELOPE_BYTES:
            raise ValueError("invalid envelope")
    if "code_verifier" in frame:
        _base64url_decode(frame["code_verifier"], expected_bytes=32)


def _validate_contact_schema(name: str, frame: dict[str, Any]) -> None:
    _validate_json_schema(CONTACT_JSON_SCHEMAS[name], frame)
    if "envelope" in frame:
        envelope = _base64url_decode(frame["envelope"])
        if not envelope or len(envelope) > CONTACT_MAX_ENVELOPE_BYTES:
            raise ValueError("invalid envelope")


def _validate_json_schema(schema: dict[str, Any], frame: dict[str, Any]) -> None:
    properties = schema["properties"]
    required = set(schema["required"])
    allowed = required | set(schema.get("optional", ()))
    if not required <= set(frame) <= allowed:
        raise ValueError("unexpected request shape")
    for field_name, rules in properties.items():
        if field_name not in frame:
            continue
        value = frame[field_name]
        expected_type = rules.get("type")
        if expected_type == "string":
            if not isinstance(value, str):
                raise ValueError("invalid request field")
            pattern = rules.get("pattern")
            if pattern and re.fullmatch(pattern, value) is None:
                raise ValueError("invalid request field")
            if len(value) > rules.get("maxLength", len(value)):
                raise ValueError("invalid request field")
        elif expected_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("invalid request field")
            if value < rules.get("minimum", 0):
                raise ValueError("invalid request field")
        elif "const" in rules and value != rules["const"]:
            raise ValueError("invalid request field")
    if frame.get("v") != 1 or isinstance(frame.get("v"), bool):
        raise ValueError("invalid request version")


class RelayConfigError(ValueError):
    """Invalid relay process configuration."""


def normalize_origin(value: str) -> str:
    """Validate a canonical HTTPS origin with no path, query, or fragment."""
    if not isinstance(value, str) or len(value) > 512:
        raise RelayConfigError("origin must be an absolute HTTPS origin")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise RelayConfigError("origin must use HTTPS and include a host")
        if (
            parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RelayConfigError("origin must contain only scheme and authority")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parsed.port
        if port == 443:
            port = None
        canonical = f"https://{host}" + (f":{port}" if port is not None else "")
        if value != canonical:
            raise RelayConfigError(
                "origin must be canonical lowercase HTTPS without a trailing slash"
            )
        return canonical
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, RelayConfigError):
            raise
        raise RelayConfigError(f"invalid HTTPS origin: {exc}") from exc


@dataclass(frozen=True)
class RelayConfig:
    origin: str
    node_id: str
    backend_url: str | None = None
    backend_cluster: bool = False
    dev_in_memory: bool = False
    bind: str = "127.0.0.1"
    port: int = 8765
    trusted_proxies: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] = (
        frozenset()
    )
    limits: RelayLimits = RelayLimits()

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", normalize_origin(self.origin))
        if not _HEX_32.fullmatch(self.node_id):
            raise RelayConfigError(
                "node_id must be 32 lowercase hexadecimal characters"
            )
        if self.dev_in_memory:
            if self.backend_url is not None or self.backend_cluster:
                raise RelayConfigError(
                    "in-memory development mode cannot use a shared backend"
                )
        else:
            if not self.backend_url:
                raise RelayConfigError("a Redis-compatible backend URL is required")
            try:
                validate_backend_url(self.backend_url, cluster=self.backend_cluster)
            except ValueError as exc:
                raise RelayConfigError(str(exc)) from exc
        try:
            ipaddress.ip_address(self.bind)
        except ValueError as exc:
            raise RelayConfigError(
                "bind must be a literal IPv4 or IPv6 address"
            ) from exc
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise RelayConfigError("port must be in 1..65535")
        if not isinstance(self.trusted_proxies, frozenset):
            raise RelayConfigError(
                "trusted_proxies must be a frozenset of exact IP addresses"
            )


@dataclass(eq=False, slots=True)
class PeerConnection:
    websocket: web.WebSocketResponse
    source_ip: str
    connection_id: str = field(default_factory=lambda: secrets.token_hex(16))
    reservation: str | None = None
    tokens: float = TOKEN_BUCKET_BURST
    last_refill: float = field(default_factory=time.monotonic)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    room_hash: str | None = None
    key: str | None = None
    session: str | None = None
    announced: bool = False


class RelayState:
    """Worker sockets plus shared-backend leases and presence."""

    def __init__(self, config: RelayConfig):
        self.config = config
        self.backend = (
            InMemoryBackend(config.node_id, config.limits)
            if config.dev_in_memory
            else RedisRelayBackend(
                config.backend_url or "",
                config.node_id,
                cluster=config.backend_cluster,
                limits=config.limits,
            )
        )
        self.connections: set[PeerConnection] = set()
        self.connections_by_id: dict[str, PeerConnection] = {}
        self.rooms: dict[str, dict[str, PeerConnection]] = {}
        self.room_snapshots: dict[str, tuple[tuple[str, str], ...]] = {}
        self.ready = False
        self.shutting_down = False
        self.maintenance_task: asyncio.Task[None] | None = None
        self.metrics = {
            "relay_forward_attempts_total": 0,
            "relay_forward_delivered_total": 0,
            "relay_forward_errors_total": 0,
            "relay_registration_rejections_total": 0,
            "relay_rate_rejections_total": 0,
        }

    async def start(self) -> None:
        await self.backend.start(self)
        self.ready = True
        self.maintenance_task = asyncio.create_task(
            self._maintain(), name=f"kollab-relay-maintenance-{self.config.node_id}"
        )

    async def reserve(self, client: PeerConnection) -> str | None:
        if self.shutting_down:
            return "shutting_down"
        if not self.ready:
            raise RelayBackendError("relay backend is not ready")
        client.reservation = await self.backend.reserve_connection(
            client.connection_id, client.source_ip
        )
        if client.reservation is None:
            return "capacity"
        self.connections.add(client)
        self.connections_by_id[client.connection_id] = client
        return None

    async def register(
        self, client: PeerConnection, *, room_hash: str, key: str, session: str
    ) -> tuple[str | None, list[PeerRecord]]:
        member = PeerRecord(key, session, self.config.node_id, client.connection_id)
        # Retain expected identity before the backend mutation so disconnect
        # cleanup can conditionally remove it even if the reply is lost.
        client.room_hash = room_hash
        client.key = key
        client.session = session
        error, records = await self.backend.register_room(room_hash, member)
        if error is not None:
            return error, []
        return None, records

    async def activate(self, client: PeerConnection, records: list[PeerRecord]) -> None:
        if client.room_hash is None or client.key is None:
            raise RelayBackendError("cannot activate an incomplete room registration")
        client.announced = True
        self.rooms.setdefault(client.room_hash, {})[client.key] = client
        nodes = {record.node_id for record in records} - {self.config.node_id}
        await self.backend.notify_room_change(client.room_hash, nodes)
        await self.room_changed(
            client.room_hash, exclude_connection_id=client.connection_id
        )

    async def release(self, client: PeerConnection) -> None:
        if client in self.connections:
            self.connections.remove(client)
            self.connections_by_id.pop(client.connection_id, None)
        room_hash, key = client.room_hash, client.key
        if room_hash is not None and key is not None:
            room = self.rooms.get(room_hash)
            if room is not None and room.get(key) is client:
                room.pop(key, None)
                if not room:
                    self.rooms.pop(room_hash, None)
            try:
                member = PeerRecord(
                    key, client.session or "", self.config.node_id, client.connection_id
                )
                removed, records = await self.backend.remove_peer(room_hash, member)
                if removed:
                    await self.backend.notify_room_change(
                        room_hash,
                        {record.node_id for record in records} - {self.config.node_id},
                    )
                    await self.room_changed(room_hash)
            except RelayBackendError:
                pass
        try:
            await self.backend.release_connection(
                client.connection_id, client.source_ip, client.reservation
            )
        except RelayBackendError:
            pass
        client.room_hash = client.key = client.session = None
        client.reservation = None
        client.announced = False

    async def destination(self, room_hash: str, key: str) -> PeerRecord | None:
        records, pruned = await self.backend.list_room(room_hash)
        if pruned:
            await self.backend.notify_room_change(
                room_hash, {record.node_id for record in records}
            )
        return next((record for record in records if record.key == key), None)

    async def forward(self, destination: PeerRecord, route: dict[str, str]) -> bool:
        return await self.backend.forward(destination, route)

    async def deliver_local(self, route: dict[str, Any]) -> bool:
        client = self.connections_by_id.get(str(route.get("connection_id", "")))
        if (
            client is None
            or client.websocket.closed
            or not client.announced
            or client.room_hash != route.get("room_hash")
            or client.key != route.get("to")
            or client.session != route.get("to_session")
        ):
            return False
        if not await self.backend.owner_valid():
            return False
        delivered = await _send_json(
            client,
            {
                "type": "message",
                "from": route["from"],
                "session": route["session"],
                "id": route["id"],
                "ciphertext": route["ciphertext"],
            },
        )
        if not delivered:
            await _disconnect_peer(self, client)
        return delivered

    async def room_changed(
        self, room_hash: str, *, exclude_connection_id: str | None = None
    ) -> None:
        await self._sync_room(
            room_hash, force=True, exclude_connection_id=exclude_connection_id
        )

    async def _sync_room(
        self,
        room_hash: str,
        *,
        force: bool,
        exclude_connection_id: str | None = None,
    ) -> None:
        local = list(self.rooms.get(room_hash, {}).values())
        if not local:
            self.room_snapshots.pop(room_hash, None)
            return
        records, pruned = await self.backend.list_room(room_hash)
        signature = tuple(
            (record.key, record.session)
            for record in sorted(records, key=lambda item: item.key)
        )
        changed = signature != self.room_snapshots.get(room_hash)
        if not force and not pruned and not changed:
            return
        self.room_snapshots[room_hash] = signature
        if pruned:
            nodes = {record.node_id for record in records} - {self.config.node_id}
            if nodes:
                await self.backend.notify_room_change(room_hash, nodes)
        for peer in local:
            if (
                peer.key is None
                or peer not in self.connections
                or peer.connection_id == exclude_connection_id
            ):
                continue
            snapshot = [
                {"key": record.key, "session": record.session}
                for record in sorted(records, key=lambda item: item.key)
                if record.key != peer.key
            ]
            if not await _send_json(peer, {"type": "peers", "peers": snapshot}):
                await _disconnect_peer(self, peer)

    async def backend_failed(self) -> None:
        self.ready = False
        self.shutting_down = True
        await _close_websockets(
            list(self.connections),
            code=WSCloseCode.SERVICE_RESTART,
            message=b"relay backplane unavailable",
        )

    async def _maintain(self) -> None:
        semaphore = asyncio.Semaphore(32)
        try:
            while not self.shutting_down:
                await asyncio.sleep(10)
                if not await self.backend.health():
                    await self.backend_failed()
                    return
                clients = list(self.connections)
                invalid: list[PeerConnection] = []

                async def renew_client(client: PeerConnection) -> None:
                    async with semaphore:
                        member = None
                        if client.room_hash and client.key and client.session:
                            member = PeerRecord(
                                client.key,
                                client.session,
                                self.config.node_id,
                                client.connection_id,
                            )
                        valid = await self.backend.renew(
                            client.connection_id,
                            client.source_ip,
                            client.reservation,
                            member,
                            client.room_hash,
                        )
                    if not valid:
                        invalid.append(client)

                await asyncio.gather(*(renew_client(client) for client in clients))
                if invalid:
                    await _close_websockets(
                        invalid,
                        code=WSCloseCode.GOING_AWAY,
                        message=b"relay lease expired",
                    )
                if not self.ready:
                    return
                rooms = list(self.rooms)

                async def reconcile(room_hash: str) -> None:
                    async with semaphore:
                        await self._sync_room(room_hash, force=False)

                await asyncio.gather(*(reconcile(room) for room in rooms))
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.backend_failed()


def create_app(config: RelayConfig) -> web.Application:
    """Build the standalone aiohttp application for one configured origin."""
    state = RelayState(config)
    app = web.Application(client_max_size=MAX_FRAME_BYTES)
    app["relay_state"] = state
    app.router.add_get(HEALTH_PATH, health_handler)
    app.router.add_get("/relay/v1/metrics", metrics_handler)
    app.router.add_get(WEBSOCKET_PATH, websocket_handler)
    app.router.add_post(ENROLLMENT_OFFERS_PATH, enrollment_offer_handler)
    app.router.add_post(ENROLLMENT_LOOKUP_PATH, enrollment_lookup_handler)
    app.router.add_post(ENROLLMENT_REQUEST_PATH, enrollment_request_handler)
    app.router.add_post(ENROLLMENT_ISSUER_POLL_PATH, enrollment_issuer_poll_handler)
    app.router.add_post(ENROLLMENT_CHALLENGE_PATH, enrollment_challenge_handler)
    app.router.add_post(ENROLLMENT_PROOF_PATH, enrollment_proof_handler)
    app.router.add_post(ENROLLMENT_DECISION_PATH, enrollment_decision_handler)
    app.router.add_post(ENROLLMENT_REPLY_POLL_PATH, enrollment_reply_poll_handler)
    app.router.add_post(ENROLLMENT_ACK_PATH, enrollment_install_ack_handler)
    app.router.add_post(ENROLLMENT_ACK_POLL_PATH, enrollment_install_ack_poll_handler)
    app.router.add_post(CONTACT_REQUESTS_PATH, contact_request_handler)
    app.router.add_post(CONTACT_INBOX_PATH, contact_inbox_handler)
    app.router.add_post(CONTACT_DECISIONS_PATH, contact_decision_handler)
    app.on_startup.append(start_relay)
    app.on_shutdown.append(shutdown_relay)
    app.on_cleanup.append(cleanup_relay)
    return app


SUPERVISOR_PID_ENV = "KOLLAB_RELAY_SUPERVISOR_PID"
SUPERVISOR_CHECK_SECONDS = 2.0


async def _watch_supervisor(supervisor_pid: int) -> None:
    """Stop this worker once the runtime that spawned it is gone.

    Workers run in their own session, so a supervisor that dies without a
    clean stop would otherwise leave them serving and renewing their owner
    lease, which blocks every replacement runtime.
    """
    while True:
        if os.getppid() != supervisor_pid:
            os.kill(os.getpid(), signal.SIGTERM)
            return
        await asyncio.sleep(SUPERVISOR_CHECK_SECONDS)


async def start_relay(app: web.Application) -> None:
    state: RelayState = app["relay_state"]
    try:
        await state.start()
    except RelayBackendError:
        # Do not include backend connection details in startup logs.
        raise RuntimeError("relay backend startup failed") from None
    supervisor = os.environ.get(SUPERVISOR_PID_ENV, "")
    if supervisor.isdigit():
        app["supervisor_watch"] = asyncio.create_task(_watch_supervisor(int(supervisor)))


async def health_handler(request: web.Request) -> web.Response:
    if request.query_string:
        raise web.HTTPBadRequest(text="query parameters are not accepted")
    state: RelayState = request.app["relay_state"]
    healthy = state.ready and state.backend.is_ready and not state.shutting_down
    return web.json_response(
        {
            "status": "ok" if healthy else "unavailable",
            "protocol": PROTOCOL,
            "origin": state.config.origin,
            "node_id": state.config.node_id,
        },
        status=200 if healthy else 503,
        headers={"Cache-Control": "no-store"},
    )


async def metrics_handler(request: web.Request) -> web.Response:
    if request.query_string:
        raise web.HTTPBadRequest(text="query parameters are not accepted")
    state: RelayState = request.app["relay_state"]
    admission_metrics_available = 1
    try:
        admission_usage = await state.backend.enrollment_admission_usage()
    except RelayBackendError:
        admission_metrics_available = 0
        admission_usage = {"rate_sources": -1, "nonce_records": -1}
    values = {
        **state.metrics,
        "relay_active_connections": len(state.connections),
        "relay_active_rooms": len(state.rooms),
        "relay_enrollment_admission_metrics_available": admission_metrics_available,
        "relay_enrollment_rate_buckets_tracked": admission_usage["rate_sources"],
        "relay_enrollment_rate_buckets_limit": ENROLLMENT_MAX_RATE_SOURCES,
        "relay_enrollment_nonce_records_tracked": admission_usage["nonce_records"],
        "relay_enrollment_nonce_records_limit": ENROLLMENT_MAX_NONCES,
        "relay_backend_ready": int(
            state.ready and state.backend.is_ready and not state.shutting_down
        ),
    }
    body = "".join(f"{name} {value}\n" for name, value in sorted(values.items()))
    return web.Response(
        text=body,
        content_type="text/plain",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


async def websocket_handler(request: web.Request) -> web.StreamResponse:
    state: RelayState = request.app["relay_state"]
    if request.query_string:
        raise web.HTTPBadRequest(
            text="credentials and parameters are not accepted in the URL"
        )
    if "Origin" in request.headers:
        raise web.HTTPForbidden(text="browser Origin headers are not accepted")
    source_ip = _source_ip(request, state.config.trusted_proxies)
    if source_ip is None:
        raise web.HTTPForbidden(text="could not determine a literal source IP")

    ws = web.WebSocketResponse(
        max_msg_size=MAX_FRAME_BYTES,
        heartbeat=HEARTBEAT_SECONDS,
        autoping=True,
        autoclose=True,
        compress=False,
    )
    client = PeerConnection(websocket=ws, source_ip=source_ip)
    try:
        reservation_error = await state.reserve(client)
    except RelayBackendError:
        raise web.HTTPServiceUnavailable(text="relay backend unavailable")
    if reservation_error is not None:
        state.metrics["relay_registration_rejections_total"] += 1
        raise web.HTTPServiceUnavailable(text=reservation_error)

    try:
        await ws.prepare(request)
        nonce = os.urandom(32).hex()
        await _send_json(
            client,
            {
                "type": "challenge",
                "protocol": PROTOCOL,
                "origin": state.config.origin,
                "nonce": nonce,
            },
        )
        try:
            message = await asyncio.wait_for(
                ws.receive(), timeout=REGISTRATION_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            await _send_error(client, "registration_timeout")
            await ws.close(code=WSCloseCode.POLICY_VIOLATION)
            return ws

        if not _consume_rate_token(client) or message.type != WSMsgType.TEXT:
            await _send_error(client, "invalid_registration")
            await ws.close(code=WSCloseCode.POLICY_VIOLATION)
            return ws
        try:
            frame = _strict_json(message.data)
            key, room_capability, session = _parse_registration_shape(frame)
            signature = bytes.fromhex(frame["signature"])
            message_to_sign = _registration_message(
                state.config.origin, nonce, key, room_capability, session
            )
            VerifyKey(bytes.fromhex(key)).verify(message_to_sign, signature)
        except (ValueError, TypeError, KeyError, BadSignatureError):
            await _send_error(client, "invalid_registration")
            await ws.close(code=WSCloseCode.POLICY_VIOLATION)
            return ws

        room_hash = room_digest(room_capability)
        try:
            registration_error, room_records = await state.register(
                client, room_hash=room_hash, key=key, session=session
            )
        except RelayBackendError:
            await state.backend_failed()
            await _send_error(client, "backend_unavailable")
            await ws.close(code=WSCloseCode.SERVICE_RESTART)
            return ws
        if registration_error is not None:
            state.metrics["relay_registration_rejections_total"] += 1
            await _send_error(client, registration_error)
            await ws.close(code=WSCloseCode.POLICY_VIOLATION)
            return ws

        if not await _send_json(
            client,
            {
                "type": "registered",
                "protocol": PROTOCOL,
                "key": key,
                "session": session,
            },
        ):
            return ws
        if not await _send_json(
            client,
            {
                "type": "peers",
                "peers": [
                    {"key": peer.key, "session": peer.session}
                    for peer in sorted(room_records, key=lambda item: item.key)
                    if peer.key != client.key
                ],
            },
        ):
            return ws
        try:
            await state.activate(client, room_records)
        except RelayBackendError:
            await state.backend_failed()
            await _send_error(client, "backend_unavailable")
            await ws.close(code=WSCloseCode.SERVICE_RESTART)
            return ws
        async for message in ws:
            if message.type == WSMsgType.ERROR:
                break
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                break
            if message.type != WSMsgType.TEXT:
                await _send_error(client, "invalid_frame")
                await ws.close(code=WSCloseCode.POLICY_VIOLATION)
                break
            if not _consume_rate_token(client):
                state.metrics["relay_rate_rejections_total"] += 1
                await _send_error(client, "rate_limited")
                await ws.close(code=WSCloseCode.POLICY_VIOLATION)
                break
            try:
                frame = _strict_json(message.data)
                recipient_key, message_id, ciphertext = _parse_send_shape(frame)
            except (ValueError, TypeError, KeyError):
                await _send_error(client, "invalid_frame")
                continue

            if client.room_hash is None or client.key is None or client.session is None:
                await _send_error(client, "not_registered", message_id)
                await ws.close(code=WSCloseCode.POLICY_VIOLATION)
                break
            state.metrics["relay_forward_attempts_total"] += 1
            try:
                recipient = await state.destination(client.room_hash, recipient_key)
            except RelayBackendError:
                state.metrics["relay_forward_errors_total"] += 1
                await _send_error(client, "backend_unavailable", message_id)
                await state.backend_failed()
                continue
            if recipient is None:
                state.metrics["relay_forward_errors_total"] += 1
                await _send_error(client, "peer_offline", message_id)
                continue
            route = {
                "room_hash": client.room_hash,
                "from": client.key,
                "session": client.session,
                "id": message_id,
                "ciphertext": ciphertext,
            }
            try:
                delivered = await state.forward(recipient, route)
            except RelayBackendError:
                state.metrics["relay_forward_errors_total"] += 1
                await _send_error(client, "backend_unavailable", message_id)
                await state.backend_failed()
                continue
            if not delivered:
                state.metrics["relay_forward_errors_total"] += 1
                await _send_error(client, "peer_offline", message_id)
            else:
                state.metrics["relay_forward_delivered_total"] += 1
        return ws
    finally:
        await state.release(client)


async def shutdown_relay(app: web.Application) -> None:
    state: RelayState = app["relay_state"]
    state.shutting_down = True
    clients = list(state.connections)
    state.ready = False
    await _close_websockets(
        clients,
        code=WSCloseCode.GOING_AWAY,
        message=b"relay shutting down",
    )


async def _close_websockets(
    clients: list[PeerConnection], *, code: int, message: bytes
) -> None:
    semaphore = asyncio.Semaphore(64)

    async def close_one(client: PeerConnection) -> None:
        async with semaphore:
            try:
                await asyncio.wait_for(
                    client.websocket.close(code=code, message=message), timeout=1
                )
            except (asyncio.TimeoutError, ConnectionError, RuntimeError, OSError):
                pass

    if not clients:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*(close_one(client) for client in clients)), timeout=4
        )
    except asyncio.TimeoutError:
        pass


async def cleanup_relay(app: web.Application) -> None:
    state: RelayState = app["relay_state"]
    watch = app.get("supervisor_watch")
    if watch is not None:
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
    if state.maintenance_task is not None:
        state.maintenance_task.cancel()
        await asyncio.gather(state.maintenance_task, return_exceptions=True)
        state.maintenance_task = None
    await state.backend.close()


async def _disconnect_peer(state: RelayState, peer: PeerConnection) -> None:
    await state.release(peer)
    try:
        await peer.websocket.close(code=WSCloseCode.GOING_AWAY)
    except (ConnectionError, RuntimeError):
        pass


async def _send_json(client: PeerConnection, payload: dict[str, Any]) -> bool:
    try:
        async with asyncio.timeout(SEND_DEADLINE_SECONDS):
            async with client.send_lock:
                encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
                if len(encoded.encode("utf-8")) > MAX_FRAME_BYTES:
                    return False
                await client.websocket.send_str(encoded)
                return True
    except (asyncio.TimeoutError, ConnectionError, RuntimeError, OSError):
        return False


async def _send_error(
    client: PeerConnection, code: str, message_id: str | None = None
) -> bool:
    payload: dict[str, str] = {"type": "error", "code": code}
    if message_id is not None:
        payload["id"] = message_id
    return await _send_json(client, payload)


def _consume_rate_token(client: PeerConnection) -> bool:
    now = time.monotonic()
    elapsed = max(0.0, now - client.last_refill)
    client.tokens = min(TOKEN_BUCKET_BURST, client.tokens + elapsed * TOKEN_BUCKET_RATE)
    client.last_refill = now
    if client.tokens < 1.0:
        return False
    client.tokens -= 1.0
    return True


def _strict_json(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("frame exceeds the maximum size")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON object key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid JSON frame") from exc
    if not isinstance(parsed, dict):
        raise ValueError("frame must be a JSON object")
    return parsed


def _parse_registration_shape(frame: dict[str, Any]) -> tuple[str, str, str]:
    if set(frame) != {"type", "key", "room", "session", "signature"}:
        raise ValueError("registration has an unexpected shape")
    if frame.get("type") != "register":
        raise ValueError("first client frame must register")
    key = frame.get("key")
    room = frame.get("room")
    session = frame.get("session")
    signature = frame.get("signature")
    if not isinstance(key, str) or not _HEX_64.fullmatch(key):
        raise ValueError("invalid key encoding")
    if not isinstance(room, str) or not _HEX_64.fullmatch(room):
        raise ValueError("invalid room capability encoding")
    if not isinstance(session, str) or not _HEX_32.fullmatch(session):
        raise ValueError("invalid session encoding")
    if not isinstance(signature, str) or not _HEX_128.fullmatch(signature):
        raise ValueError("invalid signature encoding")
    return key, room, session


def _parse_send_shape(frame: dict[str, Any]) -> tuple[str, str, str]:
    if set(frame) != {"type", "to", "id", "ciphertext"}:
        raise ValueError("send frame has an unexpected shape")
    if frame.get("type") != "send":
        raise ValueError("only send frames are supported")
    recipient = frame.get("to")
    message_id = frame.get("id")
    ciphertext = frame.get("ciphertext")
    if not isinstance(recipient, str) or not _HEX_64.fullmatch(recipient):
        raise ValueError("invalid destination key")
    if not isinstance(message_id, str) or not _HEX_32.fullmatch(message_id):
        raise ValueError("invalid message id")
    if (
        not isinstance(ciphertext, str)
        or not ciphertext
        or len(ciphertext) > MAX_CIPHERTEXT_CHARS
        or len(ciphertext) % 4 != 0
    ):
        raise ValueError("ciphertext must be at most 48 KiB of standard base64")
    try:
        base64.b64decode(ciphertext, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("ciphertext must be strict standard base64") from exc
    return recipient, message_id, ciphertext


def _registration_message(
    origin: str, nonce: str, key: str, room: str, session: str
) -> bytes:
    return f"{PROTOCOL}\n{origin}\n{nonce}\n{key}\n{room}\n{session}".encode("utf-8")


def room_digest(room_capability: str) -> str:
    """Hash the decoded 32-byte room capability; never retain the raw invite."""
    if not isinstance(room_capability, str) or not _HEX_64.fullmatch(room_capability):
        raise ValueError("room capability must be 64 lowercase hexadecimal characters")
    return hashlib.sha256(bytes.fromhex(room_capability)).hexdigest()


def _source_ip(
    request: web.Request,
    trusted_proxies: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> str | None:
    transport = request.transport
    peername = transport.get_extra_info("peername") if transport is not None else None
    if not isinstance(peername, tuple) or not peername:
        return None
    try:
        peer_ip = ipaddress.ip_address(str(peername[0]).split("%", 1)[0])
    except ValueError:
        return None

    if peer_ip in trusted_proxies:
        forwarded = request.headers.getall("X-Real-IP", [])
        real_ip = forwarded[0] if len(forwarded) == 1 else ""
        if real_ip and "," not in real_ip:
            try:
                return str(ipaddress.ip_address(real_ip.strip()))
            except ValueError:
                pass
    return str(peer_ip)


class _EnrollmentHTTPError(Exception):
    def __init__(
        self, status: int, code: str, *, retry_after_seconds: int | None = None
    ):
        self.status = status
        self.code = code
        self.retry_after_seconds = retry_after_seconds


def _enrollment_endpoint(handler):
    async def wrapped(request: web.Request) -> web.Response:
        try:
            state: RelayState = request.app["relay_state"]
            await _consume_enrollment_request_rate(
                request, state, endpoint=handler.__name__
            )
            return await handler(request)
        except _EnrollmentHTTPError as exc:
            headers = (
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds is not None
                else None
            )
            return web.json_response(
                {"error": exc.code}, status=exc.status, headers=headers
            )
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"error": "request_too_large"}, status=413)
        except RelayBackendError:
            return web.json_response({"error": "backend_unavailable"}, status=503)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            return web.json_response({"error": "invalid_request"}, status=400)

    wrapped.__name__ = handler.__name__
    return wrapped


async def _read_enrollment_frame(
    request: web.Request, schema_name: str
) -> dict[str, Any]:
    if request.query_string or "Origin" in request.headers:
        raise _EnrollmentHTTPError(400, "invalid_request")
    if request.content_type != "application/json":
        raise _EnrollmentHTTPError(400, "invalid_request")
    try:
        raw = await request.read()
        frame = _strict_json(raw.decode("utf-8"))
        _validate_enrollment_schema(schema_name, frame)
    except web.HTTPRequestEntityTooLarge:
        raise
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise _EnrollmentHTTPError(400, "invalid_request") from exc
    offer_in_path = request.match_info.get("offer_id")
    if offer_in_path is not None and (
        not _HEX_32.fullmatch(offer_in_path) or frame.get("offer_id") != offer_in_path
    ):
        raise _EnrollmentHTTPError(400, "invalid_request")
    return frame


async def _read_contact_frame(request: web.Request, schema_name: str) -> dict[str, Any]:
    if request.query_string or "Origin" in request.headers:
        raise _EnrollmentHTTPError(400, "invalid_request")
    if request.content_type != "application/json":
        raise _EnrollmentHTTPError(400, "invalid_request")
    try:
        raw = await request.read()
        frame = _strict_json(raw.decode("utf-8"))
        _validate_contact_schema(schema_name, frame)
    except web.HTTPRequestEntityTooLarge:
        raise
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise _EnrollmentHTTPError(400, "invalid_request") from exc
    return frame


def _verify_contact_frame(
    state: RelayState,
    request: web.Request,
    frame: dict[str, Any],
    key_field: str,
) -> None:
    issued_at = frame["issued_at"]
    if abs(int(time.time()) - issued_at) > CONTACT_TIMESTAMP_SKEW_SECONDS:
        raise _EnrollmentHTTPError(401, "invalid_contact")
    try:
        VerifyKey(bytes.fromhex(frame["recipient_key"])).to_curve25519_public_key()
    except (ValueError, CryptoError) as exc:
        raise _EnrollmentHTTPError(400, "invalid_request") from exc
    if frame["recipient_identity"] != "ed25519:" + frame["recipient_key"]:
        raise _EnrollmentHTTPError(400, "invalid_request")
    if not verify_contact_request_signature(
        frame[key_field],
        state.config.origin,
        request.method,
        request.path,
        frame,
    ):
        raise _EnrollmentHTTPError(401, "invalid_contact")


def _contact_recipient_hash(public_key: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key)).hexdigest()


async def _consume_enrollment_request_rate(
    request: web.Request, state: RelayState, *, endpoint: str
) -> None:
    if state.shutting_down or not state.ready or not await state.backend.owner_valid():
        raise _EnrollmentHTTPError(503, "backend_unavailable")
    source_ip = _source_ip(request, state.config.trusted_proxies)
    if source_ip is None:
        raise _EnrollmentHTTPError(403, "unauthorized")
    source_hash = hashlib.sha256(f"{source_ip}\0{endpoint}".encode("ascii")).hexdigest()
    limit = (
        ENROLLMENT_POLL_RATE_LIMIT
        if endpoint
        in {
            "enrollment_issuer_poll_handler",
            "enrollment_reply_poll_handler",
            "enrollment_install_ack_poll_handler",
            "contact_inbox_handler",
        }
        else ENROLLMENT_RATE_LIMIT
    )
    if not await state.backend.consume_enrollment_rate(
        source_hash,
        limit=limit,
        window_ms=ENROLLMENT_RATE_WINDOW_MS,
    ):
        raise _EnrollmentHTTPError(
            429,
            "rate_limited",
            retry_after_seconds=max(1, ENROLLMENT_RATE_WINDOW_MS // 1000),
        )


def _verify_enrollment_request(
    state: RelayState, request: web.Request, frame: dict[str, Any], key_field: str
) -> None:
    issued_at = frame["issued_at"]
    if abs(int(time.time()) - issued_at) > ENROLLMENT_TIMESTAMP_SKEW_SECONDS:
        raise _EnrollmentHTTPError(401, "invalid_contact")
    if not verify_enrollment_request_signature(
        frame[key_field], state.config.origin, request.method, request.path, frame
    ):
        raise _EnrollmentHTTPError(401, "invalid_contact")


async def _consume_enrollment_nonce(
    state: RelayState, frame: dict[str, Any], key_field: str
) -> None:
    key = frame[key_field]
    nonce = frame["nonce"]
    principal_hash = hashlib.sha256(key.encode("ascii")).hexdigest()
    nonce_hash = hashlib.sha256(f"{key}\0{nonce}".encode("ascii")).hexdigest()
    if not await state.backend.consume_enrollment_nonce(
        principal_hash,
        nonce_hash,
        ttl_ms=ENROLLMENT_NONCE_TTL_MS,
        capacity=ENROLLMENT_MAX_NONCES_PER_PRINCIPAL,
    ):
        raise _EnrollmentHTTPError(409, "replayed")


async def _verify_enrollment_issuer(
    state: RelayState, request: web.Request, frame: dict[str, Any]
) -> None:
    _verify_enrollment_request(state, request, frame, "issuer_key")
    try:
        room_hash = room_digest(frame["room_capability"])
    except ValueError as exc:
        raise _EnrollmentHTTPError(401, "invalid_contact") from exc
    records, _ = await state.backend.list_room(room_hash)
    if not any(
        record.key == frame["issuer_key"] and record.session == frame["session"]
        for record in records
    ):
        raise _EnrollmentHTTPError(403, "unauthorized")
    await _consume_enrollment_nonce(state, frame, "issuer_key")


def _content_digest(fields: dict[str, Any]) -> str:
    canonical = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _phase_fields(
    offer_id: str, phase: str, round_id: str, destination_key: str, envelope: str
) -> dict[str, str]:
    return {
        "offer_id": offer_id,
        "phase": phase,
        "round_id": round_id,
        "destination_key": destination_key,
        "envelope": envelope,
    }


@_enrollment_endpoint
async def enrollment_offer_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "create_offer")
    await _verify_enrollment_issuer(state, request, frame)
    now = int(time.time())
    expires_at = frame["expires_at"]
    ttl_seconds = expires_at - now
    if not ENROLLMENT_MIN_TTL_SECONDS <= ttl_seconds <= ENROLLMENT_MAX_TTL_SECONDS:
        raise _EnrollmentHTTPError(400, "invalid_request")
    lookup_hash = frame.get("lookup_hash")
    digest_fields = {
        "offer_id": frame["offer_id"],
        "issuer_key": frame["issuer_key"],
        "expires_at": expires_at,
        "code_verifier_hash": frame["code_verifier_hash"],
    }
    fields = {
        "issuer_key": frame["issuer_key"],
        "expires_at": str(expires_at),
        "code_verifier_hash": frame["code_verifier_hash"],
    }
    if lookup_hash is not None:
        digest_fields["lookup_hash"] = lookup_hash
        fields["lookup_hash"] = lookup_hash
    fields["create_digest"] = _content_digest(digest_fields)
    result = await state.backend.create_enrollment_offer(
        frame["offer_id"],
        fields,
        ttl_ms=ttl_seconds * 1000,
        capacity=MAX_ACTIVE_ENROLLMENT_OFFERS,
    )
    if result == "capacity":
        raise _EnrollmentHTTPError(429, "capacity")
    if result == "conflict":
        raise _EnrollmentHTTPError(409, "conflict")
    status = 200 if result == "duplicate" else 201
    return web.json_response(
        {"status": "offered", "offer_id": frame["offer_id"], "expires_at": expires_at},
        status=status,
    )


@_enrollment_endpoint
async def enrollment_lookup_handler(request: web.Request) -> web.Response:
    """Resolve a short code's lookup tag to its offer id.

    The same generic error covers a wrong guess, an expired offer, an
    already-used offer, and a burned one -- a caller cannot tell them apart.
    """
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "lookup_offer")
    _verify_enrollment_request(state, request, frame, "destination_key")
    await _consume_enrollment_nonce(state, frame, "destination_key")
    lookup_hash = enrollment_lookup_hash(frame["lookup"])
    offer_id = await state.backend.find_enrollment_offer_by_lookup(lookup_hash)
    if offer_id is None:
        raise _EnrollmentHTTPError(404, "unavailable")
    return web.json_response({"offer_id": offer_id})


@_enrollment_endpoint
async def enrollment_request_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "submit_request")
    _verify_enrollment_request(state, request, frame, "destination_key")
    await _consume_enrollment_nonce(state, frame, "destination_key")
    candidate_hash = enrollment_verifier_hash(frame["offer_id"], frame["code_verifier"])
    device_name = frame.get("device_name", "")
    digest = _content_digest(
        {
            "offer_id": frame["offer_id"],
            "round_id": frame["round_id"],
            "destination_key": frame["destination_key"],
            "candidate_hash": candidate_hash,
            "envelope": frame["envelope"],
            "device_name": device_name,
        }
    )
    result = await state.backend.submit_enrollment_request(
        frame["offer_id"],
        round_id=frame["round_id"],
        destination_key=frame["destination_key"],
        candidate_hash=candidate_hash,
        envelope=frame["envelope"],
        content_digest=digest,
        device_name=device_name,
    )
    if result == "invalid_code":
        raise _EnrollmentHTTPError(401, "invalid_contact")
    if result == "rate_limited":
        raise _EnrollmentHTTPError(429, "rate_limited")
    if result == "bound":
        raise _EnrollmentHTTPError(409, "bound")
    if result == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    status_code = 200 if result == "duplicate" else 202
    return web.json_response(
        {"status": "pending", "receipt": frame["round_id"]}, status=status_code
    )


@_enrollment_endpoint
async def enrollment_issuer_poll_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "issuer_poll")
    await _verify_enrollment_issuer(state, request, frame)
    result = await state.backend.claim_enrollment_round(
        frame["offer_id"],
        issuer_key=frame["issuer_key"],
        claim_id=frame["claim_id"],
    )
    if result["status"] == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    if result["status"] == "claimed" and "envelope" not in result:
        raise _EnrollmentHTTPError(409, "claimed")
    return web.json_response(result)


@_enrollment_endpoint
async def enrollment_challenge_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "publish_challenge")
    await _verify_enrollment_issuer(state, request, frame)
    digest = _content_digest(
        _phase_fields(
            frame["offer_id"],
            "challenge",
            frame["round_id"],
            frame["destination_key"],
            frame["envelope"],
        )
    )
    result = await state.backend.publish_enrollment_challenge(
        frame["offer_id"],
        issuer_key=frame["issuer_key"],
        destination_key=frame["destination_key"],
        round_id=frame["round_id"],
        envelope=frame["envelope"],
        content_digest=digest,
    )
    if result == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    if result == "not_ready":
        raise _EnrollmentHTTPError(409, "not_ready")
    if result == "conflict":
        raise _EnrollmentHTTPError(409, "conflict")
    status = 200 if result == "duplicate" else 202
    return web.json_response(
        {"status": "stored", "receipt": frame["round_id"]}, status=status
    )


@_enrollment_endpoint
async def enrollment_proof_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "submit_proof")
    _verify_enrollment_request(state, request, frame, "destination_key")
    await _consume_enrollment_nonce(state, frame, "destination_key")
    digest = _content_digest(
        _phase_fields(
            frame["offer_id"],
            "proof",
            frame["round_id"],
            frame["destination_key"],
            frame["envelope"],
        )
    )
    result = await state.backend.submit_enrollment_proof(
        frame["offer_id"],
        round_id=frame["round_id"],
        destination_key=frame["destination_key"],
        envelope=frame["envelope"],
        content_digest=digest,
    )
    if result == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    if result == "not_ready":
        raise _EnrollmentHTTPError(409, "not_ready")
    if result == "conflict":
        raise _EnrollmentHTTPError(409, "conflict")
    status = 200 if result == "duplicate" else 202
    return web.json_response(
        {"status": "stored", "receipt": frame["round_id"]}, status=status
    )


@_enrollment_endpoint
async def enrollment_decision_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "publish_decision")
    await _verify_enrollment_issuer(state, request, frame)
    digest = _content_digest(
        _phase_fields(
            frame["offer_id"],
            "decision",
            frame["round_id"],
            frame["destination_key"],
            frame["envelope"],
        )
    )
    result = await state.backend.publish_enrollment_decision(
        frame["offer_id"],
        issuer_key=frame["issuer_key"],
        destination_key=frame["destination_key"],
        round_id=frame["round_id"],
        envelope=frame["envelope"],
        content_digest=digest,
    )
    if result == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    if result == "not_ready":
        raise _EnrollmentHTTPError(409, "not_ready")
    if result == "conflict":
        raise _EnrollmentHTTPError(409, "conflict")
    status = 200 if result == "duplicate" else 202
    return web.json_response(
        {"status": "stored", "receipt": frame["round_id"]}, status=status
    )


@_enrollment_endpoint
async def enrollment_reply_poll_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "reply_poll")
    _verify_enrollment_request(state, request, frame, "destination_key")
    await _consume_enrollment_nonce(state, frame, "destination_key")
    result = await state.backend.poll_enrollment_reply(
        frame["offer_id"], destination_key=frame["destination_key"]
    )
    if result["status"] == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    return web.json_response(result)


@_enrollment_endpoint
async def enrollment_install_ack_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "submit_install_ack")
    _verify_enrollment_request(state, request, frame, "destination_key")
    await _consume_enrollment_nonce(state, frame, "destination_key")
    digest = _content_digest(
        {
            "round_id": frame["round_id"],
            "destination_key": frame["destination_key"],
            "envelope": frame["envelope"],
        }
    )
    result = await state.backend.submit_enrollment_install_ack(
        frame["offer_id"],
        round_id=frame["round_id"],
        destination_key=frame["destination_key"],
        frame=frame,
        content_digest=digest,
    )
    if result == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    if result == "conflict":
        raise _EnrollmentHTTPError(409, "conflict")
    status = 200 if result == "duplicate" else 202
    return web.json_response(
        {"status": "stored", "receipt": frame["round_id"]}, status=status
    )


@_enrollment_endpoint
async def enrollment_install_ack_poll_handler(
    request: web.Request,
) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_enrollment_frame(request, "poll_install_ack")
    await _verify_enrollment_issuer(state, request, frame)
    result = await state.backend.poll_enrollment_install_ack(
        frame["offer_id"], issuer_key=frame["issuer_key"]
    )
    if result["status"] == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    if result["status"] == "ready":
        ack = result.get("frame")
        try:
            _validate_enrollment_schema("submit_install_ack", ack)
        except (ValueError, TypeError, KeyError) as exc:
            raise RelayBackendError("enrollment install receipt is malformed") from exc
        if ack.get("offer_id") != frame["offer_id"]:
            raise RelayBackendError("enrollment install receipt belongs to another offer")
        return web.json_response({"status": "ready", "frame": ack})
    if result["status"] != "pending":
        raise RelayBackendError("enrollment install receipt state is invalid")
    return web.json_response({"status": "pending"})


@_enrollment_endpoint
async def contact_request_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_contact_frame(request, "submit")
    _verify_contact_frame(state, request, frame, "sender_key")
    now = int(time.time())
    expires_at = frame["expires_at"]
    if (
        expires_at <= now
        or expires_at > now + CONTACT_REQUEST_TTL_MS // 1000
        or expires_at <= frame["issued_at"]
    ):
        raise _EnrollmentHTTPError(400, "invalid_request")
    await _consume_enrollment_nonce(state, frame, "sender_key")
    digest = _content_digest(frame)
    recipient_hash = _contact_recipient_hash(frame["recipient_key"])
    result = await state.backend.store_contact_request(
        recipient_hash,
        frame["request_id"],
        {
            "request_id": frame["request_id"],
            "frame": frame,
            "content_digest": digest,
            "expires_at": str(expires_at),
        },
        ttl_ms=(expires_at - now) * 1000,
    )
    if result in {"capacity", "recipient_capacity"}:
        raise _EnrollmentHTTPError(429, "capacity")
    if result == "conflict":
        raise _EnrollmentHTTPError(409, "conflict")
    status = 200 if result == "duplicate" else 202
    return web.json_response(
        {"status": "queued", "receipt": frame["request_id"]}, status=status
    )


@_enrollment_endpoint
async def contact_inbox_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_contact_frame(request, "inbox")
    _verify_contact_frame(state, request, frame, "recipient_key")
    await _consume_enrollment_nonce(state, frame, "recipient_key")
    recipient_hash = _contact_recipient_hash(frame["recipient_key"])
    rows = await state.backend.list_contact_requests(
        recipient_hash, limit=MAX_CONTACT_REQUESTS_PER_RECIPIENT
    )
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("frame"), dict)
        or row["frame"].get("recipient_key") != frame["recipient_key"]
        or row["frame"].get("recipient_identity") != frame["recipient_identity"]
        for row in rows
    ):
        raise RelayBackendError("contact request inbox returned malformed data")
    return web.json_response(
        {"status": "ok", "requests": [row["frame"] for row in rows]}
    )


@_enrollment_endpoint
async def contact_decision_handler(request: web.Request) -> web.Response:
    state: RelayState = request.app["relay_state"]
    frame = await _read_contact_frame(request, "decision")
    _verify_contact_frame(state, request, frame, "recipient_key")
    await _consume_enrollment_nonce(state, frame, "recipient_key")
    recipient_hash = _contact_recipient_hash(frame["recipient_key"])
    result = await state.backend.decide_contact_request(
        recipient_hash,
        frame["request_id"],
        "accepted" if frame["decision"] == "accept" else "rejected",
    )
    if result == "unavailable":
        raise _EnrollmentHTTPError(404, "unavailable")
    if result == "conflict":
        raise _EnrollmentHTTPError(409, "conflict")
    status = "accepted" if frame["decision"] == "accept" else "rejected"
    return web.json_response(
        {"status": status, "receipt": frame["request_id"]}
    )


def _parse_trusted_proxy(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "trusted proxy must be a literal IP address"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kollab's account-free, shared-backend ciphertext rendezvous worker."
    )
    parser.add_argument(
        "--bind", default="127.0.0.1", help="literal listener IP (default: loopback)"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--origin",
        required=True,
        help="canonical external HTTPS origin, without a path",
    )
    parser.add_argument(
        "--node-id",
        help="unique 32-hex worker identity; supervisors should keep it stable across restarts",
    )
    parser.add_argument(
        "--backend-url-env",
        default="KOLLAB_RELAY_BACKEND_URL",
        metavar="ENV_NAME",
        help="environment variable containing the Redis/Valkey URL (never pass secrets in argv)",
    )
    parser.add_argument(
        "--backend-cluster",
        action="store_true",
        help="use Redis/Valkey Cluster with sharded Pub/Sub (requires server support for SSUBSCRIBE)",
    )
    parser.add_argument(
        "--dev-in-memory",
        action="store_true",
        help="explicit single-process development backend; no cross-worker routing",
    )
    parser.add_argument(
        "--max-connections-per-node",
        type=int,
        default=MAX_CONNECTIONS_PER_NODE,
    )
    parser.add_argument(
        "--max-connections-per-room",
        type=int,
        default=MAX_CONNECTIONS_PER_ROOM,
        help="maximum 256 so a complete roster fits in the wire-frame limit",
    )
    parser.add_argument(
        "--max-connections-per-source",
        type=int,
        default=MAX_CONNECTIONS_PER_SOURCE,
    )
    parser.add_argument(
        "--trusted-proxy",
        action="append",
        type=_parse_trusted_proxy,
        default=[],
        metavar="IP",
        help="exact reverse-proxy source IP allowed to supply X-Real-IP (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        limits = RelayLimits(
            max_connections_per_node=args.max_connections_per_node,
            max_connections_per_room=args.max_connections_per_room,
            max_connections_per_source=args.max_connections_per_source,
        )
        backend_url = None
        if not args.dev_in_memory:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.backend_url_env):
                raise RelayConfigError(
                    "backend-url-env must name a valid environment variable"
                )
            backend_url = os.environ.get(args.backend_url_env)
            if not backend_url:
                raise RelayConfigError(
                    f"set {args.backend_url_env} or explicitly select --dev-in-memory"
                )
        config = RelayConfig(
            origin=args.origin,
            node_id=args.node_id or secrets.token_hex(16),
            backend_url=backend_url,
            backend_cluster=args.backend_cluster,
            dev_in_memory=args.dev_in_memory,
            bind=args.bind,
            port=args.port,
            trusted_proxies=frozenset(args.trusted_proxy),
            limits=limits,
        )
    except (RelayConfigError, ValueError) as exc:
        raise SystemExit(f"relay configuration error: {exc}") from exc
    app = create_app(config)
    # Access logs are disabled so a rejected URL cannot accidentally log a
    # caller-supplied query string. Frames and registration material are never
    # emitted to logs.
    web.run_app(
        app,
        host=config.bind,
        port=config.port,
        access_log=None,
        print=None,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke check
    raise SystemExit(main())
