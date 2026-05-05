"""Thread-safe ring buffer for capturing agent subprocess output."""

import threading
from collections import deque
from typing import List


class RingBuffer:
    """Thread-safe ring buffer backed by collections.deque.

    Used by the stdout pump thread to store output lines from agent
    subprocesses. The pump thread is synchronous, so this uses
    threading.Lock (not asyncio.Lock).
    """

    def __init__(self, maxlen: int = 2000):
        self._buffer: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        """Append a line to the buffer."""
        with self._lock:
            self._buffer.append(line)

    def get_all(self) -> List[str]:
        """Return all lines in the buffer."""
        with self._lock:
            return list(self._buffer)

    def get_last(self, n: int) -> List[str]:
        """Return the last n lines from the buffer."""
        with self._lock:
            if n >= len(self._buffer):
                return list(self._buffer)
            return list(self._buffer)[-n:]

    def clear(self) -> None:
        """Clear all lines from the buffer."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
