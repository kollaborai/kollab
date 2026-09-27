"""Kollab's narrow signing profile for standard A2A Agent Cards.

This module signs and verifies the exact JSON dictionary served at
``/.well-known/agent-card.json``. It does not publish a Card for an
identity-only discovery service, establish group membership, or admit a peer
to the Hub.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import rfc8785
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

AGENT_CARD_PATH = "/.well-known/agent-card.json"
MAX_AGENT_CARD_BYTES = 64 * 1024
MAX_JSON_DEPTH = 32
_KID_RE = re.compile(r"ed25519:[0-9a-f]{64}\Z")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_HEX_RE = re.compile(r"[0-9a-fA-F]+\Z")


class AgentCardSigningError(ValueError):
    """Invalid Agent Card, signature, locator, or signing key."""


@dataclass(frozen=True)
class AgentCardVerification:
    """Evidence returned after an Agent Card verifies against an origin pin."""

    origin: str
    kid: str
    public_key_hex: str
    payload_sha256: str


def normalize_https_origin(value: str) -> str:
    """Return a canonical HTTPS origin; reject credentials, paths and IPs."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise AgentCardSigningError("invalid_origin: expected an HTTPS origin")
    if any(ch.isspace() or ord(ch) < 32 for ch in value) or any(
        ch in value for ch in "\\%?#"
    ):
        raise AgentCardSigningError(
            "invalid_origin: escaped URLs, whitespace, queries and fragments are unsupported"
        )
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("HTTPS origin without credentials required")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("origin must not include a path, query or fragment")
        raw_host = parsed.hostname
        if not raw_host:
            raise ValueError("origin has no host")
        host = raw_host.rstrip(".").encode("idna").decode("ascii").lower()
        if (
            not host
            or len(host) > 253
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            )
        ):
            raise ValueError("invalid DNS hostname")
        if host.replace(".", "").isdigit():
            raise ValueError("numeric IP-like hostnames are not supported")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP literals are not supported")
        port = parsed.port or 443
        if not 1 <= port <= 65535 or parsed.port == 0:
            raise ValueError("invalid port")
        return f"https://{host}" + (f":{port}" if port != 443 else "")
    except (ValueError, UnicodeError) as exc:
        raise AgentCardSigningError(f"invalid_origin: {exc}") from exc


def resolve_agent_card_url(origin: str, advertised_url: str | None) -> str | None:
    """Validate the optional locator field for the standard public Agent Card.

    Missing ``endpoints.agent_card`` means the publisher is identity-only. A
    present URL must be the canonical standard path on the pinned origin.
    """
    canonical_origin = normalize_https_origin(origin)
    if advertised_url is None:
        return None
    if not isinstance(advertised_url, str) or len(advertised_url) > 2048:
        raise AgentCardSigningError(
            "invalid_card_url: expected a standard HTTPS Agent Card URL"
        )
    if any(ch.isspace() or ord(ch) < 32 for ch in advertised_url) or any(
        ch in advertised_url for ch in "\\%?#"
    ):
        raise AgentCardSigningError(
            "invalid_card_url: escaped URLs, whitespace, queries and fragments are unsupported"
        )
    try:
        parsed = urlsplit(advertised_url)
        parsed_origin = normalize_https_origin(f"https://{parsed.netloc}")
        if parsed_origin != canonical_origin:
            raise ValueError("Agent Card URL must use the descriptor-pinned origin")
        if parsed.path != AGENT_CARD_PATH or parsed.query or parsed.fragment:
            raise ValueError(f"Agent Card URL must use exactly {AGENT_CARD_PATH}")
        canonical_url = canonical_origin + AGENT_CARD_PATH
        if advertised_url != canonical_url:
            raise ValueError("Agent Card URL must be canonical")
        return canonical_url
    except (ValueError, UnicodeError) as exc:
        raise AgentCardSigningError(f"invalid_card_url: {exc}") from exc


def decode_agent_card(raw: bytes) -> dict[str, Any]:
    """Decode a bounded JSON Agent Card, rejecting duplicate keys and NaN."""
    if not isinstance(raw, bytes):
        raise AgentCardSigningError("invalid_card: expected bytes")
    if len(raw) > MAX_AGENT_CARD_BYTES:
        raise AgentCardSigningError("card_too_large: Agent Card exceeds 64 KiB")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    def check_depth(value: Any, depth: int = 1) -> None:
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"JSON nesting exceeds {MAX_JSON_DEPTH} levels")
        if isinstance(value, dict):
            for child in value.values():
                check_depth(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check_depth(child, depth + 1)

    try:
        card = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant
        )
        if not isinstance(card, dict):
            raise ValueError("Agent Card JSON root must be an object")
        check_depth(card)
        _canonical_payload(card)
        return card
    except (UnicodeError, ValueError, RecursionError, TypeError) as exc:
        raise AgentCardSigningError(f"invalid_card: {exc}") from exc


def kid_for_public_key(public_key: bytes | str | VerifyKey) -> str:
    """Return the profile's deterministic key id for a raw Ed25519 key."""
    return "ed25519:" + _public_key_bytes(public_key).hex()


def sign_agent_card(
    card: Mapping[str, Any],
    private_key: SigningKey | bytes | str,
    *,
    kid: str | None = None,
) -> dict[str, Any]:
    """Return a copy of an unsigned Agent Card with one EdDSA JWS signature.

    ``bytes`` and hexadecimal strings may contain a 32-byte Ed25519 seed or a
    64-byte libsodium secret key. ``kid`` is optional but, when supplied, must
    match the deterministic id derived from the signing key.
    """
    if not isinstance(card, Mapping):
        raise AgentCardSigningError("invalid_card: expected an object")
    if "signatures" in card:
        raise AgentCardSigningError(
            "invalid_card: remove existing signatures before signing"
        )
    key = _signing_key(private_key)
    public_key = key.verify_key.encode()
    expected_kid = kid_for_public_key(public_key)
    if kid is not None and kid != expected_kid:
        raise AgentCardSigningError("kid_mismatch: kid must identify the signing key")

    unsigned_card = dict(card)
    payload = _canonical_payload(unsigned_card)
    protected_json = rfc8785.dumps({"alg": "EdDSA", "kid": expected_kid, "typ": "JOSE"})
    protected = _b64url(protected_json)
    signing_input = protected.encode("ascii") + b"." + _b64url(payload).encode("ascii")
    signature = _b64url(key.sign(signing_input).signature)
    signed = dict(unsigned_card)
    signed["signatures"] = [{"protected": protected, "signature": signature}]
    return signed


def verify_agent_card(
    card: Mapping[str, Any],
    *,
    origin: str,
    pinned_public_key: bytes | str | VerifyKey,
) -> AgentCardVerification:
    """Verify the single-profile signature using an origin-scoped pinned key.

    This profile deliberately rejects ``jku`` and unprotected headers. The key
    comes from Kollab's separately verified origin pin; a Card cannot select a
    replacement key. Key rotation therefore requires explicit human/out-of-
    band repinning before a new Card can verify.
    """
    canonical_origin = normalize_https_origin(origin)
    if not isinstance(card, Mapping):
        raise AgentCardSigningError("invalid_card: expected an object")
    signatures = card.get("signatures")
    if not isinstance(signatures, list) or len(signatures) != 1:
        raise AgentCardSigningError(
            "invalid_signature: expected exactly one profile signature"
        )
    entry = signatures[0]
    if not isinstance(entry, Mapping) or set(entry) != {"protected", "signature"}:
        raise AgentCardSigningError(
            "invalid_signature: unprotected or unknown signature fields are forbidden"
        )
    protected = entry.get("protected")
    signature = entry.get("signature")
    if not isinstance(protected, str) or not isinstance(signature, str):
        raise AgentCardSigningError(
            "invalid_signature: protected and signature must be strings"
        )
    protected_bytes = _decode_b64url(protected, "protected header")
    header = _decode_json_object(protected_bytes, "protected header")
    if set(header) != {"alg", "kid", "typ"}:
        raise AgentCardSigningError(
            "invalid_header: only alg, kid and typ are permitted"
        )
    if header.get("alg") != "EdDSA":
        raise AgentCardSigningError("unsupported_algorithm: only EdDSA is permitted")
    if header.get("typ") != "JOSE":
        raise AgentCardSigningError("invalid_header: typ must be JOSE")

    key_bytes = _public_key_bytes(pinned_public_key)
    expected_kid = "ed25519:" + key_bytes.hex()
    if not isinstance(header.get("kid"), str) or not _KID_RE.fullmatch(header["kid"]):
        raise AgentCardSigningError(
            "invalid_header: kid must use the Kollab Ed25519 key-id format"
        )
    if header["kid"] != expected_kid:
        raise AgentCardSigningError(
            "key_changed: Card signer does not match the origin-pinned Ed25519 key"
        )

    signature_bytes = _decode_b64url(signature, "signature")
    if len(signature_bytes) != 64:
        raise AgentCardSigningError(
            "invalid_signature: Ed25519 signature must be 64 bytes"
        )
    unsigned_card = {key: value for key, value in card.items() if key != "signatures"}
    payload = _canonical_payload(unsigned_card)
    signing_input = protected.encode("ascii") + b"." + _b64url(payload).encode("ascii")
    try:
        VerifyKey(key_bytes).verify(signing_input, signature_bytes)
    except BadSignatureError as exc:
        raise AgentCardSigningError(
            "invalid_signature: Agent Card signature verification failed"
        ) from exc
    return AgentCardVerification(
        origin=canonical_origin,
        kid=expected_kid,
        public_key_hex=key_bytes.hex(),
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _canonical_payload(card: Mapping[str, Any]) -> bytes:
    if not isinstance(card, Mapping):
        raise AgentCardSigningError("invalid_card: expected an object")
    unsigned = {key: value for key, value in card.items() if key != "signatures"}
    try:
        return rfc8785.dumps(unsigned)
    except (ValueError, TypeError, OverflowError) as exc:
        raise AgentCardSigningError(
            f"invalid_card: JCS canonicalization failed: {exc}"
        ) from exc


def _signing_key(value: SigningKey | bytes | str) -> SigningKey:
    if isinstance(value, SigningKey):
        return value
    if isinstance(value, str):
        if not _HEX_RE.fullmatch(value) or len(value) not in (64, 128):
            raise AgentCardSigningError(
                "invalid_private_key: expected 32- or 64-byte hexadecimal key"
            )
        try:
            value = bytes.fromhex(value)
        except ValueError as exc:
            raise AgentCardSigningError(
                "invalid_private_key: malformed hexadecimal key"
            ) from exc
    if not isinstance(value, bytes) or len(value) not in (32, 64):
        raise AgentCardSigningError(
            "invalid_private_key: expected a 32-byte seed or 64-byte secret key"
        )
    key = SigningKey(value[:32])
    if len(value) == 64 and value[32:] != key.verify_key.encode():
        raise AgentCardSigningError(
            "invalid_private_key: 64-byte key has inconsistent public half"
        )
    return key


def _public_key_bytes(value: bytes | str | VerifyKey) -> bytes:
    if isinstance(value, VerifyKey):
        return value.encode()
    if isinstance(value, str):
        if len(value) != 64 or not _HEX_RE.fullmatch(value):
            raise AgentCardSigningError(
                "invalid_public_key: expected 32-byte hexadecimal Ed25519 key"
            )
        value = bytes.fromhex(value)
    if not isinstance(value, bytes) or len(value) != 32:
        raise AgentCardSigningError("invalid_public_key: expected 32-byte Ed25519 key")
    return value


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_b64url(value: str, label: str) -> bytes:
    if not value or not _B64URL_RE.fullmatch(value):
        raise AgentCardSigningError(f"invalid_signature: invalid base64url {label}")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise AgentCardSigningError(
            f"invalid_signature: invalid base64url {label}"
        ) from exc
    if _b64url(decoded) != value:
        raise AgentCardSigningError(
            f"invalid_signature: noncanonical base64url {label}"
        )
    return decoded


def _decode_json_object(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, ValueError) as exc:
        raise AgentCardSigningError(f"invalid_header: malformed {label}") from exc
    if not isinstance(value, dict):
        raise AgentCardSigningError(f"invalid_header: {label} must be an object")
    try:
        if rfc8785.dumps(value) != raw:
            raise ValueError("protected header must use canonical JCS")
    except (ValueError, TypeError, OverflowError) as exc:
        raise AgentCardSigningError(
            f"invalid_header: malformed {label}: {exc}"
        ) from exc
    return value
