"""Paste processing component for Kollab.

Responsible for detecting, storing, and expanding pasted content.
Implements a dual paste detection system:
1. PRIMARY (chunk-based): Detects large chunks >10 chars, always active
2. SECONDARY (timing-based): Detects rapid typing, currently disabled

The PRIMARY system is handled in InputLoopManager (chunk detection).
This component handles placeholder creation, storage, and expansion.
"""

import logging
import re
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class PasteProcessor:
    """Processes paste detection, placeholder creation, and content expansion.

    This component manages the "genius paste system" which:
    1. Stores pasted content immediately in a bucket
    2. Shows a placeholder to the user: [Pasted #N X lines, Y chars]
    3. Expands placeholders with actual content on submit

    Attributes:
        buffer_manager: Buffer manager for inserting characters.
        display_callback: Async callback to update display after paste operations.
    """

    def __init__(
        self,
        buffer_manager: Any,
        display_callback: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> None:
        """Initialize the paste processor.

        Args:
            buffer_manager: Buffer manager instance for character insertion.
            display_callback: Optional async callback for display updates.
        """
        self.buffer_manager = buffer_manager
        self._display_callback = display_callback

        # PRIMARY paste system state (chunk-based, always active)
        self._paste_bucket: Dict[str, str] = {}  # {paste_id: actual_content}
        self._paste_counter = 0  # Counter for paste numbering
        self._current_paste_id: Optional[str] = None  # Currently building paste ID
        self._last_paste_time = 0.0  # Last chunk timestamp

        # SECONDARY paste system state (timing-based, disabled by default)
        self.paste_detection_enabled = False  # Only enables SECONDARY system
        self._paste_buffer: list = []
        self._last_char_time = 0.0
        self._paste_cooldown = 0.0
        # These would need to be configured if secondary system is enabled:
        self._paste_timeout_ms = 100.0  # Timeout for paste buffer
        self.paste_threshold_ms = 50.0  # Threshold for rapid typing detection
        self.paste_min_chars = 5  # Minimum chars to consider as paste

        logger.debug("PasteProcessor initialized")

    @property
    def paste_bucket(self) -> Dict[str, str]:
        """Get the paste bucket (read-only access for external checks)."""
        return self._paste_bucket

    @property
    def current_paste_id(self) -> Optional[str]:
        """Get the current paste ID being built."""
        return self._current_paste_id

    @property
    def last_paste_time(self) -> float:
        """Get the last paste timestamp."""
        return self._last_paste_time

    def expand_paste_placeholders(self, message: str) -> str:
        """Expand paste placeholders with actual content from paste bucket.

        Replaces [Pasted #N X lines, Y chars] with actual pasted content.

        Args:
            message: Message containing paste placeholders.

        Returns:
            Message with placeholders expanded to actual content.
        """
        logger.debug(f"PASTE DEBUG: Expanding message: '{message}'")
        logger.debug(
            f"PASTE DEBUG: Paste bucket contains: {list(self._paste_bucket.keys())}"
        )

        expanded = message
        expanded_ids = []

        # Find and replace each paste placeholder
        for paste_id, content in list(self._paste_bucket.items()):
            # Extract paste number from paste_id (PASTE_1 -> 1)
            paste_num = paste_id.split("_")[1]

            # Pattern to match: [Pasted #N X lines, Y chars]
            pattern = rf"\[Pasted #{paste_num} \d+ lines?, \d+ chars\]"

            logger.debug(f"PASTE DEBUG: Looking for pattern: {pattern}")
            logger.debug(f"PASTE DEBUG: Will replace with content: '{content[:50]}...'")

            # Replace with actual content
            matches = re.findall(pattern, expanded)
            logger.debug(f"PASTE DEBUG: Found {len(matches)} matches")

            if matches:
                # Use lambda to treat content as literal text, not a replacement template
                # (avoids backslashes being interpreted as regex backreferences)
                expanded = re.sub(pattern, lambda m: content, expanded)
                expanded_ids.append(paste_id)

        logger.debug(f"PASTE DEBUG: Final expanded message: '{expanded[:100]}...'")
        logger.info(f"Paste expansion: {len(expanded_ids)} placeholders expanded")

        # Only clear entries that were actually expanded (preserves other pastes)
        for paste_id in expanded_ids:
            self._paste_bucket.pop(paste_id, None)

        return expanded

    async def create_paste_placeholder(self, paste_id: str) -> None:
        """Create placeholder for paste - GENIUS IMMEDIATE VERSION.

        Creates an elegant placeholder that the user sees in the input buffer,
        while the actual content is stored in the paste bucket.

        Args:
            paste_id: The ID of the paste (e.g., "PASTE_1").
        """
        content = self._paste_bucket[paste_id]

        # Create elegant placeholder for user to see
        line_count = content.count("\n") + 1 if "\n" in content else 1
        char_count = len(content)
        paste_num = paste_id.split("_")[1]  # Extract number from PASTE_1
        placeholder = f"[Pasted #{paste_num} {line_count} lines, {char_count} chars]"

        # Insert placeholder into buffer (what user sees)
        for char in placeholder:
            self.buffer_manager.insert_char(char)

        logger.info(f"GENIUS: Created placeholder for {char_count} chars as {paste_id}")

        # Update display once at the end
        if self._display_callback:
            await self._display_callback(force_render=True)

    async def update_paste_placeholder(self) -> None:
        """Update existing placeholder when paste grows - GENIUS VERSION.

        For now, just logs - updating existing placeholder is complex.
        The merge approach usually works fast enough that this isn't needed.
        """
        if self._current_paste_id is None:
            return
        content = self._paste_bucket[self._current_paste_id]
        logger.info(f"GENIUS: Updated {self._current_paste_id} to {len(content)} chars")

    async def simple_paste_detection(self, char: str, current_time: float) -> bool:
        """Simple, reliable paste detection using timing only.

        This is the SECONDARY paste detection system (disabled by default).

        Args:
            char: The character to process.
            current_time: Current timestamp.

        Returns:
            True if character was consumed by paste detection, False otherwise.
        """
        # Check cooldown to prevent overlapping paste detections
        if self._paste_cooldown > 0 and (current_time - self._paste_cooldown) < 1.0:
            # Still in cooldown period, skip paste detection
            self._last_char_time = current_time
            return False

        # Check if we have a pending paste buffer that timed out
        if self._paste_buffer and self._last_char_time > 0:
            gap_ms = (current_time - self._last_char_time) * 1000

            if gap_ms > self._paste_timeout_ms:
                # Buffer timed out, process it
                if len(self._paste_buffer) >= self.paste_min_chars:
                    self._process_simple_paste_sync()
                    self._paste_cooldown = current_time  # Set cooldown
                else:
                    # Too few chars, process them as individual keystrokes
                    self._flush_paste_buffer_as_keystrokes_sync()
                self._paste_buffer = []

        # Now handle the current character
        if self._last_char_time > 0:
            gap_ms = (current_time - self._last_char_time) * 1000

            # If character arrived quickly, start/continue paste buffer
            if gap_ms < self.paste_threshold_ms:
                self._paste_buffer.append(char)
                self._last_char_time = current_time
                return True  # Character consumed by paste buffer

        # Character not part of paste, process normally
        self._last_char_time = current_time
        return False

    def _flush_paste_buffer_as_keystrokes_sync(self) -> None:
        """Process paste buffer contents as individual keystrokes (sync version)."""
        logger.debug(
            f"Flushing {len(self._paste_buffer)} chars as individual keystrokes"
        )

        # Just add characters to buffer without async processing
        for char in self._paste_buffer:
            if char.isprintable() or char in [" ", "\t"]:
                self.buffer_manager.insert_char(char)

    def _process_simple_paste_sync(self) -> None:
        """Process detected paste content (sync version with inline indicator)."""
        if not self._paste_buffer:
            return

        # Get the content and clean any terminal markers
        content = "".join(self._paste_buffer)

        # Clean bracketed paste markers if present
        if content.startswith("[200~"):
            content = content[5:]
        if content.endswith("01~"):
            content = content[:-3]
        elif content.endswith("[201~"):
            content = content[:-6]

        # Count lines
        line_count = content.count("\n") + 1
        char_count = len(content)

        # Increment paste counter
        self._paste_counter += 1

        # Create inline paste indicator exactly as user requested
        indicator = f"[Pasted #{self._paste_counter} {line_count} lines]"

        # Insert the indicator into the buffer at current position
        try:
            for char in indicator:
                self.buffer_manager.insert_char(char)
            logger.info(
                f"Paste #{self._paste_counter}: {char_count} chars, {line_count} lines"
            )
        except Exception as e:
            logger.error(f"Paste processing error: {e}")

        # Clear paste buffer
        self._paste_buffer = []

    async def flush_paste_buffer_as_keystrokes(self) -> None:
        """Process paste buffer contents as individual keystrokes."""
        self._flush_paste_buffer_as_keystrokes_sync()

    async def process_simple_paste(self) -> None:
        """Process detected paste content."""
        self._process_simple_paste_sync()
        if self._display_callback:
            await self._display_callback(force_render=True)

    # Methods for InputLoopManager to manage paste state during chunk detection

    def _normalize_line_endings(self, text: str) -> str:
        """Normalize line endings to Unix style (LF only).

        Converts Windows (CRLF) and old Mac (CR) line endings to Unix (LF).
        This prevents display issues where CR causes lines to overwrite each other.

        Args:
            text: Text with potentially mixed line endings.

        Returns:
            Text with normalized line endings.
        """
        # First convert CRLF to LF, then convert remaining CR to LF
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def start_new_paste(self, chunk: str, current_time: float) -> str:
        """Start a new paste with the given chunk.

        Args:
            chunk: The pasted content.
            current_time: Current timestamp.

        Returns:
            The paste ID for this paste.
        """
        self._paste_counter += 1
        self._current_paste_id = f"PASTE_{self._paste_counter}"
        # Normalize line endings to prevent display issues
        self._paste_bucket[self._current_paste_id] = self._normalize_line_endings(chunk)
        self._last_paste_time = current_time
        logger.debug(f"Started new paste: {self._current_paste_id}")
        return self._current_paste_id

    def append_to_current_paste(self, chunk: str, current_time: float) -> None:
        """Append content to the current paste being built.

        Args:
            chunk: Additional content to append.
            current_time: Current timestamp.
        """
        if self._current_paste_id and self._current_paste_id in self._paste_bucket:
            # Normalize line endings to prevent display issues
            self._paste_bucket[self._current_paste_id] += self._normalize_line_endings(
                chunk
            )
            self._last_paste_time = current_time
            logger.debug(
                f"Appended to {self._current_paste_id}: "
                f"now {len(self._paste_bucket[self._current_paste_id])} chars"
            )

    def should_merge_paste(self, current_time: float, threshold: float = 0.1) -> bool:
        """Check if a new chunk should merge with current paste.

        Args:
            current_time: Current timestamp.
            threshold: Time threshold in seconds for merging (default 0.1s).

        Returns:
            True if the chunk should merge with current paste.
        """
        return (
            self._current_paste_id is not None
            and self._last_paste_time > 0
            and (current_time - self._last_paste_time) < threshold
        )
