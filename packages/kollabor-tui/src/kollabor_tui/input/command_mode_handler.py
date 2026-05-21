"""Command mode handler component for Kollab.

Responsible for managing slash command mode interactions including:
- Command menu popup navigation
- Command execution
- Modal transitions
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from kollabor_events.models import CommandMode, EventType
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class CommandModeHandler:
    """Handles slash command mode interactions and navigation.

    This component manages:
    - Entering/exiting command mode
    - Command menu popup with filtering
    - Arrow key navigation in menu
    - Command execution
    - Status takeover mode

    Attributes:
        buffer_manager: Buffer manager for command text.
        renderer: Terminal renderer for status access.
        event_bus: Event bus for emitting command events.
        command_registry: Registry of available commands.
        command_executor: Executor for running commands.
        command_menu_renderer: Renderer for command menu display.
        slash_parser: Parser for slash command syntax.
    """

    def __init__(
        self,
        buffer_manager: Any,
        renderer: Any,
        event_bus: Any,
        command_registry: Any,
        command_executor: Any,
        command_menu_renderer: Any,
        slash_parser: Any,
        error_handler: Optional[Any] = None,
    ) -> None:
        """Initialize the command mode handler.

        Args:
            buffer_manager: Buffer manager for command text.
            renderer: Terminal renderer for status access.
            event_bus: Event bus for emitting command events.
            command_registry: Registry of available commands.
            command_executor: Executor for running commands.
            command_menu_renderer: Renderer for command menu display.
            slash_parser: Parser for slash command syntax.
            error_handler: Optional error handler for command errors.
        """
        self.buffer_manager = buffer_manager
        self.renderer = renderer
        self.event_bus = event_bus
        self.command_registry = command_registry
        self.command_executor = command_executor
        self.command_menu_renderer = command_menu_renderer
        self.slash_parser = slash_parser
        self.error_handler = error_handler

        # Command mode state
        self.command_mode = CommandMode.NORMAL
        self.command_menu_active = False
        self.selected_command_index = 0

        # Callbacks for operations that require access to parent InputHandler
        self._update_display_callback: Optional[Callable] = None
        self._exit_modal_callback: Optional[Callable] = None

        # Callbacks for modal mode handling (delegated to ModalController)
        self._handle_modal_keypress_callback: Optional[
            Callable[..., Awaitable[bool]]
        ] = None
        self._handle_status_modal_keypress_callback: Optional[
            Callable[..., Awaitable[bool]]
        ] = None

        logger.debug("CommandModeHandler initialized")

    def set_update_display_callback(self, callback: Callable) -> None:
        """Set callback for updating display.

        Args:
            callback: Async function to call for display updates.
        """
        self._update_display_callback = callback

    def set_exit_modal_callback(self, callback: Callable) -> None:
        """Set callback for exiting modal mode.

        Args:
            callback: Async function to call for modal exit.
        """
        self._exit_modal_callback = callback

    def set_modal_callbacks(
        self,
        handle_modal_keypress: Optional[Callable[..., Awaitable[bool]]] = None,
        handle_status_modal_keypress: Optional[Callable[..., Awaitable[bool]]] = None,
    ) -> None:
        """Set callbacks for modal mode handling.

        These callbacks delegate to ModalController for actual handling.

        Args:
            handle_modal_keypress: Callback for MODAL mode key handling.
            handle_status_modal_keypress: Callback for STATUS_MODAL mode key handling.
        """
        self._handle_modal_keypress_callback = handle_modal_keypress
        self._handle_status_modal_keypress_callback = handle_status_modal_keypress

    async def enter_command_mode(self) -> None:
        """Enter slash command mode and show command menu."""
        try:
            logger.info("Entering slash command mode")
            self.command_mode = CommandMode.MENU_POPUP
            self.command_menu_active = True

            # Reset selection to first command
            self.selected_command_index = 0

            # Add the '/' character to buffer for visual feedback
            self.buffer_manager.insert_char("/")

            # Show command menu via renderer
            available_commands = self._get_available_commands()
            self.command_menu_renderer.show_command_menu(available_commands, "")

            # Emit command menu show event
            await self.event_bus.emit_with_hooks(
                EventType.COMMAND_MENU_SHOW,
                {"available_commands": available_commands, "filter_text": ""},
                "commands",
            )

            # Update display to show command mode
            if self._update_display_callback:
                await self._update_display_callback(force_render=True)

            logger.info("Command menu activated")

        except Exception as e:
            logger.error(f"Error entering command mode: {e}")
            await self.exit_command_mode()

    async def exit_command_mode(self) -> None:
        """Exit command mode and restore normal input."""
        try:
            import traceback

            logger.info("Exiting slash command mode")
            logger.debug(f"Exit called from: {traceback.format_stack()[-2].strip()}")

            # Hide command menu via renderer
            self.command_menu_renderer.hide_menu()

            # Emit command menu hide event
            if self.command_menu_active:
                await self.event_bus.emit_with_hooks(
                    EventType.COMMAND_MENU_HIDE,
                    {"reason": "manual_exit"},
                    "commands",
                )

            self.command_mode = CommandMode.NORMAL
            self.command_menu_active = False

            # Clear command buffer (remove the '/' and any partial command)
            self.buffer_manager.clear()

            # Update display
            if self._update_display_callback:
                await self._update_display_callback(force_render=True)

            logger.info("Returned to normal input mode")

        except Exception as e:
            logger.error(f"Error exiting command mode: {e}")

    async def handle_command_mode_keypress(self, key_press: KeyPress) -> bool:
        """Handle KeyPress while in command mode (supports arrow keys).

        Args:
            key_press: Parsed key press to process.

        Returns:
            True if key was handled, False to fall through to normal processing.
        """
        try:
            if self.command_mode == CommandMode.MENU_POPUP:
                return await self.handle_menu_popup_keypress(key_press)
            elif self.command_mode == CommandMode.STATUS_TAKEOVER:
                return await self.handle_status_takeover_keypress(key_press)
            elif self.command_mode == CommandMode.MODAL:
                # Delegate to ModalController via callback
                if self._handle_modal_keypress_callback:
                    return await self._handle_modal_keypress_callback(key_press)
                else:
                    logger.warning("MODAL mode active but no callback set")
                    return False
            elif self.command_mode == CommandMode.STATUS_MODAL:
                # Delegate to ModalController via callback
                if self._handle_status_modal_keypress_callback:
                    return await self._handle_status_modal_keypress_callback(key_press)
                else:
                    logger.warning("STATUS_MODAL mode active but no callback set")
                    return False
            else:
                # Unknown command mode, exit to normal
                await self.exit_command_mode()
                return False

        except Exception as e:
            logger.error(f"Error handling command mode keypress: {e}")
            await self.exit_command_mode()
            return False

    async def handle_command_mode_input(self, char: str) -> bool:
        """Handle input while in command mode.

        Args:
            char: Character input to process.

        Returns:
            True if input was handled, False to fall through to normal processing.
        """
        try:
            if self.command_mode == CommandMode.MENU_POPUP:
                return await self.handle_menu_popup_input(char)
            elif self.command_mode == CommandMode.STATUS_TAKEOVER:
                return await self.handle_status_takeover_input(char)
            elif self.command_mode == CommandMode.STATUS_MODAL:
                # STATUS_MODAL input is handled via keypress callback
                # Character input falls through to keypress handler
                return False
            else:
                # Unknown command mode, exit to normal
                await self.exit_command_mode()
                return False

        except Exception as e:
            logger.error(f"Error handling command mode input: {e}")
            await self.exit_command_mode()
            return False

    async def handle_menu_popup_input(self, char: str) -> bool:
        """Handle input during menu popup mode.

        Args:
            char: Character input to process.

        Returns:
            True if input was handled.
        """
        # Handle special keys first
        if ord(char) == 27:  # Escape key
            await self.exit_command_mode()
            return True
        elif ord(char) == 13:  # Enter key
            await self._execute_selected_command()
            return True
        elif ord(char) == 8 or ord(char) == 127:  # Backspace or Delete
            # If buffer only has '/', exit command mode
            if len(self.buffer_manager.content) <= 1:
                await self.exit_command_mode()
                return True
            else:
                # Remove character and update command filter
                self.buffer_manager.delete_char()
                await self._update_command_filter()
                return True

        # Handle printable characters (add to command filter)
        if char.isprintable():
            self.buffer_manager.insert_char(char)
            await self._update_command_filter()
            return True

        # Let other keys fall through for now
        return False

    async def handle_menu_popup_keypress(self, key_press: KeyPress) -> bool:
        """Handle KeyPress during menu popup mode with arrow key navigation.

        Args:
            key_press: Parsed key press to process.

        Returns:
            True if key was handled.
        """
        try:
            # Handle arrow key navigation
            if key_press.name == "ArrowUp":
                await self._navigate_menu("up")
                return True
            elif key_press.name == "ArrowDown":
                await self._navigate_menu("down")
                return True
            elif key_press.name == "Enter":
                await self._execute_selected_command()
                return True
            elif key_press.name == "Escape":
                await self.exit_command_mode()
                return True

            # Handle printable characters (for filtering)
            elif key_press.char and key_press.char.isprintable():
                self.buffer_manager.insert_char(key_press.char)
                await self._update_command_filter()
                return True

            # Handle backspace/delete
            elif key_press.name in ["Backspace", "Delete"]:
                # If buffer only has '/', exit command mode
                if len(self.buffer_manager.content) <= 1:
                    await self.exit_command_mode()
                    return True
                else:
                    # Remove character and update command filter
                    self.buffer_manager.delete_char()
                    await self._update_command_filter()
                    return True

            # Other keys not handled
            return False

        except Exception as e:
            logger.error(f"Error handling menu popup keypress: {e}")
            await self.exit_command_mode()
            return False

    async def handle_status_takeover_input(self, char: str) -> bool:
        """Handle input during status area takeover mode.

        Args:
            char: Character input to process.

        Returns:
            True if input was handled.
        """
        # For now, just handle Escape to exit
        if ord(char) == 27:  # Escape key
            await self.exit_command_mode()
            return True

        # TODO: Implement status area navigation
        return True

    async def handle_status_takeover_keypress(self, key_press: KeyPress) -> bool:
        """Handle KeyPress during status area takeover mode.

        Args:
            key_press: Parsed key press to process.

        Returns:
            True if key was handled.
        """
        # For now, just handle Escape to exit
        if key_press.name == "Escape":
            await self.exit_command_mode()
            return True

        # TODO: Implement status area navigation
        return True

    # ==================== PRIVATE HELPER METHODS ====================

    async def _navigate_menu(self, direction: str) -> None:
        """Navigate the command menu up or down.

        Args:
            direction: "up" or "down"
        """
        try:
            # Use menu_items count from renderer (includes subcommands)
            menu_items = self.command_menu_renderer.menu_items
            if not menu_items:
                return

            # Update selection index
            if direction == "up":
                self.selected_command_index = max(0, self.selected_command_index - 1)
            elif direction == "down":
                self.selected_command_index = min(
                    len(menu_items) - 1, self.selected_command_index + 1
                )

            # Update menu renderer with new selection
            self.command_menu_renderer.set_selected_index(self.selected_command_index)

            # Re-render the menu with new selection
            self.command_menu_renderer._render_menu()

        except Exception as e:
            logger.error(f"Error navigating menu: {e}")

    async def _update_command_filter(self) -> None:
        """Update command menu based on current buffer content."""
        try:
            # Get current input (minus the leading '/')
            current_input = self.buffer_manager.content
            filter_text = (
                current_input[1:] if current_input.startswith("/") else current_input
            )

            # Update menu renderer with filtered commands
            filtered_commands = self._filter_commands(filter_text)

            # Reset selection when filtering
            self.selected_command_index = 0
            self.command_menu_renderer.set_selected_index(self.selected_command_index)
            self.command_menu_renderer.filter_commands(filtered_commands, filter_text)

            # Emit filter update event
            await self.event_bus.emit_with_hooks(
                EventType.COMMAND_MENU_FILTER,
                {
                    "filter_text": filter_text,
                    "available_commands": self._get_available_commands(),
                    "filtered_commands": filtered_commands,
                },
                "commands",
            )

            # Update display
            if self._update_display_callback:
                await self._update_display_callback(force_render=True)

        except Exception as e:
            logger.error(f"Error updating command filter: {e}")

    async def _execute_selected_command(self) -> None:
        """Execute the currently selected command or insert subcommand."""
        try:
            # PRIORITY 1: If menu is active with a selection, use the highlighted item
            if self.command_menu_active:
                selected_item = self.command_menu_renderer.get_selected_command()
                if selected_item:
                    typed_command_string = self._exact_typed_command_string(
                        self.buffer_manager.content, selected_item
                    )
                    if typed_command_string:
                        command_string = typed_command_string
                    # Check if it's a subcommand
                    elif selected_item.get("is_subcommand"):
                        parent_name = selected_item.get("parent_name", "")
                        subcommand_name = selected_item.get("subcommand_name", "")
                        subcommand_args = selected_item.get("subcommand_args", "")

                        # If subcommand has no args, execute immediately
                        if not subcommand_args:
                            command_string = f"/{parent_name} {subcommand_name}"
                            logger.info(
                                f"Executing no-arg subcommand: {command_string}"
                            )
                        else:
                            # Has args - insert text for user to complete
                            new_content = f"/{parent_name} {subcommand_name} "

                            # Update buffer with new content
                            self.buffer_manager.clear()
                            for char in new_content:
                                self.buffer_manager.insert_char(char)

                            logger.info(f"Inserted subcommand: {new_content}")

                            # Hide menu - user is now typing arguments
                            self.command_menu_renderer.hide_menu()
                            self.command_menu_active = False

                            if self._update_display_callback:
                                await self._update_display_callback(force_render=True)
                            return
                    else:
                        # Regular command - execute it
                        # But preserve any arguments the user typed
                        buffer_content = self.buffer_manager.content
                        # Extract args from buffer (everything after first space)
                        if " " in buffer_content:
                            args_part = buffer_content.split(" ", 1)[1]
                            command_string = f"/{selected_item['name']} {args_part}"
                        else:
                            command_string = f"/{selected_item['name']}"
                        logger.info(
                            f"Executing highlighted menu command: {command_string}"
                        )
                else:
                    # No menu selection - fall through to parse buffer content
                    # User may have typed valid command with args that don't match filter
                    command_string = self.buffer_manager.content
                    if not command_string or command_string == "/":
                        logger.warning("Menu active but no command to execute")
                        await self.exit_command_mode()
                        return
            else:
                # FALLBACK: Menu not active, use buffer content
                command_string = self.buffer_manager.content
                if not command_string or command_string == "/":
                    logger.warning("No command to execute")
                    await self.exit_command_mode()
                    return

            # Parse the command
            command = self.slash_parser.parse_command(command_string)
            if command:
                logger.info(f"Executing selected command: {command.name}")

                # Add to history BEFORE clearing buffer
                self.buffer_manager.add_to_history(command_string)

                # Exit command mode first
                await self.exit_command_mode()

                # Execute the command
                result = await self.command_executor.execute_command(
                    command, self.event_bus
                )

                # Handle the result
                if result.success:
                    logger.info(f"Command {command.name} completed successfully")

                    # Modal display is handled by event bus trigger, not here
                    if result.message:
                        # Display success message in status area
                        logger.info(f"Command result: {result.message}")
                        # TODO: Display in status area
                else:
                    logger.warning(f"Command {command.name} failed: {result.message}")
                    # TODO: Display error message in status area
            else:
                logger.warning("Failed to parse selected command")
                await self.exit_command_mode()

        except Exception as e:
            logger.error(f"Error executing command: {e}")
            await self.exit_command_mode()

    def _exact_typed_command_string(
        self, buffer_content: str, selected_item: Dict[str, Any]
    ) -> Optional[str]:
        """Return typed command when it exactly names a command different from selection."""
        if selected_item.get("is_subcommand"):
            return None

        typed_name = self._typed_command_name(buffer_content)
        if not typed_name or typed_name == selected_item.get("name"):
            return None

        get_command = getattr(self.command_registry, "get_command", None)
        if not callable(get_command):
            return None

        if get_command(typed_name):
            return buffer_content

        return None

    @staticmethod
    def _typed_command_name(buffer_content: str) -> Optional[str]:
        """Extract the command token from the current slash buffer."""
        stripped = buffer_content.strip()
        if not stripped.startswith("/") or stripped == "/":
            return None

        return stripped[1:].split(None, 1)[0].lower()

    async def execute_command_string(self, command_string: str) -> bool:
        """Execute a slash command directly from a command string.

        Used by widget activation to execute commands like "/profile".

        Args:
            command_string: Command to execute (e.g., "/profile", "/model")

        Returns:
            True if command was executed successfully, False otherwise
        """
        try:
            logger.info(f"Executing command string: {command_string}")

            # Parse the command
            command = self.slash_parser.parse_command(command_string)
            if not command:
                logger.warning(f"Failed to parse command: {command_string}")
                return False

            # Execute the command
            result = await self.command_executor.execute_command(
                command, self.event_bus
            )

            if result.success:
                logger.info(f"Command {command.name} completed successfully")
                return True
            else:
                logger.warning(f"Command {command.name} failed: {result.message}")
                return False

        except Exception as e:
            logger.error(f"Error executing command string: {e}", exc_info=True)
            return False

    def _get_available_commands(self) -> List[Dict[str, Any]]:
        """Get list of available commands for menu display.

        Returns:
            List of command dictionaries for menu rendering.
        """
        commands = []
        command_defs = self.command_registry.list_commands()

        for cmd_def in command_defs:
            # Convert subcommands to dicts for JSON serialization
            subcommands = []
            if cmd_def.subcommands:
                for sub in cmd_def.subcommands:
                    subcommands.append(
                        {
                            "name": sub.name,
                            "args": sub.args,
                            "description": sub.description,
                        }
                    )

            commands.append(
                {
                    "name": cmd_def.name,
                    "description": cmd_def.description,
                    "aliases": cmd_def.aliases,
                    "category": cmd_def.category.value,
                    "plugin": cmd_def.plugin_name,
                    "icon": cmd_def.icon,
                    "subcommands": subcommands,
                }
            )

        return commands

    def _filter_commands(self, filter_text: str) -> List[Dict[str, Any]]:
        """Filter commands based on input text.

        Supports subcommand filtering: "mcp s" will find /mcp and filter its
        subcommands to those starting with "s" (e.g., setup, show).

        Args:
            filter_text: Text to filter commands by.

        Returns:
            List of filtered command dictionaries.
        """
        if not filter_text:
            return self._get_available_commands()

        # Check for subcommand filtering pattern: "command subfilter"
        parts = filter_text.split(" ", 1)
        command_part = parts[0]
        subcommand_filter = parts[1].lower() if len(parts) > 1 else None

        # Search for commands matching the command part
        matching_defs = self.command_registry.search_commands(command_part)

        # If we have a subcommand filter and exactly one command matches,
        # filter its subcommands
        if subcommand_filter is not None and len(matching_defs) == 1:
            cmd_def = matching_defs[0]
            # Filter subcommands that start with the subcommand filter
            filtered_subcommands = []
            if cmd_def.subcommands:
                for sub in cmd_def.subcommands:
                    if sub.name.lower().startswith(subcommand_filter):
                        filtered_subcommands.append(
                            {
                                "name": sub.name,
                                "args": sub.args,
                                "description": sub.description,
                            }
                        )

            return [
                {
                    "name": cmd_def.name,
                    "description": cmd_def.description,
                    "aliases": cmd_def.aliases,
                    "category": cmd_def.category.value,
                    "plugin": cmd_def.plugin_name,
                    "icon": cmd_def.icon,
                    "subcommands": filtered_subcommands,
                }
            ]

        # Standard command filtering (no subcommand filter)
        filtered_commands = []
        for cmd_def in matching_defs:
            # Convert subcommands to dicts for JSON serialization
            subcommands = []
            if cmd_def.subcommands:
                for sub in cmd_def.subcommands:
                    subcommands.append(
                        {
                            "name": sub.name,
                            "args": sub.args,
                            "description": sub.description,
                        }
                    )

            filtered_commands.append(
                {
                    "name": cmd_def.name,
                    "description": cmd_def.description,
                    "aliases": cmd_def.aliases,
                    "category": cmd_def.category.value,
                    "plugin": cmd_def.plugin_name,
                    "icon": cmd_def.icon,
                    "subcommands": subcommands,
                }
            )

        return filtered_commands
