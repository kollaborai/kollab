"""Thread-safe buffer for agent notification events.

Collapse keys dedup repeated events (bumps ``count`` + timestamp
instead of appending a new row). Max-size eviction drops the oldest
entries when the buffer fills — no priority tiers, no observers.
"""

from __future__ import annotations

import threading
from typing import Dict, List

from .models import EnvEvent


class EnvQueue:
    """Thread-safe buffer of EnvEvent instances."""

    def __init__(self, max_size: int = 50) -> None:
        self._buffer: List[EnvEvent] = []
        self._collapse_index: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def push(self, event: EnvEvent) -> None:
        """Append an event. Collapse keys fold duplicates."""
        with self._lock:
            if event.collapse_key:
                idx = self._collapse_index.get(event.collapse_key)
                if idx is not None and idx < len(self._buffer):
                    existing = self._buffer[idx]
                    if existing.collapse_key == event.collapse_key:
                        existing.count += 1
                        existing.timestamp = event.timestamp
                        return

            self._buffer.append(event)
            if event.collapse_key:
                self._collapse_index[event.collapse_key] = len(self._buffer) - 1

            if len(self._buffer) > self._max_size:
                self._buffer = self._buffer[-self._max_size :]
                self._rebuild_collapse_index()

    def drain(self) -> List[EnvEvent]:
        """Return all pending events and clear the buffer."""
        with self._lock:
            result = list(self._buffer)
            self._buffer.clear()
            self._collapse_index.clear()
        return result

    def peek(self) -> List[EnvEvent]:
        """Return a snapshot without clearing the buffer."""
        with self._lock:
            return list(self._buffer)

    def clear(self) -> int:
        """Discard all pending events. Returns the count dropped."""
        with self._lock:
            count = len(self._buffer)
            self._buffer.clear()
            self._collapse_index.clear()
        return count

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _rebuild_collapse_index(self) -> None:
        self._collapse_index = {
            e.collapse_key: i
            for i, e in enumerate(self._buffer)
            if e.collapse_key
        }
