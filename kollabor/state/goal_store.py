"""Durable goal storage (SQLite) for the /goal continuation contract.

Implements the persistence layer of
docs/specs/goal-command-harnesses.md (sections 6 and 7):

- one SQLite database per project (goals.db) holding goals, events,
  attempts, and evidence rows;
- a partial unique index enforcing one unfinished goal per conversation;
- record_version CAS on every update, lease_epoch fencing on attempt
  writes, and execution_generation for invalidating pre-pause work;
- crash recovery per 7.4: ambiguous attempts pause for reconciliation
  and are never automatically re-executed.

Storage is transactional; JSON layouts are forbidden by the spec after
round 3 reproduced a lost-update race in a shared index file.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("complete", "cleared")
UNFINISHED_EXCLUDE = TERMINAL_STATUSES
UNFINISHED_SET = frozenset(TERMINAL_STATUSES)
ATTEMPT_STATES = (
    "pending",
    "dispatched",
    "acked",
    "settled",
    "cancelled",
    "ambiguous",
)
EVENT_KINDS = (
    "created",
    "claimed",
    "turn_started",
    "progress",
    "turn_completed",
    "paused",
    "resumed",
    "blocked",
    "usage_limited",
    "budget_limited",
    "complete",
    "cleared",
    "lease_recovered",
    "error",
    "pause_requested",
    "clear_requested",
    "reconcile_paused",
    "generation_bumped",
    "artifact_missing",
)

MAX_REASON_CHARS = 600
REDACTION_VERSION = 1

_JSON_FIELDS = (
    "last_evidence",
    "attachment_refs",
    "task_ids",
    "scope_snapshot",
    "pending_completion",
    "blocker_keys",
)


class GoalStoreError(Exception):
    """Base error for the goal store."""


class GoalNotFound(GoalStoreError):
    """No goal with the given id exists."""


class StaleVersion(GoalStoreError):
    """record_version CAS failed; someone else wrote first."""

    def __init__(self, current_version: int):
        super().__init__(f"stale record_version (current: {current_version})")
        self.current_version = current_version


class UnfinishedGoalExists(GoalStoreError):
    """The conversation already has an unfinished goal."""

    def __init__(self, goal_id: str):
        super().__init__(f"unfinished goal {goal_id} already exists")
        self.goal_id = goal_id


class LeaseHeld(GoalStoreError):
    """Another live daemon holds an unexpired lease."""


class StaleEpoch(GoalStoreError):
    """Attempt write fenced out: lease_epoch does not match."""


class ConstraintError(GoalStoreError):
    """A storage constraint rejected the write (e.g. duplicate attempt)."""


def _redact_reason(reason: Optional[str]) -> Optional[str]:
    """Bound free-text reasons; objectives/logs never carry media bytes."""
    if reason is None:
        return None
    reason = str(reason)
    if len(reason) > MAX_REASON_CHARS:
        reason = reason[: MAX_REASON_CHARS - 3] + "..."
    return reason


@dataclass
class GoalRecord:
    goal_id: str
    conversation_uid: str
    objective: str
    status: str
    project_root: str
    created_at: float
    updated_at: float
    session_id: Optional[str] = None
    turn_count: int = 0
    continuation_seq: int = 0
    tokens_used: Optional[float] = None
    token_budget: Optional[float] = None
    time_used_seconds: float = 0.0
    last_reason: Optional[str] = None
    blocked_reason: Optional[str] = None
    no_progress_count: int = 0
    last_evidence: List[Dict[str, Any]] = field(default_factory=list)
    attachment_refs: List[Dict[str, Any]] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)
    scope_snapshot: Dict[str, Any] = field(default_factory=dict)
    owner_daemon_id: Optional[str] = None
    owner_lease_expires_at: Optional[float] = None
    lease_epoch: int = 0
    record_version: int = 1
    execution_generation: int = 1
    auto_turn_limit: int = 40
    auto_time_limit_s: int = 7200
    completion_declared: bool = False
    pause_requested: bool = False
    clear_requested: bool = False
    pending_completion: Optional[Dict[str, Any]] = None
    blocker_keys: List[str] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.goal_id[:8]

    @property
    def is_unfinished(self) -> bool:
        return self.status not in UNFINISHED_SET


@dataclass
class GoalEvent:
    event_id: str
    goal_id: str
    sequence: int
    timestamp: float
    kind: str
    actor: str = "system"
    daemon_id: Optional[str] = None
    turn_id: Optional[str] = None
    reason: Optional[str] = None
    status_before: Optional[str] = None
    status_after: Optional[str] = None
    usage_delta: Optional[Dict[str, Any]] = None
    evidence_refs: Optional[List[Dict[str, Any]]] = None
    task_ids: Optional[List[str]] = None
    redaction_version: int = REDACTION_VERSION


@dataclass
class GoalAttempt:
    attempt_id: str
    goal_id: str
    conversation_uid: str
    continuation_seq: int
    lease_epoch: int
    execution_generation: int
    state: str
    created_at: float
    updated_at: float
    provider_request_ref: Optional[str] = None
    turn_id: Optional[str] = None
    outcome: Optional[Dict[str, Any]] = None
    usage_deltas: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class GoalEvidence:
    evidence_id: str
    goal_id: str
    attempt_id: str
    turn_id: Optional[str]
    tool_result_id: str
    kind: str
    ref: str
    checksum: Optional[str]
    tool_seq: int
    is_mutating: bool
    created_at: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    goal_id TEXT PRIMARY KEY,
    conversation_uid TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    project_root TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    session_id TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    continuation_seq INTEGER NOT NULL DEFAULT 0,
    tokens_used REAL,
    token_budget REAL,
    time_used_seconds REAL NOT NULL DEFAULT 0,
    last_reason TEXT,
    blocked_reason TEXT,
    no_progress_count INTEGER NOT NULL DEFAULT 0,
    last_evidence TEXT NOT NULL DEFAULT '[]',
    attachment_refs TEXT NOT NULL DEFAULT '[]',
    task_ids TEXT NOT NULL DEFAULT '[]',
    scope_snapshot TEXT NOT NULL DEFAULT '{}',
    owner_daemon_id TEXT,
    owner_lease_expires_at REAL,
    lease_epoch INTEGER NOT NULL DEFAULT 0,
    record_version INTEGER NOT NULL DEFAULT 1,
    execution_generation INTEGER NOT NULL DEFAULT 1,
    auto_turn_limit INTEGER NOT NULL DEFAULT 40,
    auto_time_limit_s INTEGER NOT NULL DEFAULT 7200,
    completion_declared INTEGER NOT NULL DEFAULT 0,
    pause_requested INTEGER NOT NULL DEFAULT 0,
    clear_requested INTEGER NOT NULL DEFAULT 0,
    pending_completion TEXT,
    blocker_keys TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS one_unfinished_goal_per_conversation
    ON goals(conversation_uid)
    WHERE status NOT IN ('complete', 'cleared');
CREATE TABLE IF NOT EXISTS goal_events (
    event_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    daemon_id TEXT,
    turn_id TEXT,
    reason TEXT,
    status_before TEXT,
    status_after TEXT,
    usage_delta TEXT,
    evidence_refs TEXT,
    task_ids TEXT,
    redaction_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_goal_events_seq ON goal_events(goal_id, sequence);
CREATE TABLE IF NOT EXISTS goal_attempts (
    attempt_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    conversation_uid TEXT NOT NULL,
    continuation_seq INTEGER NOT NULL,
    lease_epoch INTEGER NOT NULL,
    execution_generation INTEGER NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    provider_request_ref TEXT,
    turn_id TEXT,
    outcome TEXT,
    usage_deltas TEXT NOT NULL DEFAULT '[]',
    UNIQUE(goal_id, continuation_seq)
);
CREATE TABLE IF NOT EXISTS goal_evidence (
    evidence_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    turn_id TEXT,
    tool_result_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    checksum TEXT,
    tool_seq INTEGER NOT NULL,
    is_mutating INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_evidence_goal ON goal_evidence(goal_id);
"""

# Record fields a caller may update through transition()/update_fields().
_UPDATABLE = {
    "status",
    "session_id",
    "turn_count",
    "continuation_seq",
    "tokens_used",
    "token_budget",
    "time_used_seconds",
    "last_reason",
    "blocked_reason",
    "no_progress_count",
    "last_evidence",
    "attachment_refs",
    "task_ids",
    "scope_snapshot",
    "pending_completion",
    "owner_daemon_id",
    "owner_lease_expires_at",
    "lease_epoch",
    "execution_generation",
    "pause_requested",
    "clear_requested",
    "completion_declared",
    "blocker_keys",
    "auto_turn_limit",
    "auto_time_limit_s",
}


class GoalStore:
    """SQLite-backed goal persistence. One connection per daemon."""

    def __init__(self, db_path: Path | str, clock: Callable[[], float] = time.time):
        self.db_path = Path(db_path)
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,  # manual transactions
            check_same_thread=False,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def _now(self) -> float:
        return self._clock()

    def _begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._conn.execute("COMMIT")

    def _rollback(self) -> None:
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> GoalRecord:
        data = dict(row)
        for key in _JSON_FIELDS:
            raw = data.get(key)
            if isinstance(raw, str):
                try:
                    data[key] = json.loads(raw)
                except (ValueError, TypeError):
                    data[key] = [] if key != "scope_snapshot" else {}
            elif raw is None and key == "scope_snapshot":
                data[key] = {}
            elif raw is None and key != "pending_completion":
                data[key] = []
        data["completion_declared"] = bool(data.get("completion_declared"))
        data["pause_requested"] = bool(data.get("pause_requested"))
        data["clear_requested"] = bool(data.get("clear_requested"))
        return GoalRecord(**data)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> GoalEvent:
        data = dict(row)
        for key in ("usage_delta", "evidence_refs"):
            raw = data.get(key)
            if isinstance(raw, str):
                try:
                    data[key] = json.loads(raw)
                except (ValueError, TypeError):
                    data[key] = None
        raw_tasks = data.get("task_ids")
        if isinstance(raw_tasks, str):
            try:
                data["task_ids"] = json.loads(raw_tasks)
            except (ValueError, TypeError):
                data["task_ids"] = None
        return GoalEvent(**data)

    @staticmethod
    def _row_to_attempt(row: sqlite3.Row) -> GoalAttempt:
        data = dict(row)
        for key in ("outcome", "usage_deltas"):
            raw = data.get(key)
            if isinstance(raw, str):
                try:
                    data[key] = json.loads(raw)
                except (ValueError, TypeError):
                    data[key] = None if key == "outcome" else []
        return GoalAttempt(**data)

    def _next_sequence(self, goal_id: str) -> int:
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM goal_events WHERE goal_id = ?",
            (goal_id,),
        )
        return int(cur.fetchone()[0])

    def _append_event_row(
        self,
        goal_id: str,
        kind: str,
        actor: str = "system",
        daemon_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        reason: Optional[str] = None,
        status_before: Optional[str] = None,
        status_after: Optional[str] = None,
        usage_delta: Optional[Dict[str, Any]] = None,
        evidence_refs: Optional[List[Dict[str, Any]]] = None,
        task_ids: Optional[List[str]] = None,
    ) -> GoalEvent:
        event = GoalEvent(
            event_id=uuid.uuid4().hex,
            goal_id=goal_id,
            sequence=self._next_sequence(goal_id),
            timestamp=self._now(),
            kind=kind,
            actor=actor,
            daemon_id=daemon_id,
            turn_id=turn_id,
            reason=_redact_reason(reason),
            status_before=status_before,
            status_after=status_after,
            usage_delta=usage_delta,
            evidence_refs=evidence_refs,
            task_ids=task_ids,
        )
        self._conn.execute(
            """INSERT INTO goal_events
               (event_id, goal_id, sequence, timestamp, kind, actor, daemon_id,
                turn_id, reason, status_before, status_after, usage_delta,
                evidence_refs, task_ids, redaction_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event.event_id,
                event.goal_id,
                event.sequence,
                event.timestamp,
                event.kind,
                event.actor,
                event.daemon_id,
                event.turn_id,
                event.reason,
                event.status_before,
                event.status_after,
                json.dumps(event.usage_delta) if event.usage_delta else None,
                json.dumps(event.evidence_refs) if event.evidence_refs else None,
                json.dumps(event.task_ids) if event.task_ids else None,
                event.redaction_version,
            ),
        )
        return event

    # ------------------------------------------------------------------
    # goals
    # ------------------------------------------------------------------

    def create(
        self,
        conversation_uid: str,
        objective: str,
        project_root: str,
        session_id: Optional[str] = None,
        token_budget: Optional[float] = None,
        auto_turn_limit: int = 40,
        auto_time_limit_s: int = 7200,
        attachment_refs: Optional[List[Dict[str, Any]]] = None,
        task_ids: Optional[List[str]] = None,
        scope_snapshot: Optional[Dict[str, Any]] = None,
    ) -> GoalRecord:
        """Atomically create one goal. One `BEGIN IMMEDIATE` transaction.

        Raises UnfinishedGoalExists (via the partial unique index) when the
        conversation already has an unfinished goal.
        """
        now = self._now()
        record = GoalRecord(
            goal_id=uuid.uuid4().hex,
            conversation_uid=conversation_uid,
            objective=objective,
            status="active",
            project_root=project_root,
            created_at=now,
            updated_at=now,
            session_id=session_id,
            token_budget=token_budget,
            auto_turn_limit=auto_turn_limit,
            auto_time_limit_s=auto_time_limit_s,
            attachment_refs=list(attachment_refs or []),
            task_ids=list(task_ids or []),
            scope_snapshot=dict(scope_snapshot or {}),
        )
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    """INSERT INTO goals
                       (goal_id, conversation_uid, objective, status, project_root,
                        created_at, updated_at, session_id, token_budget,
                        auto_turn_limit, auto_time_limit_s, attachment_refs,
                        task_ids, scope_snapshot)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.goal_id,
                        record.conversation_uid,
                        record.objective,
                        record.status,
                        record.project_root,
                        record.created_at,
                        record.updated_at,
                        record.session_id,
                        record.token_budget,
                        record.auto_turn_limit,
                        record.auto_time_limit_s,
                        json.dumps(record.attachment_refs),
                        json.dumps(record.task_ids),
                        json.dumps(record.scope_snapshot),
                    ),
                )
                self._append_event_row(
                    record.goal_id,
                    "created",
                    actor="user",
                    status_after="active",
                    task_ids=record.task_ids,
                )
                self._commit()
            except sqlite3.IntegrityError as exc:
                self._rollback()
                existing = self.get_active(record.conversation_uid)
                if existing is not None:
                    raise UnfinishedGoalExists(existing.goal_id) from exc
                raise ConstraintError(str(exc)) from exc
            except Exception:
                self._rollback()
                raise
        return record

    def get(self, goal_id: str) -> GoalRecord:
        cur = self._conn.execute("SELECT * FROM goals WHERE goal_id = ?", (goal_id,))
        row = cur.fetchone()
        if row is None:
            raise GoalNotFound(goal_id)
        return self._row_to_record(row)

    def get_active(self, conversation_uid: str) -> Optional[GoalRecord]:
        cur = self._conn.execute(
            "SELECT * FROM goals WHERE conversation_uid = ? "
            f"AND status NOT IN {UNFINISHED_EXCLUDE_PLACEHOLDER}",
            (conversation_uid,),
        )
        row = cur.fetchone()
        return self._row_to_record(row) if row is not None else None

    def history(self, conversation_uid: str, limit: int = 10) -> List[GoalRecord]:
        cur = self._conn.execute(
            "SELECT * FROM goals WHERE conversation_uid = ? "
            f"AND status IN {UNFINISHED_EXCLUDE_PLACEHOLDER} "
            "ORDER BY updated_at DESC LIMIT ?",
            (conversation_uid, limit),
        )
        return [self._row_to_record(r) for r in cur.fetchall()]

    def transition(
        self,
        goal_id: str,
        expected_version: int,
        event_kind: str,
        *,
        actor: str = "daemon",
        daemon_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        reason: Optional[str] = None,
        event_evidence_refs: Optional[List[Dict[str, Any]]] = None,
        **field_updates: Any,
    ) -> GoalRecord:
        """CAS-update the record and append one event in a single transaction.

        field_updates keys must be in _UPDATABLE. JSON fields accept python
        values and are serialized here.
        """
        bad = set(field_updates) - _UPDATABLE
        if bad:
            raise GoalStoreError(f"non-updatable fields: {sorted(bad)}")
        with self._lock:
            self._begin()
            try:
                cur = self._conn.execute(
                    "SELECT record_version, status FROM goals WHERE goal_id = ?",
                    (goal_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise GoalNotFound(goal_id)
                if row["record_version"] != expected_version:
                    raise StaleVersion(row["record_version"])
                status_before = row["status"]

                sets: List[str] = []
                values: List[Any] = []
                for key, value in field_updates.items():
                    if key in _JSON_FIELDS:
                        value = json.dumps(value)
                    sets.append(f"{key} = ?")
                    values.append(value)
                sets.append("record_version = record_version + 1")
                sets.append("updated_at = ?")
                values.append(self._now())
                values.extend((goal_id, expected_version))
                cur = self._conn.execute(
                    f"UPDATE goals SET {', '.join(sets)} "
                    "WHERE goal_id = ? AND record_version = ?",
                    values,
                )
                if cur.rowcount != 1:
                    raise StaleVersion(expected_version + 1)

                status_after = field_updates.get("status", status_before)
                self._append_event_row(
                    goal_id,
                    event_kind,
                    actor=actor,
                    daemon_id=daemon_id,
                    turn_id=turn_id,
                    reason=reason,
                    status_before=status_before,
                    status_after=status_after,
                    evidence_refs=event_evidence_refs,
                )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get(goal_id)

    def update_fields(
        self, goal_id: str, expected_version: int, **field_updates: Any
    ) -> GoalRecord:
        """CAS field update without a lifecycle event (accounting, leases)."""
        bad = set(field_updates) - _UPDATABLE
        if bad:
            raise GoalStoreError(f"non-updatable fields: {sorted(bad)}")
        with self._lock:
            self._begin()
            try:
                sets: List[str] = []
                values: List[Any] = []
                for key, value in field_updates.items():
                    if key in _JSON_FIELDS:
                        value = json.dumps(value)
                    sets.append(f"{key} = ?")
                    values.append(value)
                sets.append("record_version = record_version + 1")
                sets.append("updated_at = ?")
                values.append(self._now())
                values.extend((goal_id, expected_version))
                cur = self._conn.execute(
                    f"UPDATE goals SET {', '.join(sets)} "
                    "WHERE goal_id = ? AND record_version = ?",
                    values,
                )
                if cur.rowcount != 1:
                    current = self._conn.execute(
                        "SELECT record_version FROM goals WHERE goal_id = ?",
                        (goal_id,),
                    ).fetchone()
                    if current is None:
                        raise GoalNotFound(goal_id)
                    raise StaleVersion(int(current["record_version"]))
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get(goal_id)

    # ------------------------------------------------------------------
    # lease / recovery
    # ------------------------------------------------------------------

    def claim(
        self,
        goal_id: str,
        daemon_id: str,
        lease_seconds: float,
        expected_version: Optional[int] = None,
    ) -> GoalRecord:
        """Claim continuation ownership. Fails while another live lease holds."""
        now = self._now()
        record = self.get(goal_id)
        if (
            record.owner_daemon_id is not None
            and record.owner_daemon_id != daemon_id
            and record.owner_lease_expires_at is not None
            and record.owner_lease_expires_at > now
        ):
            raise LeaseHeld(
                f"daemon {record.owner_daemon_id} holds lease until "
                f"{record.owner_lease_expires_at}"
            )
        version = expected_version or record.record_version
        return self.transition(
            goal_id,
            version,
            "claimed",
            actor="daemon",
            daemon_id=daemon_id,
            owner_daemon_id=daemon_id,
            owner_lease_expires_at=now + lease_seconds,
        )

    def recover_lease(
        self, goal_id: str, new_daemon_id: str, lease_seconds: float = 90.0
    ) -> tuple[GoalRecord, Dict[str, Any]]:
        """Recover an expired lease: bump epoch, fence and reconcile attempts.

        Per 7.4: pending attempts are cancelled (auto-retryable); dispatched
        or acked attempts without settle become ambiguous and the goal pauses
        for reconciliation. Ambiguous work is never automatically re-run.
        """
        now = self._now()
        with self._lock:
            self._begin()
            try:
                record = self.get(goal_id)
                if (
                    record.owner_daemon_id is not None
                    and record.owner_daemon_id != new_daemon_id
                    and record.owner_lease_expires_at is not None
                    and record.owner_lease_expires_at > now
                ):
                    raise LeaseHeld(
                        f"lease held by {record.owner_daemon_id} until "
                        f"{record.owner_lease_expires_at}"
                    )
                new_epoch = record.lease_epoch + 1

                report: Dict[str, Any] = {
                    "cancelled": [],
                    "ambiguous": [],
                    "epoch": new_epoch,
                }
                for att in self.unfinished_attempts(goal_id):
                    if att.state == "pending":
                        self._conn.execute(
                            "UPDATE goal_attempts SET state='cancelled', "
                            "lease_epoch=?, updated_at=? WHERE attempt_id=?",
                            (new_epoch, now, att.attempt_id),
                        )
                        report["cancelled"].append(att.attempt_id)
                    else:  # dispatched / acked without settle
                        self._conn.execute(
                            "UPDATE goal_attempts SET state='ambiguous', "
                            "lease_epoch=?, updated_at=? WHERE attempt_id=?",
                            (new_epoch, now, att.attempt_id),
                        )
                        report["ambiguous"].append(att.attempt_id)

                status = record.status
                reason = None
                if report["ambiguous"]:
                    status = "paused"
                    reason = "reconcile: dispatch outcome unknown after owner crash"

                sets = (
                    "lease_epoch=?",
                    "owner_daemon_id=?",
                    "owner_lease_expires_at=?",
                    "updated_at=?",
                    "record_version=record_version+1",
                )
                if report["ambiguous"]:
                    sets_extended = sets + ("status=?", "blocked_reason=?")
                    self._conn.execute(
                        f"UPDATE goals SET {', '.join(sets_extended)} WHERE goal_id=?",
                        (
                            new_epoch,
                            new_daemon_id,
                            now + lease_seconds,
                            now,
                            status,
                            reason,
                            goal_id,
                        ),
                    )
                else:
                    self._conn.execute(
                        f"UPDATE goals SET {', '.join(sets)} WHERE goal_id=?",
                        (new_epoch, new_daemon_id, now + lease_seconds, now, goal_id),
                    )

                self._append_event_row(
                    goal_id,
                    "lease_recovered",
                    actor="system",
                    daemon_id=new_daemon_id,
                    reason=reason,
                    status_before=record.status,
                    status_after=status,
                )
                if report["ambiguous"]:
                    self._append_event_row(
                        goal_id,
                        "reconcile_paused",
                        actor="system",
                        daemon_id=new_daemon_id,
                        reason=reason,
                        status_before=record.status,
                        status_after="paused",
                    )
                self._commit()
            except Exception:
                self._rollback()
                raise
        return self.get(goal_id), report

    # ------------------------------------------------------------------
    # attempts
    # ------------------------------------------------------------------

    def insert_attempt(
        self,
        goal_id: str,
        continuation_seq: int,
        lease_epoch: int,
        execution_generation: int,
        state: str = "dispatched",
        conversation_uid: Optional[str] = None,
    ) -> GoalAttempt:
        """Commit dispatch intent before provider submission (7.4 step 1)."""
        if state not in ATTEMPT_STATES:
            raise GoalStoreError(f"invalid attempt state: {state}")
        now = self._now()
        uid = conversation_uid
        if uid is None:
            uid = self.get(goal_id).conversation_uid
        attempt = GoalAttempt(
            attempt_id=uuid.uuid4().hex,
            goal_id=goal_id,
            conversation_uid=uid,
            continuation_seq=continuation_seq,
            lease_epoch=lease_epoch,
            execution_generation=execution_generation,
            state=state,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._begin()
            try:
                self._conn.execute(
                    """INSERT INTO goal_attempts
                       (attempt_id, goal_id, conversation_uid, continuation_seq,
                        lease_epoch, execution_generation, state, created_at,
                        updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        attempt.attempt_id,
                        attempt.goal_id,
                        attempt.conversation_uid,
                        attempt.continuation_seq,
                        attempt.lease_epoch,
                        attempt.execution_generation,
                        attempt.state,
                        attempt.created_at,
                        attempt.updated_at,
                    ),
                )
                self._commit()
            except sqlite3.IntegrityError as exc:
                self._rollback()
                raise ConstraintError(
                    f"attempt already exists for seq {continuation_seq}"
                ) from exc
            except Exception:
                self._rollback()
                raise
        return attempt

    def _fenced_attempt_update(
        self, attempt_id: str, lease_epoch: int, sets: str, values: tuple
    ) -> GoalAttempt:
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE goal_attempts SET {sets}, updated_at=? "
                "WHERE attempt_id=? AND lease_epoch=?",
                (*values, self._now(), attempt_id, lease_epoch),
            )
            if cur.rowcount != 1:
                row = self._conn.execute(
                    "SELECT lease_epoch FROM goal_attempts WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()
                if row is None:
                    raise GoalNotFound(attempt_id)
                raise StaleEpoch(
                    f"attempt fenced: wrote epoch {lease_epoch}, "
                    f"current {row['lease_epoch']}"
                )
        return self.get_attempt(attempt_id)

    def mark_ack(
        self, attempt_id: str, lease_epoch: int, provider_request_ref: Optional[str]
    ) -> GoalAttempt:
        """Provider accepted the request (7.4 step 2)."""
        return self._fenced_attempt_update(
            attempt_id,
            lease_epoch,
            "state='acked', provider_request_ref=?",
            (provider_request_ref,),
        )

    def settle_attempt(
        self,
        attempt_id: str,
        lease_epoch: int,
        turn_id: Optional[str],
        outcome: Dict[str, Any],
        usage_deltas: Optional[List[Dict[str, Any]]] = None,
    ) -> GoalAttempt:
        """Turn settled with typed outcome + per-request usage (7.4 step 3)."""
        return self._fenced_attempt_update(
            attempt_id,
            lease_epoch,
            "state='settled', turn_id=?, outcome=?, usage_deltas=?",
            (turn_id, json.dumps(outcome), json.dumps(usage_deltas or [])),
        )

    def cancel_attempt(self, attempt_id: str, lease_epoch: int) -> GoalAttempt:
        return self._fenced_attempt_update(
            attempt_id, lease_epoch, "state='cancelled'", ()
        )

    def get_attempt(self, attempt_id: str) -> GoalAttempt:
        cur = self._conn.execute(
            "SELECT * FROM goal_attempts WHERE attempt_id = ?", (attempt_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise GoalNotFound(attempt_id)
        return self._row_to_attempt(row)

    def unfinished_attempts(self, goal_id: str) -> List[GoalAttempt]:
        cur = self._conn.execute(
            "SELECT * FROM goal_attempts WHERE goal_id = ? "
            "AND state NOT IN ('settled', 'cancelled', 'ambiguous') "
            "ORDER BY continuation_seq",
            (goal_id,),
        )
        return [self._row_to_attempt(r) for r in cur.fetchall()]

    def attempts(self, goal_id: str) -> List[GoalAttempt]:
        cur = self._conn.execute(
            "SELECT * FROM goal_attempts WHERE goal_id = ? ORDER BY continuation_seq",
            (goal_id,),
        )
        return [self._row_to_attempt(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    def record_evidence(
        self,
        goal_id: str,
        attempt_id: str,
        tool_result_id: str,
        kind: str,
        ref: str,
        tool_seq: int,
        turn_id: Optional[str] = None,
        checksum: Optional[str] = None,
        is_mutating: bool = False,
    ) -> GoalEvidence:
        """Runtime records evidence provenance itself (8.5); model claims
        alone never establish it."""
        now = self._now()
        ev = GoalEvidence(
            evidence_id=uuid.uuid4().hex,
            goal_id=goal_id,
            attempt_id=attempt_id,
            turn_id=turn_id,
            tool_result_id=tool_result_id,
            kind=kind,
            ref=ref,
            checksum=checksum,
            tool_seq=tool_seq,
            is_mutating=is_mutating,
            created_at=now,
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO goal_evidence
                   (evidence_id, goal_id, attempt_id, turn_id, tool_result_id,
                    kind, ref, checksum, tool_seq, is_mutating, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ev.evidence_id,
                    ev.goal_id,
                    ev.attempt_id,
                    ev.turn_id,
                    ev.tool_result_id,
                    ev.kind,
                    ev.ref,
                    ev.checksum,
                    ev.tool_seq,
                    int(ev.is_mutating),
                    ev.created_at,
                ),
            )
        return ev

    def evidence_for(self, goal_id: str) -> List[GoalEvidence]:
        cur = self._conn.execute(
            "SELECT * FROM goal_evidence WHERE goal_id = ? ORDER BY created_at",
            (goal_id,),
        )
        rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_mutating"] = bool(d["is_mutating"])
            out.append(GoalEvidence(**d))
        return out

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------

    def events(self, goal_id: str, limit: int = 50) -> List[GoalEvent]:
        cur = self._conn.execute(
            "SELECT * FROM goal_events WHERE goal_id = ? "
            "ORDER BY sequence DESC LIMIT ?",
            (goal_id, limit),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]


# "complete","cleared" placeholders built once (fixed tuple, SQL-injection safe)
UNFINISHED_EXCLUDE_PLACEHOLDER = (
    "(" + ",".join(f"'{s}'" for s in UNFINISHED_EXCLUDE) + ")"
)


def get_goals_dir() -> Path:
    """Project-scoped goals data directory per the config path policy."""
    from kollabor_config.config_utils import get_project_data_dir

    try:
        from plugins.hub.project_scope import resolve_project_root

        root = resolve_project_root()
    except Exception:
        root = None
    d = get_project_data_dir(root) / "goals"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def open_default_store() -> GoalStore:
    """Open the canonical per-project goal store."""
    return GoalStore(get_goals_dir() / "goals.db")
