"""Task Ledger - compaction-proof task persistence.

TaskCards live on disk as JSON files. They get injected into
the system prompt on every LLM turn via the roster injection hook.
Compaction cannot touch the system prompt, so agents can never
forget their active tasks.

Lifecycle:
  assign -> active -> (checkpoint)* -> complete/fail -> QA review -> closed
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskCard:
    """A task assignment that survives context compaction."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "active"  # active, paused, done, failed, qa_review, closed
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

    # Progress
    checkpoints: List[Dict] = field(default_factory=list)

    # Result
    result: Optional[str] = None
    error: Optional[str] = None
    qa_reviewer: Optional[str] = None
    qa_passed: Optional[bool] = None
    qa_notes: Optional[str] = None

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
            "checkpoints": self.checkpoints,
            "result": self.result,
            "error": self.error,
            "qa_reviewer": self.qa_reviewer,
            "qa_passed": self.qa_passed,
            "qa_notes": self.qa_notes,
            "project": self.project,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TaskCard":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class TaskLedger:
    """Manages TaskCards on disk. Survives any crash or compaction."""

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

    def _load_pending_replies(self) -> List[Dict[str, Any]]:
        path = self._pending_replies_path()
        if not path.exists():
            return []
        try:
            with open(path) as f:
                data = json.load(f)
            replies = data.get("expected_replies", [])
            return replies if isinstance(replies, list) else []
        except Exception as e:
            logger.debug(f"Failed to load pending replies: {e}")
            return []

    def _save_pending_replies(self, replies: List[Dict[str, Any]]) -> None:
        path = self._pending_replies_path()
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"expected_replies": replies}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(path)

    def _save(self, card: TaskCard) -> None:
        card.updated_at = time.time()
        path = self._task_path(card.id)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(card.to_dict(), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(path)

    def _load(self, task_id: str) -> Optional[TaskCard]:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return TaskCard.from_dict(json.load(f))
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
        replies = self._load_pending_replies()
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
        self._save_pending_replies(replies)

    def pending_replies(self) -> List[Dict[str, Any]]:
        return [
            item
            for item in self._load_pending_replies()
            if item.get("status") == "pending"
        ]

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

        replies = self._load_pending_replies()
        for item in replies:
            if item.get("assignee") == assignee and item.get("status") == "pending":
                item["status"] = "resolved"
                item["resolved_by_message_id"] = message_id
                item["resolved_at"] = time.time()
                self._save_pending_replies(replies)
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
                and card.status in ("active", "qa_review")
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

    def fail(self, task_id: str, error: str) -> Optional[TaskCard]:
        card = self._load(task_id)
        if not card:
            return None
        card.status = "failed"
        card.error = error
        card.cron_active = False
        self._save(card)
        return card

    def cancel(self, task_id: str) -> bool:
        card = self._load(task_id)
        if not card:
            return False
        card.status = "closed"
        card.cron_active = False
        self._save(card)
        return True

    def get_cron_due(self) -> List[TaskCard]:
        """Get tasks whose cron reminder is due."""
        now = time.time()
        due = []
        for card in self.get_all(status="active"):
            if not card.cron_active:
                continue
            last_update = card.updated_at
            if now - last_update >= card.cron_interval:
                due.append(card)
        return due

    def cleanup_stale(self, max_age_hours: float = 24) -> int:
        cutoff = time.time() - (max_age_hours * 3600)
        count = 0
        for card in self.get_all():
            if card.status in ("closed", "failed", "done") and card.updated_at < cutoff:
                self._task_path(card.id).unlink(missing_ok=True)
                count += 1
        return count
