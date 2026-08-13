"""Task Ledger - compaction-proof task persistence.

TaskCards live on disk as JSON files. They get injected into
the system prompt on every LLM turn via the roster injection hook.
Compaction cannot touch the system prompt, so agents can never
forget their active tasks.

Lifecycle:
  assign -> active -> (checkpoint)* -> complete/fail -> QA review -> closed
  assign -> standby -> active -> (checkpoint)* -> ... (standby = no cron)
  assign -> obsolete/cancelled (audited terminal state, no cron)
"""

import fcntl
import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskCard:
    """A task assignment that survives context compaction."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "active"  # active, standby, paused, done, failed, qa_review, closed, cancelled, obsolete
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # Contract (set on creation)
    assigner: str = ""  # who assigned this
    assignee: str = ""  # who owns this
    directive: str = ""  # what to do
    deliverable: str = ""  # expected output format
    report_to: str = ""  # who gets the result
    priority: int = 5  # 1-10
    timeout_seconds: float = 0  # 0 = no timeout

    # Auto-cron (task reminder)
    cron_interval: float = 300  # remind every 5 min by default
    cron_active: bool = True  # auto-enabled on assignment
    cron_ttl_seconds: float = 7200  # auto-silence cron after 2h of no updates
    snoozed_until: float = 0  # agent asked for quiet until this timestamp

    # Progress
    checkpoints: List[Dict] = field(default_factory=list)

    # Result
    result: Optional[str] = None
    error: Optional[str] = None
    qa_reviewer: Optional[str] = None
    qa_passed: Optional[bool] = None
    qa_notes: Optional[str] = None

    # Terminalization audit
    terminal_reason: Optional[str] = None
    terminal_actor: Optional[str] = None
    terminal_message_id: Optional[str] = None
    terminalized_at: Optional[float] = None

    # Metadata
    project: str = field(default_factory=os.getcwd)

    def add_checkpoint(self, note: str, data: Optional[Dict] = None):
        self.checkpoints.append(
            {
                "ts": time.time(),
                "note": note,
                "data": data or {},
            }
        )
        self.updated_at = time.time()

    def elapsed_seconds(self) -> float:
        return time.time() - self.created_at

    def elapsed_str(self) -> str:
        s = self.elapsed_seconds()
        if s < 60:
            return f"{int(s)}s"
        if s < 3600:
            return f"{int(s / 60)}m"
        return f"{int(s / 3600)}h{int((s % 3600) / 60)}m"

    def is_snoozed(self) -> bool:
        return self.snoozed_until > time.time()

    def snooze_remaining_str(self) -> str:
        left = self.snoozed_until - time.time()
        if left <= 0:
            return ""
        return f"{int(left / 60)}m" if left >= 60 else f"{int(left)}s"

    def is_timed_out(self) -> bool:
        return (
            self.timeout_seconds > 0 and self.elapsed_seconds() > self.timeout_seconds
        )

    def last_checkpoint_note(self) -> str:
        if self.checkpoints:
            return str(self.checkpoints[-1].get("note", ""))
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "assigner": self.assigner,
            "assignee": self.assignee,
            "directive": self.directive,
            "deliverable": self.deliverable,
            "report_to": self.report_to,
            "priority": self.priority,
            "timeout_seconds": self.timeout_seconds,
            "cron_interval": self.cron_interval,
            "cron_active": self.cron_active,
            "cron_ttl_seconds": self.cron_ttl_seconds,
            "snoozed_until": self.snoozed_until,
            "checkpoints": self.checkpoints,
            "result": self.result,
            "error": self.error,
            "qa_reviewer": self.qa_reviewer,
            "qa_passed": self.qa_passed,
            "qa_notes": self.qa_notes,
            "terminal_reason": self.terminal_reason,
            "terminal_actor": self.terminal_actor,
            "terminal_message_id": self.terminal_message_id,
            "terminalized_at": self.terminalized_at,
            "project": self.project,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TaskCard":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class TaskLedger:
    """Manages TaskCards on disk. Survives any crash or compaction."""

    TERMINAL_STATUSES = frozenset(
        {"done", "failed", "closed", "cancelled", "obsolete"}
    )
    EXPLICIT_TERMINAL_STATUSES = frozenset({"cancelled", "obsolete"})

    def __init__(self, tasks_dir: Optional[str] = None):
        if tasks_dir:
            self._tasks_dir = Path(tasks_dir)
        else:
            from .presence import get_hub_dir

            self._tasks_dir = get_hub_dir() / "tasks"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

    def _task_path(self, task_id: str) -> Path:
        return self._tasks_dir / f"{task_id}.json"

    def _pending_replies_path(self) -> Path:
        return self._tasks_dir / "_pending_replies.json"

    @contextmanager
    def _file_lock(self, path: Path):
        """Hold an advisory lock beside a shared JSON file."""
        lock_path = path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _load_json_object(path: Path) -> Any:
        """Load one JSON value, recovering a valid prefix after old corruption."""
        raw = path.read_text()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            value, end = decoder.raw_decode(raw.lstrip())
            if not isinstance(value, dict):
                raise
            logger.warning("Recovered valid JSON prefix from corrupted ledger file %s", path)
            return value

    @staticmethod
    def _write_json_atomic(path: Path, data: Any) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _read_pending_replies_unlocked(self) -> List[Dict[str, Any]]:
        path = self._pending_replies_path()
        if not path.exists():
            return []
        try:
            data = self._load_json_object(path)
            replies = data.get("expected_replies", [])
            return replies if isinstance(replies, list) else []
        except Exception as e:
            logger.debug(f"Failed to load pending replies: {e}")
            return []

    def _write_pending_replies_unlocked(self, replies: List[Dict[str, Any]]) -> None:
        self._write_json_atomic(
            self._pending_replies_path(), {"expected_replies": replies}
        )

    def _load_pending_replies(self) -> List[Dict[str, Any]]:
        with self._file_lock(self._pending_replies_path()):
            return self._read_pending_replies_unlocked()

    def _save_pending_replies(self, replies: List[Dict[str, Any]]) -> None:
        with self._file_lock(self._pending_replies_path()):
            self._write_pending_replies_unlocked(replies)

    def _save(self, card: TaskCard) -> None:
        card.updated_at = time.time()
        self._write_card(card)

    def _save_preserve(self, card: TaskCard) -> None:
        """Save card without updating updated_at.

        Used for internal state changes (e.g. cron silencing) that
        should not reset the TTL timer.
        """
        self._write_card(card)

    def _write_card(self, card: TaskCard) -> None:
        path = self._task_path(card.id)
        with self._file_lock(path):
            self._write_json_atomic(path, card.to_dict())

    def _load(self, task_id: str) -> Optional[TaskCard]:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            return TaskCard.from_dict(self._load_json_object(path))
        except Exception as e:
            logger.debug(f"Failed to load task {task_id}: {e}")
            return None

    def create(
        self,
        assigner: str,
        assignee: str,
        directive: str,
        deliverable: str = "",
        report_to: str = "",
        priority: int = 5,
        timeout: float = 0,
        cron_interval: float = 300,
        status: str = "active",
    ) -> TaskCard:
        card = TaskCard(
            assigner=assigner,
            assignee=assignee,
            directive=directive,
            deliverable=deliverable,
            report_to=report_to or assigner,
            priority=priority,
            timeout_seconds=timeout,
            cron_interval=cron_interval,
            status=status,
            cron_active=(status != "standby"),
        )
        self._save(card)
        logger.info(
            f"Task {card.id} created: {assigner} -> {assignee}: {directive[:60]}"
        )
        return card

    def get(self, task_id: str) -> Optional[TaskCard]:
        return self._load(task_id)

    def expect_reply(
        self,
        *,
        task_id: str,
        assignee: str,
        requested_by: str,
        message_id: str,
        deadline_seconds: int,
    ) -> None:
        with self._file_lock(self._pending_replies_path()):
            replies = self._read_pending_replies_unlocked()
            replies.append(
                {
                    "task_id": task_id,
                    "assignee": assignee,
                    "requested_by": requested_by,
                    "message_id": message_id,
                    "created_at": time.time(),
                    "deadline_seconds": deadline_seconds,
                    "status": "pending",
                }
            )
            self._write_pending_replies_unlocked(replies)

    # Replies older than this are auto-expired on read.
    PENDING_REPLY_TTL = 86400  # 24 hours

    def pending_replies(self) -> List[Dict[str, Any]]:
        """Return pending replies, auto-expiring stale ones.

        Entries older than PENDING_REPLY_TTL are marked expired and
        pruned from the file. This prevents the pending list from
        growing unbounded when agents go offline without resolving.
        """
        with self._file_lock(self._pending_replies_path()):
            replies = self._read_pending_replies_unlocked()
            now = time.time()
            changed = False
            kept = []
            for item in replies:
                if item.get("status") == "pending":
                    age = now - item.get("created_at", 0)
                    if age > self.PENDING_REPLY_TTL:
                        item["status"] = "expired"
                        item["expired_at"] = now
                        changed = True
                kept.append(item)
            if changed:
                # Prune: keep only non-expired entries to prevent unbounded growth
                kept = [r for r in kept if r.get("status") != "expired"]
                self._write_pending_replies_unlocked(kept)
            return [item for item in kept if item.get("status") == "pending"]

    def resolve_reply(
        self,
        *,
        assignee: str,
        evidence: str,
        message_id: str,
    ) -> bool:
        strong_markers = (
            "task complete",
            "shipped",
            "resolved",
            "verdict",
            "no blockers",
            "review delivered",
            "done",
        )
        if not any(marker in evidence.lower() for marker in strong_markers):
            return False

        with self._file_lock(self._pending_replies_path()):
            replies = self._read_pending_replies_unlocked()
            for item in replies:
                if item.get("assignee") == assignee and item.get("status") == "pending":
                    item["status"] = "resolved"
                    item["resolved_by_message_id"] = message_id
                    item["resolved_at"] = time.time()
                    self._write_pending_replies_unlocked(replies)
                    return True
            return False

    def get_active_for(self, identity: str) -> List[TaskCard]:
        """Get all active tasks assigned to this agent."""
        result = []
        for f in self._tasks_dir.glob("*.json"):
            card = self._load(f.stem)
            if (
                card
                and card.assignee == identity
                and card.status in ("active", "standby", "qa_review")
            ):
                result.append(card)
        return sorted(result, key=lambda c: c.priority)

    def get_all(self, status: Optional[str] = None) -> List[TaskCard]:
        result = []
        for f in self._tasks_dir.glob("*.json"):
            card = self._load(f.stem)
            if card and (status is None or card.status == status):
                result.append(card)
        return result

    def checkpoint(self, task_id: str, note: str, data: Optional[Dict] = None) -> bool:
        card = self._load(task_id)
        if not card:
            return False
        card.add_checkpoint(note, data)
        self._save(card)
        return True

    # Longest a single snooze may run. Prevents an agent from silencing a
    # task for the rest of the session with one tag.
    MAX_SNOOZE_SECONDS = 3600

    def snooze(self, task_id: str, minutes: float) -> Optional[TaskCard]:
        """Quiet reminders for this task without changing its status.

        The card stays active and stays in the system prompt -- only the
        cron and the checkpoint nudge go quiet. Saved with _save_preserve
        so a snooze does not reset the cron TTL clock.
        """
        card = self._load(task_id)
        if not card:
            return None
        seconds = max(0.0, min(minutes * 60.0, self.MAX_SNOOZE_SECONDS))
        card.snoozed_until = time.time() + seconds
        self._save_preserve(card)
        logger.info(f"Task {card.id} snoozed for {int(seconds / 60)}m")
        return card

    def complete(self, task_id: str, result: str) -> Optional[TaskCard]:
        card = self._load(task_id)
        if not card:
            return None
        card.status = "done"
        card.result = result
        card.cron_active = False
        self._save(card)
        logger.info(f"Task {card.id} completed by {card.assignee}")
        return card

    def request_qa(self, task_id: str, result: str) -> Optional[TaskCard]:
        """Mark task as done and request QA review."""
        card = self._load(task_id)
        if not card:
            return None
        card.status = "qa_review"
        card.result = result
        card.cron_active = False
        self._save(card)
        return card

    def qa_approve(
        self, task_id: str, reviewer: str, notes: str = ""
    ) -> Optional[TaskCard]:
        card = self._load(task_id)
        if not card:
            return None
        card.status = "closed"
        card.qa_reviewer = reviewer
        card.qa_passed = True
        card.qa_notes = notes
        card.cron_active = False
        self._save(card)
        return card

    def qa_reject(self, task_id: str, reviewer: str, notes: str) -> Optional[TaskCard]:
        card = self._load(task_id)
        if not card:
            return None
        card.status = "active"  # re-activate for rework
        card.qa_reviewer = reviewer
        card.qa_passed = False
        card.qa_notes = notes
        card.cron_active = True  # re-enable cron
        self._save(card)
        return card

    def cancel(self, task_id: str) -> bool:
        """Compatibility wrapper for the audited cancelled terminal state."""
        return (
            self.terminalize(
                task_id,
                status="cancelled",
                reason="cancelled via task ledger",
            )
            is not None
        )

    def _resolve_replies_for_task(
        self,
        task_id: str,
        *,
        terminal_status: str,
        reason: str,
        message_id: str = "",
    ) -> int:
        with self._file_lock(self._pending_replies_path()):
            replies = self._read_pending_replies_unlocked()
            now = time.time()
            resolved = 0
            for item in replies:
                if item.get("task_id") != task_id or item.get("status") != "pending":
                    continue
                item["status"] = "resolved"
                item["resolution"] = "task_terminalized"
                item["terminal_status"] = terminal_status
                item["resolved_reason"] = reason
                item["resolved_by_message_id"] = message_id
                item["resolved_at"] = now
                resolved += 1
            if resolved:
                self._write_pending_replies_unlocked(replies)
            return resolved

    def terminalize(
        self,
        task_id: str,
        *,
        status: str,
        reason: str,
        actor: str = "",
        message_id: str = "",
    ) -> Optional[TaskCard]:
        """Move a task to an audited, non-cron terminal state.

        Terminalization is idempotent: a stale or duplicate directive cannot
        reopen a finished task or append duplicate audit checkpoints. The
        reason is mandatory so the task does not disappear without an
        explanation.
        """
        status = (status or "").strip().lower()
        reason = (reason or "").strip()
        if status not in self.EXPLICIT_TERMINAL_STATUSES:
            raise ValueError(f"unsupported terminal task status: {status!r}")
        if not reason:
            raise ValueError("terminalization reason is required")

        card = self._load(task_id)
        if not card:
            return None
        if card.status in self.TERMINAL_STATUSES:
            return card

        card.status = status
        card.cron_active = False
        card.snoozed_until = 0
        card.terminal_reason = reason
        card.terminal_actor = actor or None
        card.terminal_message_id = message_id or None
        card.terminalized_at = time.time()
        card.add_checkpoint(
            f"task {status}: {reason}",
            {
                "status": status,
                "reason": reason,
                "actor": actor,
                "message_id": message_id,
            },
        )
        self._save(card)
        self._resolve_replies_for_task(
            card.id,
            terminal_status=status,
            reason=reason,
            message_id=message_id,
        )
        logger.info("Task %s terminalized as %s: %s", card.id, status, reason)
        return card

    def get_cron_due(self) -> List[TaskCard]:
        """Get tasks whose cron reminder is due.

        Excludes:
        - Tasks with status != "active" (standby, qa_review, closed, etc.)
        - Tasks with cron_active=False
        - Tasks the assignee snoozed (see snooze())
        - Tasks that have exceeded their cron_ttl_seconds with no updates
          (auto-silences stale tasks to prevent infinite reactivation)
        """
        now = time.time()
        due = []
        for card in self.get_all(status="active"):
            if not card.cron_active:
                continue
            if card.snoozed_until > now:
                continue

            # TTL auto-expire: if no update in cron_ttl_seconds, silence cron
            ttl = card.cron_ttl_seconds if card.cron_ttl_seconds > 0 else 0
            if ttl > 0 and (now - card.updated_at) > ttl:
                logger.info(
                    f"Task {card.id} cron auto-expired "
                    f"(no update in {int(ttl / 3600)}h)"
                )
                card.cron_active = False
                self._save_preserve(card)
                continue

            last_update = card.updated_at
            if now - last_update >= card.cron_interval:
                due.append(card)
        return due

    def cleanup_stale(self, max_age_hours: float = 24) -> int:
        cutoff = time.time() - (max_age_hours * 3600)
        count = 0
        for card in self.get_all():
            if (
                card.status
                in ("closed", "failed", "done", "qa_review", "cancelled", "obsolete")
                and card.updated_at < cutoff
            ):
                self._task_path(card.id).unlink(missing_ok=True)
                count += 1
        return count
