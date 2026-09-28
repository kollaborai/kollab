"""Private owner/device enrollment and receiver-side conversation grants.

This module is a protocol primitive, not an HTTP endpoint. Callers must keep
``approve_pairing`` and ``issue_conversation_grant`` on a locally authenticated
operator path. A challenge proves possession of a new device key; it never
enrolls that key by itself.

All credentials use compact JOSE JWS with ``alg=EdDSA`` and Ed25519. Owner and
device keys are independent. This module stores only public credentials,
revocations, and replay records; private keys remain with the calling device.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import urlsplit

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

try:  # pragma: no cover - Windows has a different standard-library lock API
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - only imported on Windows
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]


STATE_VERSION = 1
MAX_PAIRING_TTL_SECONDS = 15 * 60
DEFAULT_PAIRING_TTL_SECONDS = 5 * 60
MAX_CREDENTIAL_TTL_SECONDS = 365 * 24 * 60 * 60
DEFAULT_CREDENTIAL_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_GRANT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_GRANT_TTL_SECONDS = 15 * 60
MAX_REQUEST_PROOF_TTL_SECONDS = 5 * 60
CLOCK_SKEW_SECONDS = 30
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 2048
MAX_PENDING_PAIRINGS = 256
MAX_ACTIVE_PROOFS = 4096
MAX_ACTIVE_MESSAGES = 4096
MAX_ACCEPTED_GRANTS = 256
MAX_ISSUED_GRANTS = 256
MAX_REVOCATIONS = 4096

_KEY_ID = re.compile(r"ed25519:([0-9a-f]{64})\Z")
_PURPOSE = re.compile(r"[a-z][a-z0-9._:-]{0,127}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN_TYPES = {
    "pairing": "kollab-pairing-v1",
    "pairing_proof": "kollab-pairing-proof-v1",
    "device": "kollab-device-credential-v1",
    "grant": "kollab-conversation-grant-v1",
    "request": "kollab-request-proof-v1",
    "revocation": "kollab-revocation-v1",
}


class PrivateDirectoryError(ValueError):
    """Base error for invalid credentials, state, or authorization."""


class CredentialError(PrivateDirectoryError):
    """A JWS or its claims failed validation."""


class AuthorizationError(PrivateDirectoryError):
    """A request is not authorized for the receiver's workspace."""


class ReplayError(AuthorizationError):
    """A request proof or message identifier has already been consumed."""


class StateCapacityError(PrivateDirectoryError):
    """A bounded security ledger is full; callers must fail closed."""


@dataclass(frozen=True)
class PairingChallenge:
    token: str
    challenge_id: str
    owner_id: str
    expected_device_id: str
    expires_at: int


@dataclass(frozen=True)
class PairingProof:
    token: str
    device_id: str
    public_key: bytes


@dataclass(frozen=True)
class PendingPairing:
    challenge_id: str
    device_id: str
    public_key: bytes
    expires_at: int


@dataclass(frozen=True)
class DeviceCredential:
    token: str
    credential_id: str
    owner_id: str
    device_id: str
    public_key: bytes
    expires_at: int


@dataclass(frozen=True)
class ConversationGrant:
    token: str
    grant_id: str
    owner_id: str
    device_id: str
    workspace_id: str
    purpose: str
    conversation_id: str
    expires_at: int


@dataclass(frozen=True)
class Revocation:
    token: str
    revocation_id: str
    target_type: Literal["device", "credential", "grant"]
    target_id: str


@dataclass(frozen=True)
class AuthorizedPrincipal:
    """Trusted request identity returned only after every receiver check passes."""

    owner_id: str
    device_id: str
    public_key: bytes
    credential_id: str
    grant_id: str
    workspace_id: str
    purpose: str
    conversation_id: str
    message_id: str
    target_uri: str
    credential_expires_at: int
    grant_expires_at: int


def public_key_id(public_key: bytes) -> str:
    """Return the stable principal identifier for a raw Ed25519 public key."""
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise ValueError("Ed25519 public keys must be exactly 32 bytes")
    return "ed25519:" + public_key.hex()


def new_device_signing_key() -> SigningKey:
    """Create a fresh device key; the caller must persist it locally.

    The returned private key is never serialized or included in any token by
    this module. The owner key must not be passed here or copied to the device.
    """
    return SigningKey.generate()


def prove_pairing(
    challenge: PairingChallenge | str,
    device_signing_key: SigningKey,
    *,
    owner_public_key: bytes,
    now: int | None = None,
) -> PairingProof:
    """Prove possession of a distinct new device key for an owner challenge."""
    token = challenge.token if isinstance(challenge, PairingChallenge) else challenge
    challenge_claims = _verify_pairing_challenge(token, owner_public_key, now=now)
    owner_id = _string_claim(challenge_claims, "iss")
    challenge_id = _string_claim(challenge_claims, "jti")
    expires_at = _integer_claim(challenge_claims, "exp")
    nonce = _string_claim(challenge_claims, "nonce")

    device_public_key = bytes(device_signing_key.verify_key)
    device_id = public_key_id(device_public_key)
    expected_device_id = _validate_key_id(
        _string_claim(challenge_claims, "expected_device_id")
    )
    if device_id != expected_device_id:
        raise CredentialError("pairing challenge is pinned to a different device key")
    proof_claims = {
        "kollab_type": _TOKEN_TYPES["pairing_proof"],
        "iss": device_id,
        "sub": device_id,
        "aud": owner_id,
        "iat": _now(now),
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
        "challenge_id": challenge_id,
        "challenge_sha256": _sha256(token.encode("ascii")),
        "challenge_nonce": nonce,
    }
    return PairingProof(
        token=_sign_jws(proof_claims, device_signing_key, kid=device_id),
        device_id=device_id,
        public_key=device_public_key,
    )


def verify_pairing_proof(
    challenge_token: str,
    proof_token: str,
    *,
    owner_public_key: bytes,
    now: int | None = None,
) -> PairingProof:
    """Verify a submitted proof without consuming or approving the challenge."""
    timestamp = _now(now)
    challenge_claims = _verify_pairing_challenge(
        challenge_token, owner_public_key, now=timestamp
    )
    challenge_id = _string_claim(challenge_claims, "jti")
    owner_id = _string_claim(challenge_claims, "iss")
    header, unverified = _decode_unverified(proof_token)
    _require_token_type(unverified, "pairing_proof")
    device_id = _string_claim(unverified, "sub")
    _validate_key_id(device_id)
    expected_device_id = _validate_key_id(
        _string_claim(challenge_claims, "expected_device_id")
    )
    if device_id != expected_device_id:
        raise CredentialError("pairing challenge is pinned to a different device key")
    if header.get("kid") != device_id or unverified.get("iss") != device_id:
        raise CredentialError("pairing proof key id does not match its device subject")
    public_key = _public_key_from_id(device_id)
    claims = _verify_jws(proof_token, VerifyKey(public_key), expected_kid=device_id)
    if claims.get("aud") != owner_id or claims.get("challenge_id") != challenge_id:
        raise CredentialError("pairing proof targets a different owner or challenge")
    if claims.get("challenge_sha256") != _sha256(challenge_token.encode("ascii")):
        raise CredentialError("pairing proof does not bind the issued challenge")
    if claims.get("challenge_nonce") != challenge_claims.get("nonce"):
        raise CredentialError("pairing proof nonce mismatch")
    issued_at = _integer_claim(claims, "iat")
    expires_at = _integer_claim(claims, "exp")
    challenge_issued_at = _integer_claim(challenge_claims, "iat")
    challenge_expires_at = _integer_claim(challenge_claims, "exp")
    if (
        issued_at < challenge_issued_at - CLOCK_SKEW_SECONDS
        or issued_at > timestamp + CLOCK_SKEW_SECONDS
    ):
        raise CredentialError("pairing proof timestamp is outside the challenge window")
    if expires_at > challenge_expires_at:
        raise CredentialError("pairing proof outlives its challenge")
    _validate_time_window(
        issued_at, expires_at, now=timestamp, max_lifetime=MAX_PAIRING_TTL_SECONDS
    )
    return PairingProof(proof_token, device_id, public_key)


def sign_request(
    device_signing_key: SigningKey,
    *,
    credential_jws: str,
    grant_jws: str,
    body: bytes,
    method: str,
    path: str,
    target_uri: str,
    recipient_workspace_id: str,
    purpose: str,
    conversation_id: str,
    message_id: str,
    now: int | None = None,
) -> str:
    """Sign one exact HTTP request with the enrolled device key.

    The proof is bound to the exact credential and grant strings, body bytes,
    HTTP method/path, destination workspace, purpose, conversation, and message
    id. A copied bearer token alone cannot impersonate the device.
    """
    timestamp = _now(now)
    _validate_request_fields(
        method=method,
        path=path,
        target_uri=target_uri,
        workspace_id=recipient_workspace_id,
        purpose=purpose,
        conversation_id=conversation_id,
        message_id=message_id,
        body=body,
    )
    public_key = bytes(device_signing_key.verify_key)
    device_id = public_key_id(public_key)
    claims = {
        "kollab_type": _TOKEN_TYPES["request"],
        "iss": device_id,
        "sub": device_id,
        "iat": timestamp,
        "exp": timestamp + MAX_REQUEST_PROOF_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
        "nonce": _b64url(os.urandom(24)),
        "credential_sha256": _sha256(_token_bytes(credential_jws)),
        "grant_sha256": _sha256(_token_bytes(grant_jws)),
        "body_sha256": _sha256(body),
        "htm": method.upper(),
        "htu": target_uri,
        "aud": _workspace_audience(recipient_workspace_id),
        "purpose": purpose,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
    return _sign_jws(claims, device_signing_key, kid=device_id)


class PrivateDirectory:
    """Persist a private member roster, pairing state, revocations, and replay data.

    ``state_path`` must identify a private local data file, not a public web
    root. The directory stores public keys and signed credentials only. An
    optional ``workspace_id`` is an opaque configured identifier; filesystem
    paths are rejected and are never included in credentials or responses.
    """

    def __init__(
        self,
        state_path: str | os.PathLike[str],
        *,
        owner_public_key: bytes,
        workspace_id: str | None = None,
    ) -> None:
        self._path = Path(state_path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        if not isinstance(owner_public_key, bytes) or len(owner_public_key) != 32:
            raise ValueError("owner_public_key must be a raw 32-byte Ed25519 key")
        self.owner_public_key = owner_public_key
        self.owner_id = public_key_id(owner_public_key)
        self.workspace_id = (
            _validate_workspace_id(workspace_id) if workspace_id is not None else None
        )
        self._thread_lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._locked_file():
            if self._path.exists():
                state = self._read_state_unlocked()
                self._check_owner(state)
            else:
                self._write_state_unlocked(self._empty_state())

    def begin_pairing(
        self,
        owner_signing_key: SigningKey,
        *,
        expected_device_public_key: bytes,
        expires_in_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
        now: int | None = None,
    ) -> PairingChallenge:
        """Create and persist a one-use owner-signed pairing challenge."""
        self._require_owner_key(owner_signing_key)
        timestamp = _now(now)
        expected_device_id = public_key_id(expected_device_public_key)
        if not 1 <= expires_in_seconds <= MAX_PAIRING_TTL_SECONDS:
            raise ValueError("pairing challenge lifetime must be 1..900 seconds")
        expires_at = timestamp + expires_in_seconds
        challenge_id = str(uuid.uuid4())
        claims = {
            "kollab_type": _TOKEN_TYPES["pairing"],
            "iss": self.owner_id,
            "sub": self.owner_id,
            "aud": "kollab-private-pairing",
            "expected_device_id": expected_device_id,
            "iat": timestamp,
            "exp": expires_at,
            "jti": challenge_id,
            "nonce": _b64url(os.urandom(32)),
        }
        token = _sign_jws(claims, owner_signing_key, kid=self.owner_id)
        with self._mutating_state(now=timestamp) as state:
            _require_capacity(state, "pending_pairings", MAX_PENDING_PAIRINGS)
            state["pending_pairings"][challenge_id] = {
                "token": token,
                "expires_at": expires_at,
                "used": False,
            }
        return PairingChallenge(
            token, challenge_id, self.owner_id, expected_device_id, expires_at
        )

    def register_pairing_challenge(
        self,
        challenge: PairingChallenge | str,
        *,
        now: int | None = None,
    ) -> PairingChallenge:
        """Install an owner-signed challenge on a receiver without owner secret."""
        token = (
            challenge.token if isinstance(challenge, PairingChallenge) else challenge
        )
        timestamp = _now(now)
        claims = _verify_pairing_challenge(token, self.owner_public_key, now=timestamp)
        challenge_id = _string_claim(claims, "jti")
        expires_at = _integer_claim(claims, "exp")
        with self._mutating_state(now=timestamp) as state:
            current = state["pending_pairings"].get(challenge_id)
            if current is not None and current.get("token") != token:
                raise CredentialError(
                    "challenge id is already bound to a different token"
                )
            if current is None:
                _require_capacity(state, "pending_pairings", MAX_PENDING_PAIRINGS)
                state["pending_pairings"][challenge_id] = {
                    "token": token,
                    "expires_at": expires_at,
                    "used": False,
                }
        return PairingChallenge(
            token,
            challenge_id,
            self.owner_id,
            _validate_key_id(_string_claim(claims, "expected_device_id")),
            expires_at,
        )

    def get_pairing_challenge(
        self,
        challenge_id: str,
        *,
        now: int | None = None,
    ) -> PairingChallenge:
        """Return one configured, unused challenge for a public pairing route."""
        timestamp = _now(now)
        _validate_opaque_id(challenge_id, "challenge_id")
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
        pending = state["pending_pairings"].get(challenge_id)
        if (
            not pending
            or pending.get("used")
            or pending.get("expires_at", 0) <= timestamp
        ):
            raise AuthorizationError("pairing challenge is missing, used, or expired")
        claims = self._verify_owner_jws(pending["token"], "pairing")
        return PairingChallenge(
            pending["token"],
            challenge_id,
            self.owner_id,
            _validate_key_id(_string_claim(claims, "expected_device_id")),
            pending["expires_at"],
        )

    def approve_pairing(
        self,
        challenge: PairingChallenge | str,
        proof: PairingProof | str | None,
        owner_signing_key: SigningKey,
        *,
        approved_by_human: bool,
        scopes: tuple[str, ...] = ("conversation:send",),
        credential_ttl_seconds: int = DEFAULT_CREDENTIAL_TTL_SECONDS,
        now: int | None = None,
    ) -> DeviceCredential:
        """Enroll a device after the local operator explicitly approves it.

        The pairing proof alone is insufficient. This API is intentionally a
        local signing operation, never a route or method exposed to peers.
        """
        self._require_owner_key(owner_signing_key)
        if approved_by_human is not True:
            raise AuthorizationError("local human approval is required")
        timestamp = _now(now)
        if not 1 <= credential_ttl_seconds <= MAX_CREDENTIAL_TTL_SECONDS:
            raise ValueError("credential lifetime must be 1..31536000 seconds")
        challenge_token = (
            challenge.token if isinstance(challenge, PairingChallenge) else challenge
        )
        proof_token = proof.token if isinstance(proof, PairingProof) else proof
        challenge_claims = self._verify_owner_jws(challenge_token, "pairing")
        challenge_id = _string_claim(challenge_claims, "jti")
        pending = self._get_pending_pairing(
            challenge_id, challenge_token, timestamp, allow_used=True
        )
        if proof_token is None:
            proof_token = pending.get("proof_token")
        if not isinstance(proof_token, str):
            raise AuthorizationError(
                "no proof has been submitted for local owner review"
            )
        verified_proof = verify_pairing_proof(
            challenge_token,
            proof_token,
            owner_public_key=self.owner_public_key,
            now=timestamp,
        )
        device_id = verified_proof.device_id
        device_key = verified_proof.public_key
        if timestamp >= pending["expires_at"]:
            raise CredentialError("pairing challenge has expired")
        clean_scopes = _validate_scopes(scopes)
        credential_id = str(uuid.uuid4())
        expires_at = timestamp + credential_ttl_seconds
        credential_claims = {
            "kollab_type": _TOKEN_TYPES["device"],
            "iss": self.owner_id,
            "sub": device_id,
            "aud": _directory_audience(self.owner_id),
            "iat": timestamp,
            "nbf": timestamp,
            "exp": expires_at,
            "jti": credential_id,
            "scope": list(clean_scopes),
            "pairing_jti": challenge_id,
        }
        credential_token = _sign_jws(
            credential_claims, owner_signing_key, kid=self.owner_id  # gitleaks:allow -- runtime key reference
        )
        reused_token = None
        with self._mutating_state(now=timestamp) as state:
            current = state["pending_pairings"].get(challenge_id)
            if (
                not current
                or current.get("token") != challenge_token
            ):
                raise AuthorizationError(
                    "pairing challenge is unknown"
                )
            if current.get("proof_token") != proof_token:
                raise AuthorizationError(
                    "proof has not been submitted for local owner review"
                )
            if timestamp >= current.get("expires_at", 0):
                raise CredentialError("pairing challenge has expired")
            if current.get("used"):
                reused_token = self._pairing_credential_token(state, challenge_id)
            else:
                if credential_id not in state["members"]:
                    _require_capacity(state, "members", MAX_MEMBERS)
                current["used"] = True
                state["members"][credential_id] = credential_token
        if reused_token is not None:
            reused_claims = self._verify_owner_jws(reused_token, "device")
            if (
                reused_claims.get("pairing_jti") != challenge_id
                or reused_claims.get("scope") != list(clean_scopes)
                or _integer_claim(reused_claims, "exp") - _integer_claim(reused_claims, "iat")
                != credential_ttl_seconds
            ):
                raise AuthorizationError(
                    "pairing challenge was already approved with different terms"
                )
            return self.validate_device_credential(reused_token, now=timestamp)
        return DeviceCredential(
            credential_token,
            credential_id,
            self.owner_id,
            device_id,
            device_key,
            expires_at,
        )

    def _pairing_credential_token(
        self, state: dict[str, Any], challenge_id: str
    ) -> str:
        """Find the single exact owner credential already issued for a pairing."""
        matches: list[str] = []
        for token in state["members"].values():
            try:
                claims = self._verify_owner_jws(token, "device")
            except (CredentialError, TypeError, ValueError):
                continue
            if claims.get("pairing_jti") == challenge_id:
                matches.append(token)
        if len(matches) != 1:
            raise AuthorizationError(
                "consumed pairing challenge has no unique issued credential"
            )
        return matches[0]

    def record_pairing_proof(
        self,
        challenge: PairingChallenge | str,
        proof: PairingProof | str,
        *,
        now: int | None = None,
    ) -> PairingProof:
        """Persist a valid remote proof as pending; this does not enroll it."""
        timestamp = _now(now)
        challenge_token = (
            challenge.token if isinstance(challenge, PairingChallenge) else challenge
        )
        proof_token = proof.token if isinstance(proof, PairingProof) else proof
        verified = verify_pairing_proof(
            challenge_token,
            proof_token,
            owner_public_key=self.owner_public_key,
            now=timestamp,
        )
        challenge_claims = self._verify_owner_jws(challenge_token, "pairing")
        challenge_id = _string_claim(challenge_claims, "jti")
        with self._mutating_state(now=timestamp) as state:
            pending = state["pending_pairings"].get(challenge_id)
            if (
                not pending
                or pending.get("token") != challenge_token
                or pending.get("used")
            ):
                raise AuthorizationError(
                    "pairing challenge is not pending in this directory"
                )
            if timestamp >= pending.get("expires_at", 0):
                raise CredentialError("pairing challenge has expired")
            previous = pending.get("proof_token")
            if previous is not None and previous != proof_token:
                raise AuthorizationError(
                    "pairing challenge already has a different submitted proof"
                )
            pending["proof_token"] = proof_token
            pending["submitted_at"] = timestamp
        return verified

    def pending_pairing_proofs(
        self, *, now: int | None = None
    ) -> tuple[PendingPairing, ...]:
        """Return unexpired proof submissions for a local operator to review."""
        timestamp = _now(now)
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
        pending_proofs: list[PendingPairing] = []
        for challenge_id, item in state["pending_pairings"].items():
            if item.get("used") or item.get("expires_at", 0) <= timestamp:
                continue
            proof_token = item.get("proof_token")
            if not isinstance(proof_token, str):
                continue
            try:
                proof = verify_pairing_proof(
                    item["token"],
                    proof_token,
                    owner_public_key=self.owner_public_key,
                    now=timestamp,
                )
            except (CredentialError, ValueError, TypeError, KeyError):
                continue
            pending_proofs.append(
                PendingPairing(
                    challenge_id, proof.device_id, proof.public_key, item["expires_at"]
                )
            )
        return tuple(
            sorted(pending_proofs, key=lambda item: (item.expires_at, item.device_id))
        )

    def get_pending_pairing_proof(
        self,
        challenge_id: str,
        *,
        now: int | None = None,
    ) -> PairingProof:
        """Read one pending proof for local operator review and approval."""
        challenge = self.get_pairing_challenge(challenge_id, now=now)
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
        pending = state["pending_pairings"].get(challenge_id)
        proof_token = pending.get("proof_token") if pending else None
        if not isinstance(proof_token, str):
            raise AuthorizationError("no proof has been submitted for this challenge")
        return verify_pairing_proof(
            challenge.token,
            proof_token,
            owner_public_key=self.owner_public_key,
            now=now,
        )

    def import_device_credential(
        self,
        credential: DeviceCredential | str,
        *,
        now: int | None = None,
    ) -> DeviceCredential:
        """Install one owner-signed member credential received over a trusted channel."""
        validated = self.validate_device_credential(credential, now=now)
        token = validated.token
        credential_id = validated.credential_id
        device_id = validated.device_id
        with self._mutating_state(now=_now(now)) as state:
            existing = state["members"].get(credential_id)
            if existing is not None and existing != token:
                raise AuthorizationError(
                    "credential id is already bound to a different token"
                )
            if _is_revoked(state, credential_id=credential_id, device_id=device_id):
                raise AuthorizationError("credential or device has been revoked")
            if existing is None:
                _require_capacity(state, "members", MAX_MEMBERS)
            state["members"][credential_id] = token
        return validated

    def validate_device_credential(
        self,
        credential: DeviceCredential | str,
        *,
        now: int | None = None,
    ) -> DeviceCredential:
        """Validate a member credential without changing private-directory state."""
        token = (
            credential.token if isinstance(credential, DeviceCredential) else credential
        )
        timestamp = _now(now)
        claims = self._verify_owner_jws(token, "device")
        _validate_credential_claims(claims, self.owner_id, now=timestamp)
        credential_id = _string_claim(claims, "jti")
        device_id = _string_claim(claims, "sub")
        public_key = _public_key_from_id(device_id)
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
            if _is_revoked(state, credential_id=credential_id, device_id=device_id):
                raise AuthorizationError("credential or device has been revoked")
        return DeviceCredential(
            token,
            credential_id,
            self.owner_id,
            device_id,
            public_key,
            claims["exp"],
        )

    def members(self, *, now: int | None = None) -> tuple[DeviceCredential, ...]:
        """Return only valid, nonrevoked members; no local filesystem paths."""
        timestamp = _now(now)
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
        result: list[DeviceCredential] = []
        for credential_id, token in state["members"].items():
            try:
                claims = self._verify_owner_jws(token, "device")
                _validate_credential_claims(claims, self.owner_id, now=timestamp)
                if claims["jti"] != credential_id:
                    continue
                device_id = claims["sub"]
                if _is_revoked(state, credential_id=credential_id, device_id=device_id):
                    continue
                result.append(
                    DeviceCredential(
                        token,
                        credential_id,
                        self.owner_id,
                        device_id,
                        _public_key_from_id(device_id),
                        claims["exp"],
                    )
                )
            except (CredentialError, ValueError, TypeError):
                continue
        return tuple(sorted(result, key=lambda member: member.device_id))

    def issue_conversation_grant(
        self,
        device_credential: DeviceCredential | str,
        owner_signing_key: SigningKey,
        *,
        recipient_workspace_id: str,
        purpose: str,
        conversation_id: str,
        approved_by_human: bool,
        expires_in_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
        now: int | None = None,
    ) -> ConversationGrant:
        """Issue a human-approved, purpose/audience-bound conversation grant."""
        self._require_owner_key(owner_signing_key)
        if approved_by_human is not True:
            raise AuthorizationError("local human approval is required")
        timestamp = _now(now)
        workspace_id = _validate_workspace_id(recipient_workspace_id)
        purpose = _validate_purpose(purpose)
        conversation_id = _validate_opaque_id(conversation_id, "conversation_id")
        if not 1 <= expires_in_seconds <= MAX_GRANT_TTL_SECONDS:
            raise ValueError("grant lifetime must be 1..86400 seconds")
        credential_token = (
            device_credential.token
            if isinstance(device_credential, DeviceCredential)
            else device_credential
        )
        credential_claims = self._verify_owner_jws(credential_token, "device")
        _validate_credential_claims(credential_claims, self.owner_id, now=timestamp)
        credential_id = _string_claim(credential_claims, "jti")
        device_id = _string_claim(credential_claims, "sub")
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
            if state["members"].get(credential_id) != credential_token:
                raise AuthorizationError(
                    "device is not enrolled in this private directory"
                )
            if _is_revoked(state, credential_id=credential_id, device_id=device_id):
                raise AuthorizationError("device credential has been revoked")
        credential_scopes = set(
            _validate_scopes(tuple(credential_claims.get("scope", ())))
        )
        if "conversation:send" not in credential_scopes:
            raise AuthorizationError(
                "device credential does not permit conversation sending"
            )
        grant_id = str(uuid.uuid4())
        expires_at = timestamp + expires_in_seconds
        claims = {
            "kollab_type": _TOKEN_TYPES["grant"],
            "iss": self.owner_id,
            "sub": device_id,
            "aud": _workspace_audience(workspace_id),
            "iat": timestamp,
            "nbf": timestamp,
            "exp": expires_at,
            "jti": grant_id,
            "credential_jti": credential_id,
            "scope": ["conversation:send", purpose],
            "purpose": purpose,
            "conversation_id": conversation_id,
        }
        token = _sign_jws(claims, owner_signing_key, kid=self.owner_id)
        with self._mutating_state(now=timestamp) as state:
            if _is_revoked(state, credential_id=credential_id, device_id=device_id):
                raise AuthorizationError("device credential has been revoked")
            _require_capacity(state, "issued_grants", MAX_ISSUED_GRANTS)
            state["issued_grants"][grant_id] = token
        return ConversationGrant(
            token,
            grant_id,
            self.owner_id,
            device_id,
            workspace_id,
            purpose,
            conversation_id,
            expires_at,
        )

    def revoke(
        self,
        target_type: Literal["device", "credential", "grant"],
        target_id: str,
        owner_signing_key: SigningKey,
        *,
        now: int | None = None,
    ) -> Revocation:
        """Create, sign, and persist a revocation; return it for private sync."""
        self._require_owner_key(owner_signing_key)
        if target_type not in {"device", "credential", "grant"}:
            raise ValueError("target_type must be device, credential, or grant")
        if target_type == "device":
            _validate_key_id(target_id)
        else:
            _validate_opaque_id(target_id, "target_id")
        revocation_id = str(uuid.uuid4())
        claims = {
            "kollab_type": _TOKEN_TYPES["revocation"],
            "iss": self.owner_id,
            "sub": self.owner_id,
            "aud": _directory_audience(self.owner_id),
            "iat": _now(now),
            "jti": revocation_id,
            "target_type": target_type,
            "target_id": target_id,
        }
        token = _sign_jws(claims, owner_signing_key, kid=self.owner_id)
        with self._mutating_state(now=_now(now)) as state:
            _require_capacity(state, "revocations", MAX_REVOCATIONS)
            state["revocations"][revocation_id] = token
        return Revocation(token, revocation_id, target_type, target_id)

    def revoke_issued_credential(
        self,
        credential: DeviceCredential | str,
        owner_signing_key: SigningKey,
        *,
        expected_device_public_key: bytes,
        now: int | None = None,
    ) -> Revocation | None:
        """Revoke only this directory's exact credential for the expected key.

        The signed token is verified at its issuance time so cleanup can safely
        identify an already-expired credential. Expired members are pruned
        without adding a permanent revocation. Active credentials are revoked
        atomically only while the exact token remains stored as a member.
        """
        self._require_owner_key(owner_signing_key)
        if not isinstance(expected_device_public_key, bytes) or len(expected_device_public_key) != 32:
            raise AuthorizationError("expected device key is invalid")
        token = credential.token if isinstance(credential, DeviceCredential) else credential
        if not isinstance(token, str) or not token:
            raise CredentialError("issued credential is invalid")
        claims = self._verify_owner_jws(token, "device")
        issued_at = _integer_claim(claims, "iat")
        _validate_credential_claims(claims, self.owner_id, now=issued_at)
        credential_id = _string_claim(claims, "jti")
        device_id = _string_claim(claims, "sub")
        if _public_key_from_id(device_id) != expected_device_public_key:
            raise AuthorizationError("issued credential is for a different device")

        timestamp = _now(now)
        if _integer_claim(claims, "exp") <= timestamp:
            with self._locked_file():
                state = self._read_state_unlocked()
                self._check_owner(state)
                existing = state["members"].get(credential_id)
                if existing is None:
                    return None
                if existing != token:
                    raise AuthorizationError(
                        "credential id is bound to a different stored token"
                    )
                self._prune_expired_state(state, timestamp)
                self._write_state_unlocked(state)
            return None

        revocation_id = str(uuid.uuid4())
        revocation_claims = {
            "kollab_type": _TOKEN_TYPES["revocation"],
            "iss": self.owner_id,
            "sub": self.owner_id,
            "aud": _directory_audience(self.owner_id),
            "iat": timestamp,
            "jti": revocation_id,
            "target_type": "credential",
            "target_id": credential_id,
        }
        revocation_token = _sign_jws(
            revocation_claims, owner_signing_key, kid=self.owner_id
        )
        with self._mutating_state(now=timestamp) as state:
            if state["members"].get(credential_id) != token:
                raise AuthorizationError(
                    "issued credential is not the exact stored member token"
                )
            if _is_revoked(
                state, credential_id=credential_id, device_id=device_id
            ):
                return None
            _require_capacity(state, "revocations", MAX_REVOCATIONS)
            state["revocations"][revocation_id] = revocation_token
        return Revocation(
            revocation_token, revocation_id, "credential", credential_id
        )

    def apply_revocation(self, revocation: Revocation | str) -> Revocation:
        """Persist an owner-signed revocation received through private sync."""
        token = revocation.token if isinstance(revocation, Revocation) else revocation
        claims = self._verify_owner_jws(token, "revocation")
        if claims.get("aud") != _directory_audience(self.owner_id):
            raise CredentialError("revocation is for a different private directory")
        target_type = claims.get("target_type")
        target_id = _string_claim(claims, "target_id")
        if target_type == "device":
            _validate_key_id(target_id)
        elif target_type in {"credential", "grant"}:
            _validate_opaque_id(target_id, "target_id")
        else:
            raise CredentialError("unsupported revocation target type")
        revocation_id = _string_claim(claims, "jti")
        with self._mutating_state() as state:
            existing = state["revocations"].get(revocation_id)
            if existing is not None and existing != token:
                raise CredentialError(
                    "revocation id is already bound to a different token"
                )
            if existing is None:
                _require_capacity(state, "revocations", MAX_REVOCATIONS)
            state["revocations"][revocation_id] = token
        return Revocation(token, revocation_id, target_type, target_id)

    def authorize_request(
        self,
        credential_jws: str,
        grant_jws: str,
        proof_jws: str,
        *,
        body: bytes,
        method: str,
        path: str,
        target_uri: str,
        recipient_workspace_id: str,
        purpose: str,
        conversation_id: str,
        message_id: str,
        now: int | None = None,
    ) -> AuthorizedPrincipal:
        """Validate identity, membership, grant, request proof, expiry and replay.

        The returned principal is suitable as the adapter's trusted task owner.
        Do not start conversation handling or tool execution before this returns.
        """
        timestamp = _now(now)
        workspace_id = _validate_workspace_id(recipient_workspace_id)
        purpose = _validate_purpose(purpose)
        conversation_id = _validate_opaque_id(conversation_id, "conversation_id")
        message_id = _validate_opaque_id(message_id, "message_id")
        _validate_request_fields(
            method=method,
            path=path,
            target_uri=target_uri,
            workspace_id=workspace_id,
            purpose=purpose,
            conversation_id=conversation_id,
            message_id=message_id,
            body=body,
        )
        if self.workspace_id is not None and workspace_id != self.workspace_id:
            raise AuthorizationError(
                "request is addressed to a different local workspace"
            )

        credential_claims = self._verify_owner_jws(credential_jws, "device")
        _validate_credential_claims(credential_claims, self.owner_id, now=timestamp)
        device_id = _string_claim(credential_claims, "sub")
        credential_id = _string_claim(credential_claims, "jti")
        device_public_key = _public_key_from_id(device_id)
        grant_claims = self._verify_owner_jws(grant_jws, "grant")
        _validate_time_claims(
            grant_claims, now=timestamp, max_lifetime=MAX_GRANT_TTL_SECONDS
        )
        grant_id = _string_claim(grant_claims, "jti")
        if grant_claims.get("iss") != self.owner_id:
            raise AuthorizationError("grant issuer is not this private directory owner")
        if grant_claims.get("sub") != device_id:
            raise AuthorizationError("grant subject does not match the sending device")
        if grant_claims.get("credential_jti") != credential_id:
            raise AuthorizationError("grant is not bound to this device credential")
        if grant_claims.get("aud") != _workspace_audience(workspace_id):
            raise AuthorizationError(
                "grant audience does not match the recipient workspace"
            )
        if grant_claims.get("purpose") != purpose or purpose not in grant_claims.get(
            "scope", []
        ):
            raise AuthorizationError("grant does not authorize this purpose")
        if grant_claims.get("conversation_id") != conversation_id:
            raise AuthorizationError("grant does not authorize this conversation")
        if "conversation:send" not in credential_claims.get("scope", []):
            raise AuthorizationError(
                "device credential does not permit conversation sending"
            )

        proof_header, proof_unverified = _decode_unverified(proof_jws)
        _require_token_type(proof_unverified, "request")
        if (
            proof_header.get("kid") != device_id
            or proof_unverified.get("sub") != device_id
        ):
            raise AuthorizationError(
                "request proof is not signed by the enrolled device"
            )
        proof_claims = _verify_jws(
            proof_jws, VerifyKey(device_public_key), expected_kid=device_id
        )
        _validate_time_claims(
            proof_claims,
            now=timestamp,
            max_lifetime=MAX_REQUEST_PROOF_TTL_SECONDS,
            require_iat=True,
        )
        expected_proof_claims = {
            "iss": device_id,
            "sub": device_id,
            "credential_sha256": _sha256(_token_bytes(credential_jws)),
            "grant_sha256": _sha256(_token_bytes(grant_jws)),
            "body_sha256": _sha256(body),
            "htm": method.upper(),
            "htu": target_uri,
            "aud": _workspace_audience(workspace_id),
            "purpose": purpose,
            "conversation_id": conversation_id,
            "message_id": message_id,
        }
        for name, expected in expected_proof_claims.items():
            if proof_claims.get(name) != expected:
                raise AuthorizationError(f"request proof does not match {name}")
        _string_claim(proof_claims, "nonce")
        proof_id = _string_claim(proof_claims, "jti")
        replay_id = _message_replay_id(device_id, conversation_id, message_id)

        with self._mutating_state(now=timestamp) as state:
            self._require_member(
                state, credential_id, credential_jws, device_id, timestamp
            )
            if _is_revoked(
                state,
                device_id=device_id,
                credential_id=credential_id,
                grant_id=grant_id,
            ):
                raise AuthorizationError(
                    "device, credential, or conversation grant has been revoked"
                )
            if proof_id in state["used_proofs"] or replay_id in state["seen_messages"]:
                raise ReplayError(
                    "request proof or message id has already been consumed"
                )
            _require_capacity(state, "used_proofs", MAX_ACTIVE_PROOFS)
            _require_capacity(state, "seen_messages", MAX_ACTIVE_MESSAGES)
            existing_grant = state["accepted_grants"].get(grant_id)
            if existing_grant is not None and existing_grant != grant_jws:
                raise AuthorizationError(
                    "grant id is already bound to a different token"
                )
            if existing_grant is None:
                _require_capacity(state, "accepted_grants", MAX_ACCEPTED_GRANTS)
            state["used_proofs"][proof_id] = proof_claims["exp"]
            state["seen_messages"][replay_id] = grant_claims["exp"]
            state["accepted_grants"][grant_id] = grant_jws

        return AuthorizedPrincipal(
            owner_id=self.owner_id,
            device_id=device_id,
            public_key=device_public_key,
            credential_id=credential_id,
            grant_id=grant_id,
            workspace_id=workspace_id,
            purpose=purpose,
            conversation_id=conversation_id,
            message_id=message_id,
            target_uri=target_uri,
            credential_expires_at=credential_claims["exp"],
            grant_expires_at=grant_claims["exp"],
        )

    def revalidate(
        self,
        principal: AuthorizedPrincipal,
        *,
        now: int | None = None,
    ) -> AuthorizedPrincipal:
        """Recheck a principal immediately before queued work starts."""
        timestamp = _now(now)
        if not isinstance(principal, AuthorizedPrincipal):
            raise TypeError("principal must be an AuthorizedPrincipal")
        if principal.owner_id != self.owner_id:
            raise AuthorizationError("principal belongs to a different owner")
        if (
            self.workspace_id is not None
            and principal.workspace_id != self.workspace_id
        ):
            raise AuthorizationError(
                "principal is addressed to a different local workspace"
            )
        if (
            timestamp >= principal.credential_expires_at
            or timestamp >= principal.grant_expires_at
        ):
            raise CredentialError("credential or conversation grant has expired")
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
        token = state["members"].get(principal.credential_id)
        if token is None:
            raise AuthorizationError(
                "device credential is no longer in the private directory"
            )
        claims = self._verify_owner_jws(token, "device")
        _validate_credential_claims(claims, self.owner_id, now=timestamp)
        if (
            claims.get("sub") != principal.device_id
            or _public_key_from_id(principal.device_id) != principal.public_key
        ):
            raise AuthorizationError(
                "principal no longer matches its member credential"
            )
        grant_token = state["accepted_grants"].get(principal.grant_id)
        if grant_token is None:
            raise AuthorizationError(
                "grant is not in the receiver's validated grant ledger"
            )
        grant_claims = self._verify_owner_jws(grant_token, "grant")
        _validate_time_claims(
            grant_claims, now=timestamp, max_lifetime=MAX_GRANT_TTL_SECONDS
        )
        if (
            grant_claims.get("jti") != principal.grant_id
            or grant_claims.get("sub") != principal.device_id
            or grant_claims.get("credential_jti") != principal.credential_id
            or grant_claims.get("aud") != _workspace_audience(principal.workspace_id)
            or grant_claims.get("purpose") != principal.purpose
            or grant_claims.get("conversation_id") != principal.conversation_id
        ):
            raise AuthorizationError("grant no longer matches the authorized principal")
        if _is_revoked(
            state,
            credential_id=principal.credential_id,
            device_id=principal.device_id,
            grant_id=principal.grant_id,
        ):
            raise AuthorizationError("device, credential, or grant has been revoked")
        return principal

    def _require_member(
        self,
        state: dict[str, Any],
        credential_id: str,
        token: str,
        device_id: str,
        now: int,
    ) -> None:
        if state["members"].get(credential_id) != token:
            raise AuthorizationError(
                "sender is not in the receiver's private directory"
            )
        if _is_revoked(state, credential_id=credential_id, device_id=device_id):
            raise AuthorizationError("sender is revoked from the private directory")
        claims = self._verify_owner_jws(token, "device")
        _validate_credential_claims(claims, self.owner_id, now=now)

    def _get_pending_pairing(
        self, challenge_id: str, token: str, now: int, *, allow_used: bool = False
    ) -> dict[str, Any]:
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
            pending = state["pending_pairings"].get(challenge_id)
            if not pending or pending.get("token") != token:
                raise AuthorizationError(
                    "pairing challenge is not pending in this directory"
                )
            if pending.get("used") and not allow_used:
                raise AuthorizationError("pairing challenge has already been consumed")
            if now >= pending.get("expires_at", 0):
                raise CredentialError("pairing challenge has expired")
            return pending

    def _verify_owner_jws(self, token: str, token_kind: str) -> dict[str, Any]:
        header, unverified = _decode_unverified(token)
        _require_token_type(unverified, token_kind)
        if header.get("kid") != self.owner_id:
            raise CredentialError(
                "JWS signer is not the pinned private-directory owner"
            )
        claims = _verify_jws(
            token, VerifyKey(self.owner_public_key), expected_kid=self.owner_id
        )
        if claims.get("iss") != self.owner_id:
            raise CredentialError(
                "JWS issuer is not the pinned private-directory owner"
            )
        return claims

    def _require_owner_key(self, signing_key: SigningKey) -> None:
        if not isinstance(signing_key, SigningKey):
            raise TypeError("owner_signing_key must be a PyNaCl Ed25519 SigningKey")
        if bytes(signing_key.verify_key) != self.owner_public_key:
            raise AuthorizationError(
                "owner signing key does not match the pinned owner key"
            )

    @contextmanager
    def _mutating_state(self, *, now: int | None = None) -> Iterator[dict[str, Any]]:
        with self._locked_file():
            state = self._read_state_unlocked()
            self._check_owner(state)
            self._prune_expired_state(state, _now(now))
            yield state
            self._write_state_unlocked(state)

    def _prune_expired_state(self, state: dict[str, Any], now: int) -> None:
        """Prune only state whose signed authorization window has ended.

        Revocations are intentionally permanent and never pass through this
        cleanup. Invalid signed artifacts are retained (and rejected on use)
        rather than silently discarded as if they had expired.
        """
        _prune_replay_state(state, now)
        for challenge_id, item in tuple(state["pending_pairings"].items()):
            expires_at = item.get("expires_at")
            if (
                isinstance(expires_at, int)
                and not isinstance(expires_at, bool)
                and expires_at <= now
            ):
                del state["pending_pairings"][challenge_id]

        for bucket, token_kind in (
            ("members", "device"),
            ("issued_grants", "grant"),
            ("accepted_grants", "grant"),
        ):
            for item_id, token in tuple(state[bucket].items()):
                try:
                    claims = self._verify_owner_jws(token, token_kind)
                    expires_at = _integer_claim(claims, "exp")
                except (CredentialError, TypeError, ValueError):
                    continue
                if expires_at <= now:
                    del state[bucket][item_id]

    @contextmanager
    def _locked_file(self) -> Iterator[None]:
        self._thread_lock.acquire()
        fd: int | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fd is not None:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                os.close(fd)
            self._thread_lock.release()

    def _read_state_unlocked(self) -> dict[str, Any]:
        try:
            with self._path.open("rb") as stream:
                file_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(file_stat.st_mode):
                    raise PrivateDirectoryError(
                        "private-directory state must be a regular file"
                    )
                if file_stat.st_size > MAX_STATE_BYTES:
                    raise StateCapacityError(
                        f"private-directory state exceeds the {MAX_STATE_BYTES}-byte read limit"
                    )
                raw = stream.read(MAX_STATE_BYTES + 1)
                if len(raw) > MAX_STATE_BYTES:
                    raise StateCapacityError(
                        f"private-directory state exceeds the {MAX_STATE_BYTES}-byte read limit"
                    )
                state = json.loads(raw)
        except FileNotFoundError:
            return self._empty_state()
        except PrivateDirectoryError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PrivateDirectoryError(
                f"cannot read private-directory state: {exc}"
            ) from exc
        _validate_state(state)
        return state

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        _validate_state(state)
        serialized = _json_bytes(state)
        if len(serialized) > MAX_STATE_BYTES:
            raise StateCapacityError(
                f"private-directory state exceeds the {MAX_STATE_BYTES}-byte write limit"
            )
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
            try:
                directory_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    def _empty_state(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "owner_id": self.owner_id,
            "members": {},
            "pending_pairings": {},
            "issued_grants": {},
            "accepted_grants": {},
            "revocations": {},
            "used_proofs": {},
            "seen_messages": {},
        }

    def _check_owner(self, state: dict[str, Any]) -> None:
        if state.get("owner_id") != self.owner_id:
            raise PrivateDirectoryError("state file belongs to a different owner key")


def _sign_jws(claims: dict[str, Any], signing_key: SigningKey, *, kid: str) -> str:
    header = {"alg": "EdDSA", "kid": kid, "typ": "JWT"}
    protected = _b64url(_json_bytes(header))
    payload = _b64url(_json_bytes(claims))
    signing_input = f"{protected}.{payload}".encode("ascii")
    signature = signing_key.sign(signing_input).signature
    return f"{protected}.{payload}.{_b64url(signature)}"


def _decode_unverified(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(token, str) or len(token) > 64 * 1024:
        raise CredentialError("JWS must be a string of at most 64 KiB")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise CredentialError("expected compact JWS with three parts")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialError("malformed compact JWS") from exc
    if (
        not isinstance(header, dict)
        or not isinstance(claims, dict)
        or len(signature) != 64
    ):
        raise CredentialError("malformed JWS header, claims, or Ed25519 signature")
    if header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
        raise CredentialError("only JOSE EdDSA JWTs are accepted")
    return header, claims


def _verify_jws(
    token: str, verify_key: VerifyKey, *, expected_kid: str
) -> dict[str, Any]:
    header, claims = _decode_unverified(token)
    if header.get("kid") != expected_kid:
        raise CredentialError("JWS key id mismatch")
    protected, payload, signature = token.split(".")
    try:
        verify_key.verify(
            f"{protected}.{payload}".encode("ascii"), _b64url_decode(signature)
        )
    except (BadSignatureError, ValueError) as exc:
        raise CredentialError("invalid JWS signature") from exc
    return claims


def _verify_pairing_challenge(
    token: str,
    owner_public_key: bytes,
    *,
    now: int | None,
) -> dict[str, Any]:
    if not isinstance(owner_public_key, bytes) or len(owner_public_key) != 32:
        raise ValueError("owner_public_key must be a raw 32-byte Ed25519 key")
    header, claims = _decode_unverified(token)
    _require_token_type(claims, "pairing")
    owner_id = _validate_key_id(_string_claim(claims, "iss"))
    if public_key_id(owner_public_key) != owner_id or header.get("kid") != owner_id:
        raise CredentialError("pairing challenge does not match the pinned owner key")
    verified = _verify_jws(token, VerifyKey(owner_public_key), expected_kid=owner_id)
    if (
        verified.get("aud") != "kollab-private-pairing"
        or verified.get("sub") != owner_id
    ):
        raise CredentialError("pairing challenge issuer or audience mismatch")
    _validate_time_window(
        _integer_claim(verified, "iat"),
        _integer_claim(verified, "exp"),
        now=now,
        max_lifetime=MAX_PAIRING_TTL_SECONDS,
    )
    _string_claim(verified, "jti")
    _string_claim(verified, "nonce")
    _validate_key_id(_string_claim(verified, "expected_device_id"))
    return verified


def _require_token_type(claims: dict[str, Any], token_kind: str) -> None:
    expected = _TOKEN_TYPES.get(token_kind)
    if expected is None or claims.get("kollab_type") != expected:
        raise CredentialError("unexpected credential type")


def _validate_credential_claims(
    claims: dict[str, Any], owner_id: str, *, now: int
) -> None:
    _validate_time_claims(claims, now=now, max_lifetime=MAX_CREDENTIAL_TTL_SECONDS)
    if claims.get("iss") != owner_id or claims.get("aud") != _directory_audience(
        owner_id
    ):
        raise CredentialError("credential owner or audience mismatch")
    _validate_key_id(_string_claim(claims, "sub"))
    _validate_scopes(tuple(claims.get("scope", ())))


def _validate_time_claims(
    claims: dict[str, Any],
    *,
    now: int,
    max_lifetime: int,
    require_iat: bool = True,
) -> None:
    exp = _integer_claim(claims, "exp")
    iat = _integer_claim(claims, "iat") if require_iat else claims.get("iat", now)
    if isinstance(iat, bool) or not isinstance(iat, int):
        raise CredentialError("iat must be an integer Unix timestamp")
    nbf = claims.get("nbf", iat)
    if isinstance(nbf, bool) or not isinstance(nbf, int):
        raise CredentialError("nbf must be an integer Unix timestamp")
    if iat > now + CLOCK_SKEW_SECONDS or nbf > now + CLOCK_SKEW_SECONDS:
        raise CredentialError("credential is not yet valid")
    if exp <= now:
        raise CredentialError("credential has expired")
    if exp <= iat or exp - iat > max_lifetime:
        raise CredentialError("credential lifetime is invalid")


def _validate_time_window(
    issued_at: int,
    expires_at: int,
    *,
    now: int | None,
    max_lifetime: int,
) -> None:
    timestamp = _now(now)
    if expires_at <= issued_at or expires_at - issued_at > max_lifetime:
        raise CredentialError("credential lifetime is invalid")
    if issued_at > timestamp + CLOCK_SKEW_SECONDS:
        raise CredentialError("credential was issued in the future")
    if expires_at <= timestamp:
        raise CredentialError("credential has expired")


def _validate_request_fields(
    *,
    method: str,
    path: str,
    target_uri: str,
    workspace_id: str,
    purpose: str,
    conversation_id: str,
    message_id: str,
    body: bytes,
) -> None:
    if not isinstance(body, bytes):
        raise TypeError("body must be the exact raw request bytes")
    if not isinstance(method, str) or not re.fullmatch(r"[A-Z]{3,10}", method.upper()):
        raise ValueError("method must be an HTTP token")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or any(ord(ch) < 33 for ch in path)
        or "#" in path
    ):
        raise ValueError(
            "path must be an exact absolute-path target without a fragment"
        )
    _validate_target_uri(target_uri, path)
    _validate_workspace_id(workspace_id)
    _validate_purpose(purpose)
    _validate_opaque_id(conversation_id, "conversation_id")
    _validate_opaque_id(message_id, "message_id")


def _validate_workspace_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            "workspace_id must be an opaque identifier, not a filesystem path"
        )
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(
            "workspace_id must be an opaque identifier, not a filesystem path"
        )
    value = _validate_opaque_id(value, "workspace_id")
    return value


def _validate_opaque_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_ID.fullmatch(value):
        raise ValueError(
            f"{name} must be a nonempty opaque identifier of at most 256 characters"
        )
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{name} cannot contain control characters")
    return value


def _validate_purpose(value: str) -> str:
    if not isinstance(value, str) or not _PURPOSE.fullmatch(value):
        raise ValueError("purpose must be a lower-case scoped identifier")
    return value


def _validate_scopes(scopes: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(scopes, (tuple, list)) or not scopes:
        raise ValueError("scopes must be a nonempty list of scoped identifiers")
    normalized = tuple(sorted(set(_validate_purpose(scope) for scope in scopes)))
    if len(normalized) != len(scopes):
        raise ValueError("scopes must not contain duplicates")
    return normalized


def _validate_key_id(value: str) -> str:
    if not isinstance(value, str) or not _KEY_ID.fullmatch(value):
        raise CredentialError("expected an Ed25519 public-key principal")
    return value


def _public_key_from_id(value: str) -> bytes:
    _validate_key_id(value)
    return bytes.fromhex(value.removeprefix("ed25519:"))


def _is_revoked(
    state: dict[str, Any],
    *,
    credential_id: str | None = None,
    device_id: str | None = None,
    grant_id: str | None = None,
) -> bool:
    targets = _revoked_targets(state)
    return bool(
        (credential_id and ("credential", credential_id) in targets)
        or (device_id and ("device", device_id) in targets)
        or (grant_id and ("grant", grant_id) in targets)
    )


def _revoked_targets(state: dict[str, Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for token in state["revocations"].values():
        try:
            _, claims = _decode_unverified(token)
            target_type = claims.get("target_type")
            target_id = claims.get("target_id")
            if target_type in {"device", "credential", "grant"} and isinstance(
                target_id, str
            ):
                result.add((target_type, target_id))
        except CredentialError:
            continue
    return result


def _prune_replay_state(state: dict[str, Any], now: int) -> None:
    for bucket in ("used_proofs", "seen_messages"):
        state[bucket] = {
            key: expiry for key, expiry in state[bucket].items() if expiry > now
        }


def _require_capacity(state: dict[str, Any], bucket: str, limit: int) -> None:
    if len(state[bucket]) >= limit:
        raise StateCapacityError(
            f"private-directory {bucket} ledger is full ({limit} entries)"
        )


def _message_replay_id(device_id: str, conversation_id: str, message_id: str) -> str:
    return _sha256(_json_bytes([device_id, conversation_id, message_id]))


def _directory_audience(owner_id: str) -> str:
    return f"kollab-private-directory:{owner_id}"


def _workspace_audience(workspace_id: str) -> str:
    """Return a path-free JOSE audience URI for a configured workspace id."""
    return f"urn:kollab:workspace:{_validate_workspace_id(workspace_id)}"


def _validate_target_uri(value: str, path: str) -> str:
    """Require the exact externally configured HTTPS target, independent of Host."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("target_uri must be an absolute configured HTTPS URI")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("target_uri must be HTTPS without user information")
        if parsed.fragment:
            raise ValueError("target_uri cannot contain a fragment")
        request_target = parsed.path or "/"
        if parsed.query:
            request_target += "?" + parsed.query
        if request_target != path:
            raise ValueError("target_uri path and query must exactly match path")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parsed.port
        if port == 443:
            port = None
        authority = host + (f":{port}" if port is not None else "")
        canonical = f"https://{authority}{request_target}"
        if value != canonical:
            raise ValueError("target_uri must use canonical lowercase HTTPS authority")
        return canonical
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid target_uri: {exc}") from exc


def _string_claim(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise CredentialError(f"{name} must be a nonempty string")
    return value


def _integer_claim(claims: dict[str, Any], name: str) -> int:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CredentialError(f"{name} must be an integer Unix timestamp")
    return value


def _token_bytes(token: str) -> bytes:
    if not isinstance(token, str):
        raise TypeError("JWS credential must be a string")
    try:
        return token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise CredentialError("compact JWS must contain ASCII only") from exc


def _now(value: int | None) -> int:
    if value is None:
        return int(time.time())
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("now must be an integer Unix timestamp")
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _validate_state(state: Any) -> None:
    expected = {
        "version",
        "owner_id",
        "members",
        "pending_pairings",
        "issued_grants",
        "accepted_grants",
        "revocations",
        "used_proofs",
        "seen_messages",
    }
    if (
        not isinstance(state, dict)
        or set(state) != expected
        or state.get("version") != STATE_VERSION
    ):
        raise PrivateDirectoryError("unsupported or malformed private-directory state")
    if not isinstance(state["owner_id"], str):
        raise PrivateDirectoryError("private-directory owner id is malformed")
    limits = {
        "members": MAX_MEMBERS,
        "pending_pairings": MAX_PENDING_PAIRINGS,
        "issued_grants": MAX_ISSUED_GRANTS,
        "accepted_grants": MAX_ACCEPTED_GRANTS,
        "revocations": MAX_REVOCATIONS,
        "used_proofs": MAX_ACTIVE_PROOFS,
        "seen_messages": MAX_ACTIVE_MESSAGES,
    }
    for key in expected - {"version", "owner_id"}:
        if not isinstance(state[key], dict):
            raise PrivateDirectoryError(
                f"private-directory state field {key} is malformed"
            )
        if len(state[key]) > limits[key]:
            raise StateCapacityError(
                f"private-directory {key} ledger exceeds its {limits[key]}-entry limit"
            )

    for bucket in ("members", "issued_grants", "accepted_grants", "revocations"):
        for item_id, token in state[bucket].items():
            if (
                not isinstance(item_id, str)
                or not isinstance(token, str)
                or len(token) > 64 * 1024
            ):
                raise PrivateDirectoryError(
                    f"private-directory {bucket} entry is malformed"
                )

    for challenge_id, item in state["pending_pairings"].items():
        if (
            not isinstance(challenge_id, str)
            or not isinstance(item, dict)
            or not isinstance(item.get("token"), str)
            or len(item["token"]) > 64 * 1024
            or isinstance(item.get("expires_at"), bool)
            or not isinstance(item.get("expires_at"), int)
            or not isinstance(item.get("used"), bool)
        ):
            raise PrivateDirectoryError(
                "private-directory pending pairing entry is malformed"
            )
        proof_token = item.get("proof_token")
        if proof_token is not None and (
            not isinstance(proof_token, str) or len(proof_token) > 64 * 1024
        ):
            raise PrivateDirectoryError(
                "private-directory pending pairing proof is malformed"
            )
        submitted_at = item.get("submitted_at")
        if submitted_at is not None and (
            isinstance(submitted_at, bool) or not isinstance(submitted_at, int)
        ):
            raise PrivateDirectoryError(
                "private-directory pending pairing timestamp is malformed"
            )

    for bucket in ("used_proofs", "seen_messages"):
        for replay_id, expires_at in state[bucket].items():
            if (
                not isinstance(replay_id, str)
                or (bucket == "used_proofs" and not _OPAQUE_ID.fullmatch(replay_id))
                or (bucket == "seen_messages" and not _HEX_DIGEST.fullmatch(replay_id))
                or isinstance(expires_at, bool)
                or not isinstance(expires_at, int)
            ):
                raise PrivateDirectoryError(
                    f"private-directory {bucket} entry is malformed"
                )


def _private_home(path: str | os.PathLike[str]) -> Path:
    home = Path(path).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix" and stat.S_IMODE(home.stat().st_mode) & 0o077:
        raise PrivateDirectoryError(f"private home must have mode 0700: {home}")
    return home


def _save_signing_key(path: Path, key: SigningKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, bytes(key))
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    os.close(fd)


def _load_signing_key(path: Path) -> SigningKey:
    try:
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise PrivateDirectoryError("signing key path must be a regular file")
        if os.name == "posix" and stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise PrivateDirectoryError("signing key file must have mode 0600")
        raw = path.read_bytes()
    except OSError as exc:
        raise PrivateDirectoryError(f"cannot read local signing key: {exc}") from exc
    if len(raw) != 32:
        raise PrivateDirectoryError(
            "local Ed25519 signing key must be exactly 32 bytes"
        )
    return SigningKey(raw)


def _load_owner(home: Path) -> tuple[PrivateDirectory, SigningKey]:
    owner_key = _load_signing_key(home / "owner.key")
    directory = PrivateDirectory(
        home / "private-directory.json", owner_public_key=bytes(owner_key.verify_key)
    )
    return directory, owner_key


def _load_directory(
    home: Path,
    owner_public_key_hex: str | None,
    *,
    workspace_id: str | None = None,
) -> PrivateDirectory:
    owner_key_path = home / "owner.key"
    if owner_key_path.exists():
        owner_public = bytes(_load_signing_key(owner_key_path).verify_key)
    elif owner_public_key_hex:
        try:
            owner_public = bytes.fromhex(owner_public_key_hex)
        except ValueError as exc:
            raise ValueError(
                "owner public key must be 64 hexadecimal characters"
            ) from exc
    else:
        raise ValueError(
            "provide --owner-public-key-hex on a receiver without owner.key"
        )
    return PrivateDirectory(
        home / "private-directory.json",
        owner_public_key=owner_public,
        workspace_id=workspace_id,
    )


def _read_artifact(path: str | os.PathLike[str]) -> str:
    try:
        value = Path(path).expanduser().read_text(encoding="ascii").strip()
    except OSError as exc:
        raise PrivateDirectoryError(f"cannot read signed artifact: {exc}") from exc
    if not value:
        raise ValueError("signed artifact file is empty")
    return value


def _write_artifact(path: str | os.PathLike[str], token: str) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, token.encode("ascii") + b"\n")
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            target.unlink()
        except OSError:
            pass
        raise
    os.close(fd)


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        raise AuthorizationError(
            "owner approval requires an interactive local terminal"
        )
    return input(prompt + " [y/N] ").strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    """Run local owner/device setup commands without printing private keys."""
    parser = argparse.ArgumentParser(
        prog="python -m plugins.hub.dns.private_directory",
        description="Pair a new device and issue private, scoped Kollab conversation grants.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init-owner", help="create a local owner key and private directory"
    )
    init_parser.add_argument("--home", required=True)

    create_parser = subparsers.add_parser(
        "create-pairing", help="write a short-lived owner-signed challenge"
    )
    create_parser.add_argument("--home", required=True)
    create_parser.add_argument("--device-public-key-hex", required=True)
    create_parser.add_argument("--out", required=True)
    create_parser.add_argument(
        "--expires-in", type=int, default=DEFAULT_PAIRING_TTL_SECONDS
    )

    device_key_parser = subparsers.add_parser(
        "create-device-key",
        help="create a device-local key and display only its public key",
    )
    device_key_parser.add_argument("--home", required=True)

    register_parser = subparsers.add_parser(
        "register-challenge", help="install a public challenge on a receiver"
    )
    register_parser.add_argument("--home", required=True)
    register_parser.add_argument("--owner-public-key-hex", required=True)
    register_parser.add_argument("--challenge-file", required=True)

    prove_parser = subparsers.add_parser(
        "prove-pairing", help="create a distinct local device key and proof"
    )
    prove_parser.add_argument("--home", required=True)
    prove_parser.add_argument("--owner-public-key-hex", required=True)
    prove_parser.add_argument("--challenge-file", required=True)
    prove_parser.add_argument("--proof-out", required=True)

    record_parser = subparsers.add_parser(
        "record-proof", help="record a proof as pending, without enrolling it"
    )
    record_parser.add_argument("--home", required=True)
    record_parser.add_argument("--owner-public-key-hex")
    record_parser.add_argument("--challenge-file", required=True)
    record_parser.add_argument("--proof-file", required=True)

    pending_parser = subparsers.add_parser(
        "pending", help="list pending device proofs for local review"
    )
    pending_parser.add_argument("--home", required=True)
    pending_parser.add_argument("--owner-public-key-hex")

    export_parser = subparsers.add_parser(
        "export-proof", help="write a pending proof for private transfer to its owner"
    )
    export_parser.add_argument("--home", required=True)
    export_parser.add_argument("--owner-public-key-hex")
    export_parser.add_argument("--challenge-id", required=True)
    export_parser.add_argument("--out", required=True)

    approve_parser = subparsers.add_parser(
        "approve-pairing", help="locally approve one pending device"
    )
    approve_parser.add_argument("--home", required=True)
    approve_parser.add_argument("--challenge-id", required=True)
    approve_parser.add_argument("--credential-out", required=True)
    approve_parser.add_argument("--credential-ttl-days", type=int, default=30)

    members_parser = subparsers.add_parser(
        "list-members", help="list only active, nonrevoked members"
    )
    members_parser.add_argument("--home", required=True)
    members_parser.add_argument("--owner-public-key-hex")
    members_parser.add_argument("--workspace-id")

    import_parser = subparsers.add_parser(
        "import-member", help="install an owner-signed credential privately"
    )
    import_parser.add_argument("--home", required=True)
    import_parser.add_argument("--owner-public-key-hex", required=True)
    import_parser.add_argument("--workspace-id", required=True)
    import_parser.add_argument("--credential-file", required=True)

    grant_parser = subparsers.add_parser(
        "issue-grant", help="issue a human-approved scoped conversation grant"
    )
    grant_parser.add_argument("--home", required=True)
    grant_parser.add_argument("--credential-file", required=True)
    grant_parser.add_argument("--workspace-id", required=True)
    grant_parser.add_argument("--purpose", required=True)
    grant_parser.add_argument("--conversation-id", required=True)
    grant_parser.add_argument(
        "--expires-in", type=int, default=DEFAULT_GRANT_TTL_SECONDS
    )
    grant_parser.add_argument("--out", required=True)

    revoke_parser = subparsers.add_parser(
        "revoke", help="locally revoke a device, credential, or grant"
    )
    revoke_parser.add_argument("--home", required=True)
    revoke_parser.add_argument(
        "--target-type", choices=("device", "credential", "grant"), required=True
    )
    revoke_parser.add_argument("--target-id", required=True)
    revoke_parser.add_argument("--out", required=True)

    apply_revoke_parser = subparsers.add_parser(
        "apply-revocation", help="apply an owner-signed revocation locally"
    )
    apply_revoke_parser.add_argument("--home", required=True)
    apply_revoke_parser.add_argument("--owner-public-key-hex", required=True)
    apply_revoke_parser.add_argument("--workspace-id")
    apply_revoke_parser.add_argument("--revocation-file", required=True)

    args = parser.parse_args(argv)
    try:
        home = _private_home(args.home)
        if args.command == "init-owner":
            key_path = home / "owner.key"
            if key_path.exists() or (home / "private-directory.json").exists():
                raise PrivateDirectoryError(
                    "owner key or directory already exists; refusing to overwrite"
                )
            owner_key = SigningKey.generate()
            _save_signing_key(key_path, owner_key)
            directory = PrivateDirectory(
                home / "private-directory.json",
                owner_public_key=bytes(owner_key.verify_key),
            )
            print(
                json.dumps(
                    {
                        "owner_id": directory.owner_id,
                        "owner_public_key_hex": bytes(owner_key.verify_key).hex(),
                    }
                )
            )
            return 0
        if args.command == "create-pairing":
            directory, owner_key = _load_owner(home)
            try:
                expected_device_public_key = bytes.fromhex(args.device_public_key_hex)
            except ValueError as exc:
                raise ValueError(
                    "device public key must be 64 hexadecimal characters"
                ) from exc
            challenge = directory.begin_pairing(
                owner_key,
                expected_device_public_key=expected_device_public_key,
                expires_in_seconds=args.expires_in,
            )
            _write_artifact(args.out, challenge.token)
            print(
                json.dumps(
                    {
                        "challenge_id": challenge.challenge_id,
                        "expires_at": challenge.expires_at,
                        "saved_to": str(Path(args.out).expanduser().resolve()),
                    }
                )
            )
            return 0
        if args.command == "create-device-key":
            device_key_path = home / "device.key"
            if device_key_path.exists():
                raise PrivateDirectoryError(
                    "device.key already exists; refusing to replace this device identity"
                )
            device_key = new_device_signing_key()
            _save_signing_key(device_key_path, device_key)
            device_public = bytes(device_key.verify_key)
            print(
                json.dumps(
                    {
                        "device_id": public_key_id(device_public),
                        "device_public_key_hex": device_public.hex(),
                    }
                )
            )
            return 0
        if args.command == "register-challenge":
            directory = _load_directory(home, args.owner_public_key_hex)
            challenge = directory.register_pairing_challenge(
                _read_artifact(args.challenge_file)
            )
            print(
                json.dumps(
                    {
                        "challenge_id": challenge.challenge_id,
                        "expires_at": challenge.expires_at,
                    }
                )
            )
            return 0
        if args.command == "prove-pairing":
            try:
                owner_public = bytes.fromhex(args.owner_public_key_hex)
            except ValueError as exc:
                raise ValueError(
                    "owner public key must be 64 hexadecimal characters"
                ) from exc
            device_key_path = home / "device.key"
            if not device_key_path.exists():
                raise PrivateDirectoryError(
                    "run create-device-key first on this device; keys are never copied from the owner"
                )
            device_key = _load_signing_key(device_key_path)
            proof = prove_pairing(
                _read_artifact(args.challenge_file),
                device_key,
                owner_public_key=owner_public,
            )
            _write_artifact(args.proof_out, proof.token)
            print(
                json.dumps(
                    {
                        "device_id": proof.device_id,
                        "proof_saved_to": str(
                            Path(args.proof_out).expanduser().resolve()
                        ),
                    }
                )
            )
            return 0
        if args.command == "record-proof":
            directory = _load_directory(home, args.owner_public_key_hex)
            challenge = directory.register_pairing_challenge(
                _read_artifact(args.challenge_file)
            )
            proof = directory.record_pairing_proof(
                challenge, _read_artifact(args.proof_file)
            )
            print(
                json.dumps(
                    {
                        "challenge_id": challenge.challenge_id,
                        "device_id": proof.device_id,
                        "admitted": False,
                    }
                )
            )
            return 0
        if args.command == "pending":
            directory = _load_directory(home, args.owner_public_key_hex)
            print(
                json.dumps(
                    [
                        {
                            "challenge_id": item.challenge_id,
                            "device_id": item.device_id,
                            "public_key_hex": item.public_key.hex(),
                            "expires_at": item.expires_at,
                        }
                        for item in directory.pending_pairing_proofs()
                    ]
                )
            )
            return 0
        if args.command == "export-proof":
            directory = _load_directory(home, args.owner_public_key_hex)
            proof = directory.get_pending_pairing_proof(args.challenge_id)
            _write_artifact(args.out, proof.token)
            print(
                json.dumps(
                    {
                        "device_id": proof.device_id,
                        "proof_saved_to": str(Path(args.out).expanduser().resolve()),
                    }
                )
            )
            return 0
        if args.command == "approve-pairing":
            directory, owner_key = _load_owner(home)
            challenge = directory.get_pairing_challenge(args.challenge_id)
            proof = directory.get_pending_pairing_proof(args.challenge_id)
            approved = _confirm(
                f"Approve device {proof.device_id} for private-directory membership until credential expiry?"
            )
            credential = directory.approve_pairing(
                challenge,
                None,
                owner_key,
                approved_by_human=approved,
                credential_ttl_seconds=args.credential_ttl_days * 24 * 60 * 60,
            )
            _write_artifact(args.credential_out, credential.token)
            print(
                json.dumps(
                    {
                        "device_id": credential.device_id,
                        "credential_expires_at": credential.expires_at,
                        "credential_saved_to": str(
                            Path(args.credential_out).expanduser().resolve()
                        ),
                    }
                )
            )
            return 0
        if args.command == "list-members":
            directory = _load_directory(
                home, args.owner_public_key_hex, workspace_id=args.workspace_id
            )
            print(
                json.dumps(
                    [
                        {
                            "device_id": member.device_id,
                            "credential_id": member.credential_id,
                            "expires_at": member.expires_at,
                        }
                        for member in directory.members()
                    ]
                )
            )
            return 0
        if args.command == "import-member":
            directory = _load_directory(
                home, args.owner_public_key_hex, workspace_id=args.workspace_id
            )
            member = directory.import_device_credential(
                _read_artifact(args.credential_file)
            )
            print(
                json.dumps(
                    {
                        "device_id": member.device_id,
                        "credential_id": member.credential_id,
                        "expires_at": member.expires_at,
                    }
                )
            )
            return 0
        if args.command == "issue-grant":
            directory, owner_key = _load_owner(home)
            credential = _read_artifact(args.credential_file)
            purpose = _validate_purpose(args.purpose)
            workspace_id = _validate_workspace_id(args.workspace_id)
            conversation_id = _validate_opaque_id(
                args.conversation_id, "conversation_id"
            )
            approved = _confirm(
                "Allow this member to use "
                f"{purpose} in conversation {conversation_id} at workspace {workspace_id} "
                f"for {args.expires_in} seconds?"
            )
            grant = directory.issue_conversation_grant(
                credential,
                owner_key,
                recipient_workspace_id=workspace_id,
                purpose=purpose,
                conversation_id=conversation_id,
                approved_by_human=approved,
                expires_in_seconds=args.expires_in,
            )
            _write_artifact(args.out, grant.token)
            print(
                json.dumps(
                    {
                        "grant_id": grant.grant_id,
                        "expires_at": grant.expires_at,
                        "grant_saved_to": str(Path(args.out).expanduser().resolve()),
                    }
                )
            )
            return 0
        if args.command == "revoke":
            directory, owner_key = _load_owner(home)
            approved = _confirm(
                f"Revoke {args.target_type} {args.target_id}? "
                "This blocks it after recipients receive the signed update."
            )
            if not approved:
                raise AuthorizationError("revocation cancelled")
            revocation = directory.revoke(args.target_type, args.target_id, owner_key)
            _write_artifact(args.out, revocation.token)
            print(
                json.dumps(
                    {
                        "revocation_id": revocation.revocation_id,
                        "target_type": revocation.target_type,
                        "target_id": revocation.target_id,
                        "saved_to": str(Path(args.out).expanduser().resolve()),
                    }
                )
            )
            return 0
        if args.command == "apply-revocation":
            directory = _load_directory(
                home, args.owner_public_key_hex, workspace_id=args.workspace_id
            )
            revocation = directory.apply_revocation(
                _read_artifact(args.revocation_file)
            )
            print(
                json.dumps(
                    {
                        "revocation_id": revocation.revocation_id,
                        "target_type": revocation.target_type,
                        "target_id": revocation.target_id,
                    }
                )
            )
            return 0
    except (PrivateDirectoryError, OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke tests
    raise SystemExit(main())
