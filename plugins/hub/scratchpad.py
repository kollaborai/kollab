"""Agent scratchpad - ephemeral notes persisted across sessions.

Lightweight key-value store for temporary agent notes.
Max 4000 chars, truncates oldest when exceeded.
Thread-safe via lock.
"""

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

SCRATCHPAD_MAX = 4000


class Scratchpad:
    """Ephemeral scratchpad tied to a vault directory.

    Parameters:
        vault_dir: Path to the agent's vault directory.
    """

    def __init__(self, vault_dir: Path) -> None:
        self._path = vault_dir / "scratchpad.md"
        self._lock = threading.Lock()

    def write(self, content: str) -> None:
        """Overwrite scratchpad with new content."""
        content = content[:SCRATCHPAD_MAX]
        try:
            with self._lock:
                self._path.write_text(content)
        except OSError as e:
            logger.warning("scratchpad write error: %s", e)

    def append(self, line: str) -> None:
        """Append a line to scratchpad. Truncates oldest if over max."""
        try:
            with self._lock:
                current = ""
                if self._path.exists():
                    current = self._path.read_text()
                current = f"{current}\n{line}" if current else line
                if len(current) > SCRATCHPAD_MAX:
                    current = current[-SCRATCHPAD_MAX:]
                self._path.write_text(current)
        except OSError as e:
            logger.warning("scratchpad append error: %s", e)

    def get(self) -> str:
        """Get current scratchpad contents."""
        try:
            if self._path.exists():
                return self._path.read_text()
        except OSError:
            pass
        return ""

    def clear(self) -> None:
        """Wipe the scratchpad."""
        try:
            with self._lock:
                if self._path.exists():
                    self._path.unlink()
        except OSError as e:
            logger.warning("scratchpad clear error: %s", e)

    def get_path(self) -> Path:
        """Return the scratchpad file path."""
        return self._path

    @staticmethod
    def max_chars() -> int:
        """Return the character limit."""
        return SCRATCHPAD_MAX
