"""Full-screen renderer for plugin terminal output."""

import logging
import sys
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from kollabor_tui.terminal_state import get_terminal_size
from kollabor_tui.visual_effects import ColorPalette

# Platform-specific imports for terminal control
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    STD_OUTPUT_HANDLE = -11
    STD_INPUT_HANDLE = -10
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    ENABLE_ECHO_INPUT = 0x0004
    ENABLE_LINE_INPUT = 0x0002
    ENABLE_PROCESSED_INPUT = 0x0001
else:
    import termios

logger = logging.getLogger(__name__)


@dataclass
class TerminalSnapshot:
    """Snapshot of terminal state for restoration."""

    termios_settings: Any = None  # Unix termios settings
    console_mode: Any = None  # Windows console mode
    cursor_visible: bool = True
    terminal_size: Tuple[int, int] = (80, 24)


class FullScreenRenderer:
    """Handles full-screen rendering with alternate buffer management.

    This class provides direct terminal control for plugins, including
    alternate buffer management, cursor control, and direct output.
    """

    def __init__(self):
        """Initialize the full-screen renderer."""
        self.active: bool = False
        self.terminal_snapshot: Optional[TerminalSnapshot] = None
        self.terminal_width = 80
        self.terminal_height = 24

        # Frame buffering to eliminate flicker
        self._frame_buffer = []
        self._buffering_enabled = False

        # Skip-if-unchanged: if a frame is byte-identical to the last one, write
        # nothing. This stops timer-driven views (setup wizard, model picker at
        # 12-15fps) from repainting while idle -- which is the flicker. A frame
        # that *did* change is flushed in full (the buffer still starts with
        # clear_screen's \033[2J), so a changed frame always repaints cleanly
        # and can never leave stale rows behind -- no ghosting.
        self._last_frame: str = ""

        logger.info("FullScreenRenderer initialized")

    def setup_terminal(self) -> bool:
        """Setup terminal for full-screen rendering.

        Returns:
            True if setup was successful, False otherwise.
        """
        try:
            # Get terminal size from global terminal state
            self.terminal_width, self.terminal_height = get_terminal_size()

            # Save current terminal state
            self.terminal_snapshot = TerminalSnapshot()
            # CRITICAL FIX: Don't set raw mode when running within main application
            # The main app already handles input properly via InputHandler
            if sys.stdin.isatty():
                if IS_WINDOWS:
                    # Windows: Save console mode
                    stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
                    mode = ctypes.c_ulong()
                    kernel32.GetConsoleMode(stdin_handle, ctypes.byref(mode))
                    self.terminal_snapshot.console_mode = mode.value

                    # Enable VT processing for ANSI escape sequences
                    stdout_handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
                    out_mode = ctypes.c_ulong()
                    kernel32.GetConsoleMode(stdout_handle, ctypes.byref(out_mode))
                    kernel32.SetConsoleMode(
                        stdout_handle,
                        out_mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING,
                    )
                else:
                    # Unix: Save termios settings
                    self.terminal_snapshot.termios_settings = termios.tcgetattr(
                        sys.stdin.fileno()
                    )
                # Skip tty.setraw() - let main application handle input

            # Enter alternate buffer and setup
            sys.stdout.write("\033[?1049h")  # Enter alternate buffer
            sys.stdout.flush()

            # Clear and setup alternate buffer
            self.clear_screen()
            self.hide_cursor()
            sys.stdout.flush()

            # Fill screen with spaces to ensure clean state
            for row in range(self.terminal_height):
                sys.stdout.write(f"\033[{row+1};1H{' ' * self.terminal_width}")
            sys.stdout.write("\033[H")  # Return to home
            sys.stdout.flush()

            # Screen is now blank -- force the next frame to paint in full
            # (also covers session re-entry, which calls setup_terminal again).
            self._last_frame = ""

            self.active = True
            logger.info(
                f"Terminal setup complete: {self.terminal_width}x{self.terminal_height}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to setup terminal: {e}")
            return False

    def restore_terminal(self) -> bool:
        """Restore terminal to original state.

        Returns:
            True if restoration was successful, False otherwise.
        """
        try:
            if not self.active:
                return True

            # Clear alternate buffer before exiting
            self.clear_screen()
            # Don't call show_cursor() - normal mode keeps cursor hidden anyway

            # Exit alternate buffer (automatically restores screen and cursor position)
            sys.stdout.write("\033[?1049l")  # Exit alternate buffer
            sys.stdout.flush()

            # CRITICAL FIX: Don't clear screen or move cursor - alternate buffer already restored everything
            # Removed: sys.stdout.write("\033[2J") and sys.stdout.write("\033[H")
            # The alternate buffer automatically restores the exact screen and cursor position

            # Restore terminal settings
            if self.terminal_snapshot and sys.stdin.isatty():
                if IS_WINDOWS:
                    # Windows: Restore console mode
                    if self.terminal_snapshot.console_mode is not None:
                        stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
                        kernel32.SetConsoleMode(
                            stdin_handle, self.terminal_snapshot.console_mode
                        )
                else:
                    # Unix: Restore termios settings
                    if self.terminal_snapshot.termios_settings:
                        termios.tcsetattr(
                            sys.stdin.fileno(),
                            termios.TCSADRAIN,
                            self.terminal_snapshot.termios_settings,
                        )

            self.active = False
            self.terminal_snapshot = None
            logger.info("Terminal restored successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to restore terminal: {e}")
            return False

    def begin_frame(self):
        """Begin a new frame with buffering enabled.

        All writes are buffered until end_frame(), which flushes the whole
        frame as one write -- or skips it entirely if it is unchanged.
        """
        self._frame_buffer = []
        self._buffering_enabled = True

    def end_frame(self):
        """End the current frame and flush it, unless it is unchanged.

        The frame is written as a single operation only when it differs from
        the previous frame. An identical frame writes nothing (no idle
        flicker); a changed frame is written in full -- its buffer starts with
        clear_screen's \\033[2J, so it always repaints cleanly (no ghosting).
        """
        if not self._buffering_enabled:
            return
        try:
            composed = "".join(self._frame_buffer)
            if composed and composed != self._last_frame:
                sys.stdout.write(composed)
                sys.stdout.flush()
                self._last_frame = composed
        finally:
            self._buffering_enabled = False
            self._frame_buffer = []

    def invalidate_render_cache(self) -> None:
        """Force the next frame to repaint in full.

        Mirrors terminal_renderer.invalidate_render_cache(); call after a
        resize or any out-of-band screen change.
        """
        self._last_frame = ""

    def _write(self, text: str):
        """Internal write method that respects buffering.

        Args:
            text: Text to write (can include ANSI codes)
        """
        if self._buffering_enabled:
            self._frame_buffer.append(text)
        else:
            sys.stdout.write(text)
            sys.stdout.flush()

    def clear_screen(self):
        """Clear the entire screen."""
        self._write("\033[2J\033[H")
        if not self._buffering_enabled:
            # A standalone full clear blanks the screen -- force a full repaint.
            self._last_frame = ""

    def clear_line(self, row: int):
        """Clear a specific line.

        Args:
            row: Row number (1-based).
        """
        self._write(f"\033[{row};1H\033[K")

    def move_cursor(self, x: int, y: int):
        """Move cursor to specific position.

        Args:
            x: Column position (0-based).
            y: Row position (0-based).
        """
        self._write(f"\033[{y+1};{x+1}H")

    def hide_cursor(self):
        """Hide the cursor."""
        self._write("\033[?25l")

    def show_cursor(self):
        """Show the cursor."""
        self._write("\033[?25h")

    def write_raw(self, text: str):
        """Write raw text to terminal.

        Args:
            text: Text to write (can include ANSI codes).
        """
        self._write(text)

    def write_at(self, x: int, y: int, text: str, color: str = ColorPalette.RESET):
        """Write text at specific position with optional color.

        Args:
            x: Column position (0-based).
            y: Row position (0-based).
            text: Text to write.
            color: ANSI color code.
        """
        if 0 <= x < self.terminal_width and 0 <= y < self.terminal_height:
            self.move_cursor(x, y)
            self._write(f"{color}{text}{ColorPalette.RESET}")

    def draw_box(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        border_color: str = ColorPalette.WHITE,
        fill_color: str = ColorPalette.RESET,
    ):
        """Draw a box at the specified position.

        Args:
            x: Left column (0-based).
            y: Top row (0-based).
            width: Box width.
            height: Box height.
            border_color: Color for border.
            fill_color: Color for interior.
        """
        # Draw top border
        self.write_at(x, y, f"{border_color}╭{'─' * (width-2)}╮{ColorPalette.RESET}")

        # Draw sides and interior
        for row in range(1, height - 1):
            self.write_at(
                x,
                y + row,
                f"{border_color}│{fill_color}{' ' * (width-2)}{border_color}│{ColorPalette.RESET}",
            )

        # Draw bottom border
        if height > 1:
            self.write_at(
                x,
                y + height - 1,
                f"{border_color}╰{'─' * (width-2)}╯{ColorPalette.RESET}",
            )

    def draw_line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        char: str = "─",
        color: str = ColorPalette.WHITE,
    ):
        """Draw a line between two points.

        Args:
            x1, y1: Start position.
            x2, y2: End position.
            char: Character to use for line.
            color: Line color.
        """
        # Simple horizontal/vertical line implementation
        if y1 == y2:  # Horizontal line
            start_x, end_x = min(x1, x2), max(x1, x2)
            line_text = char * (end_x - start_x + 1)
            self.write_at(start_x, y1, line_text, color)
        elif x1 == x2:  # Vertical line
            start_y, end_y = min(y1, y2), max(y1, y2)
            for y in range(start_y, end_y + 1):
                self.write_at(x1, y, char, color)

    def get_terminal_size(self) -> Tuple[int, int]:
        """Get current terminal size.

        Returns:
            Tuple of (width, height).
        """
        return (self.terminal_width, self.terminal_height)

    def is_active(self) -> bool:
        """Check if renderer is currently active.

        Returns:
            True if renderer is active, False otherwise.
        """
        return self.active

    def flush(self):
        """Flush output buffer.

        If frame buffering is enabled, this does nothing.
        Use end_frame() to flush buffered content.
        """
        if not self._buffering_enabled:
            sys.stdout.flush()
