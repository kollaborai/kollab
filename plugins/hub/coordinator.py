"""Coordinator election and work queue management."""

import json
import logging
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from kollabor_agent.runtime import AgentRuntime
from kollabor_config.file_lock import try_lock, unlock

from .models import WorkSlot
from .presence import _atomic_write, get_hub_dir, pid_alive
from .rosters import roster_names

logger = logging.getLogger(__name__)

# --- Work-queue boot sweep (issue #640) ---
# The persisted work queue never removed entries whose upstream work had
# landed, so slots referencing merged PRs / closed issues kept looking
# actionable across restarts. Issue/PR references are embedded in the slot
# text ("fix the #219 wave"), not stored as structured fields.
UPSTREAM_REF_RE = re.compile(r"#(\d+)\b")
DEFAULT_UPSTREAM_REPO = "kollaborai/kollab"
# Bounds boot-time upstream verification: at most this many distinct numbers
# are checked per sweep, each with a short timeout. Unchecked numbers are
# treated as unknown and their slots are kept (fail-open).
MAX_UPSTREAM_CHECKS = 20
UPSTREAM_CHECK_TIMEOUT_SECS = 5.0


def github_upstream_check(
    repo: str = DEFAULT_UPSTREAM_REPO,
) -> Callable[[int], Optional[str]]:
    """Build an upstream state checker backed by the ``gh`` CLI.

    Maps an issue/PR number to ``"open"`` or ``"closed"``. A merged PR and
    a closed-unmerged PR both report ``closed`` through the issues API:
    either way the queue entry's work is no longer live. Returns ``None``
    when the state cannot be determined (gh missing, unauthenticated,
    network error, timeout, unexpected output) — callers must treat
    ``None`` as "unknown" and fail open by keeping the slot.
    """

    def check(number: int) -> Optional[str]:
        try:
            proc = subprocess.run(
                [
                    "gh",
                    "api",
                    f"repos/{repo}/issues/{number}",
                    "--jq",
                    ".state",
                ],
                capture_output=True,
                text=True,
                timeout=UPSTREAM_CHECK_TIMEOUT_SECS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        state = (proc.stdout or "").strip()
        return state if state in ("open", "closed") else None

    return check


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


class WorkQueue:
    """Manages pending work slots."""

    def __init__(self):
        self._path = get_hub_dir() / "work-queue.json"

    def _load(self) -> List[WorkSlot]:
        try:
            if self._path.exists():
                with open(self._path) as f:
                    data = json.load(f)
                return [WorkSlot.from_dict(s) for s in data.get("slots", [])]
        except Exception:
            pass
        return []

    def _save(self, slots: List[WorkSlot]) -> None:
        _atomic_write(self._path, {"slots": [s.to_dict() for s in slots]})

    def add(
        self,
        task: str,
        project: str = "",
        queued_by: str = "",
        priority: int = 5,
        context: str = "",
    ) -> WorkSlot:
        """Add a work slot to the queue."""
        slot = WorkSlot(
            task=task,
            project=project,
            queued_by=queued_by,
            priority=priority,
            context=context,
        )
        slots = self._load()
        slots.append(slot)
        self._save(slots)
        logger.info(f"Queued work: {slot.id} - {task[:60]}")
        return slot

    def claim_next(self, agent_identity: str, project: str = "") -> Optional[WorkSlot]:
        """Claim the next available work slot."""
        slots = self._load()
        for slot in sorted(slots, key=lambda s: -s.priority):
            if slot.status == "pending":
                if project and slot.project and slot.project != project:
                    continue
                slot.assigned_to = agent_identity
                slot.status = "assigned"
                self._save(slots)
                logger.info(f"Assigned {slot.id} to {agent_identity}")
                return slot
        return None

    def claim_by_id(self, slot_id: str, agent_identity: str) -> Optional[WorkSlot]:
        """Claim a specific work slot by ID."""
        slots = self._load()
        for slot in slots:
            if slot.id == slot_id and slot.status == "pending":
                slot.assigned_to = agent_identity
                slot.status = "assigned"
                self._save(slots)
                logger.info(f"Assigned {slot.id} to {agent_identity}")
                return slot
        return None

    def complete(self, slot_id: str) -> None:
        """Mark a work slot as complete."""
        slots = self._load()
        for slot in slots:
            if slot.id == slot_id:
                slot.status = "completed"
                break
        self._save(slots)

    def requeue_orphans(self, live: set) -> List[tuple]:
        """Return work held by agents not in ``live`` to pending.

        Returns ``(slot_id, dead_agent)`` pairs. Saves once, and only when
        something changed.
        """
        slots = self._load()
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
        if requeued:
            self._save(slots)
        return requeued

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

    def sweep(
        self,
        upstream_check: Optional[Callable[[int], Optional[str]]] = None,
        max_checks: int = MAX_UPSTREAM_CHECKS,
    ) -> List[Tuple[WorkSlot, str]]:
        """Retire dead work slots; returns ``[(slot, reason)]``.

        Boot-time reconciliation (issue #640): the persisted queue never
        removed items whose upstream work had landed, so entries kept
        looking actionable across restarts. Retirement rules:

        - ``completed`` slots are dead weight and are always retired.
        - ``pending`` slots are retired when at least one ``#N`` reference
          in task/context resolves upstream and every reference resolves to
          a closed issue/PR (merged or closed work is no longer live).
          Unknown or failed checks keep the slot: the sweep fails open.
        - ``assigned`` slots are live work and are never touched here;
          ``requeue_orphans`` owns their lifecycle.

        Each retired slot is logged with its reason. The queue file is
        rewritten only when something retires. Pass ``upstream_check`` to
        override the default ``gh``-backed checker (tests inject fakes).
        """
        slots = self._load()
        if not slots:
            return []
        if upstream_check is None:
            upstream_check = github_upstream_check()

        retired: List[Tuple[WorkSlot, str]] = []
        keep: List[WorkSlot] = []
        states: Dict[int, Optional[str]] = {}
        budget = max(0, int(max_checks))

        for slot in slots:
            reason: Optional[str] = None
            if slot.status == "completed":
                reason = "terminal slot (completed)"
            elif slot.status == "pending":
                refs = sorted(
                    {
                        int(match)
                        for match in UPSTREAM_REF_RE.findall(
                            f"{slot.task} {slot.context}"
                        )
                    }
                )
                if refs:
                    resolved: List[Optional[str]] = []
                    for number in refs:
                        if number not in states:
                            if budget > 0:
                                budget -= 1
                                try:
                                    states[number] = upstream_check(number)
                                except Exception as e:
                                    logger.debug(
                                        "Upstream check for #%d failed: %s",
                                        number,
                                        e,
                                    )
                                    states[number] = None
                            else:
                                # Budget exhausted: treat as unknown,
                                # fail open, keep the slot.
                                states[number] = None
                        resolved.append(states[number])
                    if resolved and all(state == "closed" for state in resolved):
                        reason = "upstream closed/merged: " + ", ".join(
                            f"#{number}" for number in refs
                        )
            if reason:
                retired.append((slot, reason))
            else:
                keep.append(slot)

        if retired:
            self._save(keep)
            for slot, why in retired:
                logger.info("Swept work slot %s (%s): %s", slot.id, slot.status, why)
            logger.info(
                "Work queue sweep: retired %d of %d slot(s)", len(retired), len(slots)
            )
        return retired
