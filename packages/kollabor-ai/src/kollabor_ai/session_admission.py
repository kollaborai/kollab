"""Local admission state machine; it never grants a snapshot permit.

This isolated scaffold does not coordinate producers, processes, or durable
history. It must not be used as evidence for compaction or session replacement.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Literal
from uuid import uuid4


class AdmissionError(Exception):
    """A token, identifier, or state transition violates the local contract."""


@dataclass(frozen=True)
class SessionKey:
    session_id: str
    incarnation: str


@dataclass(frozen=True)
class AdmissionToken:
    key: SessionKey
    op_id: str
    kind: Literal["native", "acp"]


class AdmissionState(Enum):
    PENDING = "pending"
    WRITTEN = "written"
    COMMITTED = "committed"
    ABORTED = "aborted"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class SnapshotDecision:
    allowed: bool
    reason: Literal["busy", "pending", "indeterminate", "coverage_unverified"]


@dataclass
class _Entry:
    token: AdmissionToken
    state: AdmissionState = AdmissionState.PENDING
    record_id: str | None = None


def _identifier(value: object) -> bool:
    return type(value) is str and bool(value.strip())


class LocalAdmissionFence:
    """Bounded, instance-owned local state; never a durable admission fence."""

    def __init__(self, key: SessionKey, *, max_entries: int = 4096) -> None:
        if (
            type(key) is not SessionKey
            or not _identifier(key.session_id)
            or not _identifier(key.incarnation)
            or type(max_entries) is not int
            or not 1 <= max_entries <= 4096
        ):
            raise AdmissionError("invalid session key or capacity")
        self._key = key
        self._max_entries = max_entries
        self._state_lock = Lock()
        self._entries: dict[str, _Entry] = {}
        self._records: set[str] = set()
        self._histories: set[str] = set()

    @contextmanager
    def _guard(self):
        if not self._state_lock.acquire(blocking=False):
            raise AdmissionError("admission fence busy")
        try:
            yield
        finally:
            self._state_lock.release()

    def _entry(self, token: AdmissionToken) -> _Entry:
        # Identity is intentional: an equal copy or a token from another fence
        # is not an admission made by this instance, even for equal SessionKey.
        if type(token) is not AdmissionToken or not _identifier(token.op_id):
            raise AdmissionError("invalid token")
        entry = self._entries.get(token.op_id)
        if entry is None or entry.token is not token:
            raise AdmissionError("foreign or forged token")
        return entry

    def reserve(self, kind: Literal["native", "acp"]) -> AdmissionToken:
        if type(kind) is not str or kind not in ("native", "acp"):
            raise AdmissionError("invalid admission kind")
        token = AdmissionToken(self._key, uuid4().hex, kind)
        with self._guard():
            if len(self._entries) >= self._max_entries or token.op_id in self._entries:
                raise AdmissionError("admission capacity or identifier collision")
            self._entries[token.op_id] = _Entry(token)
        return token

    def mark_written(self, token: AdmissionToken, *, record_id: str) -> None:
        if not _identifier(record_id):
            raise AdmissionError("invalid record id")
        with self._guard():
            entry = self._entry(token)
            if entry.state is not AdmissionState.PENDING or record_id in self._records:
                raise AdmissionError("invalid write transition or duplicate record")
            entry.record_id = record_id
            entry.state = AdmissionState.WRITTEN
            self._records.add(record_id)

    def commit_native_batch(
        self, tokens: tuple[AdmissionToken, ...], *, history_id: str, record_ids: tuple[str, ...]
    ) -> None:
        if (
            type(tokens) is not tuple
            or not tokens
            or type(record_ids) is not tuple
            or len(tokens) != len(record_ids)
            or len(tokens) > self._max_entries
            or not _identifier(history_id)
            or any(not _identifier(value) for value in record_ids)
        ):
            raise AdmissionError("invalid batch")
        with self._guard():
            entries = [self._entry(token) for token in tokens]
            if (
                len({id(entry) for entry in entries}) != len(entries)
                or history_id in self._histories
                or any(entry.token.kind != "native" or entry.state is not AdmissionState.WRITTEN for entry in entries)
                or tuple(entry.record_id for entry in entries) != record_ids
            ):
                raise AdmissionError("invalid batch membership or duplicate history")
            for entry in entries:
                entry.state = AdmissionState.COMMITTED
            self._histories.add(history_id)

    def commit_acp(self, token: AdmissionToken, *, history_id: str, record_id: str) -> None:
        if not _identifier(history_id) or not _identifier(record_id):
            raise AdmissionError("invalid ACP identifiers")
        with self._guard():
            entry = self._entry(token)
            if (
                entry.token.kind != "acp"
                or entry.state is not AdmissionState.WRITTEN
                or entry.record_id != record_id
                or history_id in self._histories
            ):
                raise AdmissionError("invalid ACP membership or duplicate history")
            entry.state = AdmissionState.COMMITTED
            self._histories.add(history_id)

    def abort_before_write(self, token: AdmissionToken) -> None:
        with self._guard():
            entry = self._entry(token)
            if entry.state is not AdmissionState.PENDING:
                raise AdmissionError("cannot abort after possible write")
            entry.state = AdmissionState.ABORTED

    def mark_indeterminate(self, token: AdmissionToken) -> None:
        with self._guard():
            entry = self._entry(token)
            if entry.state not in (AdmissionState.PENDING, AdmissionState.WRITTEN):
                raise AdmissionError("invalid indeterminate transition")
            entry.state = AdmissionState.INDETERMINATE

    def state(self, token: AdmissionToken) -> AdmissionState:
        with self._guard():
            return self._entry(token).state

    def try_snapshot(self) -> SnapshotDecision:
        if not self._state_lock.acquire(blocking=False):
            return SnapshotDecision(False, "busy")
        try:
            states = {entry.state for entry in self._entries.values()}
            if states & {AdmissionState.PENDING, AdmissionState.WRITTEN}:
                return SnapshotDecision(False, "pending")
            if AdmissionState.INDETERMINATE in states:
                return SnapshotDecision(False, "indeterminate")
            return SnapshotDecision(False, "coverage_unverified")
        finally:
            self._state_lock.release()
