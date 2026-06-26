"""Key press handler component for Kollab.

Responsible for processing keyboard input and dispatching to appropriate handlers.
Handles character processing, key press dispatch, Enter/Escape keys, and hook integration.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from kollabor_events import EventType
from kollabor_events.models import CommandMode
from kollabor_tui.key_parser import KeyParser, KeyPress
from kollabor_tui.key_parser import KeyType as KeyTypeEnum

logger = logging.getLogger(__name__)


class KeyPressHandler:
    """Handles key press processing and dispatch.

    This component manages:
    - Character processing with paste detection hooks
    - Key press event emission and plugin integration
    - Key dispatch to specific handlers (Enter, Escape, arrows, etc.)
    - Command mode detection and routing
    - Status view navigation (Alt+Left/Right arrows)

    Attributes:
        buffer_manager: Buffer manager for text manipulation.
        key_parser: Parser for converting raw input to KeyPress objects.
        event_bus: Event bus for emitting key press events.
        error_handler: Error handler for key processing errors.
        display_controller: Controller for display updates.
        renderer: Terminal renderer for display clearing.
    """

    def __init__(
        self,
        buffer_manager: Any,
        key_parser: KeyParser,
        event_bus: Any,
        error_handler: Any,
        display_controller: Any,
        paste_processor: Any,
        renderer: Any,
        command_mode_handler: Optional[Any] = None,
        shell_command_service: Optional[Any] = None,
        navigation_manager: Optional[Any] = None,
    ) -> None:
        """Initialize the key press handler.

        Args:
            buffer_manager: Buffer manager instance.
            key_parser: Key parser instance.
            event_bus: Event bus for emitting events.
            error_handler: Error handler for key processing errors.
            display_controller: Display controller for UI updates.
            paste_processor: Paste processor for paste detection.
            renderer: Terminal renderer for display clearing.
            command_mode_handler: Optional handler for command mode operations.
            shell_command_service: Optional service for shell command execution.
            navigation_manager: Optional navigation manager for status navigation.
        """
        self.buffer_manager = buffer_manager
        self.key_parser = key_parser
        self.event_bus = event_bus
        self.error_handler = error_handler
        self.display_controller = display_controller
        self.paste_processor = paste_processor
        self.renderer = renderer
        self.command_mode_handler = command_mode_handler
        self.shell_command_service = shell_command_service
        self.navigation_manager = navigation_manager

        # Reference to input loop manager for pending input marking
        self.input_loop_manager: Any = None  # type: ignore[assignment]

        # Reference to layout manager for permission prompts
        self.layout_manager: Any = None  # type: ignore[assignment]

        # Callbacks for methods we don't own (set by parent)
        self._enter_command_mode_callback: Optional[Callable[[], Awaitable[None]]] = (
            None
        )
        self._handle_command_mode_keypress_callback: Optional[
            Callable[[KeyPress], Awaitable[bool]]
        ] = None
        self._expand_paste_placeholders_callback: Optional[Callable[[str], str]] = None
        self._show_help_overlay_callback: Optional[Callable[[], Awaitable[None]]] = None

        # State tracking
        self._command_mode = CommandMode.NORMAL

        logger.debug("KeyPressHandler initialized")

    def set_callbacks(
        self,
        enter_command_mode: Optional[Callable[[], Awaitable[None]]] = None,
        handle_command_mode_keypress: Optional[
            Callable[[KeyPress], Awaitable[bool]]
        ] = None,
        expand_paste_placeholders: Optional[Callable[[str], str]] = None,
        show_help_overlay: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """Set callbacks for methods owned by parent InputHandler.

        Args:
            enter_command_mode: Callback to enter command mode.
            handle_command_mode_keypress: Callback to handle command mode keys.
            expand_paste_placeholders: Callback to expand paste placeholders.
            show_help_overlay: Callback to show help overlay.
        """
        self._enter_command_mode_callback = enter_command_mode
        self._handle_command_mode_keypress_callback = handle_command_mode_keypress
        self._expand_paste_placeholders_callback = expand_paste_placeholders
        self._show_help_overlay_callback = show_help_overlay

    @property
    def command_mode(self) -> CommandMode:
        """Get current command mode.

        Delegates to command_mode_handler if available for consistent state.
        """
        if self.command_mode_handler:
            return CommandMode(self.command_mode_handler.command_mode)
        return self._command_mode

    @command_mode.setter
    def command_mode(self, value: CommandMode) -> None:
        """Set current command mode."""
        if self.command_mode_handler:
            self.command_mode_handler.command_mode = value
        self._command_mode = value

    @property
    def navigation_active(self) -> bool:
        """Check if navigation mode is active.

        Returns:
            True if navigation manager is active, False otherwise.
        """
        if self.navigation_manager:
            return getattr(self.navigation_manager, "active", False)
        return False

    async def process_character(self, char: str) -> None:
        """Process a single character input.

        Args:
            char: Character received from terminal.
        """
        try:
            current_time = time.time()

            # Check for slash command initiation
            # (before parsing for immediate response)
            logger.debug(
                f"Slash check: char='{char}', is_empty={self.buffer_manager.is_empty}, mode={self.command_mode}"
            )
            if (
                char == "/"
                and self.buffer_manager.is_empty
                and self.command_mode == CommandMode.NORMAL
            ):
                logger.debug(
                    "Slash command detected - calling enter_command_mode_callback"
                )
                if self._enter_command_mode_callback:
                    await self._enter_command_mode_callback()
                else:
                    logger.warning(
                        "Slash command detected but no enter_command_mode callback set"
                    )
                return

            # SECONDARY PASTE DETECTION:
            # Character-by-character timing (DISABLED)
            # This is a fallback system - primary chunk detection
            # above handles most cases
            if self.paste_processor.paste_detection_enabled:
                # Currently False - secondary system disabled
                paste_handled = await self.paste_processor.simple_paste_detection(
                    char, current_time
                )
                if paste_handled:
                    # Character consumed by paste detection,
                    # skip normal processing
                    return

            # Parse character into structured key press
            # (this handles escape sequences)
            key_press = self.key_parser.parse_char(char)
            if not key_press:
                # Schedule delayed check for standalone escape
                # (100ms delay to distinguish ESC from escape sequences)
                if self.command_mode in (
                    CommandMode.MODAL,
                    CommandMode.STATUS_MODAL,
                    CommandMode.MENU_POPUP,
                ):

                    async def delayed_escape_check():
                        await asyncio.sleep(0.1)
                        standalone_escape = (
                            self.key_parser.check_for_standalone_escape()
                        )
                        if standalone_escape:
                            if self._handle_command_mode_keypress_callback:
                                await self._handle_command_mode_keypress_callback(
                                    standalone_escape
                                )

                    asyncio.create_task(delayed_escape_check())
                else:
                    # Normal mode: also detect standalone ESC for cancel
                    async def delayed_normal_escape_check():
                        await asyncio.sleep(0.1)
                        standalone_escape = (
                            self.key_parser.check_for_standalone_escape()
                        )
                        if standalone_escape:
                            await self._handle_escape()

                    asyncio.create_task(delayed_normal_escape_check())
                # Incomplete escape sequence - wait for more characters
                return

            # CRITICAL: Check navigation mode FIRST (before command mode)
            # Navigation mode has priority - disable normal input when active
            if self.navigation_active:
                logger.info(
                    f"Navigation active - routing key to navigation manager: {key_press.name}"
                )
                if self.navigation_manager and hasattr(
                    self.navigation_manager, "handle_keypress"
                ):
                    handled = await self.navigation_manager.handle_keypress(key_press)
                    if handled:
                        await self.display_controller.update_display()
                        return
                # Navigation manager didn't handle it, exit navigation mode
                logger.info(
                    "Navigation manager didn't handle key - exiting navigation mode"
                )
                if self.navigation_manager and hasattr(
                    self.navigation_manager, "deactivate_navigation"
                ):
                    await self.navigation_manager.deactivate_navigation()
                await self.display_controller.update_display()
                return

            # Check for slash command mode handling AFTER parsing
            # (so arrow keys work)
            if self.command_mode != CommandMode.NORMAL:
                logger.info(
                    f"Processing key '{key_press.name}' "
                    f"in command mode: {self.command_mode}"
                )
                if self._handle_command_mode_keypress_callback:
                    handled = await self._handle_command_mode_keypress_callback(
                        key_press
                    )
                    if handled:
                        return

            # Emit key press event for plugins
            key_result = await self.event_bus.emit_with_hooks(
                EventType.KEY_PRESS,
                {
                    "key": key_press.name,
                    "char_code": key_press.code,
                    "key_type": key_press.type.value,
                    "modifiers": key_press.modifiers,
                },
                "input",
            )

            # Check if any plugin handled this key
            prevent_default = self._check_prevent_default(key_result)

            # Process key if not prevented by plugins
            if not prevent_default:
                await self._handle_key_press(key_press)

            # Mark input as pending for event-driven render loop
            if self.input_loop_manager and hasattr(
                self.input_loop_manager, "mark_pending_input"
            ):
                self.input_loop_manager.mark_pending_input()

            # Update renderer
            await self.display_controller.update_display()

        except Exception as e:
            from kollabor_tui.input_errors import ErrorSeverity, ErrorType

            await self.error_handler.handle_error(
                ErrorType.PARSING_ERROR,
                f"Error processing character: {e}",
                ErrorSeverity.MEDIUM,
                {"char": repr(char), "buffer_manager": self.buffer_manager},
            )

    def _check_prevent_default(self, key_result: Dict[str, Any]) -> bool:
        """Check if plugins want to prevent default key handling.

        Args:
            key_result: Result from key press event.

        Returns:
            True if default handling should be prevented.
        """
        if "main" in key_result:
            for hook_result in key_result["main"].values():
                if isinstance(hook_result, dict) and hook_result.get("prevent_default"):
                    return True
        return False

    async def _handle_key_press(self, key_press: KeyPress) -> None:
        """Handle a parsed key press.

        Args:
            key_press: Parsed key press event.
        """
        # Process key press
        try:
            # Log all key presses for debugging
            logger.debug(
                f"Key press: name='{key_press.name}', "
                f"char='{key_press.char}', code={key_press.code}, "
                f"type={key_press.type}, "
                f"modifiers={getattr(key_press, 'modifiers', None)}"
            )

            # Emit KEY_PRESS event (fire-and-forget for config hooks)
            try:
                import asyncio

                asyncio.create_task(
                    self.event_bus.emit_with_hooks(
                        EventType.KEY_PRESS,
                        {
                            "key": key_press.name or key_press.char or "",
                            "char": key_press.char or "",
                            "code": key_press.code,
                            "type": str(key_press.type),
                            "modifiers": getattr(key_press, "modifiers", None),
                        },
                        "key_press_handler",
                    )
                )
            except Exception:
                pass  # never block input on hook failure

            # Check for permission prompt FIRST (highest priority)
            if self.layout_manager and hasattr(
                self.layout_manager, "has_active_permission_prompt"
            ):
                if self.layout_manager.has_active_permission_prompt():
                    # Route to permission view
                    if hasattr(self.layout_manager, "handle_permission_keypress"):
                        handled = self.layout_manager.handle_permission_keypress(
                            key_press.char
                        )
                        if handled:
                            # Permission view handled it, update display
                            await self.display_controller.update_display(
                                force_render=True
                            )
                            return
                    # If not handled by permission view, continue normal processing

            # CRITICAL FIX: Modal input isolation
            # capture ALL input when in modal mode
            if self.command_mode == CommandMode.MODAL:
                logger.info(
                    f"Modal mode active - routing ALL input "
                    f"to modal handler: {key_press.name}"
                )
                if self._handle_command_mode_keypress_callback:
                    await self._handle_command_mode_keypress_callback(key_press)
                return

            # Handle control keys
            if self.key_parser.is_control_key(key_press, "Ctrl+C"):
                logger.info("Ctrl+C received")
                raise KeyboardInterrupt

            elif self.key_parser.is_control_key(key_press, "Enter"):
                await self._handle_enter()

            elif key_press.name == "Shift+Enter":
                # Insert newline for multi-line input
                self.buffer_manager.insert_char("\n")
                await self.display_controller.update_display(force_render=True)

            elif self.key_parser.is_control_key(key_press, "Backspace"):
                self.buffer_manager.delete_char()

            elif key_press.name == "Escape":
                # Check if in navigation mode - route to navigation manager
                if self.navigation_manager:
                    current_mode = self.navigation_manager.state.get_mode()
                    from kollabor_tui.status.navigation_state import NavigationMode

                    if current_mode in (
                        NavigationMode.EDIT,
                        NavigationMode.STATUS_FOCUS,
                    ):
                        # Route Esc to navigation manager for cascading exit
                        logger.info(
                            f"Esc in {current_mode.value} mode -> routing to navigation manager"
                        )
                        await self.navigation_manager.transition_to_input_mode()
                        await self.display_controller.update_display(force_render=True)
                    else:
                        await self._handle_escape()
                else:
                    await self._handle_escape()

            # Handle Tab key for navigation mode transitions
            elif key_press.name == "Tab":
                logger.info("Tab key pressed - handling navigation mode transition")
                try:
                    if self.navigation_manager:
                        # Get current mode to determine correct transition
                        current_mode = self.navigation_manager.state.get_mode()
                        from kollabor_tui.status.navigation_state import NavigationMode

                        logger.info(f"Current navigation mode: {current_mode.value}")

                        if current_mode == NavigationMode.INPUT:
                            # INPUT -> STATUS_FOCUS
                            logger.info("Tab from INPUT -> activating STATUS_FOCUS")
                            if hasattr(self.navigation_manager, "activate_navigation"):
                                result = (
                                    await self.navigation_manager.activate_navigation()
                                )
                                logger.info(f"STATUS_FOCUS activated, result={result}")
                        elif current_mode == NavigationMode.EDIT:
                            # EDIT -> STATUS_FOCUS
                            logger.info(
                                "Tab from EDIT -> transitioning to STATUS_FOCUS"
                            )
                            if hasattr(self.navigation_manager, "state"):
                                await self.navigation_manager.state.transition_to_status_focus_from_edit()
                                await self.navigation_manager.render_navigation_state()
                        elif current_mode == NavigationMode.STATUS_FOCUS:
                            # STATUS_FOCUS -> INPUT (exit navigation)
                            logger.info("Tab from STATUS_FOCUS -> exiting to INPUT")
                            if hasattr(
                                self.navigation_manager, "deactivate_navigation"
                            ):
                                await self.navigation_manager.deactivate_navigation()

                        await self.display_controller.update_display(force_render=True)
                    else:
                        logger.debug("Tab pressed but no navigation manager available")
                except Exception as e:
                    logger.error(f"Error handling Tab navigation: {e}", exc_info=True)

            # Handle F1 key for help overlay
            elif key_press.name == "F1":
                logger.info("F1 key pressed - showing help overlay")
                if self._show_help_overlay_callback:
                    await self._show_help_overlay_callback()
                else:
                    logger.debug("F1 pressed but no help overlay callback available")

            elif key_press.name == "Delete":
                self.buffer_manager.delete_forward()

            # Handle arrow keys for cursor movement and history
            elif key_press.name == "ArrowLeft":
                moved = self.buffer_manager.move_cursor("left")
                if moved:
                    logger.debug(
                        f"Arrow Left: cursor moved to position {self.buffer_manager.cursor_position}"
                    )
                    await self.display_controller.update_display(force_render=True)

            elif key_press.name == "ArrowRight":
                moved = self.buffer_manager.move_cursor("right")
                if moved:
                    logger.debug(
                        f"Arrow Right: cursor moved to position {self.buffer_manager.cursor_position}"
                    )
                    await self.display_controller.update_display(force_render=True)

            elif key_press.name == "ArrowUp":
                if self.buffer_manager.is_multiline:
                    # Multi-line: try to move cursor up
                    moved = self.buffer_manager.move_cursor_vertical("up")
                    if moved:
                        await self.display_controller.update_display(force_render=True)
                    else:
                        # On first line - do history
                        self.buffer_manager.navigate_history("up")
                        await self.display_controller.update_display(force_render=True)
                else:
                    # Single-line: history navigation
                    self.buffer_manager.navigate_history("up")
                    await self.display_controller.update_display(force_render=True)

            elif key_press.name == "ArrowDown":
                if self.buffer_manager.is_multiline:
                    # Multi-line: try to move cursor down
                    moved = self.buffer_manager.move_cursor_vertical("down")
                    if moved:
                        await self.display_controller.update_display(force_render=True)
                    else:
                        # On last line - do history
                        self.buffer_manager.navigate_history("down")
                        await self.display_controller.update_display(force_render=True)
                else:
                    # Single-line: history navigation
                    self.buffer_manager.navigate_history("down")
                    await self.display_controller.update_display(force_render=True)

            # Handle Home/End keys
            elif key_press.name == "Home":
                self.buffer_manager.move_to_start()
                await self.display_controller.update_display(force_render=True)

            elif key_press.name == "End":
                self.buffer_manager.move_to_end()
                await self.display_controller.update_display(force_render=True)

            # Handle Cmd key combinations (mapped to Ctrl sequences on macOS)
            elif self.key_parser.is_control_key(key_press, "Ctrl+A"):
                logger.info("Ctrl+A (Cmd+Left) - moving cursor to start")
                self.buffer_manager.move_to_start()
                await self.display_controller.update_display(force_render=True)

            elif self.key_parser.is_control_key(key_press, "Ctrl+E"):
                logger.info("Ctrl+E (Cmd+Right) - moving cursor to end")
                self.buffer_manager.move_to_end()
                await self.display_controller.update_display(force_render=True)

            elif self.key_parser.is_control_key(key_press, "Ctrl+U"):
                logger.info("Ctrl+U (Cmd+Backspace) - clearing line")
                self.buffer_manager.clear()
                await self.display_controller.update_display(force_render=True)

            # Handle printable characters
            elif self.key_parser.is_printable_char(key_press):
                # Special case: "?" key when buffer is empty shows help overlay
                if key_press.char == "?" and self.buffer_manager.is_empty:
                    logger.info(
                        "? key pressed with empty buffer - showing help overlay"
                    )
                    if self._show_help_overlay_callback:
                        await self._show_help_overlay_callback()
                    else:
                        logger.debug("? pressed but no help overlay callback available")
                    return

                # Normal character processing
                success = self.buffer_manager.insert_char(key_press.char)
                if not success:
                    from kollabor_tui.input_errors import ErrorSeverity, ErrorType

                    await self.error_handler.handle_error(
                        ErrorType.BUFFER_ERROR,
                        "Failed to insert character - buffer limit reached",
                        ErrorSeverity.LOW,
                        {
                            "char": key_press.char,
                            "buffer_manager": self.buffer_manager,
                        },
                    )

            # Handle other special keys (F1-F12, etc.)
            elif key_press.type == KeyTypeEnum.EXTENDED:
                logger.debug(f"Extended key pressed: {key_press.name}")
                # Could emit special events for function keys, etc.

        except Exception as e:
            from kollabor_tui.input_errors import ErrorSeverity, ErrorType

            await self.error_handler.handle_error(
                ErrorType.EVENT_ERROR,
                f"Error handling key press: {e}",
                ErrorSeverity.MEDIUM,
                {
                    "key_press": key_press,
                    "buffer_manager": self.buffer_manager,
                },
            )

    async def _handle_enter(self) -> None:
        """Handle Enter key press with enhanced validation."""
        try:
            if self.buffer_manager.is_empty:
                return

            # CRITICAL: Cancel any pending permission prompt before processing new input
            # This prevents lockup when user sends a new message while a permission
            # request is waiting
            if self.layout_manager and hasattr(
                self.layout_manager, "cancel_permission_prompt"
            ):
                if self.layout_manager.has_active_permission_prompt():
                    logger.info(
                        "Cancelling pending permission prompt - user sent new message"
                    )
                    self.layout_manager.cancel_permission_prompt()

            # Validate input before processing
            validation_errors = self.buffer_manager.validate_content()
            if validation_errors:
                for error in validation_errors:
                    logger.warning(f"Input validation warning: {error}")

            # Get message and clear buffer
            message = self.buffer_manager.get_content_and_clear()

            # DEBUG: Log what we received
            starts_with_bang = message.strip().startswith("!")
            logger.info(
                f"_handle_enter received: '{message}' "
                f"(repr: {repr(message)}, starts_with!: {starts_with_bang})"
            )

            # Check if this is a shell command - delegate to shell command service
            # This handles both typed commands AND commands from history
            if message.strip().startswith("!") and self.shell_command_service:
                # Expand paste placeholders in shell commands too
                if self._expand_paste_placeholders_callback:
                    message = self._expand_paste_placeholders_callback(message)
                else:
                    message = self.paste_processor.expand_paste_placeholders(message)

                logger.info(
                    f"Detected shell command (from input or history): '{message[:120]}'"
                )
                # Add to history before execution
                self.buffer_manager.add_to_history(message)

                # Clear the input display before executing
                self.renderer.input_buffer = ""
                self.renderer.clear_active_area()

                # Delegate to shell command service
                await self.shell_command_service.execute(message.strip())
                return

            # Check if this is a slash command - execute it directly
            # This handles both typed commands AND commands from history
            if message.strip().startswith("/"):
                # Expand paste placeholders in slash command args too
                if self._expand_paste_placeholders_callback:
                    message = self._expand_paste_placeholders_callback(message)
                else:
                    message = self.paste_processor.expand_paste_placeholders(message)

                logger.info(
                    f"Detected slash command (from input or history): '{message[:120]}'"
                )
                # Add to history before execution
                self.buffer_manager.add_to_history(message)

                # Clear the input display before executing
                self.renderer.input_buffer = ""
                self.renderer.clear_active_area()

                # Parse and execute the command directly
                if self.command_mode_handler and hasattr(
                    self.command_mode_handler, "slash_parser"
                ):
                    command = self.command_mode_handler.slash_parser.parse_command(
                        message.strip()
                    )
                    if command:
                        logger.info(
                            f"Executing slash command from Enter handler: {command.name}"
                        )
                        # Execute via command executor
                        if hasattr(self.command_mode_handler, "command_executor"):
                            result = await self.command_mode_handler.command_executor.execute_command(
                                command, self.event_bus
                            )
                            if result.success:
                                logger.info(
                                    f"Command {command.name} completed successfully"
                                )
                            else:
                                logger.warning(
                                    f"Command {command.name} failed: {result.message}"
                                )
                        else:
                            logger.warning("No command executor available")
                    else:
                        logger.warning(f"Failed to parse slash command: {message}")
                else:
                    logger.warning("No command mode handler or slash parser available")
                return

            # Not a command - process as normal message with paste expansion
            # GENIUS PASTE BUCKET: Immediate expansion - no waiting needed!
            logger.debug(f"GENIUS SUBMIT: Original message: '{message}'")
            logger.debug(
                f"GENIUS SUBMIT: Paste bucket contains: {list(self.paste_processor.paste_bucket.keys())}"
            )

            if self._expand_paste_placeholders_callback:
                expanded_message = self._expand_paste_placeholders_callback(message)
            else:
                # Fallback to direct expansion if callback not set
                expanded_message = self.paste_processor.expand_paste_placeholders(
                    message
                )

            logger.debug(
                f"GENIUS SUBMIT: Final expanded: '{expanded_message[:100]}...' ({len(expanded_message)} chars)"
            )

            # Add to history (with expanded content)
            self.buffer_manager.add_to_history(expanded_message)

            # CRITICAL: Clear the input display before emitting event
            # This matches the original InputHandler._handle_enter behavior
            self.renderer.input_buffer = ""
            self.renderer.clear_active_area()

            # Emit user input event (with expanded content!)
            await self.event_bus.emit_with_hooks(
                EventType.USER_INPUT,
                {
                    "message": expanded_message,
                    "validation_errors": validation_errors,
                },
                "user",
            )

            logger.debug(
                f"Processed user input: {message[:100]}..."
                if len(message) > 100
                else f"Processed user input: {message}"
            )

        except Exception as e:
            from kollabor_tui.input_errors import ErrorSeverity, ErrorType

            await self.error_handler.handle_error(
                ErrorType.EVENT_ERROR,
                f"Error handling Enter key: {e}",
                ErrorSeverity.HIGH,
                {"buffer_manager": self.buffer_manager},
            )

    async def _handle_escape(self) -> None:
        """Handle Escape key press for request cancellation."""
        try:
            logger.info("_handle_escape called - emitting CANCEL_REQUEST event")

            # Emit cancellation event
            result = await self.event_bus.emit_with_hooks(
                EventType.CANCEL_REQUEST,
                {"reason": "user_escape", "source": "input_handler"},
                "input",
            )

            logger.info(
                f"ESC key pressed - cancellation request sent, result: {result}"
            )

        except Exception as e:
            from kollabor_tui.input_errors import ErrorSeverity, ErrorType

            await self.error_handler.handle_error(
                ErrorType.EVENT_ERROR,
                f"Error handling Escape key: {e}",
                ErrorSeverity.MEDIUM,
                {"buffer_manager": self.buffer_manager},
            )
