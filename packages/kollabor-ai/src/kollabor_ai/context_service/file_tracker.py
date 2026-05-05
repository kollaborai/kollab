"""File version tracking for ContextService.

Maintains a per-file history of LedgerEntry versions so we can
dedup by hash and generate diffs on re-read.
"""

import logging
import threading
from typing import Dict, Optional

from .models import FileVersion, LedgerEntry

logger = logging.getLogger(__name__)


class FileTracker:
    """Thread-safe per-file version history."""

    def __init__(self) -> None:
        self._versions: Dict[str, FileVersion] = {}
        self._lock = threading.Lock()

    def record_read(self, path: str, entry: LedgerEntry) -> None:
        """Record a new read of a file.

        Args:
            path: The file path.
            entry: The LedgerEntry for this read.
        """
        with self._lock:
            version = self._versions.setdefault(path, FileVersion(path=path))
            entry.file_version = len(version.versions) + 1
            version.versions.append(entry)
            logger.debug(
                f"FileTracker record: {path} v{entry.file_version} "
                f"({entry.ctx_id})"
            )

    def get_version(self, path: str) -> Optional[FileVersion]:
        """Get the version history for a file.

        Args:
            path: The file path to look up.

        Returns:
            FileVersion if the file has been read before, else None.
        """
        with self._lock:
            return self._versions.get(path)

    def has_any_version(self, path: str) -> bool:
        """Check if we've ever read this file.

        Args:
            path: The file path to check.

        Returns:
            True if the file has been read at least once.
        """
        with self._lock:
            return path in self._versions
