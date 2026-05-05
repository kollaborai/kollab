"""Input handling system for Kollab.

This is a facade that coordinates between refactored input components:
- InputLoopManager: Main loop, platform I/O, paste detection
- KeyPressHandler: Key processing, Enter/Escape handling
- CommandModeHandler: Slash commands, menus
- ModalController: All modal types
- HookRegistrar: Event hook registration
- DisplayController: Display updates, pause/resume
- PasteProcessor: Paste detection, placeholders
- StatusModalRenderer: Status modal line generation
"""

import logging
from typing import Any, Dict, Optional

from kollabor.commands.executor import SlashCommandExecutor
from kollabor.commands.parser import SlashCommandParser
from kollabor.commands.registry import SlashCommandRegistry
from kollabor_events.models import CommandMode
from kollabor_tui.buffer_manager import BufferManager
from kollabor_tui.input.command_mode_handler import CommandModeHandler
from kollabor_tui.input.display_controller import DisplayController
from kollabor_tui.input.hook_registrar import HookRegistrar

# Refactored components
from kollabor_tui.input.input_loop_manager import InputLoopManager
from kollabor_tui.input.key_press_handler import KeyPressHandler
from kollabor_tui.input.modal_controller import ModalController
from kollabor_tui.input.paste_processor import PasteProcessor
from kollabor_tui.input.status_modal_renderer import StatusModalRenderer

# input_errors is now part of kollabor_tui
from kollabor_tui.input_errors import InputErrorHandler
from kollabor_tui.key_parser import KeyParser
from kollabor_tui.menu_renderer import CommandMenuRenderer

logger = logging.getLogger(__name__)


class InputHandler:
    """Facade for the input handling system.

    This class coordinates between modular components that handle:
    - Extended key sequence support (arrow keys, function keys)
    - Robust buffer management with validation
    - Advanced error handling and recovery
    - Command history navigation
    - Cursor positioning and editing
    - Slash command menus
    - Modal interactions
    """

    def __init__(
        self,
        event_bus,
        renderer,
        config,
        shell_command_service=None,
        navigation_manager=None,
    ) -> None:
        """Initialize the input handler.

        Args:
            event_bus: Event bus for emitting input events.
            renderer: Terminal renderer for updating input display.
            config: Configuration manager for input settings.
            shell_command_service: Service for executing shell commands (optional).
            navigation_manager: Navigation manager for status navigation (optional).
        """
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self.shell_command_service = shell_command_service
        self.navigation_manager = navigation_manager
        self.running = False
        self.rendering_paused = False

        # Load configurable parameters
        buffer_limit = config.get("input.input_buffer_limit", 100000)
        history_limit = config.get("input.history_limit", 100)

        # Initialize core components
        self.key_parser = KeyParser()
        self.buffer_manager = BufferManager(buffer_limit, history_limit)

        # Initialize slash command system
        self._command_mode_local = (
            CommandMode.NORMAL
        )  # Local fallback before components created
        self.slash_parser = SlashCommandParser()
        self.command_registry = SlashCommandRegistry()
        self.command_executor = SlashCommandExecutor(self.command_registry)
        self.command_menu_renderer = CommandMenuRenderer(self.renderer)
        self.command_menu_active = False

        # Initialize error handler
        self.error_handler = InputErrorHandler(
            {
                "error_threshold": config.get("input.error_threshold", 10),
                "error_window_minutes": config.get("input.error_window_minutes", 5),
                "max_errors": config.get("input.max_errors", 100),
            }
        )

        # Create refactored components
        self._create_components(event_bus, renderer, config)
        self._wire_component_callbacks()

        logger.info("Input handler initialized with modular components")

    def _create_components(self, event_bus, renderer, config) -> None:
        """Create all refactored components.

        Args:
            event_bus: Event bus for emitting events.
            renderer: Terminal renderer.
            config: Configuration manager.
        """
        # Phase 1: Foundation components
        self._status_modal_renderer = StatusModalRenderer(renderer)
        self._display_controller = DisplayController(
            renderer, self.buffer_manager, self.error_handler
        )
        self._paste_processor = PasteProcessor(
            self.buffer_manager, self._display_controller.update_display
        )

        # Phase 2: Core processing
        self._input_loop_manager = InputLoopManager(
            renderer, self.key_parser, self.error_handler, self._paste_processor, config
        )
        self._input_loop_manager.event_bus = event_bus

        # Phase 3: Command/Modal components
        self._key_press_handler = KeyPressHandler(
            self.buffer_manager,
            self.key_parser,
            event_bus,
            self.error_handler,
            self._display_controller,
            self._paste_processor,
            renderer,
            shell_command_service=self.shell_command_service,
            navigation_manager=self.navigation_manager,
        )

        # Wire input_loop_manager to KeyPressHandler for pending input marking
        self._key_press_handler.input_loop_manager = self._input_loop_manager

        self._command_mode_handler = CommandModeHandler(
            self.buffer_manager,
            renderer,
            event_bus,
            self.command_registry,
            self.command_executor,
            self.command_menu_renderer,
            self.slash_parser,
            self.error_handler,
        )

        # Connect KeyPressHandler to CommandModeHandler for state sync
        self._key_press_handler.command_mode_handler = self._command_mode_handler

        # Connect KeyPressHandler to layout manager for permission prompts
        # The layout_manager is created inside the renderer
        if hasattr(renderer, "layout_manager"):
            self._key_press_handler.layout_manager = renderer.layout_manager

        self._modal_controller = ModalController(
            renderer,
            event_bus,
            config,
            self._status_modal_renderer,
            self._display_controller.update_display,
            self._command_mode_handler.exit_command_mode,
            self._sync_command_mode,  # Callback to sync command_mode changes
        )

        # Phase 4: Hook system
        self._hook_registrar = HookRegistrar(
            event_bus,
            self._handle_command_menu_render,
            self._modal_controller._handle_modal_trigger,
            self._modal_controller._handle_status_modal_trigger,
            self._modal_controller._handle_status_modal_render,
            self._handle_command_output_display,
            self._handle_pause_rendering,
            self._handle_resume_rendering,
            self._modal_controller._handle_modal_hide,
        )

    def _wire_component_callbacks(self) -> None:
        """Wire all component callbacks after construction."""
        # CommandModeHandler callbacks
        self._command_mode_handler.set_update_display_callback(
            self._display_controller.update_display
        )
        self._command_mode_handler.set_exit_modal_callback(
            self._modal_controller._exit_modal_mode
        )
        self._command_mode_handler.set_modal_callbacks(
            self._modal_controller._handle_modal_keypress,
            self._modal_controller._handle_status_modal_keypress,
        )

        # KeyPressHandler callbacks
        self._key_press_handler.set_callbacks(
            enter_command_mode=self._command_mode_handler.enter_command_mode,
            handle_command_mode_keypress=self._command_mode_handler.handle_command_mode_keypress,
            expand_paste_placeholders=self._paste_processor.expand_paste_placeholders,
            show_help_overlay=self._show_help_overlay,
        )

        # InputLoopManager callbacks
        self._input_loop_manager.set_callbacks(
            process_character=self._key_press_handler.process_character,
            handle_key_press=self._key_press_handler._handle_key_press,
            handle_command_mode_keypress=self._command_mode_handler.handle_command_mode_keypress,
            register_hooks=self._hook_registrar.register_all_hooks,
            get_command_mode=lambda: self._command_mode_handler.command_mode,
        )
        self._input_loop_manager.set_buffer_manager(self.buffer_manager)

    # ==================== COMMAND MODE PROPERTY ====================

    @property
    def command_mode(self) -> CommandMode:
        """Get current command mode from handler."""
        if hasattr(self, "_command_mode_handler") and self._command_mode_handler:
            return self._command_mode_handler.command_mode
        return self._command_mode_local

    @command_mode.setter
    def command_mode(self, value: CommandMode) -> None:
        """Set command mode on handler."""
        if hasattr(self, "_command_mode_handler") and self._command_mode_handler:
            self._command_mode_handler.command_mode = value
        self._command_mode_local = value

    def _sync_command_mode(self, value: CommandMode) -> None:
        """Sync command_mode from ModalController to CommandModeHandler.

        Called by ModalController when it changes command_mode.
        """
        if hasattr(self, "_command_mode_handler") and self._command_mode_handler:
            self._command_mode_handler.command_mode = value
        self._command_mode_local = value

    @property
    def current_status_modal_config(self):
        """Get current status modal config from ModalController."""
        if hasattr(self, "_modal_controller") and self._modal_controller:
            return self._modal_controller.current_status_modal_config
        return None

    @current_status_modal_config.setter
    def current_status_modal_config(self, value):
        """Set current status modal config on ModalController."""
        if hasattr(self, "_modal_controller") and self._modal_controller:
            self._modal_controller.current_status_modal_config = value

    @property
    def navigation_manager(self):
        """Get navigation manager instance."""
        return self._navigation_manager

    @navigation_manager.setter
    def navigation_manager(self, value):
        """Set navigation manager and update KeyPressHandler.

        Args:
            value: Navigation manager instance (or None).
        """
        self._navigation_manager = value
        # Update KeyPressHandler if it exists
        if hasattr(self, "_key_press_handler") and self._key_press_handler:
            self._key_press_handler.navigation_manager = value

    @property
    def display_controller(self):
        """Get display controller instance for render loop wiring."""
        return self._display_controller

    # ==================== LIFECYCLE METHODS ====================

    async def start(self) -> None:
        """Start the input handling loop.

        Delegates to InputLoopManager which handles:
        - Entering raw mode
        - Registering hooks via callback
        - Running the main input loop
        """
        self.running = True
        await self._input_loop_manager.start()

    async def stop(self) -> None:
        """Stop the input handling loop with cleanup.

        Delegates to InputLoopManager which handles:
        - Stopping the loop
        - Cleanup
        - Exiting raw mode
        """
        self.running = False
        await self._input_loop_manager.stop()

    # ==================== RENDERING CONTROL ====================

    def pause_rendering(self):
        """Pause all UI rendering for special effects."""
        self.rendering_paused = True
        self._display_controller.pause_rendering()
        logger.debug("Input rendering paused")

    def resume_rendering(self):
        """Resume normal UI rendering."""
        self.rendering_paused = False
        self._display_controller.resume_rendering()
        logger.debug("Input rendering resumed")

    # ==================== STATUS ====================

    def get_status(self) -> Dict[str, Any]:
        """Get current input handler status for debugging.

        Returns:
            Dictionary containing status information.
        """
        buffer_stats = self.buffer_manager.get_stats()
        error_stats = self.error_handler.get_error_stats()

        return {
            "running": self.running,
            "buffer": buffer_stats,
            "errors": error_stats,
            "parser_state": {
                "in_escape_sequence": self.key_parser._in_escape_sequence,
                "escape_buffer": self.key_parser._escape_buffer,
            },
        }

    # ==================== HOOK HANDLERS (kept in facade) ====================

    async def _handle_command_menu_render(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle COMMAND_MENU_RENDER events to provide command menu content.

        Args:
            event_data: Event data containing render request info.
            context: Hook execution context.

        Returns:
            Dictionary with menu_lines if command mode is active.
        """
        cmd_mode = (
            self._command_mode_handler.command_mode
            if self._command_mode_handler
            else "N/A"
        )
        logger.debug(f"Command menu render called, command_mode={cmd_mode}")
        try:
            # Only provide command menu if we're in menu popup mode
            if (
                self._command_mode_handler.command_mode == CommandMode.MENU_POPUP
                and self._command_mode_handler.command_menu_active
                and hasattr(self.command_menu_renderer, "current_menu_lines")
                and self.command_menu_renderer.current_menu_lines
            ):
                return {"menu_lines": self.command_menu_renderer.current_menu_lines}

            return {}

        except Exception as e:
            logger.error(f"Error in COMMAND_MENU_RENDER handler: {e}")
            return {}

    async def _handle_pause_rendering(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle pause rendering event."""
        logger.info("PAUSE_RENDERING event received - pausing input rendering")
        self.rendering_paused = True
        self._display_controller.pause_rendering()
        return {"status": "paused"}

    async def _handle_resume_rendering(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle resume rendering event."""
        logger.info("RESUME_RENDERING event received - resuming input rendering")
        self.rendering_paused = False
        self._display_controller.resume_rendering()
        # Force a refresh when resuming
        await self._display_controller.update_display(force_render=True)
        return {"status": "resumed"}

    async def _handle_command_output_display(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle command output display events.

        Args:
            event_data: Event data containing command output information.
            context: Hook execution context.

        Returns:
            Dictionary with display result.
        """
        try:
            message = event_data.get("message", "")
            display_type = event_data.get("display_type", "info")

            if message:
                # Note: Don't add text prefixes - message_coordinator handles display_type
                # and uses appropriate TagBox renderer with icons
                formatted_message = message

                # Clear the active input area first
                self.renderer.clear_active_area()

                # Use write_hook_message to display command output
                self.renderer.write_hook_message(
                    formatted_message,
                    display_type=display_type,
                    source="command",
                )

                # Force a display update
                await self._display_controller.update_display(force_render=True)

                logger.info(f"Command output displayed: {display_type}")

            return {
                "success": True,
                "action": "command_output_displayed",
                "display_type": display_type,
            }

        except Exception as e:
            logger.error(f"Error handling command output display: {e}")
            return {"success": False, "error": str(e)}

    # ==================== DELEGATING METHODS (for backward compatibility) ====================

    async def _handle_status_modal_trigger(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delegate to ModalController."""
        return await self._modal_controller._handle_status_modal_trigger(
            event_data, context
        )

    async def _enter_status_modal_mode(self, ui_config) -> None:
        """Delegate to ModalController."""
        await self._modal_controller._enter_status_modal_mode(ui_config)

    async def _handle_status_modal_keypress(self, key_press) -> bool:
        """Delegate to ModalController."""
        return await self._modal_controller._handle_status_modal_keypress(key_press)

    async def _exit_status_modal_mode(self) -> None:
        """Delegate to ModalController."""
        await self._modal_controller._exit_status_modal_mode()

    def _generate_status_modal_lines(self, ui_config) -> list:
        """Delegate to StatusModalRenderer."""
        return self._status_modal_renderer.generate_status_modal_lines(ui_config)

    # ==================== NAVIGATION METHODS ====================

    async def _show_help_overlay(self) -> None:
        """Show help overlay for navigation mode.

        Emits a SHOW_HELP_OVERLAY event that can be handled by plugins
        or the main application to display help content.
        """
        try:
            logger.info("Showing help overlay")
            from kollabor_events import EventType

            result = await self.event_bus.emit_with_hooks(
                EventType.SHOW_HELP_OVERLAY,
                {"source": "input_handler"},
                "input",
            )
            logger.info(f"Help overlay event emitted: {result}")
        except Exception as e:
            logger.error(f"Error showing help overlay: {e}")
