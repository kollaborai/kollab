"""Encrypted, local-only recovery journal for device enrollments.

The relay receives only the end-to-end enrollment envelopes. This journal lets
the issuer resume an already-approved delivery and the destination reconcile
an accepted install after a client restart. It never persists the enrollment
code or provider credentials in plaintext.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows relies on single-owner locking.
    fcntl = None

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

_VERSION = 1
_MAX_BYTES = 2 * 1024 * 1024
_MAX_OFFERS = 32


class EnrollmentRecoveryError(ValueError):
    """A fixed, secret-free recovery-journal failure."""


def derive_enrollment_recovery_key(relay_private_key: bytes) -> bytes:
    """Derive a journal key from the stable, local RelayClient signing key."""
    if not isinstance(relay_private_key, bytes) or len(relay_private_key) != 32:
        raise EnrollmentRecoveryError("invalid_key")
    return hashlib.blake2b(
        b"kollab-enrollment-recovery-key-v1\0" + relay_private_key,
        digest_size=SecretBox.KEY_SIZE,
    ).digest()


def derive_enrollment_destination_recovery_key(relay_private_key: bytes) -> bytes:
    """Derive a separate journal key for destination-side enrollments."""
    if not isinstance(relay_private_key, bytes) or len(relay_private_key) != 32:
        raise EnrollmentRecoveryError("invalid_key")
    return hashlib.blake2b(
        b"kollab-enrollment-destination-recovery-key-v1\0" + relay_private_key,
        digest_size=SecretBox.KEY_SIZE,
    ).digest()


class EnrollmentRecoveryJournal:
    """Atomically persist bounded encrypted recovery records under a private dir."""

    def __init__(self, path: str | os.PathLike[str], key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != SecretBox.KEY_SIZE:
            raise EnrollmentRecoveryError("invalid_key")
        requested = Path(path).expanduser()
        if requested.name in {"", ".", ".."}:
            raise EnrollmentRecoveryError("invalid_path")
        self._path = requested.parent.resolve() / requested.name
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._box = SecretBox(key)
        self._thread_lock = threading.RLock()
        self._check_parent()

    def records(self) -> tuple[dict[str, Any], ...]:
        with self._locked_file():
            state = self._read_unlocked()
        return tuple(state["offers"][key] for key in sorted(state["offers"]))

    def get(self, offer_id: str) -> dict[str, Any] | None:
        with self._locked_file():
            state = self._read_unlocked()
            record = state["offers"].get(offer_id)
        return record

    def put(self, offer_id: str, record: dict[str, Any]) -> None:
        if (
            not isinstance(offer_id, str)
            or len(offer_id) != 32
            or any(char not in "0123456789abcdef" for char in offer_id)
            or not isinstance(record, dict)
            or record.get("offer_id") != offer_id
        ):
            raise EnrollmentRecoveryError("invalid_record")
        with self._locked_file():
            state = self._read_unlocked()
            if offer_id not in state["offers"] and len(state["offers"]) >= _MAX_OFFERS:
                raise EnrollmentRecoveryError("capacity")
            state["offers"][offer_id] = record
            self._write_unlocked(state)

    def delete(self, offer_id: str) -> None:
        with self._locked_file():
            state = self._read_unlocked()
            if state["offers"].pop(offer_id, None) is not None:
                self._write_unlocked(state)

    @contextmanager
    def _locked_file(self) -> Iterator[None]:
        self._check_parent()
        with self._thread_lock:
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._lock_path, flags, 0o600)
            try:
                self._check_file(os.fstat(fd), "journal lock")
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _read_unlocked(self) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(self._path, flags)
        except FileNotFoundError:
            return {"version": _VERSION, "offers": {}}
        except OSError as exc:
            raise EnrollmentRecoveryError("read_failed") from exc
        try:
            self._check_file(os.fstat(fd), "journal")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > _MAX_BYTES:
            raise EnrollmentRecoveryError("too_large")
        try:
            outer = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_object)
            if not isinstance(outer, dict) or set(outer) != {"version", "ciphertext"}:
                raise ValueError("invalid wrapper")
            if type(outer["version"]) is not int or outer["version"] != _VERSION:
                raise ValueError("invalid version")
            ciphertext = _decode_b64(outer["ciphertext"])
            plaintext = self._box.decrypt(ciphertext)
            state = json.loads(plaintext.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, CryptoError) as exc:
            raise EnrollmentRecoveryError("invalid_journal") from exc
        if (
            not isinstance(state, dict)
            or set(state) != {"version", "offers"}
            or type(state.get("version")) is not int
            or state["version"] != _VERSION
            or not isinstance(state.get("offers"), dict)
            or len(state["offers"]) > _MAX_OFFERS
        ):
            raise EnrollmentRecoveryError("invalid_journal")
        for offer_id, record in state["offers"].items():
            if (
                not isinstance(offer_id, str)
                or len(offer_id) != 32
                or any(char not in "0123456789abcdef" for char in offer_id)
                or not isinstance(record, dict)
                or record.get("offer_id") != offer_id
            ):
                raise EnrollmentRecoveryError("invalid_journal")
        return state

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        plaintext = json.dumps(
            state, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        encrypted = bytes(self._box.encrypt(plaintext))
        outer = json.dumps(
            {"version": _VERSION, "ciphertext": _encode_b64(encrypted)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(outer) > _MAX_BYTES:
            raise EnrollmentRecoveryError("too_large")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(outer)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
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

    def _check_parent(self) -> None:
        try:
            info = self._path.parent.stat()
        except OSError as exc:
            raise EnrollmentRecoveryError("invalid_parent") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise EnrollmentRecoveryError("invalid_parent")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise EnrollmentRecoveryError("invalid_parent")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o700:
            raise EnrollmentRecoveryError("invalid_parent")

    @staticmethod
    def _check_file(info: os.stat_result, label: str) -> None:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise EnrollmentRecoveryError("invalid_file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise EnrollmentRecoveryError("invalid_file")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600:
            raise EnrollmentRecoveryError("invalid_file")


def _encode_b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_b64(value: Any) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_BYTES:
        raise ValueError("invalid encoding")
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in value):
        raise ValueError("invalid encoding")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


__all__ = [
    "EnrollmentRecoveryError",
    "EnrollmentRecoveryJournal",
    "derive_enrollment_destination_recovery_key",
    "derive_enrollment_recovery_key",
]
