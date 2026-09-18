"""Paste processing component for Kollab.

Responsible for detecting, storing, and expanding pasted content.
Implements a dual paste detection system:
1. PRIMARY (chunk-based): Detects large chunks >10 chars, always active
2. SECONDARY (timing-based): Detects rapid typing, currently disabled

The PRIMARY system is handled in InputLoopManager (chunk detection).
This component handles placeholder creation, storage, and expansion.
"""

import asyncio
import base64
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Optional

from kollabor_tui.clipboard import (
    MAX_CLIPBOARD_IMAGE_BYTES,
    read_image_from_clipboard,
    read_text_from_clipboard,
)

logger = logging.getLogger(__name__)

_IMAGE_TOKEN_PATTERN = re.compile(r"\[image\d+\]")
_MAX_TOTAL_CLIPBOARD_IMAGE_BYTES = 20 * 1024 * 1024


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

        # CLI image attachments. The terminal can only render the opaque token;
        # raw clipboard data stays here until Enter hands it to the daemon.
        self._image_attachments: Dict[str, str] = {}
        self._image_sizes: Dict[str, int] = {}
        self._image_counter = 0
        self._image_total_bytes = 0

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

    @property
    def has_image_attachments(self) -> bool:
        """Whether the current input contains live image attachments."""
        return bool(self._image_attachments)

    @property
    def image_attachments(self) -> Dict[str, str]:
        """Return a copy of the current token-to-data-URL map."""
        return dict(self._image_attachments)

    def has_live_image_token(self, message: str) -> bool:
        """Whether ``message`` still contains an attached image token."""
        return any(
            match.group(0) in self._image_attachments
            for match in _IMAGE_TOKEN_PATTERN.finditer(message)
        )

    def history_projection(self, message: str) -> str:
        """Keep image history truthful after one-shot attachments are sent."""
        return _IMAGE_TOKEN_PATTERN.sub(
            lambda match: (
                "[image attachment omitted]"
                if match.group(0) in self._image_attachments
                else match.group(0)
            ),
            message,
        )

    async def handle_clipboard_paste(self) -> bool:
        """Paste an image token, or fall back to ordinary clipboard text.

        Terminal emulators generally send only text through the PTY. A
        ``Ctrl+V`` keypress gives the CLI an explicit, platform-neutral hook to
        query the local clipboard for an image before falling back to text.
        """
        image = await asyncio.to_thread(read_image_from_clipboard)
        if image is not None:
            media_type, payload = image
            data_url = (
                f"data:{media_type};base64,"
                f"{base64.b64encode(payload).decode('ascii')}"
            )
            token = await self.add_image_attachment(data_url, len(payload))
            if token is not None:
                logger.info("Inserted clipboard image as %s", token)
                return True
            return False

        text = await asyncio.to_thread(read_text_from_clipboard)
        if text:
            return await self.buffer_manager.handle_paste(text)
        return False

    async def add_image_attachment(
        self, data_url: str, raw_size: Optional[int] = None
    ) -> Optional[str]:
        """Insert a token for a validated clipboard image."""
        if not isinstance(data_url, str) or not data_url.lower().startswith(
            "data:image/"
        ):
            logger.warning("Rejected non-image clipboard data")
            return None

        size = raw_size if raw_size is not None else len(data_url.encode("utf-8"))
        if size > MAX_CLIPBOARD_IMAGE_BYTES:
            logger.warning("Clipboard image exceeds the per-image byte limit")
            return None
        if self._image_total_bytes + size > _MAX_TOTAL_CLIPBOARD_IMAGE_BYTES:
            logger.warning("Clipboard images exceed the session memory limit")
            return None

        self._image_counter += 1
        token = f"[image{self._image_counter}]"
        inserted = await self.buffer_manager.handle_paste(token)
        if not inserted:
            self._image_counter -= 1
            return None

        self._image_attachments[token] = data_url
        self._image_sizes[token] = size
        self._image_total_bytes += size
        return token

    def clear_image_attachments(self) -> None:
        """Release all unsent CLI image data."""
        self._image_attachments.clear()
        self._image_sizes.clear()
        self._image_total_bytes = 0
        self._image_counter = 0

    def _image_token_span_at_cursor(
        self, backward: bool
    ) -> Optional[tuple[int, int, str]]:
        """Find the live image token adjacent to the current cursor."""
        content = self.buffer_manager.content
        cursor = self.buffer_manager.cursor_position
        for match in _IMAGE_TOKEN_PATTERN.finditer(content):
            token = match.group(0)
            if token not in self._image_attachments:
                continue
            if backward and match.end() == cursor:
                return match.start(), match.end(), token
            if not backward and match.start() == cursor:
                return match.start(), match.end(), token
            if match.start() < cursor < match.end():
                return match.start(), match.end(), token
        return None

    def _renumber_image_tokens(self) -> None:
        """Keep visible token numbers contiguous after an attachment delete."""
        content = self.buffer_manager.content
        cursor = self.buffer_manager.cursor_position
        matches = list(_IMAGE_TOKEN_PATTERN.finditer(content))
        live_matches = [
            match for match in matches if match.group(0) in self._image_attachments
        ]

        live_tokens = {match.group(0) for match in live_matches}
        stale_tokens = set(self._image_attachments) - live_tokens
        for token in stale_tokens:
            self._image_total_bytes -= self._image_sizes.pop(token, 0)
            self._image_attachments.pop(token, None)

        if not live_matches:
            self._image_counter = 0
            return

        renames = {
            match.group(0): f"[image{index}]"
            for index, match in enumerate(live_matches, start=1)
        }
        new_content = _IMAGE_TOKEN_PATTERN.sub(
            lambda match: renames.get(match.group(0), match.group(0)), content
        )
        new_cursor = cursor
        for match in matches:
            if match.start() < cursor:
                new_cursor += len(renames.get(match.group(0), match.group(0))) - len(
                    match.group(0)
                )

        if new_content != content:
            self.buffer_manager.replace_content(new_content, new_cursor)

        self._image_attachments = {
            renames[token]: data_url
            for token, data_url in self._image_attachments.items()
            if token in renames
        }
        self._image_sizes = {
            renames[token]: size
            for token, size in self._image_sizes.items()
            if token in renames
        }
        self._image_counter = len(live_matches)

    def _delete_image_token(self, backward: bool) -> bool:
        span = self._image_token_span_at_cursor(backward)
        if span is None:
            return False
        start, end, token = span
        if not self.buffer_manager.delete_range(start, end):
            return False
        self._image_total_bytes -= self._image_sizes.pop(token, 0)
        self._image_attachments.pop(token, None)
        self._renumber_image_tokens()
        logger.info("Removed clipboard image %s", token)
        return True

    def delete_image_token_before_cursor(self) -> bool:
        """Delete the live image token immediately before the cursor."""
        return self._delete_image_token(backward=True)

    def delete_image_token_at_cursor(self) -> bool:
        """Delete the live image token immediately after the cursor."""
        return self._delete_image_token(backward=False)

    def build_message_content(self, message: str) -> Any:
        """Convert visible image tokens into structured message parts."""
        matches = list(_IMAGE_TOKEN_PATTERN.finditer(message))
        live_matches = [
            match for match in matches if match.group(0) in self._image_attachments
        ]
        if not live_matches:
            self.clear_image_attachments()
            return message

        parts: list[dict[str, str]] = []
        cursor = 0
        for match in matches:
            token = match.group(0)
            if match.start() > cursor:
                parts.append({"type": "text", "text": message[cursor : match.start()]})
            data_url = self._image_attachments.get(token)
            if data_url is None:
                parts.append({"type": "text", "text": token})
            else:
                parts.append({"type": "image", "image": data_url})
            cursor = match.end()
        if cursor < len(message):
            parts.append({"type": "text", "text": message[cursor:]})

        self.clear_image_attachments()
        return parts

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
