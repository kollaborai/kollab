"""Thinking content display formatting for terminal UI.

Formats thinking text for display at 70% of terminal width
with word boundary truncation and incremental updates.

To be added to __init__.py:
    from .thinking_display import ThinkingDisplayFormatter
    __all__.append("ThinkingDisplayFormatter")
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# Filter out confusing thinking content that shouldn't be displayed
PLACEHOLDER_STRINGS = {
    "generating...",
    "generating",
    "processing...",
    "processing",
}

DEFAULT_DISPLAY = "Analyzing your request..."
DEFAULT_WIDTH = 80
WIDTH_RATIO = 0.7


class ThinkingDisplayFormatter:
    """Format thinking content for terminal display with stateful position tracking.

    This class maintains position state across calls to enable incremental
    display updates as thinking content streams in.
    """

    def __init__(self) -> None:
        """Initialize the formatter with reset state."""
        self.reset()

    def reset(self) -> None:
        """Reset position state for new thinking block."""
        self._last_chunk_position = 0

    def format(
        self,
        thinking_buffer: str,
        final: bool = False,
        terminal_width: Optional[int] = None,
    ) -> Optional[str]:
        """Format thinking content for terminal display.

        Updates internal position state and returns display text if ready.

        Args:
            thinking_buffer: Current thinking content buffer
            final: Whether this is the final processing (show remaining content)
            terminal_width: Terminal width (auto-detected if None)

        Returns:
            Display text if ready to update, None if waiting for more content
        """
        # Get terminal width and calculate thinking display width (70% of terminal width)
        if terminal_width is None:
            try:
                terminal_width = os.get_terminal_size().columns
            except Exception:
                terminal_width = DEFAULT_WIDTH

        chunk_width = int(terminal_width * WIDTH_RATIO)

        # Normalize whitespace in thinking buffer (convert line breaks to spaces)
        normalized_buffer = " ".join(thinking_buffer.split())

        # Filter out confusing thinking content
        if normalized_buffer.strip().lower() in PLACEHOLDER_STRINGS:
            normalized_buffer = DEFAULT_DISPLAY

        # Get content from where we left off
        remaining_content = normalized_buffer[self._last_chunk_position :]

        if final:
            # Final processing - show whatever remains
            if remaining_content.strip():
                display_text = remaining_content.strip()
                if len(display_text) > chunk_width:
                    # Truncate with word boundary
                    truncated = display_text[: chunk_width - 3]
                    last_space = truncated.rfind(" ")
                    if last_space > chunk_width * 0.8:
                        truncated = truncated[:last_space]
                    display_text = truncated + "..."
                # Reset position for next thinking block
                self._last_chunk_position = 0
                return display_text
            return None

        # Check if we have enough content for a full chunk
        if len(remaining_content) >= chunk_width:
            # Extract a chunk of chunk_width characters
            chunk = remaining_content[:chunk_width]

            # Try to break at word boundary to avoid cutting words
            last_space = chunk.rfind(" ")
            if last_space > chunk_width * 0.8:
                # Only break at space if it's not too short
                chunk = chunk[:last_space]

            chunk = chunk.strip()
            if chunk:
                display_text = chunk + "..."
                # Update position to after this chunk
                self._last_chunk_position += len(chunk)
                # Add space to position if we broke at a space
                if chunk != remaining_content[: len(chunk)].strip():
                    self._last_chunk_position += 1
                return display_text

        # Not enough content for a full chunk yet
        return None
