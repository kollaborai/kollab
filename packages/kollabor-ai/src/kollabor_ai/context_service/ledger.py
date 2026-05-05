"""In-memory ledger for ContextService.

Thread-safe. Stores LedgerEntry instances keyed by ctx_id, with
a monotonic counter for generating new IDs.
"""

import logging
import threading
from typing import Dict, List, Optional

from .models import LedgerEntry

logger = logging.getLogger(__name__)


class Ledger:
    """Thread-safe in-memory ledger for heavy item tracking."""

    def __init__(self) -> None:
        self._entries: Dict[str, LedgerEntry] = {}
        self._next_id: int = 1
        self._lock = threading.Lock()
        self.turn_count: int = 0

    def next_ctx_id(self) -> str:
        """Generate the next sequential ctx_id.

        Returns:
            A string like 'ctx-1', 'ctx-2', etc.
        """
        with self._lock:
            ctx_id = f"ctx-{self._next_id}"
            self._next_id += 1
            return ctx_id

    def add(self, entry: LedgerEntry) -> None:
        """Add an entry to the ledger.

        Args:
            entry: The LedgerEntry to store.
        """
        with self._lock:
            self._entries[entry.ctx_id] = entry
            logger.debug(f"Ledger add: {entry.ctx_id} ({entry.kind})")

    def get(self, ctx_id: str) -> Optional[LedgerEntry]:
        """Look up an entry by ctx_id.

        Args:
            ctx_id: The stable identifier to look up.

        Returns:
            The LedgerEntry, or None if not found.
        """
        with self._lock:
            return self._entries.get(ctx_id)

    def all(self) -> List[LedgerEntry]:
        """Return all entries, sorted by ctx_id number.

        Returns:
            List of LedgerEntry sorted by numeric portion of ctx_id.
        """
        with self._lock:
            entries = list(self._entries.values())

        def sort_key(e: LedgerEntry) -> int:
            try:
                return int(e.ctx_id.split("-", 1)[1])
            except (IndexError, ValueError):
                return 0

        entries.sort(key=sort_key)
        return entries

    def find_by_path(self, file_path: str) -> Optional[LedgerEntry]:
        """Return the latest non-evicted entry for a file path, or None.

        Used by the hub bridge to compare peer broadcasts against our
        local state.
        """
        with self._lock:
            matches = [
                e
                for e in self._entries.values()
                if e.file_path == file_path and e.decision != "evicted"
            ]
        if not matches:
            return None

        def _version(e: LedgerEntry) -> int:
            return e.file_version or 0

        matches.sort(key=_version, reverse=True)
        return matches[0]

    def total_bytes(self) -> int:
        """Total size of all tracked entries (non-evicted).

        Returns:
            Sum of size_bytes for entries not marked evicted.
        """
        with self._lock:
            return sum(
                e.size_bytes
                for e in self._entries.values()
                if e.decision != "evicted"
            )

    def count_pending(self) -> int:
        """Count entries with pending decision.

        Returns:
            Number of entries where decision == 'pending'.
        """
        with self._lock:
            return sum(
                1 for e in self._entries.values() if e.decision == "pending"
            )
