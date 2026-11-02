"""Coordinator election and work queue management."""

import json
import logging
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from kollabor_agent.runtime import AgentRuntime
from kollabor_config.file_lock import locked_file, try_lock, unlock

from .models import WorkSlot
from .presence import _atomic_write, get_hub_dir, pid_alive
from .rosters import roster_names

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

_UPSTREAM_REF_RE = re.compile(r"#(\d+)")
_GH_TIMEOUT_SECS = 15
_TERMINAL_CACHE: Dict[str, str] = {}


def extract_upstream_refs(text: str) -> List[str]:
    """Extract every ``#NNN`` upstream reference from free text."""
    return [f"#{n}" for n in _UPSTREAM_REF_RE.findall(text)]


def github_terminal_state(ref: str) -> Optional[str]:
    """Return a terminal reason for ``ref`` if its issue/PR is done.

    Queries ``gh`` (repo inferred from the working directory). Returns
    ``None`` for open items, unknown numbers, and any ``gh`` failure --
    unknown means keep the work item, never retire on a bad answer.
    Terminal answers are cached for the process lifetime; open answers
    are re-checked each sweep because they can change.
    """
    cached = _TERMINAL_CACHE.get(ref)
    if cached is not None:
        return cached
    num = ref.lstrip("#")
    attempts = (
        (["gh", "pr", "view", num, "--json", "state,mergedAt"], "pr"),
        (["gh", "issue", "view", num, "--json", "state,stateReason"], "issue"),
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
            if state == "MERGED":
                reason = f"{ref} merged"
            elif state == "CLOSED":
                reason = f"{ref} closed"
            else:
                return None
        else:
            if state != "CLOSED":
                return None
            state_reason = data.get("stateReason") or "closed"
            reason = f"{ref} issue closed ({state_reason})"
        _TERMINAL_CACHE[ref] = reason
        return reason
    return None


class WorkQueue:
    """Manages pending work slots."""

    def __init__(self):
        self._path = get_hub_dir() / "work-queue.json"
        # A dedicated lock file: _save() atomically renames over the
        # queue file, so a lock held on the queue's inode would not
        # survive the swap and would stop excluding anyone.
        self._lock_path = self._path.parent / (self._path.name + ".lock")

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

    def _mutate(
        self, mutate: Callable[[List[WorkSlot]], Tuple[Any, bool]]
    ) -> Any:
        """Run ``mutate(slots)`` and persist it under the queue lock.

        ``mutate`` returns ``(result, changed)``. Load, mutate, and the
        conditional save all happen inside one exclusive locked block,
        so no read-modify-write window is left open across processes
        or threads.
        """
        with locked_file(self._lock_path, "a+"):
            slots = self._load()
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
    ) -> WorkSlot:
        """Add a work slot to the queue."""

        def _append(slots: List[WorkSlot]) -> Tuple[WorkSlot, bool]:
            slot = WorkSlot(
                task=task,
                project=project,
                queued_by=queued_by,
                priority=priority,
                context=context,
            )
            slots.append(slot)
            logger.info(f"Queued work: {slot.id} - {task[:60]}")
            return slot, True

        return self._mutate(_append)

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

    def sweep_stale(
        self, checker: Optional[Callable[[str], Optional[str]]] = None
    ) -> List[Tuple[str, str]]:
        """Retire pending slots whose upstream work is already done.

        A slot retires only when it references at least one ``#NNN``
        upstream item and every referenced item reports a terminal
        state (merged PR, closed issue). Slots without references and
        slots with any live reference are kept. Assigned and completed
        slots are never touched.

        ``checker`` maps a ``#NNN`` reference to a terminal reason
        string, or None to keep. Defaults to ``github_terminal_state``.
        A checker exception is treated as "keep" so a flaky checker can
        never destroy queued work.

        Two phases keep the queue lock off the slow path: decisions are
        computed from a snapshot while the checkers run lock-free (they
        shell out to gh), then the live queue is re-loaded under the
        lock and only the decided ids that are still pending are
        retired. Anything appended or claimed while the checkers ran is
        preserved.

        Returns ``(slot_id, reason)`` pairs and saves once, only when
        something was retired.
        """
        if checker is None:
            checker = github_terminal_state
        decisions: Dict[str, str] = {}
        for slot in self._load():
            if slot.status != "pending":
                continue
            refs = extract_upstream_refs(f"{slot.task}\n{slot.context}")
            if not refs:
                continue
            reasons: List[str] = []
            for ref in refs:
                try:
                    reason = checker(ref)
                except Exception as e:
                    logger.debug(f"stale sweep checker failed for {ref}: {e}")
                    reason = None
                if not reason:
                    reasons = []
                    break
                reasons.append(reason)
            if reasons:
                decisions[slot.id] = "; ".join(reasons)
        if not decisions:
            return []

        def _retire(
            slots: List[WorkSlot],
        ) -> Tuple[List[Tuple[str, str]], bool]:
            retired: List[Tuple[str, str]] = []
            for slot in slots:
                verdict = decisions.get(slot.id)
                if verdict and slot.status == "pending":
                    slot.status = "retired"
                    slot.retired_reason = verdict
                    retired.append((slot.id, verdict))
                    logger.info(f"Retired stale work {slot.id}: {verdict}")
            return retired, bool(retired)

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
