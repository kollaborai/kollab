"""Coordinator election and work queue management."""

import json
import logging
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from kollabor_agent.runtime import AgentRuntime
from kollabor_config.file_lock import locked_file, try_lock, unlock

from .models import WorkSlot
from .presence import _atomic_write, get_hub_dir, pid_alive
from .rosters import roster_names
from .task_ledger import TERMINAL_TASK_STATUSES

logger = logging.getLogger(__name__)


def _append_stale_upstream_note(slot: WorkSlot, note: str) -> bool:
    """Append one unique note line to a slot without changing its status."""
    value = str(note or "").strip()
    if not value:
        return False
    existing = [
        line.strip()
        for line in str(slot.stale_upstream_note or "").splitlines()
        if line.strip()
    ]
    if value in existing:
        return False
    existing.append(value)
    slot.stale_upstream_note = "\n".join(existing)
    return True


def _github_repo_selector(project: str) -> Optional[str]:
    """Return gh's HOST/OWNER/REPO selector for a local project directory."""
    try:
        proc = subprocess.run(
            ["git", "-C", project, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SECS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    remote = proc.stdout.strip()
    if not remote:
        return None

    if "://" in remote:
        parsed = urlsplit(remote)
        host = parsed.hostname
        path = parsed.path
    else:
        match = re.fullmatch(r"(?:[^@]+@)?([^:/]+):(.+)", remote)
        if not match:
            return None
        host, path = match.groups()
    parts = [part for part in path.removesuffix(".git").split("/") if part]
    if not host or len(parts) < 2:
        return None
    return "/".join((host, parts[-2], parts[-1]))


class CoordinatorElection:
    """File-lock based coordinator election.

    Uses flock() on hub.lock for atomic, kernel-guaranteed election.
    When the coordinator process dies (any reason), the lock releases
    automatically and another agent can claim it.
    """

    def __init__(self):
        self._hub_dir = get_hub_dir()
        self._lock_path = self._hub_dir / "hub.lock"
        self._state_path = self._hub_dir / "state.json"
        self._lock_fd = None
        self._is_coordinator = False
        self._epoch = 0

    def try_become_coordinator(self, identity: "AgentRuntime") -> bool:
        """Attempt to acquire the coordinator lock.

        Returns True if this agent is now the coordinator.
        Non-blocking - returns immediately if lock is held.
        """
        try:
            self._lock_fd = open(self._lock_path, "w")
            if not try_lock(self._lock_fd.fileno()):
                raise BlockingIOError("coordinator lock is held")

            # We got the lock - we're the coordinator
            self._is_coordinator = True
            self._epoch = self._read_epoch() + 1

            # Write state
            state = {
                "coordinator_id": identity.agent_id,
                "coordinator_identity": identity.identity,  # external attr
                "coordinator_pid": identity.pid,
                "epoch": self._epoch,
                "elected_at": time.time(),
            }
            _atomic_write(self._state_path, state)

            # Write lock info
            self._lock_fd.seek(0)
            self._lock_fd.truncate()
            json.dump(state, self._lock_fd)
            self._lock_fd.flush()

            logger.info(f"Elected as coordinator (epoch {self._epoch})")
            return True

        except (BlockingIOError, OSError):
            # Lock is held by another process
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            self._is_coordinator = False
            return False

    def release(self) -> None:
        """Release the coordinator lock."""
        if self._lock_fd:
            try:
                unlock(self._lock_fd.fileno())
                self._lock_fd.close()
            except Exception as e:
                logger.error(f"Error releasing lock: {e}")
            self._lock_fd = None
            self._is_coordinator = False
            logger.info("Released coordinator lock")

    def get_current_coordinator(self) -> Optional[Dict[str, Any]]:
        """Read the current coordinator state."""
        try:
            if self._state_path.exists():
                with open(self._state_path) as f:
                    state = json.load(f)
                if not isinstance(state, dict):
                    return None
                # Verify coordinator is alive
                pid = state.get("coordinator_pid", 0)
                if not isinstance(pid, int):
                    return None

                # A dead coordinator leaves stale state
                return state if pid_alive(pid) else None
        except (json.JSONDecodeError, Exception):
            pass
        return None

    def update_identity(self, identity: "AgentRuntime") -> None:
        """Update coordinator_identity in state.json after identity assignment.

        Called after _designator.assign() populates identity.identity,
        since try_become_coordinator() runs before identity assignment.
        """
        if not self._is_coordinator:
            return
        try:
            if self._state_path.exists():
                with open(self._state_path) as f:
                    state = json.load(f)
                state["coordinator_identity"] = identity.identity
                _atomic_write(self._state_path, state)
        except (json.JSONDecodeError, Exception):
            pass

    def _read_epoch(self) -> int:
        """Read the current epoch from state."""
        try:
            if self._state_path.exists():
                with open(self._state_path) as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    epoch = state.get("epoch", 0)
                    return int(epoch) if isinstance(epoch, int | float) else 0
        except Exception:
            pass
        return 0

    @property
    def is_coordinator(self) -> bool:
        return bool(self._is_coordinator)

    @property
    def epoch(self) -> int:
        return int(self._epoch)


class IdentityAssigner:
    """Assigns unique identities to agents from a roster.

    With no ``pool`` the names come from the roster of ``folder`` (default: the
    current directory), read on each ``assign``.
    """

    def __init__(self, pool: Optional[List[str]] = None, folder: Optional[str] = None):
        self._hub_dir = get_hub_dir()
        self._pool = pool
        self._folder = folder

    def assign(self, taken: List[str], preferred: str = "") -> str:
        """Assign an identity from the pool.

        Args:
            taken: List of identities already in use.
            preferred: Preferred identity (used if available).

        Returns:
            Assigned identity string.
        """
        if preferred and preferred not in taken:
            return preferred

        pool = self._pool if self._pool is not None else roster_names(self._folder)
        for name in pool:
            if name not in taken:
                return name

        # Pool exhausted - use numbered names across the full pool
        for i in range(2, 100):
            for name in pool:
                candidate = f"{name}-{i}"
                if candidate not in taken:
                    return candidate

        return f"agent-{os.getpid()}"


MAX_UPSTREAM_CHECKS = 20
_GH_TIMEOUT_SECS = 5
_TERMINAL_CACHE: Dict[Tuple[str, str], str] = {}

# The stale sweep never runs more often than this. Review F1 fix: it
# used to run on every heartbeat tick, re-checking every open ref.
WORK_QUEUE_SWEEP_INTERVAL_SECS = 900.0


class SweepScheduler:
    """Bounds how often the stale-work sweep may run.

    Fires immediately on first ``due()`` (boot) and then at most once
    per ``interval_secs``."""

    def __init__(self, interval_secs: float = WORK_QUEUE_SWEEP_INTERVAL_SECS):
        self._interval = interval_secs
        self._last: Optional[float] = None

    def due(self, now: float) -> bool:
        if self._last is None or now - self._last >= self._interval:
            self._last = now
            return True
        return False


def github_terminal_state(ref: str, project: Optional[str] = None) -> Optional[str]:
    """Return a terminal reason for ``ref`` if its issue/PR is done.

    Queries ``gh`` in ``project`` (or the current directory when omitted). Returns
    ``None`` for open items, unmerged closed PRs, unknown numbers, and any
    ``gh`` failure -- unknown means keep the work item, never retire on a bad
    answer. A missing explicit project is unresolved and fails open. Merged PRs
    are cached per project for the process lifetime. Closed issues and unmerged
    closed PRs are re-checked each sweep because they can reopen.
    """
    project_path = os.getcwd() if project is None else project
    if not project_path:
        return None
    cache_key = (project_path, ref)
    cached = _TERMINAL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    repository = _github_repo_selector(project_path) if project is not None else None
    if project is not None and repository is None:
        return None
    num = ref.lstrip("#")
    repo_args = ["-R", repository] if repository else []
    attempts = (
        (
            ["gh", "pr", "view", num, "--json", "state,mergedAt", *repo_args],
            "pr",
        ),
        (
            ["gh", "issue", "view", num, "--json", "state,stateReason", *repo_args],
            "issue",
        ),
    )
    for argv, kind in attempts:
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=_GH_TIMEOUT_SECS
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        state = data.get("state", "")
        if kind == "pr":
            merged = state == "MERGED" or (
                state == "CLOSED" and bool(data.get("mergedAt"))
            )
            if merged:
                reason = f"{ref} merged"
                _TERMINAL_CACHE[cache_key] = reason
                return reason
            # A CLOSED PR may later reopen, and PRs are also GitHub issues.
            # Do not fall through to issue-state handling without merge proof.
            if state in {"OPEN", "CLOSED"}:
                return None
            continue
        if state != "CLOSED":
            return None
        state_reason = data.get("stateReason") or "closed"
        return f"{ref} issue closed ({state_reason})"
    return None


class WorkQueue:
    """Manages pending work slots."""

    def __init__(self):
        self._path = get_hub_dir() / "work-queue.json"
        # A dedicated lock file: _save() atomically renames over the
        # queue file, so a lock held on the queue's inode would not
        # survive the swap and would stop excluding anyone.
        self._lock_path = self._path.parent / (self._path.name + ".lock")

    def _load(self, strict: bool = False) -> List[WorkSlot]:
        """Load slots; with ``strict`` a corrupt file raises instead of
        reading as empty (review F2: a silent empty read inside a
        mutation would overwrite the whole queue)."""
        try:
            if self._path.exists():
                with open(self._path) as f:
                    data = json.load(f)
                return [WorkSlot.from_dict(s) for s in data.get("slots", [])]
        except Exception:
            if strict:
                raise
        return []

    def _save(self, slots: List[WorkSlot]) -> None:
        _atomic_write(self._path, {"slots": [s.to_dict() for s in slots]})

    def _mutate(self, mutate: Callable[[List[WorkSlot]], Tuple[Any, bool]]) -> Any:
        """Run ``mutate(slots)`` and persist it under the queue lock.

        ``mutate`` returns ``(result, changed)``. Load, mutate, and the
        conditional save all happen inside one exclusive locked block,
        so no read-modify-write window is left open across processes
        or threads.
        """
        with locked_file(self._lock_path, "a+"):
            slots = self._load(strict=True)
            result, changed = mutate(slots)
            if changed:
                self._save(slots)
            return result

    def add(
        self,
        task: str,
        project: str = "",
        queued_by: str = "",
        priority: int = 5,
        context: str = "",
        upstream_refs: Optional[List[str]] = None,
    ) -> WorkSlot:
        """Add a work slot to the queue."""

        def _append(slots: List[WorkSlot]) -> Tuple[WorkSlot, bool]:
            slot = WorkSlot(
                task=task,
                project=project,
                queued_by=queued_by,
                upstream_refs=sorted(set(upstream_refs or [])),
                priority=priority,
                context=context,
            )
            slots.append(slot)
            logger.info(f"Queued work: {slot.id} - {task[:60]}")
            return slot, True

        return self._mutate(_append)

    def link_task_card(self, slot_id: str, task_card_id: str) -> Optional[WorkSlot]:
        """Persist the TaskLedger card paired with a queue slot.

        Re-linking the same card is idempotent. A different card never
        replaces an existing durable association.
        """
        card_id = (task_card_id or "").strip()
        if not card_id:
            return None

        def _link(slots: List[WorkSlot]) -> Tuple[Optional[WorkSlot], bool]:
            for slot in slots:
                if slot.id != slot_id:
                    continue
                if slot.task_card_id and slot.task_card_id != card_id:
                    logger.warning(
                        "Work slot %s already links to task card %s",
                        slot_id,
                        slot.task_card_id,
                    )
                    return None, False
                if slot.task_card_id == card_id:
                    return slot, False
                slot.task_card_id = card_id
                return slot, True
            return None, False

        return self._mutate(_link)

    def claim_next(self, agent_identity: str, project: str = "") -> Optional[WorkSlot]:
        """Claim the next available work slot."""

        def _claim(slots: List[WorkSlot]) -> Tuple[Optional[WorkSlot], bool]:
            for slot in sorted(slots, key=lambda s: -s.priority):
                if slot.status == "pending":
                    if project and slot.project and slot.project != project:
                        continue
                    slot.assigned_to = agent_identity
                    slot.status = "assigned"
                    logger.info(f"Assigned {slot.id} to {agent_identity}")
                    return slot, True
            return None, False

        return self._mutate(_claim)

    def claim_by_id(self, slot_id: str, agent_identity: str) -> Optional[WorkSlot]:
        """Claim a specific work slot by ID."""

        def _claim(slots: List[WorkSlot]) -> Tuple[Optional[WorkSlot], bool]:
            for slot in slots:
                if slot.id == slot_id and slot.status == "pending":
                    slot.assigned_to = agent_identity
                    slot.status = "assigned"
                    logger.info(f"Assigned {slot.id} to {agent_identity}")
                    return slot, True
            return None, False

        return self._mutate(_claim)

    def claim_task_card(
        self, task_card_id: str, agent_identity: str
    ) -> Optional[WorkSlot]:
        """Mark a linked slot assigned after its TaskLedger card is claimed."""
        card_id = (task_card_id or "").strip()
        identity = (agent_identity or "").strip()
        if not card_id or not identity:
            return None

        def _claim(slots: List[WorkSlot]) -> Tuple[Optional[WorkSlot], bool]:
            for slot in slots:
                if slot.task_card_id != card_id:
                    continue
                if slot.status == "pending":
                    slot.assigned_to = identity
                    slot.status = "assigned"
                    logger.info(
                        "Assigned linked work slot %s to %s from task card claim",
                        slot.id,
                        identity,
                    )
                    return slot, True
                return slot, False
            return None, False

        return self._mutate(_claim)

    def complete(self, slot_id: str) -> None:
        """Mark a work slot as complete."""

        def _complete(slots: List[WorkSlot]) -> Tuple[None, bool]:
            changed = False
            for slot in slots:
                if slot.id == slot_id:
                    slot.status = "completed"
                    changed = True
                    break
            return None, changed

        self._mutate(_complete)

    def requeue_orphans(self, live: set) -> List[tuple]:
        """Return work held by agents not in ``live`` to pending.

        Returns ``(slot_id, dead_agent)`` pairs. Saves once, and only when
        something changed.
        """

        def _requeue(slots: List[WorkSlot]) -> Tuple[List[tuple], bool]:
            requeued = []
            for slot in slots:
                if (
                    slot.status == "assigned"
                    and slot.assigned_to
                    and slot.assigned_to not in live
                ):
                    requeued.append((slot.id, slot.assigned_to))
                    slot.status = "pending"
                    slot.assigned_to = None
            return requeued, bool(requeued)

        return self._mutate(_requeue)

    def record_stale_upstream_note(
        self,
        slot_id: str,
        note: str,
        *,
        restore_pending_reason: Optional[str] = None,
    ) -> Optional[WorkSlot]:
        """Persist a note and optionally undo a raced stale retirement.

        ``restore_pending_reason`` must exactly match the current retirement
        reason. It is used only when a linked card became active after the
        sweep snapshot; a concurrent claim may already have assigned the slot,
        in which case its claim-time status is left untouched.
        """

        def _record(slots: List[WorkSlot]) -> Tuple[Optional[WorkSlot], bool]:
            for slot in slots:
                if slot.id != slot_id:
                    continue
                changed = _append_stale_upstream_note(slot, note)
                if (
                    restore_pending_reason
                    and restore_pending_reason != "terminal slot (completed)"
                    and slot.status == "retired"
                    and slot.retired_reason == restore_pending_reason
                ):
                    slot.status = "pending"
                    slot.retired_reason = None
                    changed = True
                return slot, changed
            return None, False

        return self._mutate(_record)

    def sweep_stale(
        self,
        checker: Optional[Callable[[str], Optional[str]]] = None,
        task_card_status_checker: Optional[Callable[[str], Optional[str]]] = None,
    ) -> List[WorkSlot]:
        """Retire stale slots and annotate work that must remain active.

        Completed slots are always retired. A pending slot is retired when an
        explicitly linked TaskLedger card is terminal or all explicit GitHub
        upstream refs are terminal. Assigned slots are never retired. If an
        upstream is terminal while a linked card is active, or while the slot
        is assigned, only an idempotent note is persisted; status is preserved.
        A slot with neither explicit refs nor a linked TaskLedger card is
        report-only. Free text is never scanned or used to migrate old slots.

        GitHub and TaskLedger status checks run outside the queue lock. The live
        queue is then reloaded under lock and only still-pending decisions are
        retired. A slot claimed during checks is kept and receives the terminal
        reason as a note. The sweep fails open on checker errors or budget
        exhaustion. At most ``MAX_UPSTREAM_CHECKS`` unique refs are checked.

        Returns retired WorkSlot snapshots. Paired retired slots are returned
        again on later sweeps so TaskLedger cleanup can retry transient failures.
        """
        repository_checker = checker is None
        if checker is None:
            checker = github_terminal_state
        snapshot = self._load()
        decisions: Dict[str, str] = {}
        notes: Dict[str, List[str]] = {}
        states: Dict[Tuple[str, str], Optional[str]] = {}
        task_card_statuses: Dict[str, Optional[str]] = {}
        budget = MAX_UPSTREAM_CHECKS

        def add_note(slot_id: str, note: str) -> None:
            notes.setdefault(slot_id, []).append(note)

        # Check pending work first so assigned-slot annotations cannot exhaust
        # the bounded GitHub budget ahead of retireable pending slots.
        candidates = sorted(
            snapshot,
            key=lambda slot: slot.status == "assigned",
        )
        for slot in candidates:
            if slot.status == "completed":
                decisions[slot.id] = "terminal slot (completed)"
                continue
            if slot.status not in {"pending", "assigned"}:
                continue

            card_status = None
            if slot.task_card_id and task_card_status_checker is not None:
                if slot.task_card_id not in task_card_statuses:
                    try:
                        task_card_statuses[slot.task_card_id] = (
                            task_card_status_checker(slot.task_card_id)
                        )
                    except Exception as e:
                        logger.warning(
                            "TaskLedger status check failed for card %s: %s",
                            slot.task_card_id,
                            e,
                        )
                        task_card_statuses[slot.task_card_id] = None
                card_status = task_card_statuses[slot.task_card_id]

            refs = sorted(set(slot.upstream_refs or []))
            if slot.status == "pending" and card_status in TERMINAL_TASK_STATUSES:
                decisions[slot.id] = (
                    f"TaskLedger card [{slot.task_card_id}] terminal "
                    f"({card_status})"
                )
                continue
            if slot.status == "assigned" and card_status in TERMINAL_TASK_STATUSES:
                add_note(
                    slot.id,
                    f"terminal TaskLedger card [{slot.task_card_id}] ({card_status})",
                )

            if not refs:
                if not slot.task_card_id:
                    add_note(
                        slot.id,
                        "report-only: no explicit upstream refs or linked TaskLedger card",
                    )
                elif task_card_status_checker is not None and card_status is None:
                    add_note(
                        slot.id,
                        f"report-only: linked TaskLedger card [{slot.task_card_id}] "
                        "unavailable; no explicit upstream refs",
                    )
                continue

            reasons: List[str] = []
            for ref in refs:
                state_key = (slot.project, ref)
                if state_key not in states:
                    if budget > 0:
                        budget -= 1
                        try:
                            if repository_checker:
                                states[state_key] = github_terminal_state(
                                    ref, slot.project
                                )
                            else:
                                states[state_key] = checker(ref)
                        except Exception as e:
                            logger.debug(
                                "stale sweep checker failed for %s: %s", ref, e
                            )
                            states[state_key] = None
                    else:
                        # Budget exhausted: unknown, fail open, keep.
                        states[state_key] = None
                reason = states[state_key]
                if not reason:
                    reasons = []
                    break
                reasons.append(reason)
            if not reasons:
                continue

            verdict = "; ".join(reasons)
            if slot.status == "assigned" or card_status == "active":
                add_note(slot.id, f"terminal upstream: {verdict}")
            else:
                decisions[slot.id] = verdict

        if not decisions and not notes:
            return [
                slot
                for slot in snapshot
                if slot.status == "retired" and slot.task_card_id
            ]

        def _retire(
            slots: List[WorkSlot],
        ) -> Tuple[List[WorkSlot], bool]:
            retired = [
                slot for slot in slots if slot.status == "retired" and slot.task_card_id
            ]
            changed = False
            for slot in slots:
                if slot.status == "retired":
                    continue
                verdict = decisions.get(slot.id)
                requested_notes = list(notes.get(slot.id, []))
                if slot.status == "completed":
                    # Completed after phase 1 still counts: terminal is
                    # terminal, and nothing un-completes a slot.
                    slot.status = "retired"
                    slot.retired_reason = "terminal slot (completed)"
                    retired.append(slot)
                    changed = True
                    logger.info("Retired stale work %s: completed", slot.id)
                    continue
                if verdict and slot.status == "pending":
                    slot.status = "retired"
                    slot.retired_reason = verdict
                    retired.append(slot)
                    changed = True
                    logger.info("Retired stale work %s: %s", slot.id, verdict)
                    continue
                if verdict and slot.status == "assigned":
                    requested_notes.append(f"terminal upstream: {verdict}")

                if slot.status not in {"pending", "assigned"}:
                    continue
                for note in requested_notes:
                    if not _append_stale_upstream_note(slot, note):
                        continue
                    changed = True
                    if note.lower().startswith("report-only:"):
                        logger.warning(
                            "Work slot %s is report-only and remains %s: %s",
                            slot.id,
                            slot.status,
                            note,
                        )
                    elif task_card_statuses.get(slot.task_card_id or "") == "active":
                        logger.warning(
                            "Leaving active task card %s and work slot %s unchanged; %s",
                            slot.task_card_id,
                            slot.id,
                            note,
                        )
                    else:
                        logger.warning(
                            "Leaving assigned work slot %s unchanged; %s",
                            slot.id,
                            note,
                        )
            return retired, changed

        return self._mutate(_retire)

    def get_pending(self) -> List[WorkSlot]:
        """Get all pending work slots."""
        return [s for s in self._load() if s.status == "pending"]

    def find_best_agent(self, agents: list, slot: "WorkSlot") -> Optional[str]:
        """Score agents by capability overlap and return best match identity."""
        if not slot.required_capabilities:
            # No requirements, any idle non-coordinator agent works
            for a in agents:
                if getattr(a, "state", "") in ("idle", "ready") and not getattr(
                    a, "is_coordinator", False
                ):
                    return getattr(a, "identity", "")
            return None

        best_score = -1.0
        best_agent = None
        required = set(slot.required_capabilities)
        for a in agents:
            if getattr(a, "state", "") not in ("idle", "ready"):
                continue
            if getattr(a, "is_coordinator", False):
                continue
            agent_caps = set(getattr(a, "capabilities", []))
            overlap = len(agent_caps & required)
            score = overlap / len(required) if required else 0
            if score > best_score:
                best_score = score
                best_agent = getattr(a, "identity", "")
        return best_agent

    def get_all(self) -> List[WorkSlot]:
        return self._load()
