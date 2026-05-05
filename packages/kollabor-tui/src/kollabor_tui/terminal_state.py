"""Terminal state management for rendering system.

This module provides comprehensive terminal state management for rendering
systems, including mode switching, capability detection, and
cross-platform terminal control.
"""

import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

# Platform-specific imports for terminal control
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes

    # Windows console mode constants
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    ENABLE_ECHO_INPUT = 0x0004
    ENABLE_LINE_INPUT = 0x0002
    ENABLE_PROCESSED_INPUT = 0x0001

    # Get handles
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    STD_OUTPUT_HANDLE = -11
    STD_INPUT_HANDLE = -10
else:
    import signal
    import termios
    import tty


logger = logging.getLogger(__name__)


class TerminalMode(Enum):
    """Terminal operating modes."""

    NORMAL = "normal"
    RAW = "raw"
    COOKED = "cooked"


@dataclass
class TerminalCapabilities:
    """Terminal capability detection results."""

    has_color: bool = False
    has_256_color: bool = False
    has_truecolor: bool = False
    width: int = 80
    height: int = 24
    cursor_support: bool = True
    mouse_support: bool = False

    @property
    def color_level(self) -> str:
        """Get the color support level description."""
        if self.has_truecolor:
            return "truecolor"
        elif self.has_256_color:
            return "256color"
        elif self.has_color:
            return "basic"
        else:
            return "monochrome"


class TerminalDetector:
    """Detects terminal capabilities and features."""

    @staticmethod
    def detect_capabilities() -> TerminalCapabilities:
        """Detect terminal capabilities.

        Returns:
            Terminal capabilities information.
        """
        caps = TerminalCapabilities()

        # Detect terminal size
        try:
            size = shutil.get_terminal_size()
            caps.width = size.columns
            caps.height = size.lines
        except Exception as e:
            logger.debug(f"Could not get terminal size: {e}")
            caps.width = 80
            caps.height = 24

        # Detect color support from environment variables
        term = os.environ.get("TERM", "").lower()
        colorterm = os.environ.get("COLORTERM", "").lower()

        # Basic color detection
        caps.has_color = (
            "color" in term
            or "xterm" in term
            or "screen" in term
            or "tmux" in term
            or sys.stdout.isatty()
        )

        # 256 color detection
        caps.has_256_color = (
            "256" in term
            or "256color" in colorterm
            or term in ["xterm-256color", "screen-256color"]
        )

        # True color (24-bit) detection
        caps.has_truecolor = (
            colorterm in ["truecolor", "24bit"]
            or "truecolor" in term
            or os.environ.get("TERM_PROGRAM") in ["iTerm.app", "vscode"]
        )

        # Cursor support (assume yes unless proven otherwise)
        caps.cursor_support = sys.stdout.isatty()

        logger.debug(
            f"Detected terminal capabilities: {caps.color_level} color, "
            f"{caps.width}x{caps.height}"
        )
        return caps


class TerminalState:
    """Manages terminal state, mode, and low-level operations."""

    def __init__(self, config: Any = None) -> None:
        """Initialize terminal state manager.

        Args:
            config: Optional ConfigManager for reading global_width settings.
        """
        self.current_mode: TerminalMode = TerminalMode.NORMAL
        self.original_termios: Optional[Any] = None
        self.original_console_mode: Optional[int] = None  # Windows console mode
        self.is_terminal: bool = False
        self.capabilities: TerminalCapabilities = TerminalCapabilities()
        self._config: Any = config

        # Global width configuration for all UI elements
        self._global_width_mode: str = "80%"  # Will be read from config
        self._global_width_offset: int = 4  # Default: terminal_width - 4
        self._global_width_min: int = 40  # Minimum width
        self._global_width_max: Optional[int] = None  # Maximum width (None = no cap)
        self._cached_global_width: Optional[int] = None

        # State tracking
        self._cursor_hidden: bool = False
        self._last_size: tuple[int, int] = (0, 0)
        self._resize_occurred: bool = False
        self._last_resize_time: float = 0
        self._resize_debounce_delay: float = 0.9

        # Initialize terminal state
        self._initialize_terminal()

        # Set up SIGWINCH handler for terminal resize detection (Unix only)
        self._setup_resize_handler()

        # Load global width configuration
        self._load_global_width_config()

    def _initialize_terminal(self) -> None:
        """Initialize terminal and detect capabilities."""
        # Save original terminal settings
        try:
            if sys.stdin.isatty():
                if IS_WINDOWS:
                    # Windows: Save console mode and enable VT processing
                    self._setup_windows_console()
                else:
                    # Unix: Save termios settings
                    self.original_termios = termios.tcgetattr(sys.stdin)
                self.is_terminal = True
                logger.info("Terminal mode detected and settings saved")
            else:
                logger.info("Non-terminal mode detected")
                self.is_terminal = False
        except Exception as e:
            logger.warning(f"Could not save terminal settings: {e}")
            self.is_terminal = False
            self.original_termios = None
            self.original_console_mode = None

        # Detect terminal capabilities
        self.capabilities = TerminalDetector.detect_capabilities()

    def _setup_windows_console(self) -> None:
        """Setup Windows console for VT100 processing."""
        if not IS_WINDOWS:
            return

        try:
            # Get stdout handle and enable VT processing
            stdout_handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(
                stdout_handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )

            # Get stdin handle and save original mode
            stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            input_mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(stdin_handle, ctypes.byref(input_mode))
            self.original_console_mode = input_mode.value

            # Enable VT input processing
            kernel32.SetConsoleMode(
                stdin_handle, input_mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT
            )

            logger.info("Windows console VT processing enabled")
        except Exception as e:
            logger.warning(f"Could not setup Windows console: {e}")

    def _setup_resize_handler(self) -> None:
        """Set up SIGWINCH signal handler for terminal resize detection."""
        if IS_WINDOWS:
            # Windows doesn't have SIGWINCH - rely on polling terminal size
            logger.debug("Windows platform - using size polling instead of SIGWINCH")
            return

        try:

            def handle_resize(signum, frame):
                """Handle SIGWINCH signal (terminal resize) with debouncing."""
                current_time = time.time()
                self._last_resize_time = current_time
                logger.debug(f"Terminal resize signal received at {current_time}")

            signal.signal(signal.SIGWINCH, handle_resize)
            logger.debug("SIGWINCH handler registered successfully")
        except Exception as e:
            logger.warning(f"Could not set up resize handler: {e}")

    def check_and_clear_resize_flag(self) -> bool:
        """Check if resize occurred and clear the flag (with debouncing).

        Returns:
            True if resize occurred and settled, False otherwise.
        """
        current_time = time.time()

        # Check if resize signal was received
        if self._last_resize_time > 0:
            # Check if enough time has passed since last resize signal (debouncing)
            time_since_resize = current_time - self._last_resize_time

            if time_since_resize >= self._resize_debounce_delay:
                # Resize has settled - return True and reset
                logger.debug(f"Resize settled after {time_since_resize:.3f}s")
                self._last_resize_time = 0
                return True
            else:
                # Still within debounce window - resize not settled yet
                logger.debug(
                    f"Resize in progress, waiting... ({time_since_resize:.3f}s elapsed)"
                )
                return False

        return False

    def is_resize_in_progress(self) -> bool:
        """Return True while a resize debounce window is still active."""
        if self._last_resize_time <= 0:
            return False
        return (time.time() - self._last_resize_time) < self._resize_debounce_delay

    def enter_raw_mode(self) -> bool:
        """Enter raw terminal mode for character-by-character input.

        Returns:
            True if successful, False otherwise.
        """
        if not self.is_terminal or self.current_mode == TerminalMode.RAW:
            return False

        try:
            if IS_WINDOWS:
                # Windows: Disable line input and echo
                stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
                mode = ctypes.c_ulong()
                kernel32.GetConsoleMode(stdin_handle, ctypes.byref(mode))
                # Disable echo and line input, keep VT processing
                new_mode = mode.value & ~(
                    ENABLE_ECHO_INPUT | ENABLE_LINE_INPUT | ENABLE_PROCESSED_INPUT
                )
                new_mode |= ENABLE_VIRTUAL_TERMINAL_INPUT
                kernel32.SetConsoleMode(stdin_handle, new_mode)
            else:
                # Unix: Set raw mode with flow control disabled
                # tty.setraw() doesn't disable IXON, so Ctrl+S (XOFF) gets
                # intercepted by terminal driver. We need to manually clear IXON.
                fd = sys.stdin.fileno()
                tty.setraw(fd)
                # Now disable XON/XOFF flow control so Ctrl+S reaches the app
                attrs = termios.tcgetattr(fd)
                attrs[0] &= ~termios.IXON  # Disable XOFF (Ctrl+S) interception
                attrs[0] &= ~termios.IXOFF  # Disable XON (Ctrl+Q) interception
                termios.tcsetattr(fd, termios.TCSANOW, attrs)
                # Ensure stdin is in blocking mode (not non-blocking)
                import fcntl

                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
            self.current_mode = TerminalMode.RAW
            logger.debug("Entered raw terminal mode")
            return True
        except Exception as e:
            logger.error(f"Failed to enter raw mode: {e}")
            return False

    def exit_raw_mode(self) -> bool:
        """Exit raw terminal mode and restore normal settings.

        Returns:
            True if successful, False otherwise.
        """
        if not self.is_terminal:
            return False

        try:
            if IS_WINDOWS:
                # Windows: Restore original console mode
                if self.original_console_mode is not None:
                    stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
                    kernel32.SetConsoleMode(stdin_handle, self.original_console_mode)
            else:
                # Unix: Restore termios settings
                if self.original_termios:
                    termios.tcsetattr(
                        sys.stdin, termios.TCSADRAIN, self.original_termios
                    )
            self.current_mode = TerminalMode.NORMAL
            logger.debug("Exited raw terminal mode")
            return True
        except Exception as e:
            logger.error(f"Failed to exit raw mode: {e}")
            return False

    def write_raw(self, text: str) -> bool:
        """Write text directly to terminal using low-level operations.

        Args:
            text: Text to write.

        Returns:
            True if successful, False otherwise.
        """
        try:
            if self.is_terminal:
                os.write(sys.stdout.fileno(), text.encode("utf-8"))
            else:
                sys.stdout.write(text)
                sys.stdout.flush()
            return True
        except Exception as e:
            logger.debug(f"Failed to write to terminal: {e}")
            return False

    def hide_cursor(self) -> bool:
        """Hide the terminal cursor.

        Returns:
            True if successful, False otherwise.
        """
        if self._cursor_hidden or not self.capabilities.cursor_support:
            return True

        success = self.write_raw("\033[?25l")
        if success:
            self._cursor_hidden = True
            logger.debug("Cursor hidden")
        return success

    def show_cursor(self) -> bool:
        """Show the terminal cursor.

        Returns:
            True if successful, False otherwise.
        """
        if not self._cursor_hidden or not self.capabilities.cursor_support:
            return True

        success = self.write_raw("\033[?25h")
        if success:
            self._cursor_hidden = False
            logger.debug("Cursor shown")
        return success

    def clear_line(self) -> bool:
        """Clear the current line.

        Returns:
            True if successful, False otherwise.
        """
        return self.write_raw("\r\033[2K")

    def move_cursor_up(self, lines: int = 1) -> bool:
        """Move cursor up by specified number of lines.

        Args:
            lines: Number of lines to move up.

        Returns:
            True if successful, False otherwise.
        """
        if lines <= 0:
            return True
        return self.write_raw(f"\033[{lines}A")

    def move_cursor_down(self, lines: int = 1) -> bool:
        """Move cursor down by specified number of lines.

        Args:
            lines: Number of lines to move down.

        Returns:
            True if successful, False otherwise.
        """
        if lines <= 0:
            return True
        return self.write_raw(f"\033[{lines}B")

    def move_cursor_to_column(self, column: int) -> bool:
        """Move cursor to specified column.

        Args:
            column: Column number (1-based).

        Returns:
            True if successful, False otherwise.
        """
        if column <= 0:
            column = 1
        return self.write_raw(f"\033[{column}G")

    def update_size(self) -> bool:
        """Update terminal size information.

        Returns:
            True if size changed, False otherwise.
        """
        try:
            size = shutil.get_terminal_size()
            new_size = (size.columns, size.lines)

            if new_size != self._last_size:
                self.capabilities.width = size.columns
                self.capabilities.height = size.lines
                self._last_size = new_size
                # Invalidate cached global width when terminal resizes
                self._cached_global_width = None
                logger.debug(f"Terminal size updated: {size.columns}x{size.lines}")
                return True
        except Exception as e:
            logger.debug(f"Could not update terminal size: {e}")

        return False

    def reload_width_config(self) -> None:
        """Reload global width configuration from config manager and invalidate cache.

        Call this when terminal width config changes (e.g., via /config modal).
        """
        self._load_global_width_config()
        self._cached_global_width = None
        logger.debug(
            f"Width config reloaded: mode={self._global_width_mode}, "
            f"min={self._global_width_min}, max={self._global_width_max}"
        )

    def _load_global_width_config(self) -> None:
        """Load global width configuration from config manager."""
        if not self._config:
            return

        try:
            # Read global_width_mode from config
            # Modes: "80%" (default), "auto" (terminal_width - offset), "full" (terminal_width), or int (fixed width)
            self._global_width_mode = self._config.get(
                "terminal.global_width_mode", "80%"
            )
            self._global_width_offset = self._config.get(
                "terminal.global_width_offset", 4
            )
            self._global_width_min = self._config.get("terminal.global_width_min", 40)
            self._global_width_max = self._config.get("terminal.global_width_max", None)
            logger.debug(
                f"Global width config loaded: mode={self._global_width_mode}, offset={self._global_width_offset}"
            )
        except Exception as e:
            logger.warning(f"Failed to load global width config: {e}")

    def get_global_width(self) -> int:
        """Get the global width for all UI elements.

        This width is used by all UI components (input bar, thinking box, messages, etc.)
        to ensure consistent alignment.

        Returns:
            Global width in columns.

        Configuration:
            - terminal.global_width_mode: "auto", "full", "80%" (percentage), or integer
            - terminal.global_width_offset: Offset from terminal width in auto mode (default: 4)
            - terminal.global_width_min: Minimum width (default: 40)
            - terminal.global_width_max: Maximum width (default: None for no cap)

        Examples:
            >>> # Auto mode: terminal_width - 4
            >>> # Terminal is 80 cols -> global_width = 76 (80-4=76)
            >>> width = terminal_state.get_global_width()

            >>> # Percentage mode: 80% of terminal width (default)
            >>> # Terminal is 120 cols -> global_width = 96 (120 * 0.8)
            >>> # Terminal is 80 cols -> global_width = 64 (80 * 0.8)
            >>> # Set in config: terminal.global_width_mode = "80%"

            >>> # Full mode: terminal_width
            >>> # Set in config: terminal.global_width_mode = "full"

            >>> # Fixed mode: specific width
            >>> # Set in config: terminal.global_width_mode = 60
        """
        # Return cached value if available (invalidated on resize)
        if self._cached_global_width is not None:
            return self._cached_global_width

        terminal_width = self.capabilities.width
        mode = self._global_width_mode or "80%"

        # Calculate width based on mode
        if mode == "full":
            # Full terminal width
            calculated_width = terminal_width
        elif mode == "auto":
            # Auto mode: terminal_width - offset
            calculated_width = terminal_width - self._global_width_offset
        elif mode and isinstance(mode, str) and mode.endswith("%"):
            # Percentage mode: X% of terminal width
            try:
                pct = int(mode[:-1]) / 100.0
                calculated_width = int(terminal_width * pct)
            except (ValueError, TypeError):
                logger.warning(f"Invalid percentage mode '{mode}', defaulting to 80%")
                calculated_width = int(terminal_width * 0.8)
        else:
            # Try to parse as integer for fixed width
            try:
                calculated_width = int(mode)
            except (ValueError, TypeError):
                logger.warning(f"Invalid global_width_mode '{mode}', defaulting to 80%")
                calculated_width = int(terminal_width * 0.8)

        # Apply min/max constraints
        if self._global_width_max is not None:
            calculated_width = max(
                self._global_width_min, min(calculated_width, self._global_width_max)
            )
        else:
            calculated_width = max(self._global_width_min, calculated_width)

        # Cache the result
        self._cached_global_width = calculated_width

        return calculated_width

    def get_size(self) -> tuple[int, int]:
        """Get current terminal size.

        Returns:
            Tuple of (width, height).
        """
        return (self.capabilities.width, self.capabilities.height)

    def supports_color(self, color_type: str = "basic") -> bool:
        """Check if terminal supports specified color type.

        Args:
            color_type: Color type to check ("basic", "256", "truecolor").

        Returns:
            True if color type is supported.
        """
        if color_type == "truecolor":
            return self.capabilities.has_truecolor
        elif color_type == "256":
            return self.capabilities.has_256_color
        elif color_type == "basic":
            return self.capabilities.has_color
        else:
            return False

    def get_color_support_level(self) -> str:
        """Get color support level as a string.

        Returns:
            One of: "none", "basic", "256color", "truecolor".
        """
        return self.capabilities.color_level

    def cleanup(self) -> None:
        """Cleanup terminal state and restore settings."""
        try:
            # Show cursor if hidden
            if self._cursor_hidden:
                self.show_cursor()

            # Exit raw mode if active
            if self.current_mode == TerminalMode.RAW:
                self.exit_raw_mode()

            logger.debug("Terminal state cleanup completed")
        except Exception as e:
            logger.error(f"Error during terminal cleanup: {e}")


# =============================================================================
# GLOBAL TERMINAL STATE SINGLETON
# =============================================================================

# Global singleton instance - created on first access
_global_terminal_state: Optional[TerminalState] = None


def get_global_terminal_state() -> TerminalState:
    """Get the global terminal state singleton instance.

    This function provides access to a shared TerminalState instance that
    all components (plugins, renderers, etc.) can use to query terminal
    dimensions and capabilities.

    Returns:
        Global TerminalState instance.

    Example:
        >>> from kollabor_tui.terminal_state import get_global_terminal_state
        >>> ts = get_global_terminal_state()
        >>> width, height = ts.get_size()
    """
    global _global_terminal_state
    if _global_terminal_state is None:
        _global_terminal_state = TerminalState()
        logger.info("Global terminal state singleton created")
    return _global_terminal_state


def set_global_terminal_state(terminal_state: TerminalState) -> None:
    """Set the global terminal state instance.

    This is primarily used by the main application to inject an existing
    TerminalState instance. Most code should use get_global_terminal_state()
    instead of creating their own instances.

    Args:
        terminal_state: TerminalState instance to use globally.
    """
    global _global_terminal_state
    _global_terminal_state = terminal_state
    logger.info("Global terminal state set")


def get_terminal_width() -> int:
    """Get current terminal width.

    Convenience function that returns the current terminal width from
    the global terminal state.

    Returns:
        Terminal width in columns.

    Example:
        >>> from kollabor_tui.terminal_state import get_terminal_width
        >>> width = get_terminal_width()
        >>> print(f"Terminal is {width} columns wide")
    """
    ts = get_global_terminal_state()
    return ts.capabilities.width


def get_terminal_height() -> int:
    """Get current terminal height.

    Convenience function that returns the current terminal height from
    the global terminal state.

    Returns:
        Terminal height in lines.

    Example:
        >>> from kollabor_tui.terminal_state import get_terminal_height
        >>> height = get_terminal_height()
        >>> print(f"Terminal is {height} lines tall")
    """
    ts = get_global_terminal_state()
    return ts.capabilities.height


def get_terminal_size() -> tuple[int, int]:
    """Get current terminal size as (width, height) tuple.

    Convenience function that returns both width and height from
    the global terminal state.

    Returns:
        Tuple of (width, height) in (columns, lines).

    Example:
        >>> from kollabor_tui.terminal_state import get_terminal_size
        >>> width, height = get_terminal_size()
        >>> print(f"Terminal is {width}x{height}")
    """
    ts = get_global_terminal_state()
    return ts.get_size()


def get_global_width() -> int:
    """Get the global UI width for all components.

    This is the single source of truth for all UI element widths including:
    - Input bar
    - Thinking/processing indicators
    - Message blocks
    - Status bars
    - Tool execution boxes
    - Info/success/error blocks

    The width is configurable and auto-adapts to terminal size.

    Returns:
        Global UI width in columns.

    Configuration (in ~/.kollab/config.json):
        {
            "terminal": {
                "global_width_mode": "auto",  # "auto", "full", or integer (e.g., 60)
                "global_width_offset": 4,      # Offset from terminal width in auto mode
                "global_width_min": 40,        # Minimum width
                "global_width_max": 120        # Maximum width
            }
        }

    Example:
        >>> from kollabor_tui.terminal_state import get_global_width
        >>> width = get_global_width()
        >>> # All UI elements should use this width
        >>> thinking_box_width = width
        >>> input_bar_width = width
    """
    ts = get_global_terminal_state()
    return ts.get_global_width()


def reload_width_config() -> None:
    """Reload global width configuration from config manager.

    Call this when terminal width config changes (e.g., via /config modal).

    This invalidates the cached width and reloads configuration from
    the config manager, so the next get_global_width() call will use
    the new settings.
    """
    ts = get_global_terminal_state()
    if ts:
        ts.reload_width_config()
