"""Input buffer management for terminal input handling.

This module provides comprehensive buffer management for terminal input, including
character insertion/deletion, cursor movement, history navigation, and
input validation.
"""

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


class BufferManager:
    """Manages input buffer with validation, history, and editing capabilities.

    Handles text input buffer operations including character insertion, deletion,
    cursor movement, input validation, and command history.
    """

    def __init__(self, buffer_limit: int = 1000, history_limit: int = 100):
        """Initialize the buffer manager.

        Args:
            buffer_limit: Maximum characters allowed in buffer.
            history_limit: Maximum commands to keep in history.
        """
        self._buffer = ""
        self._cursor_pos = 0
        self._buffer_limit = buffer_limit
        self._history: List[str] = []
        self._history_limit = history_limit
        self._history_index = -1
        self._temp_buffer = ""  # For history navigation

        logger.debug(
            "BufferManager initialized with limits: "
            f"buffer={buffer_limit}, history={history_limit}"
        )

    @property
    def content(self) -> str:
        """Get current buffer content."""
        return self._buffer

    @property
    def cursor_position(self) -> int:
        """Get current cursor position."""
        return self._cursor_pos

    @property
    def is_empty(self) -> bool:
        """Check if buffer is empty or only whitespace."""
        return not self._buffer.strip()

    @property
    def length(self) -> int:
        """Get current buffer length."""
        return len(self._buffer)

    def insert_char(self, char: str) -> bool:
        """Insert a character at the current cursor position.

        Args:
            char: Character to insert.

        Returns:
            True if character was inserted, False if rejected.
        """
        if not self._is_valid_char(char):
            return False

        if len(self._buffer) >= self._buffer_limit:
            logger.warning(f"Buffer limit reached: {self._buffer_limit}")
            return False

        # Insert character at cursor position
        self._buffer = (
            self._buffer[: self._cursor_pos] + char + self._buffer[self._cursor_pos :]
        )
        self._cursor_pos += 1

        return True

    def delete_char(self) -> bool:
        """Delete character before cursor (backspace behavior).

        Returns:
            True if character was deleted, False if at beginning.
        """
        if self._cursor_pos == 0:
            return False

        self._buffer = (
            self._buffer[: self._cursor_pos - 1] + self._buffer[self._cursor_pos :]
        )
        self._cursor_pos -= 1
        return True

    def delete_forward(self) -> bool:
        """Delete character after cursor (delete key behavior).

        Returns:
            True if character was deleted, False if at end.
        """
        if self._cursor_pos >= len(self._buffer):
            return False

        self._buffer = (
            self._buffer[: self._cursor_pos] + self._buffer[self._cursor_pos + 1 :]
        )
        return True

    def move_cursor(self, direction: str) -> bool:
        """Move cursor left or right.

        Args:
            direction: "left" or "right".

        Returns:
            True if cursor moved, False if at boundary.
        """
        if direction == "left" and self._cursor_pos > 0:
            self._cursor_pos -= 1
            return True
        elif direction == "right" and self._cursor_pos < len(self._buffer):
            self._cursor_pos += 1
            return True
        return False

    def move_to_start(self) -> None:
        """Move cursor to start of buffer."""
        self._cursor_pos = 0

    def move_to_end(self) -> None:
        """Move cursor to end of buffer."""
        self._cursor_pos = len(self._buffer)

    @property
    def is_multiline(self) -> bool:
        """Check if buffer contains newlines."""
        return "\n" in self._buffer

    def get_cursor_line_col(self) -> Tuple[int, int]:
        """Calculate cursor line and column from absolute position.

        Returns:
            Tuple of (line_index, column_index)
        """
        lines = self._buffer.split("\n")
        chars_counted = 0
        for i, line in enumerate(lines):
            line_end = chars_counted + len(line)
            if self._cursor_pos <= line_end:
                return (i, self._cursor_pos - chars_counted)
            chars_counted = line_end + 1  # +1 for newline
        # Cursor at very end
        return (len(lines) - 1, len(lines[-1]) if lines else 0)

    def move_cursor_vertical(self, direction: str) -> bool:
        """Move cursor up or down one line.

        Args:
            direction: "up" or "down"

        Returns:
            True if cursor moved, False if at boundary
        """
        lines = self._buffer.split("\n")
        line, col = self.get_cursor_line_col()

        if direction == "up" and line > 0:
            target_line = line - 1
            target_col = min(col, len(lines[target_line]))
            self._set_cursor_to_line_col(lines, target_line, target_col)
            return True
        elif direction == "down" and line < len(lines) - 1:
            target_line = line + 1
            target_col = min(col, len(lines[target_line]))
            self._set_cursor_to_line_col(lines, target_line, target_col)
            return True
        return False

    def _set_cursor_to_line_col(self, lines: List[str], line: int, col: int) -> None:
        """Set cursor position from line and column.

        Args:
            lines: List of lines in buffer
            line: Target line index
            col: Target column index
        """
        pos = sum(len(lines[i]) + 1 for i in range(line))  # +1 for each newline
        pos += col
        self._cursor_pos = pos

    def clear(self) -> None:
        """Clear the buffer and reset cursor."""
        self._buffer = ""
        self._cursor_pos = 0
        self._reset_history_navigation()

    def get_content_and_clear(self) -> str:
        """Get buffer content and clear it.

        Returns:
            The buffer content before clearing.
        """
        content = self._buffer
        self.clear()
        return content

    def add_to_history(self, command: str) -> None:
        """Add a command to history.

        Args:
            command: Command to add to history.
        """
        if not command.strip():
            return

        # Remove duplicate if it exists
        if command in self._history:
            self._history.remove(command)

        # Add to end and maintain limit
        self._history.append(command)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

        self._reset_history_navigation()
        logger.debug(f"Added to history: {command[:50]}...")

    def navigate_history(self, direction: str) -> bool:
        """Navigate through command history.

        Args:
            direction: "up" for previous, "down" for next.

        Returns:
            True if history was navigated, False if at boundary.
        """
        if not self._history:
            return False

        # Save current buffer on first history navigation
        if self._history_index == -1:
            self._temp_buffer = self._buffer

        if direction == "up":
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                self._load_from_history()
                return True
        elif direction == "down":
            if self._history_index > -1:
                self._history_index -= 1
                if self._history_index == -1:
                    # Restore temp buffer
                    self._buffer = self._temp_buffer
                    self._cursor_pos = len(self._buffer)
                else:
                    self._load_from_history()
                return True

        return False

    def get_display_info(self) -> Tuple[str, int]:
        """Get buffer content and cursor position for display.

        Returns:
            Tuple of (buffer_content, cursor_position).
        """
        return self._buffer, self._cursor_pos

    def validate_content(self) -> List[str]:
        """Validate current buffer content.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = []

        # Check for potentially dangerous content
        dangerous_patterns = [
            "rm -rf",
            "sudo rm",
            ":(){ :|:& };:",
            "fork bomb",
        ]

        for pattern in dangerous_patterns:
            if pattern in self._buffer.lower():
                errors.append(
                    f"Potentially dangerous command pattern detected: {pattern}"
                )

        # Check for very long lines that might cause issues
        if len(self._buffer) > self._buffer_limit * 0.9:
            errors.append(
                f"Input approaching buffer limit "
                f"({len(self._buffer)}/{self._buffer_limit})"
            )

        return errors

    def _is_valid_char(self, char: str) -> bool:
        """Check if character is valid for input.

        Args:
            char: Character to validate.

        Returns:
            True if character is valid.
        """
        if not char or len(char) != 1:
            return False

        char_code = ord(char)

        # Allow printable ASCII characters
        if 32 <= char_code <= 126:
            return True

        # Allow some special characters like tab and newline
        if char_code in [9, 10]:  # Tab, Newline
            return True

        return False

    def _load_from_history(self) -> None:
        """Load buffer from history at current index."""
        if 0 <= self._history_index < len(self._history):
            # History is stored newest-first, but we navigate oldest-first
            history_item = self._history[-(self._history_index + 1)]
            self._buffer = history_item
            self._cursor_pos = len(self._buffer)

    def _reset_history_navigation(self) -> None:
        """Reset history navigation state."""
        self._history_index = -1
        self._temp_buffer = ""

    async def handle_paste(self, paste_content: str) -> bool:
        """Handle pasted content with proper line break and size management.

        Args:
            paste_content: Content that was pasted.

        Returns:
            True if paste was successfully handled, False if rejected.
        """
        if not paste_content:
            return False

        # Check if adding paste content would exceed buffer limit
        if len(self._buffer) + len(paste_content) > self._buffer_limit:
            total_len = len(self._buffer) + len(paste_content)
            logger.warning(
                "Paste rejected: would exceed buffer limit "
                f"({total_len} > {self._buffer_limit})"
            )
            return False

        # Process paste content (handle line breaks properly)
        processed_content = self._process_paste_content(paste_content)

        # Insert at current cursor position
        self._buffer = (
            self._buffer[: self._cursor_pos]
            + processed_content
            + self._buffer[self._cursor_pos :]
        )

        # Move cursor to end of pasted content
        self._cursor_pos += len(processed_content)

        # Reset history navigation since buffer was modified
        self._reset_history_navigation()

        logger.debug(
            "Paste handled successfully: " f"{len(processed_content)} chars inserted"
        )
        return True

    def _process_paste_content(self, content: str) -> str:
        """Process pasted content to handle line breaks and formatting.

        Args:
            content: Raw pasted content.

        Returns:
            Processed content suitable for buffer insertion.
        """
        # For now, convert line breaks to spaces to prevent auto-submission
        # This preserves the content while making it safe for single-line input
        processed = content.replace("\n", " ").replace("\r", " ")

        # Normalize multiple spaces to single spaces
        import re

        processed = re.sub(r"\s+", " ", processed)

        # Strip leading/trailing whitespace
        processed = processed.strip()

        return processed

    def get_stats(self) -> dict:
        """Get buffer statistics for debugging.

        Returns:
            Dictionary with buffer statistics.
        """
        return {
            "buffer_length": len(self._buffer),
            "buffer_limit": self._buffer_limit,
            "cursor_position": self._cursor_pos,
            "history_count": len(self._history),
            "history_limit": self._history_limit,
            "history_index": self._history_index,
        }
