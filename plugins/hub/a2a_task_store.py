"""Bounded SDK task history with capacity reserved before execution dispatch."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from uuid import uuid4

from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import Task, TaskState

_TERMINAL = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_CANCELED,
    }
)


@dataclass
class _Retention:
    context: ServerCallContext
    terminal_at: float | None


class BoundedTaskStore(InMemoryTaskStore):
    """Reserve admission separately from the SDK's asynchronous event saving."""

    def __init__(
        self, *, max_tasks: int = 256, max_active: int = 8, terminal_ttl: float = 900
    ):
        super().__init__()
        if (
            not 1 <= max_tasks <= 4096
            or not 1 <= max_active <= max_tasks
            or not 0 < terminal_ttl <= 86400
        ):
            raise ValueError("Invalid A2A task capacity or retention")
        self.max_tasks = max_tasks
        self.max_active = max_active
        self.terminal_ttl = terminal_ttl
        self._records: dict[str, _Retention] = {}
        self._reservations: set[str] = set()
        self._capacity_lock = asyncio.Lock()

    @property
    def retained_count(self) -> int:
        return len(self._records)

    async def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [
            task_id
            for task_id, record in self._records.items()
            if record.terminal_at is not None
            and now - record.terminal_at >= self.terminal_ttl
        ]
        for task_id in expired:
            await super().delete(task_id, self._records[task_id].context)
            del self._records[task_id]

    async def reserve(self) -> str | None:
        async with self._capacity_lock:
            await self._prune_locked()
            active = sum(
                record.terminal_at is None for record in self._records.values()
            )
            if (
                len(self._records) + len(self._reservations) >= self.max_tasks
                or active + len(self._reservations) >= self.max_active
            ):
                return None
            reservation = uuid4().hex
            self._reservations.add(reservation)
            return reservation

    async def release_reservation(self, reservation: str) -> None:
        async with self._capacity_lock:
            self._reservations.discard(reservation)

    async def save(self, task: Task, context: ServerCallContext) -> None:
        async with self._capacity_lock:
            if task.id not in self._records:
                reservation = context.state.get("task_reservation")
                if reservation not in self._reservations:
                    raise RuntimeError("Task execution has no capacity reservation")
                self._reservations.remove(reservation)
                # Retain only the identity needed for authorized SDK deletion,
                # never the incoming payload or capability closures.
                self._records[task.id] = _Retention(
                    ServerCallContext(user=context.user), None
                )
            record = self._records[task.id]
            if task.status.state in _TERMINAL and record.terminal_at is None:
                record.terminal_at = time.monotonic()
            await super().save(task, context)

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        async with self._capacity_lock:
            await self._prune_locked()
            return await super().get(task_id, context)

    async def list(self, params, context: ServerCallContext):
        async with self._capacity_lock:
            await self._prune_locked()
            return await super().list(params, context)
