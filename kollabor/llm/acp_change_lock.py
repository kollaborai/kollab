"""Serialize multi-option ACP changes with terminal option updates."""

import asyncio
from contextlib import asynccontextmanager


class _ChangeLock:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.owner = None
        self.depth = 0

    @asynccontextmanager
    async def hold(self):
        task = asyncio.current_task()
        if self.owner is task:
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1
            return
        async with self.lock:
            self.owner, self.depth = task, 1
            try:
                yield
            finally:
                self.owner, self.depth = None, 0


def change_lock(service):
    lock = getattr(service, "_model_change_lock", None)
    if lock is None:
        lock = _ChangeLock()
        service._model_change_lock = lock
    return lock.hold()
