"""Durable, bounded local authorization for delegated device enrollment.

The store contains authorization metadata only. Enrollment codes, private keys,
tokens, provider credentials, and workspace paths have no fields in its schema.
Reconciliation rows contain public keys and signed digests only. Mutations use a
private lock file and atomic replacement so concurrent approval workers cannot
exceed a delegation's device allowance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

try:  # pragma: no cover - Windows uses msvcrt below
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - only imported on Windows
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]

STATE_VERSION = 4
DEFAULT_DELEGATION_TTL_SECONDS = 10 * 60
MAX_DELEGATION_TTL_SECONDS = 15 * 60
MAX_NEW_DEVICES = 256
MAX_DELEGATIONS = 1024
MAX_CONSUMED_ENROLLMENTS = 256
MAX_ENROLLMENT_REQUESTS = 1024
MAX_NETWORK_IDS = 64
MAX_CREDENTIAL_CATEGORIES = 64
MAX_STATE_BYTES = 1024 * 1024

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_SCOPE_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_WORKSPACE_ID = re.compile(r"[0-9a-f]{32}\Z")
_PUBLIC_KEY = re.compile(r"[0-9a-f]{64}\Z")
_INSTALL_RECEIPT_DOMAIN = b"kollab-relay-enrollment-install-receipt/1\0"
_ENROLLMENT_MESSAGE_DOMAIN = b"kollab-relay-enrollment-message/1\0"
_INSTALL_RECEIPT_FIELDS = frozenset(
    {
        "v",
        "offer_id",
        "round_id",
        "phase",
        "destination_key",
        "workspace_id",
        "status",
        "revision",
        "digest",
    }
)


def sign_installation_receipt(signing_key: SigningKey, payload: dict[str, Any]) -> dict[str, Any]:
    """Sign the non-secret receipt independently of its encrypted relay frame."""
    if not isinstance(payload, dict) or set(payload) != _INSTALL_RECEIPT_FIELDS:
        raise ValueError("installation receipt has an invalid shape")
    signature = signing_key.sign(
        _INSTALL_RECEIPT_DOMAIN
        + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).signature.hex()
    return {**payload, "device_signature": signature}


def verify_installation_receipt(public_key_hex: str, payload: dict[str, Any]) -> bool:
    """Verify a persisted receipt against its independently bound device key."""
    if (
        not isinstance(public_key_hex, str)
        or not _PUBLIC_KEY.fullmatch(public_key_hex)
        or not isinstance(payload, dict)
        or not payload.keys() >= _INSTALL_RECEIPT_FIELDS | {"device_signature"}
        or not isinstance(payload.get("device_signature"), str)
        or not re.fullmatch(r"[0-9a-f]{128}", payload["device_signature"])
    ):
        return False
    signed = {key: value for key, value in payload.items() if key != "device_signature"}
    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(
            _INSTALL_RECEIPT_DOMAIN
            + json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"),
            bytes.fromhex(payload["device_signature"]),
        )
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def _delivery_intent_payload(request: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "v": 1,
        "offer_id": intent["offer_id"],
        "round_id": request["enrollment_id"],
        "phase": "installation_intent",
        "destination_key": intent["destination_public_key"],
        "workspace_id": request["scope"]["workspace_id"],
        "issuer_workspace_id": intent["issuer_workspace_id"],
        "expected_digest": intent["expected_digest"],
        "issuer_relay_key": intent["issuer_relay_key"],
        "issuer_origin": intent["issuer_origin"],
        "room_fingerprint": intent["room_fingerprint"],
        "owner_public_key": intent["owner_public_key"],
        "scope_fingerprint": request["scope_fingerprint"],
    }


def _legacy_delivery_intent_payload(request: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    """Verify v3 history without granting it the v4 workspace recovery binding."""
    return {
        "v": 1,
        "offer_id": intent["offer_id"],
        "round_id": request["enrollment_id"],
        "phase": "installation_intent",
        "destination_key": intent["destination_public_key"],
        "workspace_id": request["scope"]["workspace_id"],
        "expected_digest": intent["expected_digest"],
        "issuer_relay_key": intent["issuer_relay_key"],
        "issuer_origin": intent["issuer_origin"],
        "room_fingerprint": intent["room_fingerprint"],
        "owner_public_key": intent["owner_public_key"],
        "scope_fingerprint": request["scope_fingerprint"],
    }


def _verify_owner_signature(public_key_hex: str, payload: dict[str, Any], signature: str) -> bool:
    if (
        not isinstance(public_key_hex, str)
        or not _PUBLIC_KEY.fullmatch(public_key_hex)
        or not isinstance(signature, str)
        or not re.fullmatch(r"[0-9a-f]{128}", signature)
    ):
        return False
    message = _ENROLLMENT_MESSAGE_DOMAIN + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(message, bytes.fromhex(signature))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def _validated_reconciliation_context(
    *,
    owner_public_key: str,
    issuer_relay_key: str,
    issuer_origin: str,
    room_fingerprint: str,
    issuer_workspace_id: str,
) -> dict[str, str]:
    if (
        not isinstance(owner_public_key, str)
        or not _PUBLIC_KEY.fullmatch(owner_public_key)
        or not isinstance(issuer_relay_key, str)
        or not _PUBLIC_KEY.fullmatch(issuer_relay_key)
        or not isinstance(room_fingerprint, str)
        or not _SCOPE_DIGEST.fullmatch(room_fingerprint)
        or not isinstance(issuer_workspace_id, str)
        or not _WORKSPACE_ID.fullmatch(issuer_workspace_id)
        or not isinstance(issuer_origin, str)
        or len(issuer_origin) > 512
    ):
        raise DelegationAuthorizationError("reconciliation context is invalid")
    try:
        origin = urlsplit(issuer_origin)
    except ValueError as exc:
        raise DelegationAuthorizationError("reconciliation origin is invalid") from exc
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username is not None
        or origin.password is not None
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
    ):
        raise DelegationAuthorizationError("reconciliation origin is invalid")
    return {
        "owner_public_key": owner_public_key,
        "issuer_relay_key": issuer_relay_key,
        "issuer_origin": issuer_origin,
        "room_fingerprint": room_fingerprint,
        "issuer_workspace_id": issuer_workspace_id,
    }


class EnrollmentDelegationError(ValueError):
    """Base error for invalid or unavailable enrollment authorization."""


class DelegationAuthorizationError(EnrollmentDelegationError):
    """A delegation does not authorize the requested enrollment."""


class DelegationSessionChangedError(DelegationAuthorizationError):
    """The authorized agent's relay session ended; a reconnect cannot decide."""


class DelegationCapacityError(EnrollmentDelegationError):
    """A bounded delegation or state ledger is full."""


class DelegationNotFoundError(EnrollmentDelegationError):
    """The requested human authorization action is not present."""


class DelegationPersistenceError(EnrollmentDelegationError):
    """The private state file is unavailable, unsafe, or malformed."""


@dataclass(frozen=True, slots=True)
class EnrollmentDelegation:
    """Non-secret snapshot of one human-authorized enrollment delegation."""

    human_action_id: str
    authorized_agent_id: str
    authorized_session_id: str
    issuer: str
    network_ids: tuple[str, ...]
    configuration_profile: str | None
    credential_categories: tuple[str, ...]
    max_new_devices: int
    expires_at: int
    revoked: bool
    consumed_new_devices: int

    @property
    def remaining_new_devices(self) -> int:
        return self.max_new_devices - self.consumed_new_devices


@dataclass(frozen=True, slots=True)
class EnrollmentRequestRecord:
    """Durable, non-secret request and exact-receipt metadata."""

    enrollment_id: str
    human_action_id: str
    device_key_fingerprint: str
    expires_at: int
    status: str
    scope_fingerprint: str
    authorized_agent_id: str
    authorized_session_id: str
    issuer: str
    network_ids: tuple[str, ...]
    configuration_profile: str | None
    credential_categories: tuple[str, ...]
    max_new_devices: int
    consumed_new_devices: int
    workspace_id: str | None = None
    issuer_workspace_id: str | None = None
    installation_status: str | None = None
    installation_revision: int | None = None
    installation_digest: str | None = None
    expected_install_digest: str | None = None
    destination_public_key: str | None = None
    offer_id: str | None = None
    issuer_relay_key: str | None = None
    issuer_origin: str | None = None
    room_fingerprint: str | None = None
    owner_public_key: str | None = None
    installation_signature: str | None = None
    delivery_signature: str | None = None
    peer_approved: bool = False

    @property
    def remaining_new_devices(self) -> int:
        return self.max_new_devices - self.consumed_new_devices


@dataclass(frozen=True, slots=True)
class EnrollmentRequestRecoveryState:
    """Durable local request/delegation snapshot for issuer cleanup only."""

    request: EnrollmentRequestRecord
    delegation: EnrollmentDelegation


class EnrollmentDelegationStore:
    """Store and atomically consume local enrollment delegations.

    ``state_path`` must be beneath a private directory owned by the current
    user. The directory and file modes are enforced as 0700 and 0600 on POSIX.
    """

    def __init__(self, state_path: str | os.PathLike[str]) -> None:
        requested = Path(state_path).expanduser()
        if requested.name in {"", ".", ".."}:
            raise ValueError("state_path must name a file")
        self._path = requested.parent.resolve() / requested.name
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._thread_lock = threading.RLock()

        with self._locked_file():
            try:
                self._read_state_unlocked()
            except FileNotFoundError:
                self._write_state_unlocked(self._empty_state())

    def create(
        self,
        *,
        human_action_id: str,
        authorized_agent_id: str,
        authorized_session_id: str,
        issuer: str,
        network_ids: Iterable[str],
        configuration_profile: str | None,
        credential_categories: Iterable[str] = (),
        max_new_devices: int,
        expires_at: int | None = None,
        now: int | None = None,
    ) -> EnrollmentDelegation:
        """Persist a human-authorized delegation, defaulting to a ten-minute TTL."""
        action = _identifier(human_action_id, "human_action_id")
        agent = _identifier(authorized_agent_id, "authorized_agent_id")
        session = _identifier(authorized_session_id, "authorized_session_id")
        issuer_value = _identifier(issuer, "issuer")
        networks = _identifier_list(network_ids, "network_ids", minimum=1, maximum=MAX_NETWORK_IDS)
        profile = _optional_identifier(configuration_profile, "configuration_profile")
        categories = _identifier_list(
            credential_categories,
            "credential_categories",
            minimum=0,
            maximum=MAX_CREDENTIAL_CATEGORIES,
        )
        device_limit = _bounded_int(max_new_devices, "max_new_devices", minimum=1, maximum=MAX_NEW_DEVICES)

        with self._locked_file():
            state = self._read_state_unlocked()
            timestamp = _now(now)
            expiry = (
                timestamp + DEFAULT_DELEGATION_TTL_SECONDS
                if expires_at is None
                else _bounded_int(expires_at, "expires_at", minimum=1)
            )
            if expiry <= timestamp:
                raise DelegationAuthorizationError("delegation expiry must be in the future")
            if expiry > timestamp + MAX_DELEGATION_TTL_SECONDS:
                raise DelegationAuthorizationError(
                    f"delegation expiry must be within {MAX_DELEGATION_TTL_SECONDS} seconds"
                )
            delegations = state["delegations"]
            if action in delegations:
                raise DelegationAuthorizationError("human action ID already has a delegation")
            if len(delegations) >= MAX_DELEGATIONS:
                raise DelegationCapacityError("delegation store is full")

            record = {
                "human_action_id": action,
                "authorized_agent_id": agent,
                "authorized_session_id": session,
                "issuer": issuer_value,
                "network_ids": list(networks),
                "configuration_profile": profile,
                "credential_categories": list(categories),
                "max_new_devices": device_limit,
                "expires_at": expiry,
                "revoked": False,
                "consumed_new_devices": 0,
                "consumed_enrollments": {},
            }
            delegations[action] = record
            self._write_state_unlocked(state)
            return _to_model(record)

    def get(self, human_action_id: str) -> EnrollmentDelegation | None:
        """Return a read-only snapshot without implying that it is active."""
        action = _identifier(human_action_id, "human_action_id")
        with self._locked_file():
            state = self._read_state_unlocked()
            record = state["delegations"].get(action)
            return None if record is None else _to_model(record)

    def check_eligible(
        self,
        human_action_id: str,
        *,
        agent_id: str,
        session_id: str,
        issuer: str,
        network_ids: Iterable[str],
        configuration_profile: str | None = None,
        credential_categories: Iterable[str] = (),
        now: int | None = None,
    ) -> EnrollmentDelegation:
        """Check offer-time eligibility without consuming a device allowance."""
        request = self._normalize_request(
            agent_id=agent_id,
            session_id=session_id,
            issuer=issuer,
            network_ids=network_ids,
            configuration_profile=configuration_profile,
            credential_categories=credential_categories,
        )
        with self._locked_file():
            state = self._read_state_unlocked()
            record = self._require_delegation(state, human_action_id)
            self._check_active(record, request, _now(now))
            if record["consumed_new_devices"] >= record["max_new_devices"]:
                raise DelegationCapacityError("delegation has no device allowance remaining")
            return _to_model(record)

    def record_pending(
        self,
        human_action_id: str,
        *,
        enrollment_id: str,
        device_key_fingerprint: str,
        agent_id: str,
        session_id: str,
        issuer: str,
        network_ids: Iterable[str],
        configuration_profile: str | None = None,
        credential_categories: Iterable[str] = (),
        workspace_id: str | None = None,
        expires_at: int,
        now: int | None = None,
    ) -> EnrollmentRequestRecord:
        """Persist a verified proof request without approving or consuming it."""
        enrollment = _identifier(enrollment_id, "enrollment_id")
        device_fingerprint = _fingerprint(device_key_fingerprint)
        request = self._normalize_request(
            agent_id=agent_id,
            session_id=session_id,
            issuer=issuer,
            network_ids=network_ids,
            configuration_profile=configuration_profile,
            credential_categories=credential_categories,
            device_key_fingerprint=device_fingerprint,
            workspace_id=workspace_id,
        )
        scope_fingerprint = _request_fingerprint(request)
        timestamp = _now(now)
        request_expiry = _bounded_int(expires_at, "expires_at", minimum=1)
        if request_expiry <= timestamp:
            raise DelegationAuthorizationError("enrollment request has expired")

        with self._locked_file():
            state = self._read_state_unlocked()
            action = _identifier(human_action_id, "human_action_id")
            delegation = self._require_delegation(state, action)
            self._check_active(delegation, request, timestamp)
            if request_expiry > delegation["expires_at"]:
                raise DelegationAuthorizationError("enrollment request outlives its delegation")
            records = state["enrollment_requests"]
            previous = records.get(enrollment)
            if previous is not None:
                if (
                    previous["human_action_id"] != action
                    or previous["device_key_fingerprint"] != device_fingerprint
                    or previous["scope_fingerprint"] != scope_fingerprint
                    or previous["expires_at"] != request_expiry
                ):
                    raise DelegationAuthorizationError("enrollment ID is already bound to a different request")
                return _to_request_model(previous, delegation)

            if delegation["consumed_new_devices"] >= delegation["max_new_devices"]:
                raise DelegationCapacityError("delegation has no device allowance remaining")

            if len(records) >= MAX_ENROLLMENT_REQUESTS:
                raise DelegationCapacityError("enrollment request ledger is full")
            record = {
                "enrollment_id": enrollment,
                "human_action_id": action,
                "device_key_fingerprint": device_fingerprint,
                "expires_at": request_expiry,
                "status": "pending",
                "scope": request,
                "scope_fingerprint": scope_fingerprint,
                "decided_at": None,
                "installation_receipt": None,
                "delivery_intent": None,
            }
            records[enrollment] = record
            self._write_state_unlocked(state)
            return _to_request_model(record, delegation)

    def pending_requests(
        self,
        *,
        agent_id: str,
        session_id: str,
        now: int | None = None,
    ) -> tuple[EnrollmentRequestRecord, ...]:
        """List active proof requests for exactly one authorized agent session."""
        agent = _identifier(agent_id, "agent_id")
        session = _identifier(session_id, "session_id")
        timestamp = _now(now)
        with self._locked_file():
            state = self._read_state_unlocked()
            pending: list[EnrollmentRequestRecord] = []
            for request in state["enrollment_requests"].values():
                if request["status"] != "pending" or request["expires_at"] <= timestamp:
                    continue
                delegation = state["delegations"].get(request["human_action_id"])
                if (
                    delegation is None
                    or delegation["revoked"]
                    or delegation["expires_at"] <= timestamp
                    or delegation["authorized_agent_id"] != agent
                    or delegation["authorized_session_id"] != session
                ):
                    continue
                pending.append(_to_request_model(request, delegation))
        return tuple(sorted(pending, key=lambda item: (item.expires_at, item.enrollment_id)))

    def get_enrollment_request(
        self,
        enrollment_id: str,
        *,
        agent_id: str,
        session_id: str,
        now: int | None = None,
    ) -> EnrollmentRequestRecord:
        """Return one request only to its exact active delegated session."""
        enrollment = _identifier(enrollment_id, "enrollment_id")
        agent = _identifier(agent_id, "agent_id")
        session = _identifier(session_id, "session_id")
        timestamp = _now(now)
        with self._locked_file():
            state = self._read_state_unlocked()
            request = state["enrollment_requests"].get(enrollment)
            if request is None:
                raise DelegationNotFoundError("enrollment request not found")
            delegation = state["delegations"].get(request["human_action_id"])
            if delegation is None or delegation["authorized_agent_id"] != agent:
                raise DelegationAuthorizationError("agent session is not authorized for this enrollment")
            if delegation["authorized_session_id"] != session:
                # Same agent, new relay session: a relay restart or reconnect
                # ended the session this code was issued under.
                raise DelegationSessionChangedError("the relay session for this enrollment has ended")
            if delegation["revoked"] or delegation["expires_at"] <= timestamp or request["expires_at"] <= timestamp:
                raise DelegationAuthorizationError("enrollment request is no longer active")
            return _to_request_model(request, delegation)

    def get_recovery_state(
        self,
        enrollment_id: str,
        *,
        human_action_id: str,
    ) -> EnrollmentRequestRecoveryState | None:
        """Read exact durable state for local recovery cleanup, even if inactive.

        This does not authorize an enrollment. It exposes the revoked/expired
        state only to the local issuer so cleanup can distinguish an incomplete
        request from one with a durable installation receipt.
        """
        enrollment = _identifier(enrollment_id, "enrollment_id")
        action = _identifier(human_action_id, "human_action_id")
        with self._locked_file():
            state = self._read_state_unlocked()
            request = state["enrollment_requests"].get(enrollment)
            delegation = state["delegations"].get(action)
            if (
                request is None
                or delegation is None
                or request["human_action_id"] != action
            ):
                return None
            intent = request.get("delivery_intent")
            if intent is not None and not _verify_owner_signature(
                intent["owner_public_key"],
                _delivery_intent_payload(request, intent),
                intent["owner_signature"],
            ):
                raise DelegationPersistenceError(
                    "recovery delivery intent signature is invalid"
                )
            return EnrollmentRequestRecoveryState(
                request=_to_request_model(request, delegation),
                delegation=_to_model(delegation),
            )

    def consume(
        self,
        human_action_id: str,
        *,
        enrollment_id: str,
        device_key_fingerprint: str,
        agent_id: str,
        session_id: str,
        issuer: str,
        network_ids: Iterable[str],
        configuration_profile: str | None = None,
        credential_categories: Iterable[str] = (),
        workspace_id: str | None = None,
        now: int | None = None,
    ) -> EnrollmentDelegation:
        """Atomically consume one approval allowance, idempotently by enrollment.

        The first approved use persists a non-secret scope fingerprint keyed by
        ``enrollment_id``. Retries with the same identity and scope return the
        already-consumed snapshot; reusing the ID with different scope fails.
        """
        enrollment = _identifier(enrollment_id, "enrollment_id")
        device_fingerprint = _fingerprint(device_key_fingerprint)
        request = self._normalize_request(
            agent_id=agent_id,
            session_id=session_id,
            issuer=issuer,
            network_ids=network_ids,
            configuration_profile=configuration_profile,
            credential_categories=credential_categories,
            device_key_fingerprint=device_fingerprint,
            workspace_id=workspace_id,
        )
        fingerprint = _request_fingerprint(request)

        with self._locked_file():
            state = self._read_state_unlocked()
            record = self._require_delegation(state, human_action_id)
            timestamp = _now(now)
            self._check_active(record, request, timestamp)
            consumed = record["consumed_enrollments"]
            previous = consumed.get(enrollment)
            if previous is not None and previous != fingerprint:
                raise DelegationAuthorizationError("enrollment ID was already used with a different scope")
            pending = state["enrollment_requests"].get(enrollment)
            if previous is not None:
                if (
                    pending is None
                    or pending["human_action_id"] != record["human_action_id"]
                    or pending["scope_fingerprint"] != fingerprint
                    or pending["status"] != "approved"
                ):
                    raise DelegationPersistenceError("consumed enrollment has no matching approval decision")
                return _to_model(record)
            if (
                pending is None
                or pending["human_action_id"] != record["human_action_id"]
                or pending["device_key_fingerprint"] != device_fingerprint
                or pending["scope"] != request
                or pending["scope_fingerprint"] != fingerprint
            ):
                raise DelegationAuthorizationError("enrollment proof is not pending under this scope")
            if pending["status"] != "pending":
                raise DelegationAuthorizationError("enrollment request is no longer pending")
            if pending["expires_at"] <= timestamp:
                raise DelegationAuthorizationError("enrollment request has expired")

            if record["consumed_new_devices"] >= record["max_new_devices"]:
                raise DelegationCapacityError("delegation has no device allowance remaining")
            if len(consumed) >= MAX_CONSUMED_ENROLLMENTS:
                raise DelegationCapacityError("delegation enrollment ledger is full")
            if _total_consumed(state) >= MAX_DELEGATIONS * MAX_CONSUMED_ENROLLMENTS:
                raise DelegationCapacityError("consumed enrollment ledger is full")

            consumed[enrollment] = fingerprint
            record["consumed_new_devices"] += 1
            pending["status"] = "approved"
            pending["decided_at"] = timestamp
            self._write_state_unlocked(state)
            return _to_model(record)

    def reject_pending(
        self,
        human_action_id: str,
        *,
        enrollment_id: str,
        device_key_fingerprint: str,
        agent_id: str,
        session_id: str,
        issuer: str,
        network_ids: Iterable[str],
        configuration_profile: str | None = None,
        credential_categories: Iterable[str] = (),
        workspace_id: str | None = None,
        now: int | None = None,
    ) -> EnrollmentRequestRecord:
        """Persist an explicit rejection without consuming device allowance."""
        enrollment = _identifier(enrollment_id, "enrollment_id")
        device_fingerprint = _fingerprint(device_key_fingerprint)
        request = self._normalize_request(
            agent_id=agent_id,
            session_id=session_id,
            issuer=issuer,
            network_ids=network_ids,
            configuration_profile=configuration_profile,
            credential_categories=credential_categories,
            device_key_fingerprint=device_fingerprint,
            workspace_id=workspace_id,
        )
        fingerprint = _request_fingerprint(request)
        timestamp = _now(now)
        with self._locked_file():
            state = self._read_state_unlocked()
            delegation = self._require_delegation(state, human_action_id)
            self._check_active(delegation, request, timestamp)
            pending = state["enrollment_requests"].get(enrollment)
            if (
                pending is None
                or pending["human_action_id"] != delegation["human_action_id"]
                or pending["device_key_fingerprint"] != device_fingerprint
                or pending["scope"] != request
                or pending["scope_fingerprint"] != fingerprint
            ):
                raise DelegationAuthorizationError("enrollment proof is not pending under this scope")
            if pending["status"] == "rejected":
                return _to_request_model(pending, delegation)
            if pending["status"] != "pending":
                raise DelegationAuthorizationError("enrollment request already has a different decision")
            if pending["expires_at"] <= timestamp:
                raise DelegationAuthorizationError("enrollment request has expired")
            pending["status"] = "rejected"
            pending["decided_at"] = timestamp
            self._write_state_unlocked(state)
            return _to_request_model(pending, delegation)

    def record_delivery_intent(
        self,
        enrollment_id: str,
        *,
        agent_id: str,
        session_id: str,
        offer_id: str,
        destination_public_key: str,
        expected_digest: str,
        issuer_relay_key: str,
        issuer_origin: str,
        room_fingerprint: str,
        issuer_workspace_id: str,
        owner_public_key: str,
        owner_signature: str,
        now: int | None = None,
    ) -> EnrollmentRequestRecord:
        """Persist only public, exact-scope data before sending an install bundle."""
        enrollment = _identifier(enrollment_id, "enrollment_id")
        agent = _identifier(agent_id, "agent_id")
        session = _identifier(session_id, "session_id")
        if not isinstance(offer_id, str) or not re.fullmatch(r"[0-9a-f]{32}", offer_id):
            raise DelegationAuthorizationError("offer ID is invalid")
        if not isinstance(destination_public_key, str) or not _PUBLIC_KEY.fullmatch(destination_public_key):
            raise DelegationAuthorizationError("device public key is invalid")
        if (
            not isinstance(expected_digest, str)
            or not _SCOPE_DIGEST.fullmatch(expected_digest)
            or not isinstance(issuer_relay_key, str)
            or not _PUBLIC_KEY.fullmatch(issuer_relay_key)
            or not isinstance(room_fingerprint, str)
            or not _SCOPE_DIGEST.fullmatch(room_fingerprint)
            or not isinstance(issuer_workspace_id, str)
            or not _WORKSPACE_ID.fullmatch(issuer_workspace_id)
            or not isinstance(owner_public_key, str)
            or not _PUBLIC_KEY.fullmatch(owner_public_key)
            or not isinstance(owner_signature, str)
            or not re.fullmatch(r"[0-9a-f]{128}", owner_signature)
            or not isinstance(issuer_origin, str)
            or len(issuer_origin) > 512
        ):
            raise DelegationAuthorizationError("delivery scope is invalid")
        try:
            origin = urlsplit(issuer_origin)
        except ValueError as exc:
            raise DelegationAuthorizationError("delivery origin is invalid") from exc
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise DelegationAuthorizationError("delivery origin is invalid")
        intent = {
            "offer_id": offer_id,
            "destination_public_key": destination_public_key,
            "expected_digest": expected_digest,
            "issuer_relay_key": issuer_relay_key,
            "issuer_origin": issuer_origin,
            "room_fingerprint": room_fingerprint,
            "issuer_workspace_id": issuer_workspace_id,
            "owner_public_key": owner_public_key,
            "owner_signature": owner_signature,
        }
        timestamp = _now(now)
        with self._locked_file():
            state = self._read_state_unlocked()
            request = state["enrollment_requests"].get(enrollment)
            if request is None:
                raise DelegationNotFoundError("enrollment request not found")
            delegation = state["delegations"].get(request["human_action_id"])
            if (
                delegation is None
                or delegation["revoked"]
                or delegation["expires_at"] <= timestamp
                or request["expires_at"] <= timestamp
                or request["status"] != "approved"
                or request["scope"]["agent_id"] != agent
                or request["scope"]["session_id"] != session
                or request["scope"]["workspace_id"] is None
                or _device_key_fingerprint(destination_public_key) != request["device_key_fingerprint"]
            ):
                raise DelegationAuthorizationError("delivery intent is outside its approved scope")
            signed_intent = _delivery_intent_payload(request, intent)
            if not _verify_owner_signature(owner_public_key, signed_intent, owner_signature):
                raise DelegationAuthorizationError("delivery intent signature is invalid")
            previous = request.get("delivery_intent")
            if previous is not None and previous != intent:
                raise DelegationAuthorizationError("enrollment already has a different delivery intent")
            request["delivery_intent"] = intent
            self._write_state_unlocked(state)
            return _to_request_model(request, delegation)

    def record_install_ack(
        self,
        enrollment_id: str,
        *,
        agent_id: str,
        session_id: str,
        device_key_fingerprint: str,
        workspace_id: str,
        install_status: str,
        revision: int,
        digest: str,
        offer_id: str,
        device_signature: str,
        now: int | None = None,
    ) -> EnrollmentRequestRecord:
        """Persist a device-signed receipt matching the issued digest and scope."""
        enrollment = _identifier(enrollment_id, "enrollment_id")
        agent = _identifier(agent_id, "agent_id")
        session = _identifier(session_id, "session_id")
        fingerprint = _fingerprint(device_key_fingerprint)
        if not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(workspace_id):
            raise DelegationAuthorizationError("workspace ID is invalid")
        if install_status not in {"installed", "already_installed"}:
            raise DelegationAuthorizationError("installation receipt is invalid")
        revision_value = _bounded_int(revision, "revision", minimum=1)
        if not isinstance(digest, str) or not _SCOPE_DIGEST.fullmatch(digest):
            raise DelegationAuthorizationError("installation digest is invalid")
        if not isinstance(offer_id, str) or not re.fullmatch(r"[0-9a-f]{32}", offer_id):
            raise DelegationAuthorizationError("offer ID is invalid")
        if not isinstance(device_signature, str) or not re.fullmatch(r"[0-9a-f]{128}", device_signature):
            raise DelegationAuthorizationError("installation signature is invalid")
        timestamp = _now(now)

        with self._locked_file():
            state = self._read_state_unlocked()
            request = state["enrollment_requests"].get(enrollment)
            if request is None:
                raise DelegationNotFoundError("enrollment request not found")
            delegation = state["delegations"].get(request["human_action_id"])
            if (
                delegation is None
                or delegation["revoked"]
                or delegation["expires_at"] <= timestamp
                or request["expires_at"] <= timestamp
                or request["status"] != "approved"
                or request["device_key_fingerprint"] != fingerprint
                or request["scope"].get("workspace_id") != workspace_id
                or delegation["authorized_agent_id"] != agent
                or delegation["authorized_session_id"] != session
            ):
                raise DelegationAuthorizationError("installation receipt is outside its approved scope")
            intent = request.get("delivery_intent")
            if (
                not isinstance(intent, dict)
                or intent["offer_id"] != offer_id
                or intent["expected_digest"] != digest
                or _device_key_fingerprint(intent["destination_public_key"]) != fingerprint
            ):
                raise DelegationAuthorizationError("installation receipt does not match the issued payload")
            signed_payload = {
                "v": 1,
                "offer_id": offer_id,
                "round_id": enrollment,
                "phase": "installation_ack",
                "destination_key": intent["destination_public_key"],
                "workspace_id": workspace_id,
                "status": install_status,
                "revision": revision_value,
                "digest": digest,
                "device_signature": device_signature,
            }
            if not verify_installation_receipt(intent["destination_public_key"], signed_payload):
                raise DelegationAuthorizationError("installation receipt signature is invalid")
            receipt = {
                "status": install_status,
                "workspace_id": workspace_id,
                "revision": revision_value,
                "digest": digest,
                "received_at": timestamp,
                "device_signature": device_signature,
                "peer_approved_at": None,
            }
            previous = request.get("installation_receipt")
            if previous is not None:
                if any(
                    previous[field] != receipt[field]
                    for field in (
                        "status",
                        "workspace_id",
                        "revision",
                        "digest",
                        "device_signature",
                    )
                ):
                    raise DelegationAuthorizationError("installation receipt conflicts with an existing receipt")
                return _to_request_model(request, delegation)
            request["installation_receipt"] = receipt
            self._write_state_unlocked(state)
            return _to_request_model(request, delegation)

    def install_receipts_for_reconciliation(
        self,
        *,
        owner_public_key: str,
        issuer_relay_key: str,
        issuer_origin: str,
        room_fingerprint: str,
        issuer_workspace_id: str,
    ) -> tuple[EnrollmentRequestRecord, ...]:
        """Find exact signed receipts that still need local peer approval."""
        context = _validated_reconciliation_context(
            owner_public_key=owner_public_key,
            issuer_relay_key=issuer_relay_key,
            issuer_origin=issuer_origin,
            room_fingerprint=room_fingerprint,
            issuer_workspace_id=issuer_workspace_id,
        )
        with self._locked_file():
            state = self._read_state_unlocked()
            pending: list[EnrollmentRequestRecord] = []
            for request in state["enrollment_requests"].values():
                delegation = state["delegations"].get(request["human_action_id"])
                receipt = request.get("installation_receipt")
                intent = request.get("delivery_intent")
                if (
                    request["status"] != "approved"
                    or delegation is None
                    or delegation["revoked"]
                    or not isinstance(intent, dict)
                    or any(intent.get(key) != value for key, value in context.items())
                    or not isinstance(receipt, dict)
                    or "device_signature" not in receipt
                    or receipt.get("peer_approved_at") is not None
                ):
                    continue
                pending.append(_to_request_model(request, delegation))
        return tuple(sorted(pending, key=lambda item: item.enrollment_id))

    def mark_peer_approved(
        self,
        enrollment_id: str,
        *,
        destination_public_key: str,
        owner_public_key: str,
        issuer_relay_key: str,
        issuer_origin: str,
        room_fingerprint: str,
        issuer_workspace_id: str,
        now: int | None = None,
    ) -> EnrollmentRequestRecord:
        """Commit reconciliation after the exact receipt's peer key is approved."""
        enrollment = _identifier(enrollment_id, "enrollment_id")
        context = _validated_reconciliation_context(
            owner_public_key=owner_public_key,
            issuer_relay_key=issuer_relay_key,
            issuer_origin=issuer_origin,
            room_fingerprint=room_fingerprint,
            issuer_workspace_id=issuer_workspace_id,
        )
        if not isinstance(destination_public_key, str) or not _PUBLIC_KEY.fullmatch(destination_public_key):
            raise DelegationAuthorizationError("device public key is invalid")
        timestamp = _now(now)
        with self._locked_file():
            state = self._read_state_unlocked()
            request = state["enrollment_requests"].get(enrollment)
            if request is None:
                raise DelegationNotFoundError("enrollment request not found")
            delegation = state["delegations"].get(request["human_action_id"])
            intent = request.get("delivery_intent")
            receipt = request.get("installation_receipt")
            if (
                delegation is None
                or delegation["revoked"]
                or request["status"] != "approved"
                or not isinstance(intent, dict)
                or any(intent.get(key) != value for key, value in context.items())
                or intent["destination_public_key"] != destination_public_key
                or not isinstance(receipt, dict)
                or "device_signature" not in receipt
            ):
                raise DelegationAuthorizationError("peer approval has no matching signed installation receipt")
            if receipt.get("peer_approved_at") is None:
                receipt["peer_approved_at"] = timestamp
                self._write_state_unlocked(state)
            return _to_request_model(request, delegation)

    def revoke(self, human_action_id: str, *, now: int | None = None) -> EnrollmentDelegation:
        """Permanently revoke this delegation; revocation is never auto-pruned."""
        del now  # Accepted to keep mutation calls testable with a uniform clock.
        action = _identifier(human_action_id, "human_action_id")
        with self._locked_file():
            state = self._read_state_unlocked()
            record = self._require_delegation(state, action)
            if not record["revoked"]:
                record["revoked"] = True
                self._write_state_unlocked(state)
            return _to_model(record)

    def _normalize_request(
        self,
        *,
        agent_id: str,
        session_id: str,
        issuer: str,
        network_ids: Iterable[str],
        configuration_profile: str | None,
        credential_categories: Iterable[str],
        device_key_fingerprint: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "agent_id": _identifier(agent_id, "agent_id"),
            "session_id": _identifier(session_id, "session_id"),
            "issuer": _identifier(issuer, "issuer"),
            "network_ids": list(
                _identifier_list(
                    network_ids,
                    "network_ids",
                    minimum=1,
                    maximum=MAX_NETWORK_IDS,
                )
            ),
            "configuration_profile": _optional_identifier(configuration_profile, "configuration_profile"),
            "credential_categories": list(
                _identifier_list(
                    credential_categories,
                    "credential_categories",
                    minimum=0,
                    maximum=MAX_CREDENTIAL_CATEGORIES,
                )
            ),
        }
        if device_key_fingerprint is not None:
            request["device_key_fingerprint"] = _fingerprint(device_key_fingerprint)
        if workspace_id is not None:
            if not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(workspace_id):
                raise ValueError("workspace_id is invalid")
            request["workspace_id"] = workspace_id
        return request

    @staticmethod
    def _require_delegation(state: dict[str, Any], human_action_id: str) -> dict[str, Any]:
        action = _identifier(human_action_id, "human_action_id")
        try:
            return state["delegations"][action]
        except KeyError as exc:
            raise DelegationNotFoundError("delegation not found") from exc

    @staticmethod
    def _check_active(record: dict[str, Any], request: dict[str, Any], now: int) -> None:
        if record["revoked"]:
            raise DelegationAuthorizationError("delegation has been revoked")
        if record["expires_at"] <= now:
            raise DelegationAuthorizationError("delegation has expired")
        if record["authorized_agent_id"] != request["agent_id"]:
            raise DelegationAuthorizationError("agent is not authorized by delegation")
        if record["authorized_session_id"] != request["session_id"]:
            raise DelegationAuthorizationError("session is not authorized by delegation")
        if record["issuer"] != request["issuer"]:
            raise DelegationAuthorizationError("issuer is not authorized by delegation")
        if not set(request["network_ids"]).issubset(record["network_ids"]):
            raise DelegationAuthorizationError("requested network is not authorized")
        if (
            request["configuration_profile"] is not None
            and request["configuration_profile"] != record["configuration_profile"]
        ):
            raise DelegationAuthorizationError("requested configuration profile is not authorized")
        if not set(request["credential_categories"]).issubset(record["credential_categories"]):
            raise DelegationAuthorizationError("requested credential category is not authorized")

    @contextmanager
    def _locked_file(self) -> Iterator[None]:
        self._thread_lock.acquire()
        fd: int | None = None
        locked = False
        try:
            self._ensure_private_parent()
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(self._lock_path, flags, 0o600)
            _check_private_file(os.fstat(fd), "delegation lock")
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            elif msvcrt is not None:  # pragma: no cover
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            else:  # pragma: no cover
                raise DelegationPersistenceError("no supported file-lock backend")
            yield
        except EnrollmentDelegationError:
            raise
        except OSError as exc:
            raise DelegationPersistenceError("cannot lock delegation state") from exc
        finally:
            try:
                if fd is not None:
                    try:
                        if locked and fcntl is not None:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        elif locked and msvcrt is not None:  # pragma: no cover
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    finally:
                        os.close(fd)
            finally:
                self._thread_lock.release()

    def _ensure_private_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self._path.parent.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise DelegationPersistenceError("delegation state parent must be a directory")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise DelegationPersistenceError("delegation state parent must be user-owned")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o700:
            raise DelegationPersistenceError("delegation state parent must have mode 0700")

    def _read_state_unlocked(self) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(self._path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise DelegationPersistenceError("cannot open delegation state") from exc
        try:
            info = os.fstat(fd)
            _check_private_file(info, "delegation state")
            if info.st_size > MAX_STATE_BYTES:
                raise DelegationCapacityError("delegation state exceeds the read byte limit")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                raise DelegationCapacityError("delegation state exceeds the read byte limit")
        finally:
            os.close(fd)

        try:
            state = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DelegationPersistenceError("delegation state JSON is malformed") from exc
        if (
            isinstance(state, dict)
            and set(state) == {"version", "delegations"}
            and type(state.get("version")) is int
            and state.get("version") == 1
        ):
            # Preserve existing local authorization and consumed allowance while
            # adding the bounded proof-request ledger.
            state = {
                "version": STATE_VERSION,
                "delegations": state["delegations"],
                "enrollment_requests": {},
            }
            _validate_state(state)
            self._write_state_unlocked(state)
            return state
        if (
            isinstance(state, dict)
            and set(state) == {"version", "delegations", "enrollment_requests"}
            and type(state.get("version")) is int
            and state.get("version") == 3
        ):
            # Preserve v3 intent and receipt records as signed history. The
            # legacy intent shape has no issuer-workspace binding, so exact
            # recovery-context matching will not select it.
            state = {**state, "version": STATE_VERSION}
            _validate_state(state)
            self._write_state_unlocked(state)
            return state
        if (
            isinstance(state, dict)
            and set(state) == {"version", "delegations", "enrollment_requests"}
            and type(state.get("version")) is int
            and state.get("version") == 2
        ):
            # Preserve v2 receipts as historical facts, but do not invent the
            # device signature or issued digest required for reconciliation.
            for request in state["enrollment_requests"].values():
                request.setdefault("installation_receipt", None)
                request["delivery_intent"] = None
            state = {**state, "version": STATE_VERSION}
            _validate_state(state)
            self._write_state_unlocked(state)
            return state
        _validate_state(state)
        return state

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        _validate_state(state)
        serialized = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(serialized) > MAX_STATE_BYTES:
            raise DelegationCapacityError("delegation state exceeds the write byte limit")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self._path)
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

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "delegations": {},
            "enrollment_requests": {},
        }


def _check_private_file(info: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DelegationPersistenceError(f"{label} must be a single-link regular file")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise DelegationPersistenceError(f"{label} must be owned by the current user")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600:
        raise DelegationPersistenceError(f"{label} must have mode 0600")


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a bounded opaque identifier")
    return value


def _fingerprint(value: str) -> str:
    if not isinstance(value, str) or not _SCOPE_DIGEST.fullmatch(value):
        raise ValueError("device_key_fingerprint must be a lowercase SHA-256 digest")
    return value


def _device_key_fingerprint(public_key_hex: str) -> str:
    if not isinstance(public_key_hex, str) or not _PUBLIC_KEY.fullmatch(public_key_hex):
        raise ValueError("device public key must be lowercase hexadecimal")
    return hashlib.sha256(
        b"kollab-relay-enrollment-device-fingerprint-v1\0" + bytes.fromhex(public_key_hex)
    ).hexdigest()


def _optional_identifier(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, label)


def _identifier_list(values: Iterable[str], label: str, *, minimum: int, maximum: int) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{label} must be a collection of identifiers")
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise TypeError(f"{label} must be an iterable of identifiers") from exc
    normalized: list[str] = []
    for value in iterator:
        if len(normalized) >= maximum:
            raise ValueError(f"{label} must contain at most {maximum} identifiers")
        normalized.append(_identifier(value, label))
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(f"{label} must contain {minimum}..{maximum} identifiers")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must not contain duplicates")
    return tuple(sorted(normalized))


def _bounded_int(value: int, label: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{label} is outside its allowed range")
    return value


def _now(value: int | None) -> int:
    if value is None:
        return int(time.time())
    return _bounded_int(value, "now", minimum=0)


def _request_fingerprint(request: dict[str, Any]) -> str:
    payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _total_consumed(state: dict[str, Any]) -> int:
    return sum(len(record["consumed_enrollments"]) for record in state["delegations"].values())


def _to_model(record: dict[str, Any]) -> EnrollmentDelegation:
    return EnrollmentDelegation(
        human_action_id=record["human_action_id"],
        authorized_agent_id=record["authorized_agent_id"],
        authorized_session_id=record["authorized_session_id"],
        issuer=record["issuer"],
        network_ids=tuple(record["network_ids"]),
        configuration_profile=record["configuration_profile"],
        credential_categories=tuple(record["credential_categories"]),
        max_new_devices=record["max_new_devices"],
        expires_at=record["expires_at"],
        revoked=record["revoked"],
        consumed_new_devices=record["consumed_new_devices"],
    )


def _to_request_model(request: dict[str, Any], delegation: dict[str, Any]) -> EnrollmentRequestRecord:
    scope = request["scope"]
    receipt = request.get("installation_receipt")
    intent = request.get("delivery_intent")
    return EnrollmentRequestRecord(
        enrollment_id=request["enrollment_id"],
        human_action_id=request["human_action_id"],
        device_key_fingerprint=request["device_key_fingerprint"],
        expires_at=request["expires_at"],
        status=request["status"],
        scope_fingerprint=request["scope_fingerprint"],
        authorized_agent_id=scope["agent_id"],
        authorized_session_id=scope["session_id"],
        issuer=scope["issuer"],
        network_ids=tuple(scope["network_ids"]),
        configuration_profile=scope["configuration_profile"],
        credential_categories=tuple(scope["credential_categories"]),
        max_new_devices=delegation["max_new_devices"],
        consumed_new_devices=delegation["consumed_new_devices"],
        workspace_id=scope.get("workspace_id"),
        installation_status=receipt["status"] if receipt is not None else None,
        installation_revision=receipt["revision"] if receipt is not None else None,
        installation_digest=receipt["digest"] if receipt is not None else None,
        expected_install_digest=(intent["expected_digest"] if intent is not None else None),
        destination_public_key=(intent["destination_public_key"] if intent is not None else None),
        offer_id=intent["offer_id"] if intent is not None else None,
        issuer_relay_key=intent["issuer_relay_key"] if intent is not None else None,
        issuer_origin=intent["issuer_origin"] if intent is not None else None,
        room_fingerprint=intent["room_fingerprint"] if intent is not None else None,
        issuer_workspace_id=(intent.get("issuer_workspace_id") if intent is not None else None),
        owner_public_key=intent["owner_public_key"] if intent is not None else None,
        delivery_signature=(intent.get("owner_signature") if intent is not None else None),
        installation_signature=(receipt.get("device_signature") if receipt is not None else None),
        peer_approved=(receipt.get("peer_approved_at") is not None if receipt is not None else False),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DelegationPersistenceError("delegation state has duplicate fields")
        result[key] = value
    return result


def _validate_state(state: Any) -> None:
    if not isinstance(state, dict) or set(state) != {
        "version",
        "delegations",
        "enrollment_requests",
    }:
        raise DelegationPersistenceError("delegation state has an invalid shape")
    if (
        isinstance(state["version"], bool)
        or not isinstance(state["version"], int)
        or state["version"] != STATE_VERSION
        or not isinstance(state["delegations"], dict)
        or not isinstance(state["enrollment_requests"], dict)
    ):
        raise DelegationPersistenceError("delegation state version is invalid")
    if len(state["delegations"]) > MAX_DELEGATIONS:
        raise DelegationCapacityError("delegation store exceeds its record limit")
    if len(state["enrollment_requests"]) > MAX_ENROLLMENT_REQUESTS:
        raise DelegationCapacityError("enrollment request ledger exceeds its limit")

    expected_record_fields = {
        "human_action_id",
        "authorized_agent_id",
        "authorized_session_id",
        "issuer",
        "network_ids",
        "configuration_profile",
        "credential_categories",
        "max_new_devices",
        "expires_at",
        "revoked",
        "consumed_new_devices",
        "consumed_enrollments",
    }
    for action_id, record in state["delegations"].items():
        if not isinstance(record, dict) or set(record) != expected_record_fields:
            raise DelegationPersistenceError("delegation record has an invalid shape")
        try:
            action = _identifier(action_id, "human_action_id")
            if record["human_action_id"] != action:
                raise ValueError
            _identifier(record["authorized_agent_id"], "authorized_agent_id")
            _identifier(record["authorized_session_id"], "authorized_session_id")
            _identifier(record["issuer"], "issuer")
            _optional_identifier(record["configuration_profile"], "configuration_profile")
            networks = record["network_ids"]
            categories = record["credential_categories"]
            if not isinstance(networks, list) or not isinstance(categories, list):
                raise ValueError
            _identifier_list(networks, "network_ids", minimum=1, maximum=MAX_NETWORK_IDS)
            _identifier_list(
                categories,
                "credential_categories",
                minimum=0,
                maximum=MAX_CREDENTIAL_CATEGORIES,
            )
            _bounded_int(
                record["max_new_devices"],
                "max_new_devices",
                minimum=1,
                maximum=MAX_NEW_DEVICES,
            )
            _bounded_int(record["expires_at"], "expires_at", minimum=1)
            consumed = _bounded_int(
                record["consumed_new_devices"],
                "consumed_new_devices",
                minimum=0,
                maximum=MAX_NEW_DEVICES,
            )
            if not isinstance(record["revoked"], bool):
                raise ValueError
            enrollments = record["consumed_enrollments"]
            if not isinstance(enrollments, dict) or len(enrollments) > MAX_CONSUMED_ENROLLMENTS:
                raise ValueError
            for enrollment_id, fingerprint in enrollments.items():
                _identifier(enrollment_id, "enrollment_id")
                if not isinstance(fingerprint, str) or not _SCOPE_DIGEST.fullmatch(fingerprint):
                    raise ValueError
            if consumed != len(enrollments) or consumed > record["max_new_devices"]:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise DelegationPersistenceError("delegation record is malformed") from exc
    if _total_consumed(state) > MAX_DELEGATIONS * MAX_CONSUMED_ENROLLMENTS:
        raise DelegationCapacityError("consumed enrollment ledger exceeds its record limit")

    legacy_request_fields = {
        "enrollment_id",
        "human_action_id",
        "device_key_fingerprint",
        "expires_at",
        "status",
        "scope",
        "scope_fingerprint",
        "decided_at",
    }
    expected_request_fields = legacy_request_fields | {"installation_receipt"}
    current_request_fields = expected_request_fields | {"delivery_intent"}
    total_consumed: dict[str, tuple[str, str]] = {}
    for action_id, record in state["delegations"].items():
        for enrollment_id, fingerprint in record["consumed_enrollments"].items():
            if enrollment_id in total_consumed:
                raise DelegationPersistenceError("consumed enrollment ID is duplicated across delegations")
            total_consumed[enrollment_id] = (action_id, fingerprint)
    for enrollment_id, request in state["enrollment_requests"].items():
        if not isinstance(request, dict) or (
            set(request) != legacy_request_fields
            and set(request) != expected_request_fields
            and set(request) != current_request_fields
        ):
            raise DelegationPersistenceError("enrollment request has an invalid shape")
        try:
            enrollment = _identifier(enrollment_id, "enrollment_id")
            if request["enrollment_id"] != enrollment:
                raise ValueError
            action = _identifier(request["human_action_id"], "human_action_id")
            delegation = state["delegations"].get(action)
            if delegation is None:
                raise ValueError
            _fingerprint(request["device_key_fingerprint"])
            _bounded_int(request["expires_at"], "expires_at", minimum=1)
            if request["status"] not in {"pending", "approved", "rejected"}:
                raise ValueError
            decided_at = request["decided_at"]
            if request["status"] == "pending":
                if decided_at is not None:
                    raise ValueError
            else:
                _bounded_int(decided_at, "decided_at", minimum=0)
            scope = request["scope"]
            legacy_scope_fields = {
                "agent_id",
                "session_id",
                "issuer",
                "network_ids",
                "configuration_profile",
                "credential_categories",
                "device_key_fingerprint",
            }
            scope_fields = legacy_scope_fields | {"workspace_id"}
            if not isinstance(scope, dict) or (set(scope) != legacy_scope_fields and set(scope) != scope_fields):
                raise ValueError
            _identifier(scope["agent_id"], "agent_id")
            _identifier(scope["session_id"], "session_id")
            _identifier(scope["issuer"], "issuer")
            if not isinstance(scope["network_ids"], list):
                raise ValueError
            if not isinstance(scope["credential_categories"], list):
                raise ValueError
            _identifier_list(
                scope["network_ids"],
                "network_ids",
                minimum=1,
                maximum=MAX_NETWORK_IDS,
            )
            _identifier_list(
                scope["credential_categories"],
                "credential_categories",
                minimum=0,
                maximum=MAX_CREDENTIAL_CATEGORIES,
            )
            _optional_identifier(scope["configuration_profile"], "configuration_profile")
            workspace_id = scope.get("workspace_id")
            if workspace_id is not None and (
                not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(workspace_id)
            ):
                raise ValueError
            if _fingerprint(scope["device_key_fingerprint"]) != request["device_key_fingerprint"]:
                raise ValueError
            if request["scope_fingerprint"] != _request_fingerprint(scope):
                raise ValueError
            if (
                scope["agent_id"] != delegation["authorized_agent_id"]
                or scope["session_id"] != delegation["authorized_session_id"]
                or scope["issuer"] != delegation["issuer"]
                or not set(scope["network_ids"]).issubset(delegation["network_ids"])
                or (
                    scope["configuration_profile"] is not None
                    and scope["configuration_profile"] != delegation["configuration_profile"]
                )
                or not set(scope["credential_categories"]).issubset(delegation["credential_categories"])
                or request["expires_at"] > delegation["expires_at"]
            ):
                raise ValueError
            intent = request.get("delivery_intent")
            if intent is not None:
                legacy_intent_fields = {
                    "offer_id",
                    "destination_public_key",
                    "expected_digest",
                    "issuer_relay_key",
                    "issuer_origin",
                    "room_fingerprint",
                    "owner_public_key",
                    "owner_signature",
                }
                current_intent_fields = legacy_intent_fields | {"issuer_workspace_id"}
                if (
                    request["status"] != "approved"
                    or workspace_id is None
                    or not isinstance(intent, dict)
                    or frozenset(intent) not in {frozenset(legacy_intent_fields), frozenset(current_intent_fields)}
                    or not isinstance(intent["offer_id"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", intent["offer_id"])
                    or not isinstance(intent["destination_public_key"], str)
                    or not _PUBLIC_KEY.fullmatch(intent["destination_public_key"])
                    or _device_key_fingerprint(intent["destination_public_key"]) != request["device_key_fingerprint"]
                    or not isinstance(intent["expected_digest"], str)
                    or not _SCOPE_DIGEST.fullmatch(intent["expected_digest"])
                    or not isinstance(intent["issuer_relay_key"], str)
                    or not _PUBLIC_KEY.fullmatch(intent["issuer_relay_key"])
                    or not isinstance(intent["room_fingerprint"], str)
                    or not _SCOPE_DIGEST.fullmatch(intent["room_fingerprint"])
                    or (
                        "issuer_workspace_id" in intent
                        and (
                            not isinstance(intent["issuer_workspace_id"], str)
                            or not _WORKSPACE_ID.fullmatch(intent["issuer_workspace_id"])
                        )
                    )
                    or not isinstance(intent["owner_public_key"], str)
                    or not _PUBLIC_KEY.fullmatch(intent["owner_public_key"])
                    or not isinstance(intent["owner_signature"], str)
                    or not re.fullmatch(r"[0-9a-f]{128}", intent["owner_signature"])
                    or not isinstance(intent["issuer_origin"], str)
                    or len(intent["issuer_origin"]) > 512
                ):
                    raise ValueError
                origin = urlsplit(intent["issuer_origin"])
                if (
                    origin.scheme != "https"
                    or not origin.hostname
                    or origin.username is not None
                    or origin.password is not None
                    or origin.path not in {"", "/"}
                    or origin.query
                    or origin.fragment
                ):
                    raise ValueError
                if not _verify_owner_signature(
                    intent["owner_public_key"],
                    (
                        _delivery_intent_payload(request, intent)
                        if "issuer_workspace_id" in intent
                        else _legacy_delivery_intent_payload(request, intent)
                    ),
                    intent["owner_signature"],
                ):
                    raise ValueError
            receipt = request.get("installation_receipt")
            if receipt is not None:
                legacy_receipt_fields = {
                    "status",
                    "workspace_id",
                    "revision",
                    "digest",
                    "received_at",
                }
                signed_receipt_fields = legacy_receipt_fields | {
                    "device_signature",
                    "peer_approved_at",
                }
                if (
                    request["status"] != "approved"
                    or workspace_id is None
                    or not isinstance(receipt, dict)
                    or frozenset(receipt)
                    not in {
                        frozenset(legacy_receipt_fields),
                        frozenset(signed_receipt_fields),
                    }
                    or receipt["status"] not in {"installed", "already_installed"}
                    or receipt["workspace_id"] != workspace_id
                    or isinstance(receipt["revision"], bool)
                    or not isinstance(receipt["revision"], int)
                    or receipt["revision"] < 1
                    or not isinstance(receipt["digest"], str)
                    or not _SCOPE_DIGEST.fullmatch(receipt["digest"])
                ):
                    raise ValueError
                _bounded_int(receipt["received_at"], "received_at", minimum=0)
                if set(receipt) == signed_receipt_fields:
                    if (
                        not isinstance(intent, dict)
                        or not isinstance(receipt["device_signature"], str)
                        or not re.fullmatch(r"[0-9a-f]{128}", receipt["device_signature"])
                        or receipt["digest"] != intent["expected_digest"]
                        or receipt["revision"] != 1
                    ):
                        raise ValueError
                    approved_at = receipt["peer_approved_at"]
                    if approved_at is not None:
                        _bounded_int(approved_at, "peer_approved_at", minimum=0)
                    signed_payload = {
                        "v": 1,
                        "offer_id": intent["offer_id"],
                        "round_id": enrollment,
                        "phase": "installation_ack",
                        "destination_key": intent["destination_public_key"],
                        "workspace_id": receipt["workspace_id"],
                        "status": receipt["status"],
                        "revision": receipt["revision"],
                        "digest": receipt["digest"],
                        "device_signature": receipt["device_signature"],
                    }
                    if not verify_installation_receipt(intent["destination_public_key"], signed_payload):
                        raise ValueError
                elif intent is not None:
                    raise ValueError
            consumed = total_consumed.get(enrollment)
            if request["status"] == "approved":
                if consumed != (action, request["scope_fingerprint"]):
                    raise ValueError
            elif consumed is not None:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise DelegationPersistenceError("enrollment request is malformed") from exc

    for enrollment, (action, fingerprint) in total_consumed.items():
        request = state["enrollment_requests"].get(enrollment)
        if request is not None and (
            request["human_action_id"] != action
            or request["status"] != "approved"
            or request["scope_fingerprint"] != fingerprint
        ):
            raise DelegationPersistenceError("consumed enrollment and decision ledger disagree")
