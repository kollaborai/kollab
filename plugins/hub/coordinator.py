"""Coordinator election and work queue management."""

import fcntl
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from kollabor_agent.runtime import AgentRuntime

from .models import DESIGNATION_POOL, WorkSlot
from .presence import _atomic_write, get_hub_dir

logger = logging.getLogger(__name__)


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
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

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
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
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
                try:
                    os.kill(pid, 0)
                    return state
                except (OSError, ProcessLookupError):
                    # Coordinator is dead, state is stale
                    return None
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
    """Assigns unique identities to agents from the pool."""

    def __init__(self, pool: Optional[List[str]] = None):
        self._hub_dir = get_hub_dir()
        self._pool = pool if pool is not None else DESIGNATION_POOL

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

        for name in self._pool:
            if name not in taken:
                return name

        # Pool exhausted - use numbered names across the full pool
        for i in range(2, 100):
            for name in self._pool:
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
