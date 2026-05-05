"""DisplayTap - pub/sub bus for live terminal attach.

Intercepts rendered display output and streams it to subscribers
(attach clients) over unix sockets. Maintains a ring buffer of
recent events so new attachers get instant context on connect.

Thread-safe: publish() is called from the main render thread,
subscribers are managed from asyncio tasks.
"""

import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class DisplayTap:
    """Pub/sub tap for display events.

    The message coordinator and terminal renderer publish events here.
    Attach clients subscribe and receive a live stream of everything
    that gets displayed.

    Thread-safe: publish() is called from the render thread,
    subscribers consume from asyncio tasks via queue.Queue (stdlib).
    """

    def __init__(self, history_size: int = 200):
        self._history: deque = deque(maxlen=history_size)
        self._subscribers: Dict[str, queue.Queue] = {}
        self._lock = threading.Lock()

    def publish(self, event: Dict[str, Any]) -> None:
        """Publish a display event to all subscribers.

        Thread-safe. Called from the render thread (not async).
        Uses stdlib queue.Queue which is safe across threads.
        """
        event.setdefault("ts", time.time())
        self._history.append(event)

        with self._lock:
            dead = []
            for client_id, q in self._subscribers.items():
                try:
                    q.put_nowait(event)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except (queue.Empty, queue.Full):
                        pass
                except Exception:
                    dead.append(client_id)

            for client_id in dead:
                self._subscribers.pop(client_id, None)

    def subscribe(self, client_id: str) -> queue.Queue:
        """Add a subscriber. Returns thread-safe queue to consume from."""
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers[client_id] = q
        logger.info(
            f"DisplayTap: subscriber added: {client_id} (total: {len(self._subscribers)})"
        )
        return q

    def unsubscribe(self, client_id: str) -> None:
        """Remove a subscriber."""
        with self._lock:
            self._subscribers.pop(client_id, None)
        logger.info(
            f"DisplayTap: subscriber removed: {client_id} (total: {len(self._subscribers)})"
        )

    def get_snapshot(self) -> List[Dict[str, Any]]:
        """Get recent history for catch-up on connect."""
        return list(self._history)

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)
