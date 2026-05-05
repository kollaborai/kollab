"""Modal state management for proper terminal state isolation.

This module provides comprehensive terminal state management for modals,
ensuring complete isolation from the conversation system and proper
restoration of terminal state when modals are closed.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from kollabor_tui.terminal_state import TerminalState

logger = logging.getLogger(__name__)


class ModalDisplayMode(Enum):
    """Modal display modes for different rendering strategies."""

    OVERLAY = "overlay"  # Modal overlays existing content
    FULLSCREEN = "fullscreen"  # Modal takes full screen
    INLINE = "inline"  # Modal appears inline (not recommended)


@dataclass
class TerminalSnapshot:
    """Complete snapshot of terminal state before modal display."""

    cursor_position: Tuple[int, int] = (0, 0)
    cursor_visible: bool = True
    terminal_size: Tuple[int, int] = (80, 24)
    screen_buffer: List[str] = field(default_factory=list)
    raw_mode_active: bool = False
    saved_termios: Any = None

    def __post_init__(self):
        """Post-initialization validation."""
        if not isinstance(self.screen_buffer, list):
            self.screen_buffer = []


@dataclass
class ModalLayout:
    """Modal layout configuration for positioning and sizing."""

    width: int = 80
    height: int = 20
    start_row: int = 5
    start_col: int = 10
    center_horizontal: bool = True
    center_vertical: bool = True
    padding: int = 2
    border_style: str = "box"  # "box", "simple", "none"


class ModalStateManager:
    """Manages terminal state isolation for modal displays.

    This class provides complete terminal state management for modals,
    ensuring that modal display and interaction never interferes with
    the underlying conversation or terminal state.
    """

    def __init__(self, terminal_state: TerminalState):
        """Initialize modal state manager.

        Args:
            terminal_state: TerminalState instance for terminal control.
        """
        self.terminal_state = terminal_state
        self.modal_active = False
        self.display_mode = ModalDisplayMode.OVERLAY
        self.saved_snapshot: Optional[TerminalSnapshot] = None
        self.current_layout: Optional[ModalLayout] = None
        self.modal_content_cache: List[str] = []
        # Track max dimensions seen during this modal session for proper clearing
        self._max_rendered_height: int = 0
        self._max_rendered_width: int = 0

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape codes from text.

        Args:
            text: Text with potential ANSI codes.

        Returns:
            Text with ANSI codes removed.
        """
        return re.sub(r"\033\[[0-9;]*m", "", text)

    def prepare_modal_display(
        self,
        layout: ModalLayout,
        display_mode: ModalDisplayMode = ModalDisplayMode.OVERLAY,
    ) -> bool:
        """Prepare terminal for modal display by saving current state.

        Args:
            layout: Modal layout configuration.
            display_mode: How modal should be displayed.

        Returns:
            True if preparation was successful.
        """
        try:
            if self.modal_active:
                logger.warning("Modal already active, closing previous modal first")
                self.restore_terminal_state()

            # Save current terminal state
            self.saved_snapshot = self._capture_terminal_snapshot()
            if not self.saved_snapshot:
                logger.error("Failed to capture terminal snapshot")
                return False

            # Store modal configuration
            self.current_layout = layout
            self.display_mode = display_mode

            # Calculate final layout positions
            self._calculate_modal_position(layout)

            # Prepare terminal for modal rendering
            self._prepare_modal_area()

            self.modal_active = True
            logger.info(f"Modal display prepared in {display_mode.value} mode")
            return True

        except Exception as e:
            logger.error(f"Failed to prepare modal display: {e}")
            return False

    def render_modal_content(self, content_lines: List[str]) -> bool:
        """Render modal content using isolated terminal output.

        Args:
            content_lines: Modal content lines to display.

        Returns:
            True if rendering was successful.
        """
        try:
            if not self.modal_active or not self.current_layout:
                logger.error("Modal not active or layout not configured")
                return False

            # Cache content for refresh operations
            self.modal_content_cache = content_lines.copy()

            # Track max dimensions for proper clearing when content shrinks
            current_height = len(content_lines)
            if current_height > self._max_rendered_height:
                self._max_rendered_height = current_height

            # Track max visible width (strip ANSI codes for accurate width)
            if content_lines:
                max_visible_width = max(
                    len(self._strip_ansi(line)) for line in content_lines
                )
                if max_visible_width > self._max_rendered_width:
                    self._max_rendered_width = max_visible_width

            # Clear previous modal content (uses max dimensions to clear all artifacts)
            self._clear_modal_content_area()

            # Render new content using direct terminal output
            success = self._render_content_direct(content_lines)

            if success:
                logger.debug(f"Modal content rendered: {len(content_lines)} lines")
            else:
                logger.error("Failed to render modal content")

            return success

        except Exception as e:
            logger.error(f"Failed to render modal content: {e}")
            return False

    def refresh_modal_display(self) -> bool:
        """Refresh modal display without state changes.

        This method re-renders the current modal content without
        affecting any terminal state or conversation buffers.

        Returns:
            True if refresh was successful.
        """
        try:
            if not self.modal_active:
                return False

            # Re-render cached content
            return self.render_modal_content(self.modal_content_cache)

        except Exception as e:
            logger.error(f"Failed to refresh modal display: {e}")
            return False

    def restore_terminal_state(self) -> bool:
        """Restore terminal state to pre-modal condition.

        Returns:
            True if restoration was successful.
        """
        try:
            if not self.modal_active:
                return True

            # Clear modal content area
            self._clear_modal_content_area()

            # Restore terminal state from snapshot
            if self.saved_snapshot:
                self._restore_from_snapshot(self.saved_snapshot)

            # Reset modal state
            self._reset_modal_state()

            logger.info("Terminal state restored after modal")
            return True

        except Exception as e:
            logger.error(f"Failed to restore terminal state: {e}")
            return False

    def update_modal_layout(self, new_layout: ModalLayout) -> bool:
        """Update modal layout and re-render.

        Args:
            new_layout: New layout configuration.

        Returns:
            True if layout update was successful.
        """
        try:
            if not self.modal_active:
                return False

            # Clear current modal area
            self._clear_modal_content_area()

            # Update layout
            self.current_layout = new_layout
            self._calculate_modal_position(new_layout)

            # Re-render with new layout
            return self.render_modal_content(self.modal_content_cache)

        except Exception as e:
            logger.error(f"Failed to update modal layout: {e}")
            return False

    def _capture_terminal_snapshot(self) -> Optional[TerminalSnapshot]:
        """Capture complete terminal state snapshot.

        CRITICAL: this method writes \\033[?1049h, which switches the
        terminal into the ALTERNATE screen buffer. From this moment
        until a matching \\033[?1049l is written, any stdout output
        (including ``print()``) lands on the alt screen -- on top of
        whatever the modal is about to draw.

        Callers MUST ensure that the ``MessageDisplayCoordinator`` has
        already been told about the transition via
        ``enter_alternate_buffer()`` BEFORE calling this method.
        Otherwise, any llm / hub / agent messages that arrive during
        the modal will print through ``_output_rendered()`` straight
        onto the modal (the /profile bleed bug).

        The ``_enter_modal_mode`` helper in
        ``input/modal_controller.py`` is the standard caller and does
        this correctly. If you add another caller, either go through
        that helper or make the coordinator call yourself.

        See ``message_coordinator.py`` module docstring for the full
        bleed-through contract.

        Returns:
            TerminalSnapshot or None if capture failed.
        """
        try:

            # Get current terminal dimensions
            width, height = self.terminal_state.get_size()

            # Check cursor hidden state
            cursor_hidden = getattr(self.terminal_state, "_cursor_hidden", False)

            # Check current mode
            current_mode = getattr(self.terminal_state, "current_mode", None)

            mode_value = (
                current_mode.value
                if current_mode and hasattr(current_mode, "value")
                else "unknown"
            )

            # Check original termios
            original_termios = getattr(self.terminal_state, "original_termios", None)

            # SWITCH TO ALTERNATE SCREEN BUFFER (automatically saves entire screen).
            # After this write, stdout is pointed at the alt screen -- any
            # print() call from anywhere in the process lands here. The
            # coordinator's _in_alternate_buffer flag MUST already be True
            # at this point, otherwise concurrent llm response display will
            # print on top of the modal.
            self.terminal_state.write_raw(
                "\033[?1049h"
            )  # noqa: F841 - validates terminal write

            # Create minimal snapshot (alternate buffer handles everything)
            snapshot = TerminalSnapshot(
                cursor_position=(0, 0),  # Not needed - alternate buffer preserves this
                cursor_visible=not cursor_hidden,
                terminal_size=(width, height),
                screen_buffer=[],  # Not needed - alternate buffer handles screen content
                raw_mode_active=mode_value == "raw",
                saved_termios=original_termios,
            )

            return snapshot

        except Exception as e:
            logger.error(f"Failed to capture terminal snapshot: {e}")
            import traceback

            logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    def _restore_from_snapshot(self, snapshot: TerminalSnapshot) -> bool:
        """Restore terminal state from snapshot.

        CRITICAL: this method writes \\033[?1049l, which switches the
        terminal back to the MAIN screen buffer. After this point,
        any stdout output lands in the main buffer again.

        Callers MUST then flush any messages that were buffered while
        the modal was up, via ``coordinator.exit_alternate_buffer()``
        or ``coordinator._flush_buffered_output()``. The flush MUST
        happen after this write -- print() has to see the main
        buffer, or the flushed lines will land in the alt buffer and
        vanish when the modal closes.

        Standard callers in ``input/modal_controller.py`` handle this
        in ``_exit_modal_mode`` and ``_exit_modal_mode_minimal``. If
        you add a new caller, make sure the flush happens after this
        method returns, not before.

        Args:
            snapshot: TerminalSnapshot to restore from.

        Returns:
            True if restoration was successful.
        """
        try:
            # SWITCH BACK FROM ALTERNATE SCREEN BUFFER (automatically restores entire screen).
            # After this write, stdout is pointed back at the main screen
            # buffer. Buffered coordinator messages can now be flushed safely
            # via print() -- but the caller is responsible for calling the
            # flush, this method only handles the terminal state.
            self.terminal_state.write_raw(
                "\033[?1049l"
            )  # noqa: F841 - validates terminal write

            # Restore cursor visibility (alternate buffer preserves position automatically)
            if snapshot.cursor_visible:
                self.terminal_state.show_cursor()
            else:
                self.terminal_state.hide_cursor()

            return True

        except Exception as e:
            logger.error(f"Failed to restore from snapshot: {e}")
            return False

    def _calculate_modal_position(self, layout: ModalLayout) -> None:
        """Calculate modal position based on layout configuration.

        Args:
            layout: ModalLayout configuration.
        """
        width, height = self.terminal_state.get_size()

        if layout.center_horizontal:
            layout.start_col = max(0, (width - layout.width) // 2)

        if layout.center_vertical:
            layout.start_row = max(0, (height - layout.height) // 2)

    def _prepare_modal_area(self) -> bool:
        """Prepare terminal area for modal rendering.

        Returns:
            True if preparation was successful.
        """
        try:
            # Hide cursor for clean modal display
            self.terminal_state.hide_cursor()

            # FIXED: Always clear alternate buffer for clean modal display
            # Clear entire alternate buffer and position cursor at top-left
            self.terminal_state.write_raw("\033[2J\033[H")

            # Additional preparation based on display mode
            if self.display_mode == ModalDisplayMode.FULLSCREEN:
                # Already cleared above, but add any fullscreen-specific setup here
                pass

            logger.debug("Terminal area prepared for modal")
            return True

        except Exception as e:
            logger.error(f"Failed to prepare modal area: {e}")
            return False

    def _render_content_direct(self, content_lines: List[str]) -> bool:
        """Render content using direct terminal output.

        Args:
            content_lines: Content lines to render.

        Returns:
            True if rendering was successful.
        """
        try:
            if not self.current_layout:
                return False

            layout = self.current_layout

            # Render each line at calculated position
            for i, line in enumerate(content_lines):
                if i >= layout.height:
                    break  # Don't exceed modal height

                # Calculate position for this line
                row = layout.start_row + i + 1  # 1-based positioning
                col = layout.start_col + 1  # 1-based positioning

                # Position cursor and write line
                self.terminal_state.write_raw(f"\033[{row};{col}H")

                # Truncate line if too wide
                # Don't truncate here - lines should already be properly sized by modal renderer
                # Just write the line as-is

                self.terminal_state.write_raw(line)

            logger.debug(f"Modal content rendered: {len(content_lines)} lines")
            return True

        except Exception as e:
            logger.error(f"Failed to render content directly: {e}")
            return False

    def _clear_modal_content_area(self) -> bool:
        """Clear the modal content area.

        Uses _max_rendered_height and _max_rendered_width to ensure all
        previously rendered content is cleared, even when content shrinks
        (e.g., scrolling to the bottom of a list).

        Returns:
            True if area was cleared successfully.
        """
        try:
            if not self.current_layout:
                return True

            layout = self.current_layout

            # Use max rendered dimensions to clear all possible artifacts
            # This handles the case where content shrinks (e.g., at bottom of list)
            clear_height = max(layout.height, self._max_rendered_height)
            clear_width = max(layout.width, self._max_rendered_width)

            # Clear each line of the modal area with spaces (overwrite modal content)
            for i in range(clear_height):
                row = layout.start_row + i + 1  # 1-based row positioning
                col = layout.start_col + 1  # 1-based col positioning

                # Position cursor at start of this modal line
                self.terminal_state.write_raw(f"\033[{row};{col}H")

                # Write spaces to overwrite modal content for this line
                spaces = " " * clear_width
                self.terminal_state.write_raw(spaces)

            logger.debug(f"Modal content area cleared ({clear_height}x{clear_width})")
            return True

        except Exception as e:
            logger.error(f"Failed to clear modal content area: {e}")
            return False

    def _reset_modal_state(self) -> None:
        """Reset modal state variables."""
        self.modal_active = False
        self.saved_snapshot = None
        self.current_layout = None
        self.modal_content_cache = []
        self.display_mode = ModalDisplayMode.OVERLAY
        self._max_rendered_height = 0
        self._max_rendered_width = 0

    def get_modal_state_info(self) -> Dict[str, Any]:
        """Get current modal state information.

        Returns:
            Dictionary with modal state details.
        """
        return {
            "modal_active": self.modal_active,
            "display_mode": self.display_mode.value if self.display_mode else None,
            "has_saved_snapshot": self.saved_snapshot is not None,
            "content_lines_cached": len(self.modal_content_cache),
            "current_layout": (
                {
                    "width": self.current_layout.width if self.current_layout else 0,
                    "height": self.current_layout.height if self.current_layout else 0,
                    "position": (
                        self.current_layout.start_row if self.current_layout else 0,
                        self.current_layout.start_col if self.current_layout else 0,
                    ),
                }
                if self.current_layout
                else None
            ),
            "terminal_size": self.terminal_state.get_size(),
        }
