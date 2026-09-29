"""Private K1 device-enrollment codes and relay verifiers.

The code and verifier wrappers redact their value from ordinary string and
repr formatting. Call ``for_private_display`` only on the issuing private UI,
and ``for_protocol`` only when constructing the authenticated relay request.
This module never logs or persists either value.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_LOWER = _CROCKFORD_ALPHABET.lower()
_OFFER_ID_LENGTH = 32
_SECRET_LENGTH = 8  # short join code: 8 chars from a 32-symbol alphabet = 40 bits.
_SECRET_BYTES = 5
_LEGACY_SECRET_LENGTH = 20  # K1 codes issued before short codes; parsing only.
_LEGACY_SECRET_BYTES = 13  # 104 random bits; the top 100 bits are used below.
_KDF_DOMAIN = b"kollab-relay-enrollment-code-v1\0"
_VERIFIER_DOMAIN = b"kollab-relay-enrollment-verifier-v1\0"
_ENVELOPE_SALT_DOMAIN = b"kollab-relay-enrollment-envelope-salt-v1\0"
_ENVELOPE_INFO_DOMAIN = b"kollab-relay-enrollment-envelope-key-v1\0"
_LOOKUP_DOMAIN = b"kollab-relay-enrollment-lookup-v1\0"


def _validate_offer_id(offer_id: str) -> bytes:
    if (
        not isinstance(offer_id, str)
        or len(offer_id) != _OFFER_ID_LENGTH
        or any(char not in "0123456789abcdef" for char in offer_id)
    ):
        raise ValueError("invalid enrollment offer ID")
    return bytes.fromhex(offer_id)


def _validate_secret(secret: bytes | bytearray) -> bytearray:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) not in (
        _SECRET_LENGTH,
        _LEGACY_SECRET_LENGTH,
    ):
        raise ValueError("invalid enrollment code secret")
    try:
        text = bytes(secret).decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("invalid enrollment code secret") from None
    if any(char not in _CROCKFORD_ALPHABET for char in text):
        raise ValueError("invalid enrollment code secret")
    return bytearray(text.encode("ascii"))


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class EnrollmentCode:
    """A parsed or newly generated enrollment code with redacted formatting."""

    offer_id: str
    _secret: bytearray = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_offer_id(self.offer_id)
        object.__setattr__(self, "_secret", _validate_secret(self._secret))

    def __repr__(self) -> str:
        return f"EnrollmentCode(offer_id={self.offer_id!r}, secret=<redacted>)"

    def __str__(self) -> str:
        return f"EnrollmentCode(offer_id={self.offer_id}, secret=<redacted>)"

    def for_private_display(self) -> str:
        """Return the canonical code for a private, human-only display surface."""
        secret = self._secret_text()
        if len(secret) == _SECRET_LENGTH:
            return f"{secret[:4]}-{secret[4:]}"
        groups = "-".join(
            secret[offset : offset + 4] for offset in range(0, _LEGACY_SECRET_LENGTH, 4)
        )
        return f"K1-{self.offer_id}-{groups}"

    def for_lookup_tag(self, origin: str) -> str:
        """Return this short code's lookup tag for a signed enrollment request."""
        return derive_enrollment_lookup_tag(self._secret_bytes(), origin)

    def wipe(self) -> None:
        """Best-effort zero the mutable secret buffer held by this object."""
        for index in range(len(self._secret)):
            self._secret[index] = 0
        self._secret.clear()

    def _secret_bytes(self) -> bytes:
        if len(self._secret) not in (_SECRET_LENGTH, _LEGACY_SECRET_LENGTH):
            raise ValueError("enrollment code has been cleared")
        return bytes(self._secret)

    def _secret_text(self) -> str:
        try:
            return self._secret_bytes().decode("ascii")
        except UnicodeDecodeError:
            raise ValueError("enrollment code has been cleared") from None


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class EnrollmentCodeVerifier:
    """A code verifier with redacted formatting and explicit protocol access."""

    offer_id: str
    _value: bytearray = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_offer_id(self.offer_id)
        if not isinstance(self._value, (bytes, bytearray)) or len(self._value) != 32:
            raise ValueError("invalid enrollment code verifier")
        object.__setattr__(self, "_value", bytearray(self._value))

    def __repr__(self) -> str:
        return f"EnrollmentCodeVerifier(offer_id={self.offer_id!r}, value=<redacted>)"

    def __str__(self) -> str:
        return f"EnrollmentCodeVerifier(offer_id={self.offer_id}, value=<redacted>)"

    def for_protocol(self) -> str:
        """Return the unpadded base64url verifier for an authenticated request."""
        return (
            base64.urlsafe_b64encode(self._value_bytes()).rstrip(b"=").decode("ascii")
        )

    def wipe(self) -> None:
        """Best-effort zero the mutable verifier buffer held by this object."""
        for index in range(len(self._value)):
            self._value[index] = 0
        self._value.clear()

    def _value_bytes(self) -> bytes:
        if len(self._value) != 32:
            raise ValueError("enrollment verifier has been cleared")
        return bytes(self._value)


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class EnrollmentEnvelopeKey:
    """A redacted, wipeable key for encrypting one enrollment envelope."""

    offer_id: str
    _value: bytearray = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_offer_id(self.offer_id)
        if not isinstance(self._value, (bytes, bytearray)) or len(self._value) != 32:
            raise ValueError("invalid enrollment envelope key")
        object.__setattr__(self, "_value", bytearray(self._value))

    def __repr__(self) -> str:
        return f"EnrollmentEnvelopeKey(offer_id={self.offer_id!r}, value=<redacted>)"

    def __str__(self) -> str:
        return f"EnrollmentEnvelopeKey(offer_id={self.offer_id}, value=<redacted>)"

    def for_envelope_encryption(self) -> bytes:
        """Return the key bytes for the envelope-encryption operation."""
        return self._value_bytes()

    def wipe(self) -> None:
        """Best-effort zero the mutable key buffer held by this object."""
        for index in range(len(self._value)):
            self._value[index] = 0
        self._value.clear()

    def _value_bytes(self) -> bytes:
        if len(self._value) != 32:
            raise ValueError("enrollment envelope key has been cleared")
        return bytes(self._value)


def generate_enrollment_code(offer_id: str) -> EnrollmentCode:
    """Generate a 40-bit Crockford short code; the offer id is looked up, not embedded."""
    _validate_offer_id(offer_id)
    random_value = secrets.token_bytes(_SECRET_BYTES)
    if len(random_value) != _SECRET_BYTES:
        raise ValueError("could not generate enrollment code")
    secret_value = int.from_bytes(random_value, "big")
    secret = "".join(
        _CROCKFORD_ALPHABET[(secret_value >> (5 * (_SECRET_LENGTH - 1 - index))) & 0x1F]
        for index in range(_SECRET_LENGTH)
    )
    return EnrollmentCode(offer_id, bytearray(secret.encode("ascii")))


_SHORT_CODE_IN_TEXT = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}")


def looks_like_join_code(part: str) -> bool:
    """True for a K1 code or an upper-case ``XXXX-XXXX`` join code typed as one word.

    Device names are lower-case by rule, so an upper-case short code cannot be a
    device name; lower-case short codes are left to the private form.
    """
    if not isinstance(part, str):
        return False
    return part.upper().startswith("K1-") or _SHORT_CODE_IN_TEXT.fullmatch(part) is not None


def is_short_enrollment_code(value: str) -> bool:
    """True when ``value`` looks like an ``XXXX-XXXX`` short code, not a K1 code."""
    try:
        parse_short_enrollment_code(value)
        return True
    except ValueError:
        return False


def parse_short_enrollment_code(value: str) -> bytearray:
    """Parse an 8-character short code: either case, dash optional, into its secret."""
    if not isinstance(value, str):
        raise ValueError("invalid enrollment code")
    stripped = value.strip()
    if len(stripped) == _SECRET_LENGTH + 1 and stripped[4] == "-":
        digits = stripped[:4] + stripped[5:]
    elif len(stripped) == _SECRET_LENGTH:
        digits = stripped
    else:
        raise ValueError("invalid enrollment code")
    normalized: list[str] = []
    for char in digits:
        if not char.isascii():
            raise ValueError("invalid enrollment code")
        if char in _CROCKFORD_ALPHABET:
            normalized.append(char)
        elif char in _CROCKFORD_LOWER:
            normalized.append(char.upper())
        else:
            raise ValueError("invalid enrollment code")
    return bytearray("".join(normalized).encode("ascii"))


def parse_enrollment_code(value: str) -> EnrollmentCode:
    """Parse the exact K1 format, accepting lowercase secret letters only."""
    if not isinstance(value, str) or len(value) != 60:
        raise ValueError("invalid enrollment code")
    parts = value.split("-")
    if (
        len(parts) != 7
        or parts[0] != "K1"
        or len(parts[1]) != _OFFER_ID_LENGTH
        or len(parts[2]) != 4
        or len(parts[3]) != 4
        or len(parts[4]) != 4
        or len(parts[5]) != 4
        or len(parts[6]) != 4
    ):
        raise ValueError("invalid enrollment code")
    try:
        _validate_offer_id(parts[1])
    except ValueError:
        raise ValueError("invalid enrollment code") from None

    normalized: list[str] = []
    for group in parts[2:]:
        for char in group:
            if not char.isascii():
                raise ValueError("invalid enrollment code")
            if char in _CROCKFORD_ALPHABET:
                normalized.append(char)
            elif char in _CROCKFORD_LOWER:
                normalized.append(char.upper())
            else:
                raise ValueError("invalid enrollment code")
    return EnrollmentCode(parts[1], bytearray("".join(normalized).encode("ascii")))


def derive_enrollment_code_verifier(code: EnrollmentCode) -> EnrollmentCodeVerifier:
    """Derive the specified scrypt verifier from a parsed/generated code."""
    if not isinstance(code, EnrollmentCode):
        raise TypeError("code must be an EnrollmentCode")
    offer_id_bytes = _validate_offer_id(code.offer_id)
    salt = hashlib.sha256(_KDF_DOMAIN + offer_id_bytes).digest()
    verifier = hashlib.scrypt(
        code._secret_bytes(), salt=salt, n=16_384, r=8, p=1, dklen=32
    )
    return EnrollmentCodeVerifier(code.offer_id, bytearray(verifier))


def derive_enrollment_envelope_key(code: EnrollmentCode) -> EnrollmentEnvelopeKey:
    """Derive a distinct 32-byte envelope key from the raw code secret.

    This derivation intentionally does not depend on the relay verifier. The
    offer-bound salt and info keep envelope encryption in its own HKDF domain.
    """
    if not isinstance(code, EnrollmentCode):
        raise TypeError("code must be an EnrollmentCode")
    offer_id_bytes = _validate_offer_id(code.offer_id)
    salt = hashlib.sha256(_ENVELOPE_SALT_DOMAIN + offer_id_bytes).digest()
    info = _ENVELOPE_INFO_DOMAIN + offer_id_bytes
    key = _hkdf_sha256(code._secret_bytes(), salt=salt, info=info, length=32)
    return EnrollmentEnvelopeKey(code.offer_id, bytearray(key))


def derive_enrollment_lookup_tag(secret: bytes | bytearray, origin: str) -> str:
    """Return the base64url lookup tag a joiner sends to find its short-code offer.

    Only meaningful for a short code's secret; a K1 code carries its offer id
    directly and never needs a lookup.
    """
    if not isinstance(secret, (bytes, bytearray)) or len(secret) != _SECRET_LENGTH:
        raise ValueError("invalid enrollment code secret")
    if not isinstance(origin, str) or not origin:
        raise ValueError("invalid origin")
    key = _LOOKUP_DOMAIN + origin.encode("ascii")
    tag = hmac.new(key, bytes(secret), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(tag).rstrip(b"=").decode("ascii")


def enrollment_lookup_hash(lookup: str) -> str:
    """Return the relay-storable hash of a lookup tag; the tag is bearer-equivalent."""
    if not isinstance(lookup, str) or not lookup:
        raise ValueError("invalid lookup tag")
    try:
        tag = base64.urlsafe_b64decode(lookup + "=" * (-len(lookup) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid lookup tag") from exc
    if len(tag) != 32:
        raise ValueError("invalid lookup tag")
    return hashlib.sha256(tag).hexdigest()


def enrollment_verifier_hash(offer_id: str, verifier: EnrollmentCodeVerifier) -> str:
    """Return the HMAC-SHA256 value stored for an offer's verifier."""
    offer_id_bytes = _validate_offer_id(offer_id)
    if not isinstance(verifier, EnrollmentCodeVerifier):
        raise TypeError("verifier must be an EnrollmentCodeVerifier")
    if verifier.offer_id != offer_id:
        raise ValueError("verifier belongs to a different offer")
    key = _VERIFIER_DOMAIN + offer_id_bytes
    return hmac.new(key, verifier._value_bytes(), hashlib.sha256).hexdigest()


def _hkdf_sha256(ikm: bytes, *, salt: bytes, info: bytes, length: int) -> bytes:
    """Small RFC 5869 HKDF-SHA256 implementation for the fixed local protocol."""
    hash_length = hashlib.sha256().digest_size
    if not 0 <= length <= 255 * hash_length:
        raise ValueError("invalid HKDF output length")
    pseudorandom_key = hmac.new(salt, ikm, hashlib.sha256).digest()
    output = bytearray()
    previous = b""
    for counter in range(1, (length + hash_length - 1) // hash_length + 1):
        previous = hmac.new(
            pseudorandom_key,
            previous + info + bytes((counter,)),
            hashlib.sha256,
        ).digest()
        output.extend(previous)
    return bytes(output[:length])


__all__ = [
    "EnrollmentCode",
    "EnrollmentEnvelopeKey",
    "EnrollmentCodeVerifier",
    "derive_enrollment_code_verifier",
    "derive_enrollment_envelope_key",
    "derive_enrollment_lookup_tag",
    "enrollment_lookup_hash",
    "enrollment_verifier_hash",
    "generate_enrollment_code",
    "is_short_enrollment_code",
    "parse_enrollment_code",
    "parse_short_enrollment_code",
]
