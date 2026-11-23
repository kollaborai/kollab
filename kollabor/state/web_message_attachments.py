"""Bounded, daemon-lifetime storage for queued WebUI image attachments.

This is a state primitive, not an RPC trust boundary. The daemon integration
must inject a resolver that binds an authenticated caller to a canonical
conversation and queue incarnation, plus a validator for queue-minted item
IDs. Uploads are short-lived until explicitly retained by one queue item;
retained bytes have no timer and must be released only on a definite terminal
queue outcome.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import math
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from kollabor_ai.message_content import (
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MAX_TOTAL_IMAGE_BYTES,
)

DEFAULT_MAX_ATTACHMENT_BYTES = DEFAULT_MAX_IMAGE_BYTES
DEFAULT_MAX_ITEM_BYTES = DEFAULT_MAX_TOTAL_IMAGE_BYTES
DEFAULT_MAX_SCOPE_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_ATTACHMENTS_PER_ITEM = 20
DEFAULT_MAX_ATTACHMENTS_PER_SCOPE = 64
DEFAULT_MAX_ATTACHMENTS = 512
DEFAULT_MAX_SCOPES = 16
DEFAULT_MAX_OPERATIONS_PER_SCOPE = 256
DEFAULT_MAX_OPERATIONS = 4096
DEFAULT_OPERATION_RETRY_LEASE_SECONDS = 900.0
DEFAULT_ORPHAN_TTL_SECONDS = 900.0
MAX_OPERATION_RETRY_LEASE_SECONDS = 30 * 24 * 60 * 60

_MIME_ALIASES = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
}
_SCOPE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_OWNER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_OPERATION_ID_RE = re.compile(r"op_([0-9a-f]{1,20})_([0-9a-f]{32})_([0-9a-f]{32})\Z")
_REFERENCE_RE = re.compile(r"att_([0-9a-f]{32})_([0-9a-f]{32})\Z")


class WebMessageAttachmentError(Exception):
    """Base error. Error messages never include image data or scope values."""


class AttachmentAuthorizationError(WebMessageAttachmentError, PermissionError):
    """The trusted resolver or queue-owner validator refused access."""


class AttachmentCapacityExceeded(WebMessageAttachmentError):
    """A configured byte, item, operation, or scope bound was reached."""


class AttachmentIdempotencyConflict(WebMessageAttachmentError):
    """An upload operation ID was reused for different bytes or MIME."""


class AttachmentOwnerConflict(WebMessageAttachmentError):
    """An attachment or owner ID is already bound to another item state."""


class AttachmentNotFound(WebMessageAttachmentError):
    """The reference is invalid, expired, missing, or belongs to another item."""


class InvalidAttachment(WebMessageAttachmentError):
    """The image MIME, byte type, or reference list is invalid."""


class InvalidAttachmentOperation(WebMessageAttachmentError):
    """The operation ID is malformed, altered, or bound to another scope."""


class StaleAttachmentOperation(WebMessageAttachmentError):
    """The operation retry lease expired; the ID cannot create new work."""


def verified_image_mime(mime: object, payload: object) -> str:
    """Return canonical MIME only when bytes carry its image signature."""
    if not isinstance(mime, str):
        raise InvalidAttachment("unsupported image MIME type")
    normalized = _MIME_ALIASES.get(mime.strip().lower())
    if normalized is None:
        raise InvalidAttachment("unsupported image MIME type")
    if type(payload) is not bytes or not payload:
        raise InvalidAttachment("image bytes are invalid")

    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        actual = "image/png"
    elif payload.startswith(b"\xff\xd8\xff"):
        actual = "image/jpeg"
    elif len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        actual = "image/webp"
    elif payload.startswith((b"GIF87a", b"GIF89a")):
        actual = "image/gif"
    else:
        raise InvalidAttachment("image bytes have no supported format signature")
    if actual != normalized:
        raise InvalidAttachment("image bytes do not match the declared MIME type")
    return normalized


@dataclass(frozen=True, slots=True)
class AttachmentScopeBinding:
    """Trusted identity for one principal, conversation, and queue instance.

    Both fields are deliberately hidden from reprs because they may encode
    private local identity. Construct this only inside the injected resolver.
    """

    scope_id: str = field(repr=False)
    queue_incarnation: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AttachmentReceipt:
    """Small result safe to store in a queue payload."""

    ref: str
    mime: str
    size: int


@dataclass(frozen=True, slots=True)
class StagedAttachment:
    """One owned image returned to an internal daemon worker."""

    ref: str
    mime: str
    data: bytes = field(repr=False)


@dataclass(slots=True)
class _StoredAttachment:
    binding: AttachmentScopeBinding = field(repr=False)
    ref: str
    mime: str
    data: bytes = field(repr=False)
    expires_at_ns: int | None
    owner_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _OperationRecord:
    fingerprint: str
    ref: str
    expires_at_ns: int


ScopeResolver = Callable[
    [Mapping[str, Any], object],
    AttachmentScopeBinding | Awaitable[AttachmentScopeBinding],
]
OwnerIdValidator = Callable[[AttachmentScopeBinding, str], bool]


class WebMessageAttachmentRegistry:
    """Bounded daemon-memory registry shared by all conversation scopes.

    Resolver and owner-validator callbacks are mandatory. The resolver must
    derive its result from trusted transport context and daemon state, never a
    request-selected scope. The validator must verify a queue-minted request
    ID for the exact scope/incarnation, including a still-pending ID after its
    creation lease or a terminal tombstone needed for cleanup.
    """

    def __init__(
        self,
        *,
        resolve_scope: ScopeResolver,
        validate_owner_id: OwnerIdValidator,
        max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
        max_item_bytes: int = DEFAULT_MAX_ITEM_BYTES,
        max_scope_bytes: int = DEFAULT_MAX_SCOPE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_attachments_per_item: int = DEFAULT_MAX_ATTACHMENTS_PER_ITEM,
        max_attachments_per_scope: int = DEFAULT_MAX_ATTACHMENTS_PER_SCOPE,
        max_attachments: int = DEFAULT_MAX_ATTACHMENTS,
        max_scopes: int = DEFAULT_MAX_SCOPES,
        max_operations_per_scope: int = DEFAULT_MAX_OPERATIONS_PER_SCOPE,
        max_operations: int = DEFAULT_MAX_OPERATIONS,
        operation_retry_lease_seconds: float = DEFAULT_OPERATION_RETRY_LEASE_SECONDS,
        orphan_ttl_seconds: float = DEFAULT_ORPHAN_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(resolve_scope) or not callable(validate_owner_id):
            raise TypeError("trusted scope and owner validators are required")
        integer_limits = {
            "max_attachment_bytes": max_attachment_bytes,
            "max_item_bytes": max_item_bytes,
            "max_scope_bytes": max_scope_bytes,
            "max_total_bytes": max_total_bytes,
            "max_attachments_per_item": max_attachments_per_item,
            "max_attachments_per_scope": max_attachments_per_scope,
            "max_attachments": max_attachments,
            "max_scopes": max_scopes,
            "max_operations_per_scope": max_operations_per_scope,
            "max_operations": max_operations,
        }
        for name, value in integer_limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not (
            max_attachment_bytes <= max_item_bytes <= max_scope_bytes <= max_total_bytes
        ):
            raise ValueError("attachment byte bounds must be monotonically increasing")
        if (
            max_attachments_per_item > max_attachments_per_scope
            or max_attachments_per_scope > max_attachments
            or max_operations_per_scope > max_operations
        ):
            raise ValueError("per-scope limits cannot exceed daemon-wide limits")
        for name, value in (
            ("operation retry lease", operation_retry_lease_seconds),
            ("orphan TTL", orphan_ttl_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")
        if orphan_ttl_seconds > operation_retry_lease_seconds:
            raise ValueError("orphan TTL cannot exceed the operation retry lease")
        if operation_retry_lease_seconds > MAX_OPERATION_RETRY_LEASE_SECONDS:
            raise ValueError("operation retry lease exceeds its hard maximum")
        if not callable(clock):
            raise TypeError("clock must be callable")

        retry_lease_ns = int(operation_retry_lease_seconds * 1_000_000_000)
        orphan_ttl_ns = int(orphan_ttl_seconds * 1_000_000_000)
        if retry_lease_ns < 1 or orphan_ttl_ns < 1:
            raise ValueError(
                "retry lease and orphan TTL must be at least one nanosecond"
            )

        self._resolve_scope = resolve_scope
        self._owner_id_validator = validate_owner_id
        self._clock = clock
        self._max_attachment_bytes = max_attachment_bytes
        self._max_item_bytes = max_item_bytes
        self._max_scope_bytes = max_scope_bytes
        self._max_total_bytes = max_total_bytes
        self._max_attachments_per_item = max_attachments_per_item
        self._max_attachments_per_scope = max_attachments_per_scope
        self._max_attachments = max_attachments
        self._max_scopes = max_scopes
        self._max_operations_per_scope = max_operations_per_scope
        self._max_operations = max_operations
        self._retry_lease_ns = retry_lease_ns
        self._orphan_ttl_ns = orphan_ttl_ns
        self._store_incarnation = secrets.token_hex(16)
        self._mac_key = secrets.token_bytes(32)
        self._lock = asyncio.Lock()
        self._attachments: dict[str, _StoredAttachment] = {}
        self._owners: dict[tuple[AttachmentScopeBinding, str], tuple[str, ...]] = {}
        self._operations: dict[tuple[AttachmentScopeBinding, str], _OperationRecord] = (
            {}
        )
        self._operation_counts_by_scope: dict[AttachmentScopeBinding, int] = {}
        self._bytes_by_scope: dict[AttachmentScopeBinding, int] = {}
        self._count_by_scope: dict[AttachmentScopeBinding, int] = {}
        self._used_bytes = 0

    @property
    def used_bytes(self) -> int:
        """Current decoded bytes retained in daemon memory."""
        return self._used_bytes

    @property
    def attachment_count(self) -> int:
        """Current staged and retained attachment count."""
        return len(self._attachments)

    @property
    def scope_count(self) -> int:
        """Scopes currently holding at least one attachment."""
        return len(self._count_by_scope)

    @property
    def operation_count(self) -> int:
        """Current idempotency receipts and retry tombstones."""
        return len(self._operations)

    def _now_ns(self) -> int:
        now = self._clock()
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now < 0
        ):
            raise RuntimeError("attachment clock returned an invalid value")
        return int(now * 1_000_000_000)

    @staticmethod
    def _validate_binding(binding: object) -> AttachmentScopeBinding:
        if not isinstance(binding, AttachmentScopeBinding):
            raise AttachmentAuthorizationError("attachment access denied")
        if (
            not isinstance(binding.scope_id, str)
            or _SCOPE_ID_RE.fullmatch(binding.scope_id) is None
            or not isinstance(binding.queue_incarnation, str)
            or _SCOPE_ID_RE.fullmatch(binding.queue_incarnation) is None
        ):
            raise AttachmentAuthorizationError("attachment access denied")
        return binding

    async def bind_scope(
        self,
        params: Mapping[str, Any],
        caller_context: object,
    ) -> _BoundAttachmentScope:
        """Resolve trusted caller state into an internal scope capability."""
        if not isinstance(params, Mapping):
            raise AttachmentAuthorizationError("attachment access denied")
        try:
            binding = self._resolve_scope(params, caller_context)
            if inspect.isawaitable(binding):
                binding = await binding
        except AttachmentAuthorizationError:
            raise
        except Exception:
            raise AttachmentAuthorizationError("attachment access denied") from None
        return _BoundAttachmentScope(self, self._validate_binding(binding))

    def _bind_internal_scope(
        self, binding: AttachmentScopeBinding
    ) -> _BoundAttachmentScope:
        """Create an in-process handle from trusted worker-captured queue IDs.

        This method is deliberately private and accepts no RPC parameters or
        caller-supplied principal. The owner validator still authenticates each
        queue-minted item ID before any retained bytes are opened or released.
        """
        return _BoundAttachmentScope(self, self._validate_binding(binding))

    @staticmethod
    def _binding_key(binding: AttachmentScopeBinding) -> bytes:
        return (binding.scope_id + "\0" + binding.queue_incarnation).encode("ascii")

    def _mac(self, kind: str, binding: AttachmentScopeBinding, unsigned: str) -> str:
        material = b"\0".join(
            (
                kind.encode("ascii"),
                self._binding_key(binding),
                self._store_incarnation.encode("ascii"),
                unsigned.encode("ascii"),
            )
        )
        return hmac.new(self._mac_key, material, hashlib.sha256).digest()[:16].hex()

    def _new_operation_id(self, binding: AttachmentScopeBinding) -> str:
        issued_at_ns = self._now_ns()
        nonce = secrets.token_hex(16)
        unsigned = f"op_{issued_at_ns:x}_{nonce}"
        return f"{unsigned}_{self._mac('op', binding, unsigned)}"

    def _operation_issue_time(
        self, binding: AttachmentScopeBinding, operation_id: str
    ) -> int:
        if not isinstance(operation_id, str):
            raise InvalidAttachmentOperation("invalid attachment operation")
        match = _OPERATION_ID_RE.fullmatch(operation_id)
        if match is None:
            raise InvalidAttachmentOperation("invalid attachment operation")
        issued_text, nonce, supplied_mac = match.groups()
        unsigned = f"op_{issued_text}_{nonce}"
        expected_mac = self._mac("op", binding, unsigned)
        if not hmac.compare_digest(supplied_mac, expected_mac):
            raise InvalidAttachmentOperation("invalid attachment operation")
        issued_at_ns = int(issued_text, 16)
        now_ns = self._now_ns()
        if issued_at_ns <= 0 or issued_at_ns > now_ns:
            raise InvalidAttachmentOperation("invalid attachment operation")
        return issued_at_ns

    def _new_reference(self, binding: AttachmentScopeBinding) -> str:
        while True:
            nonce = secrets.token_hex(16)
            unsigned = f"att_{nonce}"
            ref = f"{unsigned}_{self._mac('ref', binding, unsigned)}"
            if ref not in self._attachments:
                return ref

    def _valid_reference(self, binding: AttachmentScopeBinding, ref: object) -> bool:
        if not isinstance(ref, str):
            return False
        match = _REFERENCE_RE.fullmatch(ref)
        if match is None:
            return False
        nonce, supplied_mac = match.groups()
        unsigned = f"att_{nonce}"
        expected_mac = self._mac("ref", binding, unsigned)
        return hmac.compare_digest(supplied_mac, expected_mac)

    @staticmethod
    def _normalize_mime(mime: object) -> str:
        if not isinstance(mime, str):
            raise InvalidAttachment("unsupported image MIME type")
        normalized = _MIME_ALIASES.get(mime.strip().lower())
        if normalized is None:
            raise InvalidAttachment("unsupported image MIME type")
        return normalized

    @staticmethod
    def _validate_owner_id(owner_id: object) -> str:
        if not isinstance(owner_id, str) or _OWNER_ID_RE.fullmatch(owner_id) is None:
            raise AttachmentAuthorizationError("attachment owner denied")
        return owner_id

    def _owner_is_valid(self, binding: AttachmentScopeBinding, owner_id: str) -> bool:
        try:
            return self._owner_id_validator(binding, owner_id) is True
        except Exception:
            return False

    def _prune_expired_unlocked(self, now_ns: int) -> int:
        removed = 0
        for ref, stored in tuple(self._attachments.items()):
            if stored.owner_id is None and stored.expires_at_ns is not None:
                if stored.expires_at_ns <= now_ns:
                    self._remove_attachment_unlocked(ref, stored)
                    removed += 1

        for key, operation in tuple(self._operations.items()):
            if operation.expires_at_ns > now_ns:
                continue
            stored = self._attachments.get(operation.ref)
            if stored is not None and stored.owner_id is not None:
                continue
            self._operations.pop(key, None)
            count = self._operation_counts_by_scope.get(key[0], 0) - 1
            if count > 0:
                self._operation_counts_by_scope[key[0]] = count
            else:
                self._operation_counts_by_scope.pop(key[0], None)
        return removed

    def _remove_attachment_unlocked(self, ref: str, stored: _StoredAttachment) -> None:
        if self._attachments.get(ref) is not stored:
            return
        del self._attachments[ref]
        binding = stored.binding
        size = len(stored.data)
        self._used_bytes -= size
        scope_bytes = self._bytes_by_scope.get(binding, 0) - size
        if scope_bytes > 0:
            self._bytes_by_scope[binding] = scope_bytes
        else:
            self._bytes_by_scope.pop(binding, None)
        scope_count = self._count_by_scope.get(binding, 0) - 1
        if scope_count > 0:
            self._count_by_scope[binding] = scope_count
        else:
            self._count_by_scope.pop(binding, None)

    def _check_capacity_unlocked(
        self,
        binding: AttachmentScopeBinding,
        *,
        size: int,
    ) -> None:
        scope_count = self._count_by_scope.get(binding, 0)
        scope_bytes = self._bytes_by_scope.get(binding, 0)
        if (
            len(self._attachments) >= self._max_attachments
            or self._used_bytes + size > self._max_total_bytes
            or scope_count >= self._max_attachments_per_scope
            or scope_bytes + size > self._max_scope_bytes
        ):
            raise AttachmentCapacityExceeded("attachment capacity is full")
        if scope_count == 0 and len(self._count_by_scope) >= self._max_scopes:
            raise AttachmentCapacityExceeded("attachment scope capacity is full")

    async def _stage(
        self,
        binding: AttachmentScopeBinding,
        operation_id: str,
        mime: object,
        payload: object,
    ) -> AttachmentReceipt:
        normalized_mime = verified_image_mime(mime, payload)
        size = len(payload)
        if size > self._max_attachment_bytes:
            raise AttachmentCapacityExceeded("image exceeds the attachment size limit")
        issued_at_ns = self._operation_issue_time(binding, operation_id)
        fingerprint = hashlib.sha256(
            normalized_mime.encode("ascii") + b"\0" + payload
        ).hexdigest()
        operation_key = (binding, operation_id)

        async with self._lock:
            now_ns = self._now_ns()
            self._prune_expired_unlocked(now_ns)
            existing = self._operations.get(operation_key)
            if existing is not None:
                if not hmac.compare_digest(existing.fingerprint, fingerprint):
                    raise AttachmentIdempotencyConflict(
                        "attachment operation conflicts with prior input"
                    )
                stored = self._attachments.get(existing.ref)
                if stored is None:
                    raise StaleAttachmentOperation(
                        "attachment operation result has expired"
                    )
                if stored.owner_id is None and (
                    existing.expires_at_ns <= now_ns
                    or stored.expires_at_ns is None
                    or stored.expires_at_ns <= now_ns
                ):
                    raise StaleAttachmentOperation(
                        "attachment operation retry lease has expired"
                    )
                return AttachmentReceipt(stored.ref, stored.mime, len(stored.data))

            if now_ns - issued_at_ns >= self._retry_lease_ns:
                raise StaleAttachmentOperation(
                    "attachment operation retry lease has expired"
                )
            if (
                self._operation_counts_by_scope.get(binding, 0)
                >= self._max_operations_per_scope
                or len(self._operations) >= self._max_operations
            ):
                raise AttachmentCapacityExceeded("attachment retry capacity is full")
            self._check_capacity_unlocked(binding, size=size)

            ref = self._new_reference(binding)
            expires_at_ns = min(
                now_ns + self._orphan_ttl_ns,
                issued_at_ns + self._retry_lease_ns,
            )
            stored = _StoredAttachment(
                binding=binding,
                ref=ref,
                mime=normalized_mime,
                data=payload,
                expires_at_ns=expires_at_ns,
            )
            self._attachments[ref] = stored
            self._operations[operation_key] = _OperationRecord(
                fingerprint=fingerprint,
                ref=ref,
                expires_at_ns=issued_at_ns + self._retry_lease_ns,
            )
            self._operation_counts_by_scope[binding] = (
                self._operation_counts_by_scope.get(binding, 0) + 1
            )
            self._used_bytes += size
            self._bytes_by_scope[binding] = self._bytes_by_scope.get(binding, 0) + size
            self._count_by_scope[binding] = self._count_by_scope.get(binding, 0) + 1
            return AttachmentReceipt(ref, normalized_mime, size)

    async def _retain(
        self,
        binding: AttachmentScopeBinding,
        owner_id: str,
        refs: Sequence[str],
    ) -> bool:
        owner_id = self._validate_owner_id(owner_id)
        if not self._owner_is_valid(binding, owner_id):
            raise AttachmentAuthorizationError("attachment owner denied")
        if (
            not isinstance(refs, (list, tuple))
            or not refs
            or len(refs) > self._max_attachments_per_item
            or any(not isinstance(ref, str) for ref in refs)
            or len(set(refs)) != len(refs)
        ):
            raise InvalidAttachment("invalid attachment reference list")
        refs_tuple = tuple(refs)
        for ref in refs_tuple:
            if not self._valid_reference(binding, ref):
                raise AttachmentNotFound("attachment not found")

        async with self._lock:
            now_ns = self._now_ns()
            self._prune_expired_unlocked(now_ns)
            owner_key = (binding, owner_id)
            existing_refs = self._owners.get(owner_key)
            if existing_refs is not None:
                if existing_refs == refs_tuple:
                    return False
                raise AttachmentOwnerConflict(
                    "attachment owner conflicts with prior refs"
                )

            total_size = 0
            stored_records: list[_StoredAttachment] = []
            for ref in refs_tuple:
                stored = self._attachments.get(ref)
                if stored is None or stored.binding != binding:
                    raise AttachmentNotFound("attachment not found")
                if stored.owner_id is not None and stored.owner_id != owner_id:
                    raise AttachmentOwnerConflict("attachment is already retained")
                if stored.expires_at_ns is not None and stored.expires_at_ns <= now_ns:
                    raise AttachmentNotFound("attachment not found")
                total_size += len(stored.data)
                stored_records.append(stored)
            if total_size > self._max_item_bytes:
                raise AttachmentCapacityExceeded("item attachment capacity is full")

            self._owners[owner_key] = refs_tuple
            for stored in stored_records:
                stored.owner_id = owner_id
                stored.expires_at_ns = None
            return True

    def _prepare_owner_retention_unlocked(
        self,
        binding: AttachmentScopeBinding,
        owner_refs: Sequence[tuple[str, tuple[str, ...]]],
    ) -> tuple[tuple[str, tuple[str, ...], tuple[_StoredAttachment, ...]], ...]:
        """Validate a multi-owner pin operation while ``_lock`` is held.

        The returned plan has no retained-owner mutations. Queue integration
        validates its own capacity first and commits this plan without an
        intervening await.
        """
        now_ns = self._now_ns()
        self._prune_expired_unlocked(now_ns)
        plans: list[tuple[str, tuple[str, ...], tuple[_StoredAttachment, ...]]] = []
        seen_refs: set[str] = set()

        for raw_owner_id, raw_refs in owner_refs:
            owner_id = self._validate_owner_id(raw_owner_id)
            if not self._owner_is_valid(binding, owner_id):
                raise AttachmentAuthorizationError("attachment owner denied")
            if (
                not isinstance(raw_refs, tuple)
                or not raw_refs
                or len(raw_refs) > self._max_attachments_per_item
                or any(not isinstance(ref, str) for ref in raw_refs)
                or len(set(raw_refs)) != len(raw_refs)
            ):
                raise InvalidAttachment("invalid attachment reference list")
            if (binding, owner_id) in self._owners:
                raise AttachmentOwnerConflict("attachment owner is already retained")

            total_size = 0
            stored_records: list[_StoredAttachment] = []
            for ref in raw_refs:
                if ref in seen_refs:
                    raise AttachmentOwnerConflict(
                        "attachment is assigned to more than one item"
                    )
                seen_refs.add(ref)
                if not self._valid_reference(binding, ref):
                    raise AttachmentNotFound("attachment not found")
                stored = self._attachments.get(ref)
                if stored is None or stored.binding != binding:
                    raise AttachmentNotFound("attachment not found")
                if stored.owner_id is not None:
                    raise AttachmentOwnerConflict("attachment is already retained")
                if stored.expires_at_ns is None or stored.expires_at_ns <= now_ns:
                    raise AttachmentNotFound("attachment not found")
                total_size += len(stored.data)
                stored_records.append(stored)
            if total_size > self._max_item_bytes:
                raise AttachmentCapacityExceeded("item attachment capacity is full")
            plans.append((owner_id, raw_refs, tuple(stored_records)))

        return tuple(plans)

    def _commit_owner_retention_unlocked(
        self,
        binding: AttachmentScopeBinding,
        plan: tuple[tuple[str, tuple[str, ...], tuple[_StoredAttachment, ...]], ...],
    ) -> None:
        """Apply a prevalidated pin plan synchronously while ``_lock`` is held."""
        for owner_id, refs, stored_records in plan:
            self._owners[(binding, owner_id)] = refs
            for stored in stored_records:
                stored.owner_id = owner_id
                stored.expires_at_ns = None

    async def _open_for_owner(
        self,
        binding: AttachmentScopeBinding,
        owner_id: str,
        ref: str,
    ) -> StagedAttachment:
        owner_id = self._validate_owner_id(owner_id)
        if not self._owner_is_valid(binding, owner_id):
            raise AttachmentAuthorizationError("attachment owner denied")
        if not self._valid_reference(binding, ref):
            raise AttachmentNotFound("attachment not found")
        async with self._lock:
            now_ns = self._now_ns()
            self._prune_expired_unlocked(now_ns)
            stored = self._attachments.get(ref)
            if (
                stored is None
                or stored.binding != binding
                or stored.owner_id != owner_id
            ):
                raise AttachmentNotFound("attachment not found")
            return StagedAttachment(stored.ref, stored.mime, stored.data)

    async def _release_owner(
        self,
        binding: AttachmentScopeBinding,
        owner_id: str,
    ) -> int:
        # This method is for trusted daemon lifecycle code only. It must not be
        # exposed as an RPC operation that accepts a caller-selected item ID.
        if not isinstance(owner_id, str):
            return 0
        async with self._lock:
            return self._release_owner_unlocked(binding, owner_id, self._now_ns())

    def _release_owner_unlocked(
        self,
        binding: AttachmentScopeBinding,
        owner_id: str,
        now_ns: int,
    ) -> int:
        """Release one owner synchronously while ``_lock`` is held."""
        self._prune_expired_unlocked(now_ns)
        refs = self._owners.pop((binding, owner_id), ())
        removed = 0
        for ref in refs:
            stored = self._attachments.get(ref)
            if (
                stored is not None
                and stored.binding == binding
                and stored.owner_id == owner_id
            ):
                self._remove_attachment_unlocked(ref, stored)
                removed += 1
        return removed

    async def prune_expired(self) -> int:
        """Prune expired unowned blobs and unpinned retry receipts."""
        async with self._lock:
            return self._prune_expired_unlocked(self._now_ns())


class _BoundAttachmentScope:
    """Internal capability; keep it in daemon code and never serialize it."""

    __slots__ = ("_registry", "_binding")

    def __init__(
        self,
        registry: WebMessageAttachmentRegistry,
        binding: AttachmentScopeBinding,
    ) -> None:
        self._registry = registry
        self._binding = binding

    @property
    def binding(self) -> AttachmentScopeBinding:
        """Trusted identity for internal queue integration and tests."""
        return self._binding

    def new_operation_id(self) -> str:
        """Mint an upload retry ID bound to this scope and store instance."""
        return self._registry._new_operation_id(self._binding)

    async def stage(
        self, operation_id: str, mime: str, payload: bytes
    ) -> AttachmentReceipt:
        """Store one image or return the identical prior upload receipt."""
        return await self._registry._stage(self._binding, operation_id, mime, payload)

    async def retain(self, owner_id: str, refs: Sequence[str]) -> bool:
        """Atomically pin staged refs to one queue-minted item ID."""
        return await self._registry._retain(self._binding, owner_id, refs)

    async def open_for_owner(self, owner_id: str, ref: str) -> StagedAttachment:
        """Resolve bytes only for the exact retained item owner."""
        return await self._registry._open_for_owner(self._binding, owner_id, ref)

    async def release_owner(self, owner_id: str) -> int:
        """Release one owner's refs after a definite terminal queue outcome."""
        return await self._registry._release_owner(self._binding, owner_id)

    @asynccontextmanager
    async def _queue_transaction(self) -> AsyncIterator[None]:
        """Acquire the attachment lock after a queue lock for atomic updates."""
        async with self._registry._lock:
            yield

    def _prepare_owner_retention_unlocked(
        self,
        owner_refs: Sequence[tuple[str, tuple[str, ...]]],
    ) -> tuple[tuple[str, tuple[str, ...], tuple[_StoredAttachment, ...]], ...]:
        return self._registry._prepare_owner_retention_unlocked(
            self._binding, owner_refs
        )

    def _commit_owner_retention_unlocked(
        self,
        plan: tuple[tuple[str, tuple[str, ...], tuple[_StoredAttachment, ...]], ...],
    ) -> None:
        self._registry._commit_owner_retention_unlocked(self._binding, plan)

    def _release_owner_unlocked(self, owner_id: str) -> int:
        return self._registry._release_owner_unlocked(
            self._binding, owner_id, self._registry._now_ns()
        )
