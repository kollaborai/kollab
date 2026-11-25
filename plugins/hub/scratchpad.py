"""Agent scratchpad - ephemeral notes persisted across sessions.

Lightweight key-value store for temporary agent notes.
Max 4000 chars, truncates oldest when exceeded.
Thread-safe via lock.
"""

import logging
import os
import tempfile
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
                self._write_atomically(content)
        except (OSError, UnicodeError):
            logger.warning("scratchpad write failed")
            raise

    def _write_atomically(self, content: str) -> None:
        """Replace the note only after its full contents have been written."""
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            assert temporary_path is not None
            temporary_path.write_text(content, encoding="utf-8")
            os.replace(temporary_path, self._path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("scratchpad temporary file cleanup failed")

    def append(self, line: str) -> None:
        """Append a line to scratchpad. Truncates oldest if over max."""
        try:
            with self._lock:
                try:
                    current = self._path.read_text()
                except FileNotFoundError:
                    current = ""
                current = f"{current}\n{line}" if current else line
                if len(current) > SCRATCHPAD_MAX:
                    current = current[-SCRATCHPAD_MAX:]
                self._write_atomically(current)
        except (OSError, UnicodeError):
            logger.warning("scratchpad append failed")
            raise

    def get(self) -> str:
        """Get current scratchpad contents."""
        try:
            with self._lock:
                return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeError):
            logger.warning("scratchpad read failed")
            raise

    def clear(self) -> None:
        """Wipe the scratchpad."""
        try:
            with self._lock:
                try:
                    self._path.unlink()
                except FileNotFoundError:
                    pass
        except OSError:
            logger.warning("scratchpad clear failed")
            raise

    def get_path(self) -> Path:
        """Return the scratchpad file path."""
        return self._path

    @staticmethod
    def max_chars() -> int:
        """Return the character limit."""
        return SCRATCHPAD_MAX
