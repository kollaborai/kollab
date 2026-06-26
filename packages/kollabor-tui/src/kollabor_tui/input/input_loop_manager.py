"""Input loop manager for Kollab.

This module handles the main input processing loop, platform-specific I/O,
and coordinates between raw terminal input and character processing.
"""

import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Any, Callable, Optional

from kollabor_tui.input_errors import ErrorSeverity, ErrorType, InputErrorHandler

from .paste_processor import PasteProcessor

if TYPE_CHECKING:
    from kollabor_tui.key_parser import KeyParser, KeyPress

logger = logging.getLogger(__name__)

# Platform-specific imports
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import msvcrt
else:
    import select


class InputLoopManager:
    """Manages the main input processing loop.

    Responsibilities:
    - Main input loop execution
    - Platform-specific input checking (Windows/Unix)
    - Chunk reading and routing
    - Start/stop lifecycle
    - Error handling and recovery
    - Windows extended key mapping (arrow keys, F1-F12, etc.)
    """

    # Windows extended key code mapping to ANSI escape sequences
    WIN_KEY_MAP = {
        72: b"\x1b[A",  # ArrowUp
        80: b"\x1b[B",  # ArrowDown
        75: b"\x1b[D",  # ArrowLeft
        77: b"\x1b[C",  # ArrowRight
        71: b"\x1b[H",  # Home
        79: b"\x1b[F",  # End
        73: b"\x1b[5~",  # PageUp
        81: b"\x1b[6~",  # PageDown
        82: b"\x1b[2~",  # Insert
        83: b"\x1b[3~",  # Delete
        59: b"\x1bOP",  # F1
        60: b"\x1bOQ",  # F2
        61: b"\x1bOR",  # F3
        62: b"\x1bOS",  # F4
        63: b"\x1b[15~",  # F5
        64: b"\x1b[17~",  # F6
        65: b"\x1b[18~",  # F7
        66: b"\x1b[19~",  # F8
        67: b"\x1b[20~",  # F9
        68: b"\x1b[21~",  # F10
        133: b"\x1b[23~",  # F11
        134: b"\x1b[24~",  # F12
    }

    def __init__(
        self,
        renderer: Any,
        key_parser: "KeyParser",
        error_handler: InputErrorHandler,
        paste_processor: PasteProcessor,
        config: Any,
    ) -> None:
        """Initialize the input loop manager.

        Args:
            renderer: Terminal renderer for raw mode control.
            key_parser: Key parser for escape sequence handling.
            error_handler: Error handler for error recovery.
            paste_processor: Paste processor for paste detection.
            config: Configuration manager for timing settings.
        """
        self.renderer = renderer
        self.key_parser = key_parser
        self.error_handler = error_handler
        self.paste_processor = paste_processor
        self.config = config

        # External references (set after init)
        self.event_bus: Any = None  # type: ignore[assignment]

        # State
        self.running = False
        self._paused = False  # Set True when modal has exclusive stdin
        self._has_pending_input = (
            False  # Track if input was processed for event-driven render loop
        )

        # Config values
        self.polling_delay = config.get("input.polling_delay", 0.01)
        self.error_delay = config.get("input.error_delay", 0.1)

        # Callbacks (set by parent)
        self._process_character_callback: Optional[Callable] = None
        self._handle_key_press_callback: Optional[Callable] = None
        self._handle_command_mode_keypress_callback: Optional[Callable] = None
        self._register_hooks_callback: Optional[Callable] = None
        self._get_command_mode_callback: Optional[Callable] = None

        # Reference to buffer_manager for cleanup error context
        self._buffer_manager: Optional[Any] = None

    def set_callbacks(
        self,
        process_character: Callable,
        handle_key_press: Callable,
        handle_command_mode_keypress: Callable,
        register_hooks: Callable,
        get_command_mode: Callable,
    ) -> None:
        """Set callback functions for input processing.

        Args:
            process_character: Callback for processing individual characters.
            handle_key_press: Callback for handling parsed key presses.
            handle_command_mode_keypress: Callback for command mode key handling.
            register_hooks: Callback to register all hooks.
            get_command_mode: Callback to get current command mode.
        """
        self._process_character_callback = process_character
        self._handle_key_press_callback = handle_key_press
        self._handle_command_mode_keypress_callback = handle_command_mode_keypress
        self._register_hooks_callback = register_hooks
        self._get_command_mode_callback = get_command_mode

    def set_buffer_manager(self, buffer_manager: Any) -> None:
        """Set buffer manager reference for error context.

        Args:
            buffer_manager: Buffer manager instance.
        """
        self._buffer_manager = buffer_manager

    def mark_pending_input(self) -> None:
        """Mark that input was processed (called from input handlers).

        This flag is checked by the event-driven render loop to trigger
        immediate renders on user input for instant feedback.
        """
        self._has_pending_input = True
        logger.debug("Input marked as pending - will trigger render")

    def has_pending_input(self) -> bool:
        """Check if input is pending and clear flag.

        Returns:
            True if input was processed since last check, False otherwise.
        """
        result = self._has_pending_input
        self._has_pending_input = False
        return result

    def clear_pending_input(self) -> None:
        """Clear pending input flag."""
        self._has_pending_input = False

    def pause_stdin(self) -> None:
        """Pause stdin polling for sync modal input loops.

        Sets a flag that makes _input_loop skip stdin reads,
        so sync os.read() in modal code gets exclusive access.
        """
        self._paused = True
        logger.debug("InputLoopManager stdin paused for modal")

    def resume_stdin(self) -> None:
        """Resume stdin polling after modal completes.

        Drains any leftover bytes from kernel stdin buffer to
        prevent ghost key presses after modal exits.
        """
        self._paused = False
        # Drain leftover bytes from modal's sync stdin reads
        try:
            import os

            if not IS_WINDOWS:
                drained = b""
                while select.select([sys.stdin], [], [], 0.0)[0]:
                    chunk = os.read(0, 1024)
                    if not chunk:
                        break
                    drained += chunk
                if drained:
                    logger.debug(f"Drained {len(drained)} leftover bytes from stdin")
        except Exception:
            pass
        logger.debug("InputLoopManager stdin resumed after modal")

    async def start(self) -> None:
        """Start the input handling loop."""
        self.running = True
        self.renderer.enter_raw_mode()

        # Check if raw mode worked
        if (
            getattr(
                self.renderer.terminal_state.current_mode,
                "value",
                self.renderer.terminal_state.current_mode,
            )
            != "raw"
        ):
            logger.debug("Raw mode failed - using fallback ESC detection")

        # Register all hooks via callback
        if self._register_hooks_callback:
            await self._register_hooks_callback()

        logger.info("Input handler started")
        await self._input_loop()

    async def stop(self) -> None:
        """Stop the input handling loop with cleanup."""
        self.running = False
        await self.cleanup()
        self.renderer.exit_raw_mode()
        logger.info("Input handler stopped")

    async def _input_loop(self) -> None:
        """Main input processing loop with enhanced error handling."""

        while self.running:
            try:
                # Skip stdin reads when modal has exclusive access
                if self._paused:
                    await asyncio.sleep(self.polling_delay)
                    continue

                # Platform-specific input checking
                has_input = await self._check_input_available()

                if has_input:
                    # Read input data
                    chunk = await self._read_input_chunk()

                    if not chunk:
                        await asyncio.sleep(self.polling_delay)
                        continue

                    # Check if this is an escape sequence (arrow keys, etc.)
                    if self._is_escape_sequence(chunk):
                        # Escape sequence - process character by character
                        logger.debug(
                            f"Processing escape sequence "
                            f"character-by-character: {repr(chunk)}"
                        )
                        for char in chunk:
                            if self._process_character_callback:
                                await self._process_character_callback(char)
                    elif len(chunk) > 10 and not self._is_in_modal_mode():
                        # PRIMARY PASTE DETECTION:
                        # Large chunk detection (ONLY when not in modal mode)
                        # Skip paste detection in modals to allow normal pasting into form fields
                        await self._handle_paste_chunk(chunk)
                    else:
                        # Normal input - process each character individually
                        # This also handles paste in modal mode (chars go to form fields)
                        logger.debug(
                            f"Processing normal input "
                            f"character-by-character: {repr(chunk)}"
                        )
                        for char in chunk:
                            if self._process_character_callback:
                                await self._process_character_callback(char)
                else:
                    # No input available - check for standalone ESC key
                    esc_key = self.key_parser.check_for_standalone_escape()
                    if esc_key:
                        logger.info("DETECTED STANDALONE ESC KEY!")
                        await self._route_escape_key(esc_key)

                await asyncio.sleep(self.polling_delay)

            except KeyboardInterrupt:
                logger.info("Ctrl+C received")
                raise
            except OSError as e:
                await self.error_handler.handle_error(
                    ErrorType.IO_ERROR,
                    f"I/O error in input loop: {e}",
                    ErrorSeverity.HIGH,
                    {"buffer_manager": self._buffer_manager},
                )
                await asyncio.sleep(self.error_delay)
            except Exception as e:
                await self.error_handler.handle_error(
                    ErrorType.SYSTEM_ERROR,
                    f"Unexpected error in input loop: {e}",
                    ErrorSeverity.MEDIUM,
                    {"buffer_manager": self._buffer_manager},
                )
                await asyncio.sleep(self.error_delay)

    async def _route_escape_key(self, esc_key: "KeyPress") -> None:
        """Route escape key to correct handler based on mode.

        Args:
            esc_key: The parsed escape key press.
        """
        from kollabor_events.models import CommandMode

        command_mode = None
        if self._get_command_mode_callback:
            command_mode = self._get_command_mode_callback()

        if command_mode in (CommandMode.MODAL, CommandMode.STATUS_MODAL):
            if self._handle_command_mode_keypress_callback:
                await self._handle_command_mode_keypress_callback(esc_key)
        else:
            if self._handle_key_press_callback:
                await self._handle_key_press_callback(esc_key)

    async def _handle_paste_chunk(self, chunk: str) -> None:
        """Handle a large chunk as pasted content.

        Args:
            chunk: The input chunk to process as paste.
        """
        import asyncio
        import time

        current_time = time.time()

        # Check if this continues the current paste (within 100ms)
        if self.paste_processor.should_merge_paste(current_time, threshold=0.1):
            # Merge with existing paste
            self.paste_processor.append_to_current_paste(chunk, current_time)
            # Update the placeholder to show new size
            await self.paste_processor.update_paste_placeholder()
        else:
            # New paste - store immediately
            paste_id = self.paste_processor.start_new_paste(chunk, current_time)
            # Create placeholder immediately
            await self.paste_processor.create_paste_placeholder(paste_id)

        # Emit PASTE_DETECTED event (fire-and-forget)
        try:
            from kollabor_events.models import EventType

            if hasattr(self, "event_bus") and self.event_bus:
                asyncio.create_task(
                    self.event_bus.emit_with_hooks(
                        EventType.PASTE_DETECTED,
                        {
                            "content": chunk[:200],
                            "length": len(chunk),
                            "text": chunk[:200],
                        },
                        "input_loop_manager",
                    )
                )
        except Exception:
            pass

    def _is_escape_sequence(self, text: str) -> bool:
        """Check if input is an escape sequence that should bypass paste detection.

        Args:
            text: Input text to check.

        Returns:
            True if text is an escape sequence, False otherwise.
        """
        if not text:
            return False
        # Common escape sequences start with ESC (\x1b)
        return text.startswith("\x1b")

    def _is_in_modal_mode(self) -> bool:
        """Check if we're currently in a modal/fullscreen mode.

        Paste detection is disabled in modal mode to allow normal
        pasting into form fields and text inputs.

        Returns:
            True if in modal mode, False otherwise.
        """
        from kollabor_events.models import CommandMode

        if not self._get_command_mode_callback:
            return False

        command_mode = self._get_command_mode_callback()
        # Disable paste detection for all modal types
        return command_mode in (
            CommandMode.MODAL,
            CommandMode.STATUS_MODAL,
        )

    async def _check_input_available(self) -> bool:
        """Check if input is available on stdin.

        Returns False when paused for modal to prevent byte stealing.
        """
        if self._paused:
            return False

        if IS_WINDOWS:
            # Windows: Use msvcrt.kbhit() to check for available input
            return msvcrt.kbhit()  # type: ignore[attr-defined,no-any-return]
        else:
            # Unix: Use select with timeout
            return bool(select.select([sys.stdin], [], [], self.polling_delay)[0])

    async def _read_input_chunk(self) -> str:
        """Read a chunk of input from stdin.

        Returns empty string when paused for modal.
        """
        if self._paused:
            return ""

        if IS_WINDOWS:
            return await self._read_windows_input()
        else:
            return await self._read_unix_input()

    async def _read_windows_input(self) -> str:
        """Read input on Windows platform.

        Returns:
            Decoded input string.
        """
        chunk = b""
        while msvcrt.kbhit():  # type: ignore[attr-defined]
            char = msvcrt.getch()  # type: ignore[attr-defined]
            char_code = char[0] if isinstance(char, bytes) else ord(char)

            # Handle Windows extended keys (arrow keys, function keys, etc.)
            # Extended keys are prefixed with 0x00 or 0xE0 (224)
            if char_code in (0, 224):
                # Read the actual key code
                ext_char = msvcrt.getch()  # type: ignore[attr-defined]  # type: ignore[attr-defined]
                ext_code = ext_char[0] if isinstance(ext_char, bytes) else ord(ext_char)
                # Map Windows extended key codes to ANSI escape sequences
                if ext_code in self.WIN_KEY_MAP:
                    chunk += self.WIN_KEY_MAP[ext_code]
                else:
                    logger.debug(f"Unknown Windows extended key: {ext_code}")
            else:
                chunk += char

            # Small delay to allow for more input
            await asyncio.sleep(0.001)
            # Check if there's more data immediately available
            if not msvcrt.kbhit():  # type: ignore[attr-defined]  # type: ignore[attr-defined]
                break

        return chunk.decode("utf-8", errors="ignore") if chunk else ""

    async def _read_unix_input(self) -> str:
        """Read input on Unix platform.

        Returns:
            Decoded input string.
        """
        import os

        chunk = b""
        while True:
            try:
                # Read in 8KB chunks
                more_data = os.read(0, 8192)
                if not more_data:
                    break
                chunk += more_data
                # Check if more data is immediately available
                if not select.select([sys.stdin], [], [], 0.001)[0]:
                    break  # No more data waiting
            except OSError:
                break  # No more data available

        return chunk.decode("utf-8", errors="ignore") if chunk else ""

    async def cleanup(self) -> None:
        """Perform cleanup operations."""
        try:
            # Clear old errors
            cleared_errors = self.error_handler.clear_old_errors()
            if cleared_errors > 0:
                logger.info(f"Cleaned up {cleared_errors} old errors")

            # Reset parser state
            self.key_parser._reset_escape_state()

            logger.debug("Input handler cleanup completed")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
