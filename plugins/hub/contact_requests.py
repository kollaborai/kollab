"""Encrypted, bounded contact requests that never enter the model pipeline."""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import ssl
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import dns.asyncresolver
from nacl.exceptions import CryptoError
from nacl.public import SealedBox
from nacl.signing import SigningKey, VerifyKey

from .device_names import NAME_RE, contact_route_hex, key_label
from .dns.discovery import _PublicResolver
from .relay_state import KEY, RelayError

CONTACT_REQUESTS_PATH = "/relay/v1/contact/requests"
CONTACT_INBOX_PATH = "/relay/v1/contact/inbox"
CONTACT_DECISIONS_PATH = "/relay/v1/contact/decisions"
CONTACT_LOOKUP_PATH = "/relay/v1/contact/lookup"
CONTACT_LINKS_PATH = "/relay/v1/contact/links"
CONTACT_SIGNATURE_DOMAIN = b"kollab-relay-contact-http/1\x00"
CONTACT_MAX_TTL_SECONDS = 24 * 60 * 60
CONTACT_MAX_INTRODUCTION_BYTES = 2048
CONTACT_MAX_ENVELOPE_BYTES = 6 * 1024
CONTACT_MAX_REQUESTS = 32
CONTACT_MAX_LINK_PEERS = 64
CONTACT_MAX_HTTP_FRAME_BYTES = 512 * 1024
CONTACT_TIMESTAMP_SKEW_SECONDS = 120
_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX_16 = re.compile(r"[0-9a-f]{16}\Z")
_B64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_SAFE_ERRORS = {
    "invalid_request",
    "invalid_contact",
    "unauthorized",
    "unavailable",
    "conflict",
    "capacity",
    "rate_limited",
    "replayed",
    "backend_unavailable",
    "transport",
    "invalid_response",
    "discovery",
    "unknown_route",
    "ambiguous_route",
}


class ContactProtocolError(RelayError):
    """A safe fixed diagnostic for contact-request transport failures."""

    def __init__(self, code: str):
        self.code = code if code in _SAFE_ERRORS else "transport"
        super().__init__(self.code)


class PrivateMessage:
    """Mutable local buffer for an introduction; repr and str never expose it."""

    __slots__ = ("_value", "_cleared")

    def __init__(self, value: str | bytes | bytearray):
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if not raw:
            raise ValueError("private message must not be empty")
        self._value = bytearray(raw)
        self._cleared = False

    def reveal(self) -> str:
        if self._cleared:
            raise RuntimeError("private message has been cleared")
        return self._value.decode("utf-8")

    def clear(self) -> None:
        for index in range(len(self._value)):
            self._value[index] = 0
        self._value.clear()
        self._cleared = True

    def __repr__(self) -> str:
        return "PrivateMessage(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class PendingContactRequest:
    """Verified sender metadata and decrypted introduction for local review.

    The key, the receipt id and the introduction stay out of repr: only the
    device name and the expiry are printable.
    """

    receipt_id: str = field(repr=False)
    sender_key: str = field(repr=False)
    expires_at: int
    introduction: PrivateMessage = field(repr=False)
    # The sender's device name, sealed inside the introduction envelope so
    # the relay never sees it. Falls back to the first 8 hex of the sender's
    # contact route when missing or invalid -- never the raw key.
    device_name: str = ""


@dataclass(frozen=True, slots=True)
class ContactDecision:
    receipt_id: str = field(repr=False)
    status: str

    def __post_init__(self) -> None:
        if not _HEX_32.fullmatch(self.receipt_id):
            raise ValueError("invalid contact receipt")
        if self.status not in {"accepted", "rejected"}:
            raise ValueError("invalid contact decision")


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _strict_json(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > CONTACT_MAX_HTTP_FRAME_BYTES:
        raise ContactProtocolError("invalid_response")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON property")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContactProtocolError("invalid_response") from exc
    if not isinstance(value, dict):
        raise ContactProtocolError("invalid_response")
    return value


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not _B64URL.fullmatch(value):
        raise ContactProtocolError("invalid_response")
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise ContactProtocolError("invalid_response") from exc
    if _b64url_encode(raw) != value:
        raise ContactProtocolError("invalid_response")
    return raw


def validate_contact_key(value: str) -> str:
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise ContactProtocolError("invalid_request")
    try:
        VerifyKey(bytes.fromhex(value)).to_curve25519_public_key()
    except (ValueError, CryptoError) as exc:
        raise ContactProtocolError("invalid_request") from exc
    return value


def validate_introduction(value: str) -> str:
    if not isinstance(value, str):
        raise ContactProtocolError("invalid_request")
    normalized = value.strip()
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeError as exc:
        raise ContactProtocolError("invalid_request") from exc
    if not encoded or len(encoded) > CONTACT_MAX_INTRODUCTION_BYTES:
        raise ContactProtocolError("invalid_request")
    if any(
        (ord(char) < 32 and char not in "\n\t") or ord(char) == 127
        for char in normalized
    ):
        raise ContactProtocolError("invalid_request")
    return normalized


def contact_signature_message(
    origin: str, method: str, path: str, body: dict[str, Any]
) -> bytes:
    if not isinstance(body, dict):
        raise ContactProtocolError("invalid_request")
    signed_body = {name: value for name, value in body.items() if name != "signature"}
    try:
        return (
            CONTACT_SIGNATURE_DOMAIN
            + method.upper().encode("ascii")
            + b"\n"
            + origin.encode("ascii")
            + b"\n"
            + path.encode("ascii")
            + b"\n"
            + _canonical_json(signed_body)
        )
    except (UnicodeError, TypeError, ValueError) as exc:
        raise ContactProtocolError("invalid_request") from exc


def _signed_frame(
    signing_key: SigningKey,
    origin: str,
    path: str,
    fields: dict[str, Any],
    *,
    now: int | None = None,
) -> dict[str, Any]:
    frame = {
        "v": 1,
        **fields,
        "issued_at": int(time.time()) if now is None else now,
        "nonce": secrets.token_hex(16),
    }
    signature = signing_key.sign(
        contact_signature_message(origin, "POST", path, frame)
    ).signature.hex()
    return {**frame, "signature": signature}


def _verify_frame_signature(
    frame: dict[str, Any], public_key: str, origin: str, path: str
) -> bool:
    signature = frame.get("signature")
    if (
        not isinstance(public_key, str)
        or not KEY.fullmatch(public_key)
        or not isinstance(signature, str)
        or not re.fullmatch(r"[0-9a-f]{128}", signature)
    ):
        return False
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            contact_signature_message(origin, "POST", path, frame),
            bytes.fromhex(signature),
        )
        return True
    except (CryptoError, ValueError, TypeError, UnicodeError):
        return False


def _validate_request_frame(
    frame: Any, *, recipient_key: str, origin: str, now: int
) -> dict[str, Any]:
    expected = {
        "v",
        "recipient_identity",
        "recipient_key",
        "sender_key",
        "request_id",
        "issued_at",
        "expires_at",
        "nonce",
        "envelope",
        "signature",
    }
    if not isinstance(frame, dict) or set(frame) != expected:
        raise ContactProtocolError("invalid_response")
    if (
        frame["v"] != 1
        or isinstance(frame["v"], bool)
        or frame["recipient_key"] != recipient_key
        or frame["recipient_identity"] != "ed25519:" + recipient_key
        or not isinstance(frame["sender_key"], str)
        or not KEY.fullmatch(frame["sender_key"])
        or not isinstance(frame["request_id"], str)
        or not _HEX_32.fullmatch(frame["request_id"])
        or not isinstance(frame["nonce"], str)
        or not _HEX_32.fullmatch(frame["nonce"])
        or type(frame["issued_at"]) is not int
        or type(frame["expires_at"]) is not int
        or frame["issued_at"] > now + CONTACT_TIMESTAMP_SKEW_SECONDS
        or frame["expires_at"] <= now
        or frame["expires_at"] - frame["issued_at"] > CONTACT_MAX_TTL_SECONDS
        or not _verify_frame_signature(
            frame, frame["sender_key"], origin, CONTACT_REQUESTS_PATH
        )
    ):
        raise ContactProtocolError("invalid_response")
    envelope = _b64url_decode(frame["envelope"])
    if not envelope or len(envelope) > CONTACT_MAX_ENVELOPE_BYTES:
        raise ContactProtocolError("invalid_response")
    return frame


def _decrypt_envelope(
    frame: dict[str, Any], signing_key: SigningKey
) -> tuple[str, str]:
    """Decrypt the introduction and the sealed device name (may be empty)."""
    try:
        private_key = signing_key.to_curve25519_private_key()
        plaintext = SealedBox(private_key).decrypt(_b64url_decode(frame["envelope"]))
        payload = _strict_json(plaintext)
        if set(payload) != {"introduction", "device_name"}:
            raise ContactProtocolError("invalid_response")
        introduction = validate_introduction(payload["introduction"])
        device_name = payload["device_name"]
        if not isinstance(device_name, str):
            raise ContactProtocolError("invalid_response")
        return introduction, device_name
    except (CryptoError, ValueError, TypeError) as exc:
        if isinstance(exc, ContactProtocolError):
            raise
        raise ContactProtocolError("invalid_response") from exc


class ContactRequestManager:
    """Use a verified relay route and a key-addressed, ciphertext-only inbox."""

    def __init__(self, commands):
        self.commands = commands

    async def _route(self, domain: str):
        try:
            result, ca, cidrs, is_card = await self.commands._discover(domain)
            if is_card or not self.commands._relay_url(result):
                raise ContactProtocolError("unavailable")
            return result.origin, ca, cidrs
        except ContactProtocolError:
            raise
        except Exception as exc:
            raise ContactProtocolError("discovery") from exc

    async def _post(
        self,
        origin: str,
        path: str,
        frame: dict[str, Any],
        *,
        ca: str,
        cidrs: tuple[str, ...],
    ) -> dict[str, Any]:
        context = ssl.create_default_context(cafile=ca or None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connector = aiohttp.TCPConnector(
            resolver=_PublicResolver(dns.asyncresolver.Resolver(), cidrs),
            use_dns_cache=False,
            ssl=context,
            limit=1,
        )
        trace = aiohttp.TraceConfig()

        async def reject_redirect(*_args):
            raise ContactProtocolError("transport")

        trace.on_request_redirect.append(reject_redirect)
        try:
            timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_connect=5)
            async with aiohttp.ClientSession(
                connector=connector,
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=timeout,
                trace_configs=[trace],
            ) as session:
                async with session.post(
                    origin + path,
                    json=frame,
                    allow_redirects=False,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    raw = await response.content.read(CONTACT_MAX_HTTP_FRAME_BYTES + 1)
                    if len(raw) > CONTACT_MAX_HTTP_FRAME_BYTES:
                        raise ContactProtocolError("invalid_response")
                    payload = _strict_json(raw)
                    if response.status not in {200, 201, 202}:
                        code = payload.get("error")
                        raise ContactProtocolError(
                            code if isinstance(code, str) else "transport"
                        )
                    return payload
        except ContactProtocolError:
            raise
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as exc:
            raise ContactProtocolError("transport") from exc

    async def submit(
        self,
        domain: str,
        recipient_key: str,
        introduction: str,
        device_name: str = "",
    ) -> str:
        recipient_key = validate_contact_key(recipient_key.lower())
        introduction = validate_introduction(introduction)
        # A missing or malformed name never blocks the knock; the receiving
        # side falls back to a route-derived placeholder.
        if not isinstance(device_name, str) or not NAME_RE.fullmatch(device_name):
            device_name = ""
        origin, ca, cidrs = await self._route(domain)
        sender_key = self.commands.client._store.key
        sender_public_key = sender_key.verify_key.encode().hex()
        recipient_curve_key = VerifyKey(
            bytes.fromhex(recipient_key)
        ).to_curve25519_public_key()
        encrypted = SealedBox(recipient_curve_key).encrypt(
            _canonical_json({"introduction": introduction, "device_name": device_name})
        )
        if len(encrypted) > CONTACT_MAX_ENVELOPE_BYTES:
            raise ContactProtocolError("invalid_request")
        now = int(time.time())
        request_id = secrets.token_hex(16)
        frame = _signed_frame(
            sender_key,
            origin,
            CONTACT_REQUESTS_PATH,
            {
                "recipient_identity": "ed25519:" + recipient_key,
                "recipient_key": recipient_key,
                "sender_key": sender_public_key,
                "request_id": request_id,
                "expires_at": now + CONTACT_MAX_TTL_SECONDS,
                "envelope": _b64url_encode(encrypted),
            },
            now=now,
        )
        result = await self._post(
            origin, CONTACT_REQUESTS_PATH, frame, ca=ca, cidrs=cidrs
        )
        if (
            set(result) != {"status", "receipt"}
            or result.get("status") != "queued"
            or result.get("receipt") != request_id
        ):
            raise ContactProtocolError("invalid_response")
        return request_id

    async def pending(self, domain: str) -> list[PendingContactRequest]:
        origin, ca, cidrs = await self._route(domain)
        signing_key = self.commands.client._store.key
        recipient_key = self.commands.client.public_key
        frame = _signed_frame(
            signing_key,
            origin,
            CONTACT_INBOX_PATH,
            {
                "recipient_identity": "ed25519:" + recipient_key,
                "recipient_key": recipient_key,
            },
        )
        result = await self._post(origin, CONTACT_INBOX_PATH, frame, ca=ca, cidrs=cidrs)
        if set(result) != {"status", "requests"} or result.get("status") != "ok":
            raise ContactProtocolError("invalid_response")
        requests = result["requests"]
        if not isinstance(requests, list) or len(requests) > CONTACT_MAX_REQUESTS:
            raise ContactProtocolError("invalid_response")
        now = int(time.time())
        pending: list[PendingContactRequest] = []
        for raw in requests:
            # The sealed envelope is sender-controlled and the relay cannot
            # read it, so one malformed knock must not hide the good ones.
            try:
                frame = _validate_request_frame(
                    raw,
                    recipient_key=recipient_key,
                    origin=origin,
                    now=now,
                )
                introduction, device_name = _decrypt_envelope(frame, signing_key)
                private = PrivateMessage(introduction)
            except ValueError:  # ContactProtocolError is one
                continue
            if not device_name or not NAME_RE.fullmatch(device_name):
                device_name = key_label(frame["sender_key"])
            pending.append(
                PendingContactRequest(
                    receipt_id=frame["request_id"],
                    sender_key=frame["sender_key"],
                    expires_at=frame["expires_at"],
                    introduction=private,
                    device_name=device_name,
                )
            )
        return pending

    async def decide(
        self, domain: str, request_id: str, decision: str
    ) -> ContactDecision:
        if not isinstance(request_id, str) or not _HEX_32.fullmatch(request_id):
            raise ContactProtocolError("invalid_request")
        if decision not in {"accept", "reject"}:
            raise ContactProtocolError("invalid_request")
        origin, ca, cidrs = await self._route(domain)
        signing_key = self.commands.client._store.key
        recipient_key = self.commands.client.public_key
        frame = _signed_frame(
            signing_key,
            origin,
            CONTACT_DECISIONS_PATH,
            {
                "recipient_identity": "ed25519:" + recipient_key,
                "recipient_key": recipient_key,
                "request_id": request_id,
                "decision": decision,
            },
        )
        result = await self._post(
            origin, CONTACT_DECISIONS_PATH, frame, ca=ca, cidrs=cidrs
        )
        status = "accepted" if decision == "accept" else "rejected"
        if (
            set(result) != {"status", "receipt"}
            or result.get("status") != status
            or result.get("receipt") != request_id
        ):
            raise ContactProtocolError("invalid_response")
        return ContactDecision(request_id, status)

    async def sync_links(self, domain: str, peers: list[str]) -> None:
        """Declare which other devices this key consents to link with.

        The signed list replaces the last one. The directory routes between
        two devices in different rooms only while each one's list names the
        other, so this alone never opens a path.
        """
        keys = sorted({validate_contact_key(peer.lower()) for peer in peers})
        if len(keys) > CONTACT_MAX_LINK_PEERS:
            raise ContactProtocolError("capacity")
        origin, ca, cidrs = await self._route(domain)
        frame = _signed_frame(
            self.commands.client._store.key,
            origin,
            CONTACT_LINKS_PATH,
            {"key": self.commands.client.public_key, "peers": keys},
        )
        result = await self._post(origin, CONTACT_LINKS_PATH, frame, ca=ca, cidrs=cidrs)
        if result != {"status": "stored"}:
            raise ContactProtocolError("invalid_response")

    async def resolve_route(self, domain: str, route_hex: str) -> str:
        """Resolve a contact route to its key.

        Never trusts the relay's answer: recomputes the route from the
        returned key and refuses on any mismatch, so a compromised or
        misbehaving relay cannot redirect a knock to the wrong recipient.
        """
        if not isinstance(route_hex, str) or not _HEX_16.fullmatch(route_hex):
            raise ContactProtocolError("invalid_request")
        origin, ca, cidrs = await self._route(domain)
        result = await self._post(
            origin,
            CONTACT_LOOKUP_PATH,
            {"v": 1, "route": route_hex},
            ca=ca,
            cidrs=cidrs,
        )
        if set(result) != {"key"} or not isinstance(result["key"], str):
            raise ContactProtocolError("invalid_response")
        key = validate_contact_key(result["key"].lower())
        if contact_route_hex(key) != route_hex:
            raise ContactProtocolError("invalid_response")
        return key
