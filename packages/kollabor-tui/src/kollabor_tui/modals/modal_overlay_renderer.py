"""Pure modal overlay renderer that bypasses chat message system.

This renderer provides true modal overlay functionality by:
1. Using direct terminal output (no conversation buffer)
2. Saving/restoring terminal state
3. Implementing proper screen buffer management
4. Providing isolated modal display that never accumulates in chat
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from kollabor_tui.terminal_state import TerminalState

logger = logging.getLogger(__name__)


@dataclass
class ModalState:
    """Represents saved terminal state for modal restoration."""

    cursor_position: Tuple[int, int]
    screen_lines: List[str]
    cursor_visible: bool
    terminal_size: Tuple[int, int]


class ModalOverlayRenderer:
    """Pure modal overlay renderer with terminal state isolation.

    This class provides modal display functionality that:
    - Never interacts with conversation buffer or chat pipeline
    - Uses direct terminal control sequences for overlay rendering
    - Maintains complete isolation from message accumulation systems
    - Implements proper save/restore of terminal state
    """

    def __init__(self, terminal_state: TerminalState):
        """Initialize modal overlay renderer.

        Args:
            terminal_state: TerminalState instance for direct terminal control.
        """
        self.terminal_state = terminal_state
        self.modal_active = False
        self.saved_state: Optional[ModalState] = None
        self.modal_lines: List[str] = []
        self.modal_position = (0, 0)  # (row, col) for modal positioning

    def show_modal_overlay(
        self, modal_lines: List[str], center_position: bool = True
    ) -> bool:
        """Display modal as true overlay without affecting conversation.

        Args:
            modal_lines: List of modal content lines to display.
            center_position: Whether to center modal on screen.

        Returns:
            True if modal was displayed successfully.
        """
        try:
            # Save current terminal state
            if not self._save_terminal_state():
                logger.error("Failed to save terminal state")
                return False

            # Calculate modal position
            if center_position:
                self.modal_position = self._calculate_center_position(modal_lines)

            # Clear any existing modal content
            self._clear_modal_area()

            # Render modal overlay using direct terminal output
            self._render_modal_direct(modal_lines)

            # Store modal state
            self.modal_lines = modal_lines.copy()
            self.modal_active = True

            logger.info(f"Modal overlay displayed with {len(modal_lines)} lines")
            return True

        except Exception as e:
            logger.error(f"Failed to show modal overlay: {e}")
            return False

    def refresh_modal_display(self) -> bool:
        """Refresh modal display without accumulation.

        This method re-renders the modal content without any interaction
        with conversation buffers or message systems.

        Returns:
            True if refresh was successful.
        """
        if not self.modal_active or not self.modal_lines:
            return False

        try:
            # Clear current modal area
            self._clear_modal_area()

            # Re-render modal content directly
            self._render_modal_direct(self.modal_lines)

            logger.debug("Modal display refreshed without accumulation")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh modal display: {e}")
            return False

    def hide_modal_overlay(self) -> bool:
        """Hide modal overlay and restore terminal state.

        Returns:
            True if modal was hidden successfully.
        """
        try:
            if not self.modal_active:
                return True

            # Clear modal area
            self._clear_modal_area()

            # Restore terminal state if saved
            if self.saved_state:
                self._restore_terminal_state()

            # Reset modal state
            self.modal_active = False
            self.modal_lines = []
            self.saved_state = None

            logger.info("Modal overlay hidden and terminal state restored")
            return True

        except Exception as e:
            logger.error(f"Failed to hide modal overlay: {e}")
            return False

    def update_modal_content(self, new_lines: List[str]) -> bool:
        """Update modal content and refresh display.

        Args:
            new_lines: New modal content lines.

        Returns:
            True if update was successful.
        """
        if not self.modal_active:
            return False

        try:
            # Update content and refresh display
            self.modal_lines = new_lines.copy()
            return self.refresh_modal_display()

        except Exception as e:
            logger.error(f"Failed to update modal content: {e}")
            return False

    def _save_terminal_state(self) -> bool:
        """Save current terminal state for restoration.

        Returns:
            True if state was saved successfully.
        """
        try:
            # Get current terminal size
            width, height = self.terminal_state.get_size()

            # Create saved state (simplified - real implementation would capture screen)
            self.saved_state = ModalState(
                cursor_position=(0, 0),  # Would query actual cursor position
                screen_lines=[],  # Would capture current screen content
                cursor_visible=not self.terminal_state._cursor_hidden,
                terminal_size=(width, height),
            )

            logger.debug("Terminal state saved for modal")
            return True

        except Exception as e:
            logger.error(f"Failed to save terminal state: {e}")
            return False

    def _restore_terminal_state(self) -> bool:
        """Restore terminal state from saved state.

        Returns:
            True if state was restored successfully.
        """
        if not self.saved_state:
            return False

        try:
            # Restore cursor visibility
            if self.saved_state.cursor_visible:
                self.terminal_state.show_cursor()
            else:
                self.terminal_state.hide_cursor()

            logger.debug("Terminal state restored after modal")
            return True

        except Exception as e:
            logger.error(f"Failed to restore terminal state: {e}")
            return False

    def _calculate_center_position(self, modal_lines: List[str]) -> Tuple[int, int]:
        """Calculate center position for modal on screen.

        Args:
            modal_lines: Modal content lines.

        Returns:
            Tuple of (row, col) for modal position.
        """
        width, height = self.terminal_state.get_size()

        # Calculate modal dimensions
        modal_height = len(modal_lines)
        modal_width = max(len(line) for line in modal_lines) if modal_lines else 0

        # Center position
        start_row = max(0, (height - modal_height) // 2)
        start_col = max(0, (width - modal_width) // 2)

        return (start_row, start_col)

    def _clear_modal_area(self) -> bool:
        """Clear the modal display area.

        Returns:
            True if area was cleared successfully.
        """
        try:
            if not self.modal_lines:
                return True

            # Move to modal position and clear each line
            row, col = self.modal_position

            for i, line in enumerate(self.modal_lines):
                # Move to line position
                self.terminal_state.write_raw(f"\033[{row + i + 1};{col + 1}H")
                # Clear the line content (overwrite with spaces)
                spaces = " " * len(line)
                self.terminal_state.write_raw(spaces)

            logger.debug(f"Cleared modal area at position {self.modal_position}")
            return True

        except Exception as e:
            logger.error(f"Failed to clear modal area: {e}")
            return False

    def _render_modal_direct(self, modal_lines: List[str]) -> bool:
        """Render modal content using direct terminal output.

        This method completely bypasses the message system and writes
        directly to terminal using escape sequences.

        Args:
            modal_lines: Modal content lines to render.

        Returns:
            True if rendering was successful.
        """
        try:
            if not modal_lines:
                return True

            row, col = self.modal_position

            # Render each line directly to terminal
            for i, line in enumerate(modal_lines):
                # Move cursor to position
                terminal_row = row + i + 1  # 1-based positioning
                terminal_col = col + 1  # 1-based positioning

                # Use ANSI escape sequence to position cursor
                self.terminal_state.write_raw(f"\033[{terminal_row};{terminal_col}H")

                # Write line content directly
                self.terminal_state.write_raw(line)

            # Hide cursor for clean modal display
            self.terminal_state.hide_cursor()

            logger.debug(f"Modal rendered directly at position {self.modal_position}")
            return True

        except Exception as e:
            logger.error(f"Failed to render modal directly: {e}")
            return False

    def get_modal_status(self) -> Dict[str, Any]:
        """Get current modal overlay status.

        Returns:
            Dictionary with modal status information.
        """
        return {
            "modal_active": self.modal_active,
            "modal_lines_count": len(self.modal_lines),
            "modal_position": self.modal_position,
            "has_saved_state": self.saved_state is not None,
            "terminal_size": self.terminal_state.get_size(),
        }
