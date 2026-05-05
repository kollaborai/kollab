"""Modal controller component for managing modal interactions.

This component handles all modal-related operations including:
- Standard modals (full-screen with widgets)
- Status modals (confined to status area)
- Live modals (continuously updating content)
- Modal event handling and state management

Extracted from InputHandler as part of the refactoring effort.

================================================================
BLEED-THROUGH CONTRACT (read before touching _enter_modal_mode
or _exit_modal_mode_minimal)
================================================================

Any code path in this file that opens a modal backed by the ANSI
alternate screen buffer (``\\033[?1049h``) MUST keep the coordinator
state in sync with the actual terminal buffer. The two interact like
this:

  1. Modal wants to show up.
  2. We call ``coordinator.enter_alternate_buffer()``. This sets
     ``_in_alternate_buffer=True`` on the coordinator, which causes
     any messages that arrive during the modal (e.g. llm responses
     finishing in the background) to be queued into
     ``_buffered_output`` instead of printed to stdout.
  3. The modal renderer writes ``\\033[?1049h``; terminal is now in
     the alt screen. Messages arriving here safely land in the buffer.
  4. User dismisses the modal.
  5. The modal renderer writes ``\\033[?1049l``; terminal is back in
     the main screen. stdout now points at the main buffer.
  6. We call ``coordinator.exit_alternate_buffer()`` (or, for the
     minimal exit path, ``_flush_buffered_output()`` directly). This
     clears the flag and prints the buffered messages, landing them
     in the main buffer below the restored input box.

Every step is load-bearing. If step 2 is missing, messages print
through print() straight onto whatever modal is drawn in the altbuf
(this was the /profile bleed bug). If step 6 is missing, messages
stay orphaned in the buffer until the next alt-buffer cycle and end
up printing after future command output in the wrong order.

Helper methods in this file that DO this correctly:
  - ``_handle_modal_trigger``  (fullscreen plugin branch)
  - ``_enter_modal_mode``      (standard command modal path)
  - ``_handle_modal_hide``     (exit side for altview + fullscreen)
  - ``_exit_modal_mode``       (exit side for standard modals)
  - ``_exit_modal_mode_minimal`` (exit side for commands that
                                  immediately print their own output)

If you add a new modal path, either call these helpers or replicate
the enter/exit calls exactly. See the module docstring on
``message_coordinator.py`` for the full contract.
================================================================
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from kollabor_events.models import CommandMode, EventType
from kollabor_tui.input.status_modal_renderer import StatusModalRenderer

logger = logging.getLogger(__name__)


class ModalController:
    """Manages modal display and interaction logic.

    This component coordinates between different modal types and handles
    modal-specific input events, state transitions, and rendering.

    Responsibilities:
    - Handle modal trigger events (MODAL_TRIGGER, STATUS_MODAL_TRIGGER)
    - Manage modal state (command_mode, current_status_modal_config, modal_renderer)
    - Process modal keypresses and input
    - Coordinate modal entry/exit with proper state management
    - Handle save confirmations and modal data persistence
    """

    def __init__(
        self,
        renderer,
        event_bus,
        config,
        status_modal_renderer: StatusModalRenderer,
        update_display_callback: Callable,
        exit_command_mode_callback: Callable,
        set_command_mode_callback: Optional[Callable] = None,
    ) -> None:
        """Initialize the modal controller.

        Args:
            renderer: Terminal renderer for display operations.
            event_bus: Event bus for emitting modal events.
            config: Configuration service.
            status_modal_renderer: StatusModalRenderer for status area modals.
            update_display_callback: Callback to update display (async).
            exit_command_mode_callback: Callback to exit command mode (async).
            set_command_mode_callback: Callback to set command_mode (syncs with parent).
        """
        self.renderer = renderer
        self.event_bus = event_bus
        self.config = config
        self._status_modal_renderer = status_modal_renderer
        self._update_display = update_display_callback
        self._exit_command_mode = exit_command_mode_callback
        self._set_command_mode_callback = set_command_mode_callback

        # Modal state
        self._command_mode = CommandMode.NORMAL
        self.current_status_modal_config = None
        self.modal_renderer = None  # ModalRenderer instance when active
        self._pending_save_confirm = False  # For modal save confirmation
        self._pending_save_target = False  # For local/global save target selection
        self._fullscreen_session_active = False  # For fullscreen plugin sessions

        logger.info("ModalController initialized")

    @property
    def command_mode(self) -> CommandMode:
        """Get current command mode."""
        return self._command_mode

    @command_mode.setter
    def command_mode(self, value: CommandMode) -> None:
        """Set command mode and notify parent via callback."""
        self._command_mode = value
        if self._set_command_mode_callback:
            self._set_command_mode_callback(value)

    # ==================== EVENT HANDLERS ====================

    async def _handle_modal_trigger(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle modal trigger events to show modals.

        Args:
            event_data: Event data containing modal configuration.
            context: Hook execution context.

        Returns:
            Dictionary with modal result.
        """
        try:
            # Check if this is a Matrix effect trigger
            if event_data.get("matrix_effect"):
                logger.info(
                    "Matrix effect modal trigger received - setting modal mode for complete terminal control"
                )
                # Set modal mode directly for Matrix effect (no UI config needed)
                self.command_mode = CommandMode.MODAL
                logger.info("Command mode set to MODAL for Matrix effect")
                return {
                    "success": True,
                    "modal_activated": True,
                    "matrix_mode": True,
                }

            # Check if this is a full-screen plugin trigger
            if event_data.get("fullscreen_plugin"):
                plugin_name = event_data.get("plugin_name", "unknown")
                logger.info(f"Full-screen plugin modal trigger received: {plugin_name}")

                # Use coordinator to save state before fullscreen (handles writing_messages, etc.)
                # This is the fullscreen-plugin / altview branch and it already
                # does the right thing: call enter_alternate_buffer() BEFORE
                # the plugin writes \033[?1049h. The mirror call happens in
                # _handle_modal_hide() below. See the module docstring for
                # the full contract. The standard-modal branch below goes
                # through _enter_modal_mode() which has its own call.
                if hasattr(self.renderer, "message_coordinator"):
                    self.renderer.message_coordinator.enter_alternate_buffer()

                self.renderer.clear_active_area()

                # Set modal mode for full-screen plugin (no UI config needed)
                self.command_mode = CommandMode.MODAL
                # CRITICAL FIX: Mark fullscreen session as active for input routing
                self._fullscreen_session_active = True
                logger.info(
                    f"Command mode set to MODAL for full-screen plugin: {plugin_name}"
                )
                logger.info("Fullscreen session marked as active for input routing")
                return {
                    "success": True,
                    "modal_activated": True,
                    "fullscreen_plugin": True,
                    "plugin_name": plugin_name,
                }

            # Standard modal with UI config
            ui_config = event_data.get("ui_config")
            if ui_config:
                logger.info(f"Modal trigger received: {ui_config.title}")
                await self._enter_modal_mode(ui_config)
                return {"success": True, "modal_activated": True}
            else:
                logger.warning("Modal trigger received without ui_config")
                return {"success": False, "error": "Missing ui_config"}

        except Exception as e:
            logger.error(f"Error handling modal trigger: {e}")
            return {"success": False, "error": str(e)}

    async def _handle_modal_hide(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle modal hide event to exit modal mode.

        This is the MODAL_HIDE event handler. It runs after the
        modal/altview/fullscreen plugin has already written
        \\033[?1049l and restored the terminal to the main buffer.
        Our job here is to get the coordinator state back in sync
        with the terminal state so subsequent message display works
        normally, AND to flush any messages that were buffered
        during the modal session.

        Two sources reach this handler:

          - ``source="altview"`` -- altview stack manager emitted this
            after ``session.exit()`` already restored the terminal via
            FullScreenRenderer.restore_terminal(). We must NOT run the
            full exit_alternate_buffer() path because it would trigger
            additional terminal writes. Instead, clear the flag
            manually and flush the buffer directly.

          - anything else (fullscreen plugin, normal modal exit path):
            ``exit_alternate_buffer()`` does the full reset including
            the flush.

        In both cases, ``_flush_buffered_output()`` must run so that
        any llm / hub / agent messages that arrived during the modal
        land in the main buffer below the restored input box. Without
        it, buffered messages stay in ``_buffered_output`` until the
        next alt-buffer cycle, then print out of order.

        NOTE: This is called AFTER fullscreen renderer has already restored
        the terminal (exited alternate buffer with \\033[?1049l). We must NOT
        call clear_active_area() here as it would clear the just-restored screen.
        """
        logger.info("MODAL_HIDE event received - exiting modal mode")
        try:
            # Exit alternate buffer state via coordinator (clears _in_alternate_buffer flag)
            # For altview: session.exit() already restored the terminal, so we
            # only clear the flag without running the full restore sequence.
            # For everything else: exit_alternate_buffer() does the full reset.
            # In BOTH cases we must flush buffered messages so they land in
            # the main buffer in order -- altview previously skipped the
            # flush, which orphaned hub messages received during /hub feed.
            source = event_data.get("source", "") if event_data else ""
            if hasattr(self.renderer, "message_coordinator"):
                coordinator = self.renderer.message_coordinator
                if coordinator._in_alternate_buffer:
                    if source == "altview":
                        # Altview already restored terminal; manually clear
                        # the flag and flush (we can't call the public
                        # exit_alternate_buffer because it would re-enter
                        # raw-mode handling that session.exit() already did).
                        coordinator._in_alternate_buffer = False
                        coordinator._saved_main_buffer_state = None
                        coordinator._flush_buffered_output()
                    else:
                        coordinator.exit_alternate_buffer(restore_state=False)

            # Set render state flags (alternate buffer was already exited by fullscreen renderer)
            self.renderer.writing_messages = False
            self.renderer.input_line_written = False
            self.renderer.last_line_count = 0
            self.renderer.invalidate_render_cache()

            self.command_mode = CommandMode.NORMAL
            # Clear fullscreen session flag when exiting modal
            if hasattr(self, "_fullscreen_session_active"):
                self._fullscreen_session_active = False
                logger.info("Fullscreen session marked as inactive")
            logger.info("Command mode reset to NORMAL after modal hide")

            # Force refresh of display when exiting modal mode
            await self._update_display(force_render=True)
            return {"success": True, "modal_deactivated": True}
        except Exception as e:
            logger.error(f"Error handling modal hide: {e}")
            return {"success": False, "error": str(e)}

    async def _handle_modal_keypress(self, key_press) -> bool:
        """Handle KeyPress during modal mode.

        Args:
            key_press: Parsed key press to process.

        Returns:
            True if key was handled.
        """
        try:
            # CRITICAL FIX: Check if this is a fullscreen plugin session first
            if (
                hasattr(self, "_fullscreen_session_active")
                and self._fullscreen_session_active
            ):
                # Route input to fullscreen session through event bus
                # Let the plugin handle all input including exit keys
                await self.event_bus.emit_with_hooks(
                    EventType.FULLSCREEN_INPUT,
                    {"key_press": key_press, "source": "input_handler"},
                    "input_handler",
                )
                return True

            # Initialize modal renderer if needed
            if not self.modal_renderer:
                logger.warning("Modal keypress received but no modal renderer active")
                await self._exit_modal_mode()
                return True

            # Handle save confirmation if active
            if self._pending_save_confirm:
                handled = await self._handle_save_confirmation(key_press)
                if handled:
                    return True

            # Handle save target selection if active
            if self._pending_save_target:
                handled = await self._handle_save_target_selection(key_press)
                if handled:
                    return True

            # Handle F1 and ? keys for help overlay (from any mode including modal)
            key_name = getattr(key_press, "name", "") or getattr(key_press, "key", "")
            key_char = getattr(key_press, "char", "")
            if key_name == "F1" or key_char == "?":
                logger.info("F1 or ? pressed in modal mode - showing help overlay")
                # Emit SHOW_HELP_OVERLAY event (will be handled by application)
                await self.event_bus.emit_with_hooks(
                    EventType.SHOW_HELP_OVERLAY,
                    {"source": "modal_controller"},
                    "modal",
                )
                return True

            # Handle navigation and widget interaction
            logger.info(f"Modal processing key: {key_press.name}")

            nav_handled = self.modal_renderer._handle_widget_navigation(key_press)
            logger.info(f"Widget navigation handled: {nav_handled}")
            if nav_handled:
                # Re-render modal with updated focus
                await self._refresh_modal_display()
                return True

            # Debug: Check modal_renderer state before handling input
            has_sections = getattr(self.modal_renderer, "has_command_sections", "N/A")
            cmd_items_len = (
                len(getattr(self.modal_renderer, "command_items", []))
                if hasattr(self.modal_renderer, "command_items")
                else "N/A"
            )
            widgets_len = (
                len(getattr(self.modal_renderer, "widgets", []))
                if hasattr(self.modal_renderer, "widgets")
                else "N/A"
            )
            logger.info(
                f"modal_renderer state: has_command_sections={has_sections}, "
                f"command_items_len={cmd_items_len}, widgets_len={widgets_len}"
            )
            input_handled = self.modal_renderer._handle_widget_input(key_press)
            logger.info(f"Widget input handled: {input_handled}")
            if input_handled:
                # Check if a command was selected (for command-style modals)
                was_selected = (
                    self.modal_renderer.was_command_selected()
                    if hasattr(self.modal_renderer, "was_command_selected")
                    else "N/A"
                )
                logger.info(f"Checking was_command_selected: {was_selected}")
                if self.modal_renderer.was_command_selected():
                    selected_cmd = self.modal_renderer.get_selected_command()
                    logger.info(f"Command selected: {selected_cmd}")

                    # CRITICAL FIX: Emit event FIRST, then decide whether to exit modal
                    # This allows plugins to return show_modal for chaining without race condition
                    if selected_cmd:
                        context = {"command": selected_cmd, "source": "modal"}
                        results = await self.event_bus.emit_with_hooks(
                            EventType.MODAL_COMMAND_SELECTED, context, "input_handler"
                        )
                        # Get modified data from hook results (main phase final_data)
                        final_data = (
                            results.get("main", {}).get("final_data", {})
                            if results
                            else {}
                        )

                        # Check if plugin wants to chain to another modal
                        # This must be checked BEFORE exiting the current modal
                        if final_data.get("show_modal"):
                            from kollabor_events.models import UIConfig

                            modal_config = final_data["show_modal"]
                            ui_config = UIConfig(
                                type="modal",
                                title=modal_config.get("title", ""),
                                modal_config=modal_config,
                            )
                            # Direct transition to new modal without exiting first
                            # _enter_modal_mode will handle the terminal state transition
                            await self._enter_modal_mode(ui_config)
                        else:
                            # No modal chaining - exit modal based on exit_mode or action type
                            # Commands that display their own messages need minimal exit (no input render)
                            exit_mode = (
                                selected_cmd.get("exit_mode", "normal")
                                if selected_cmd
                                else "normal"
                            )
                            action = (
                                selected_cmd.get("action", "") if selected_cmd else ""
                            )
                            # Actions that will display messages should use minimal
                            # exit to prevent duplicate input boxes
                            minimal_actions = [
                                "resume_session",
                                "branch_select_session",
                                "branch_execute",
                            ]
                            if exit_mode == "minimal" or action in minimal_actions:
                                await self._exit_modal_mode_minimal()
                            else:
                                await self._exit_modal_mode()

                            # Display messages if plugin returned them (AFTER modal exit)
                            if final_data.get("display_messages"):
                                if hasattr(self, "renderer") and self.renderer:
                                    if hasattr(self.renderer, "message_coordinator"):
                                        self.renderer.message_coordinator.display_message_sequence(
                                            final_data["display_messages"]
                                        )
                                        # DON'T call _update_display here - render loop will handle it.
                                        # The display_message_sequence() finally block already:
                                        # - Sets writing_messages=False (unblocks render loop)
                                        # - Resets input_line_written=False, last_line_count=0
                                        # - Invalidates render cache
                                        # Calling _update_display here causes duplicate input boxes.
                    return True
                # Re-render modal with updated widget state
                await self._refresh_modal_display()
                return True

            # Handle search mode input
            if self.modal_renderer and self.modal_renderer._search_active:
                if key_press.name == "Escape":
                    self.modal_renderer._search_active = False
                    self.modal_renderer._search_query = ""
                    self.modal_renderer.scroll_offset = 0
                    await self._refresh_modal_display()
                    return True
                elif key_press.name == "Backspace":
                    if self.modal_renderer._search_query:
                        self.modal_renderer._search_query = (
                            self.modal_renderer._search_query[:-1]
                        )
                        self.modal_renderer.scroll_offset = 0
                        await self._refresh_modal_display()
                    return True
                elif key_press.name == "Enter":
                    self.modal_renderer._search_active = False
                    # Snap focus to first visible widget
                    if (
                        self.modal_renderer._visible_widget_indices
                        and self.modal_renderer.widgets
                    ):
                        old_idx = self.modal_renderer.focused_widget_index
                        if old_idx < len(self.modal_renderer.widgets):
                            self.modal_renderer.widgets[old_idx].set_focus(False)
                        new_idx = self.modal_renderer._visible_widget_indices[0]
                        self.modal_renderer.focused_widget_index = new_idx
                        self.modal_renderer.widgets[new_idx].set_focus(True)
                        self.modal_renderer.scroll_offset = 0
                    await self._refresh_modal_display()
                    return True
                elif key_press.name == "Ctrl+S":
                    pass  # Fall through to save handler below
                elif (
                    key_press.char
                    and len(key_press.char) == 1
                    and key_press.char.isprintable()
                ):
                    self.modal_renderer._search_query += key_press.char
                    self.modal_renderer.scroll_offset = 0
                    await self._refresh_modal_display()
                    return True
                else:
                    return True

            # Activate search with "/"
            if (
                self.modal_renderer
                and not self._pending_save_confirm
                and not self._pending_save_target
            ):
                if key_press.char == "/":
                    self.modal_renderer._search_active = True
                    self.modal_renderer._search_query = ""
                    self.modal_renderer.scroll_offset = 0
                    await self._refresh_modal_display()
                    return True

            # Check for custom action keys defined in modal config
            if self.modal_renderer and hasattr(
                self.modal_renderer, "current_ui_config"
            ):
                ui_config = self.modal_renderer.current_ui_config
                if (
                    ui_config
                    and hasattr(ui_config, "modal_config")
                    and ui_config.modal_config
                ):
                    actions = ui_config.modal_config.get("actions", [])
                    key_char = key_press.char or ""
                    key_name = key_press.name or ""

                    for action_def in actions:
                        action_key = action_def.get("key", "")
                        # Match by key name or char (case-insensitive for single chars)
                        if action_key == key_name or (
                            len(action_key) == 1
                            and action_key.lower() == key_char.lower()
                        ):

                            action_name = action_def.get("action", "")
                            # Skip standard actions handled below
                            if action_name in ("select", "cancel", "submit"):
                                break

                            logger.info(
                                f"Custom action key '{action_key}' matched: {action_name}"
                            )

                            # Get the currently selected command item if any
                            selected_cmd = None
                            if self.modal_renderer.has_command_sections:
                                selected_cmd = (
                                    self.modal_renderer.get_selected_command()
                                )

                            # CRITICAL FIX: Emit event FIRST, then decide whether to exit modal
                            context = {
                                "command": {
                                    "action": action_name,
                                    "profile_name": (
                                        selected_cmd.get("profile_name")
                                        if selected_cmd
                                        else None
                                    ),
                                    "agent_name": (
                                        selected_cmd.get("agent_name")
                                        if selected_cmd
                                        else None
                                    ),
                                    "skill_name": (
                                        selected_cmd.get("skill_name")
                                        if selected_cmd
                                        else None
                                    ),
                                },
                                "source": "modal_action_key",
                            }
                            results = await self.event_bus.emit_with_hooks(
                                EventType.MODAL_COMMAND_SELECTED,
                                context,
                                "input_handler",
                            )

                            # Handle results
                            final_data = (
                                results.get("main", {}).get("final_data", {})
                                if results
                                else {}
                            )

                            # Check if plugin wants to chain to another modal
                            # This must be checked BEFORE exiting the current modal
                            if final_data.get("show_modal"):
                                from kollabor_events.models import UIConfig

                                modal_config = final_data["show_modal"]
                                new_ui_config = UIConfig(
                                    type="modal",
                                    title=modal_config.get("title", ""),
                                    modal_config=modal_config,
                                )
                                # Direct transition to new modal without exiting first
                                await self._enter_modal_mode(new_ui_config)
                            else:
                                # No modal chaining - exit modal first
                                await self._exit_modal_mode()
                                # Display messages if plugin returned them (AFTER modal exit)
                                if final_data.get("display_messages"):
                                    if hasattr(self.renderer, "message_coordinator"):
                                        self.renderer.message_coordinator.display_message_sequence(
                                            final_data["display_messages"]
                                        )

                            return True

            if key_press.name in ("Escape", "Ctrl+C"):
                logger.info("Processing Escape/Ctrl+C key for modal exit")
                # Check for unsaved changes
                if self.modal_renderer and self._has_pending_modal_changes():
                    self._pending_save_confirm = True
                    await self._show_save_confirmation()
                    return True
                await self._exit_modal_mode()
                return True
            elif key_press.name == "Ctrl+S":
                logger.info("Processing Ctrl+S - showing save target prompt")
                self._pending_save_target = True
                if self.modal_renderer:
                    self.modal_renderer._save_target_active = True
                await self._refresh_modal_display()
                return True
            elif key_press.name == "Enter":
                logger.info(
                    "ENTER KEY HIJACKED - This should not happen if widget handled it!"
                )
                # Try to save modal changes and exit
                await self._save_and_exit_modal()
                return True

            return True
        except Exception as e:
            logger.error(f"Error handling modal keypress: {e}")
            await self._exit_modal_mode()
            return False

    # LiveModal removed -- terminal view and hub feed now use AltView stack.
    # See: plugins/altview/terminal_altview.py, plugins/altview/hub_feed_altview.py

    # ==================== STATUS MODAL HANDLERS ====================

    async def _handle_status_modal_trigger(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle status modal trigger events to show status modals.

        Args:
            event_data: Event data containing modal configuration.
            context: Hook execution context.

        Returns:
            Dictionary with status modal result.
        """
        try:
            ui_config = event_data.get("ui_config")
            if ui_config:
                logger.info(f"Status modal trigger received: {ui_config.title}")
                logger.info(f"Status modal trigger UI config type: {ui_config.type}")
                await self._enter_status_modal_mode(ui_config)
                return {"success": True, "status_modal_activated": True}
            else:
                logger.warning("Status modal trigger received without ui_config")
                return {"success": False, "error": "Missing ui_config"}
        except Exception as e:
            logger.error(f"Error handling status modal trigger: {e}")
            return {"success": False, "error": str(e)}

    async def _enter_status_modal_mode(self, ui_config):
        """Enter status modal mode - modal confined to status area.

        Args:
            ui_config: Status modal configuration.
        """
        try:
            # Set status modal mode
            self.command_mode = CommandMode.STATUS_MODAL
            self.current_status_modal_config = ui_config
            logger.info(f"Entered status modal mode: {ui_config.title}")

            # Unlike full modals, status modals don't take over the screen
            # They just appear in the status area via the renderer
            await self._update_display(force_render=True)

        except Exception as e:
            logger.error(f"Error entering status modal mode: {e}")
            await self._exit_command_mode()

    async def _handle_status_modal_keypress(self, key_press) -> bool:
        """Handle keypress during status modal mode.

        Args:
            key_press: Parsed key press to process.

        Returns:
            True if key was handled, False otherwise.
        """
        try:
            logger.info(
                f"Status modal received key: name='{key_press.name}', char='{key_press.char}', code={key_press.code}"
            )

            if key_press.name == "Escape":
                logger.info("Escape key detected, closing status modal")
                await self._exit_status_modal_mode()
                return True
            elif key_press.name == "Enter":
                logger.info("Enter key detected, closing status modal")
                await self._exit_status_modal_mode()
                return True
            elif key_press.char and ord(key_press.char) == 3:  # Ctrl+C
                logger.info("Ctrl+C detected, closing status modal")
                await self._exit_status_modal_mode()
                return True
            else:
                logger.info(f"Unhandled key in status modal: {key_press.name}")
                return True

        except Exception as e:
            logger.error(f"Error handling status modal keypress: {e}")
            await self._exit_status_modal_mode()
            return False

    async def _handle_status_modal_input(self, char: str) -> bool:
        """Handle input during status modal mode.

        Args:
            char: Character input to process.

        Returns:
            True if input was handled, False otherwise.
        """
        try:
            # For now, ignore character input in status modals
            # Could add search/filter functionality later
            return True
        except Exception as e:
            logger.error(f"Error handling status modal input: {e}")
            await self._exit_status_modal_mode()
            return False

    async def _exit_status_modal_mode(self):
        """Exit status modal mode and return to normal input."""
        try:
            logger.info("Exiting status modal mode...")
            self.command_mode = CommandMode.NORMAL
            self.current_status_modal_config = None
            logger.info("Status modal mode exited successfully")

            # Refresh display to remove the status modal
            await self._update_display(force_render=True)
            logger.info("Display updated after status modal exit")

        except Exception as e:
            logger.error(f"Error exiting status modal mode: {e}")
            self.command_mode = CommandMode.NORMAL

    async def _handle_status_modal_render(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle status modal render events to provide modal display lines.

        Args:
            event_data: Event data containing render request.
            context: Hook execution context.

        Returns:
            Dictionary with status modal lines if active.
        """
        try:
            if (
                self.command_mode == CommandMode.STATUS_MODAL
                and self.current_status_modal_config
            ):

                # Generate status modal display lines
                modal_lines = self._generate_status_modal_lines(
                    self.current_status_modal_config
                )

                return {"success": True, "status_modal_lines": modal_lines}
            else:
                return {"success": True, "status_modal_lines": []}

        except Exception as e:
            logger.error(f"Error handling status modal render: {e}")
            return {"success": False, "status_modal_lines": []}

    def _generate_status_modal_lines(self, ui_config) -> List[str]:
        """Generate formatted lines for status modal display using visual effects.

        Delegates to StatusModalRenderer component (Phase 1 extraction).

        Args:
            ui_config: UI configuration for the status modal.

        Returns:
            List of formatted lines for display.
        """
        return self._status_modal_renderer.generate_status_modal_lines(ui_config)

    # ==================== STANDARD MODAL OPERATIONS ====================

    async def _show_modal_from_result(self, result):
        """Show a modal from a command result.

        Args:
            result: CommandResult with ui_config for modal display.
        """
        if result and result.ui_config:
            await self._enter_modal_mode(result.ui_config)

    async def _enter_modal_mode(self, ui_config):
        """Enter modal mode and show modal renderer.

        This is the standard path for any command that returns a
        ``CommandResult`` with ``UIConfig(type="modal")`` -- /profile,
        /permissions, /login, /mcp show, /config, /resume, etc. It
        wires up three pieces of state that MUST stay in sync:

          1. ``writing_messages=True`` -- pauses the render loop
          2. ``coordinator.enter_alternate_buffer()`` -- routes messages
             into the buffer instead of stdout
          3. ``command_mode = MODAL`` -- tells the input handler to
             route keys to the modal renderer

        Step (2) is the one that's easy to forget. Without it, llm
        responses arriving while the modal is up will print straight
        through ``print()`` onto the alt screen buffer, landing on
        top of the modal (the /profile bleed bug). See the module
        docstring for the full contract.

        Modal-to-modal chain transitions (line below): if a previous
        modal is already on screen we close it without a full
        teardown, and the coordinator may already be in alt-buffer
        mode. Skip the redundant enter call in that case to avoid the
        "Already in alternate buffer" warning path.

        Args:
            ui_config: Modal configuration.
        """
        try:
            # Import modal renderer here to avoid circular imports
            from kollabor_tui.modals.modal_renderer import ModalRenderer

            # CRITICAL FIX: Handle direct modal-to-modal transitions
            # If already in modal mode, close the old modal first (without exiting modal mode).
            # The old modal's close_modal() writes \033[?1049l internally, but we're
            # about to open a new modal that will write \033[?1049h again, so the
            # terminal stays effectively in altbuf from the user's perspective.
            # We deliberately do NOT call coordinator.exit_alternate_buffer() here --
            # messages that arrived during the old modal should keep flowing into
            # the buffer through the new modal without an intermediate flush.
            if self.modal_renderer and self.command_mode == CommandMode.MODAL:
                logger.info("Direct modal-to-modal transition: closing old modal first")
                # Close the old modal renderer (exits alternate buffer)
                _ = self.modal_renderer.close_modal()
                self.modal_renderer.widgets = []
                self.modal_renderer.focused_widget_index = 0
                self.modal_renderer = None
                # DON'T reset command_mode or writing_messages - we're staying in modal mode

            # Create modal renderer instance with proper config service
            self.modal_renderer = ModalRenderer(
                terminal_renderer=self.renderer,
                visual_effects=getattr(self.renderer, "visual_effects", None),
                config_service=self.config,  # Use config as config service
            )

            # Pause render loop during modal (only if not already paused).
            # ``writing_messages`` blocks the main render loop from drawing
            # the input box, status bar, etc. on top of the modal. It does
            # NOT block display_message_sequence() -- that's handled by the
            # coordinator call below.
            if not self.renderer.writing_messages:
                self.renderer.writing_messages = True
                self.renderer.clear_active_area()

            # CRITICAL: tell the coordinator we're entering altbuf so llm
            # messages that arrive while the modal is up get buffered
            # instead of bleeding through via print().
            #
            # show_modal() below will eventually write \033[?1049h (via
            # modal_state_manager.prepare_modal_display) which switches
            # stdout to the alt screen. Without setting the coordinator
            # flag FIRST, there's a window where the coordinator thinks
            # stdout is still the main buffer -- and any llm response
            # finishing in the background during that window will call
            # display_message_sequence() -> _output_rendered() ->
            # print(), landing the text directly on top of the modal
            # we're about to draw.
            #
            # On modal-to-modal chain transitions (see branch above) the
            # flag may already be True from the previous modal. Skip the
            # call in that case -- enter_alternate_buffer() logs a
            # warning and early-returns on redundant calls, and we don't
            # want to overwrite the saved main-buffer state.
            if hasattr(self.renderer, "message_coordinator"):
                coordinator = self.renderer.message_coordinator
                if not coordinator._in_alternate_buffer:
                    coordinator.enter_alternate_buffer()

            # Set modal mode
            self.command_mode = CommandMode.MODAL
            logger.info(f"Command mode set to: {self.command_mode}")

            # Show the modal (handles its own alternate buffer)
            await self.modal_renderer.show_modal(ui_config)

            logger.info("Entered modal mode")

        except Exception as e:
            logger.error(f"Error entering modal mode: {e}")
            self.command_mode = CommandMode.NORMAL
            self.renderer.writing_messages = False
            # Best-effort coordinator cleanup on error: if we already
            # entered altbuf but show_modal() blew up, the coordinator
            # is out of sync with the actual terminal state. Clear the
            # flag so subsequent message display works normally.
            # We do NOT call _flush_buffered_output here because the
            # terminal may or may not actually be in altbuf -- safer
            # to let the next modal cycle drain the buffer cleanly.
            if hasattr(self.renderer, "message_coordinator"):
                coordinator = self.renderer.message_coordinator
                if coordinator._in_alternate_buffer:
                    coordinator._in_alternate_buffer = False
                    coordinator._saved_main_buffer_state = None

    async def _refresh_modal_display(self):
        """Refresh modal display after widget interactions."""
        try:
            if self.modal_renderer and hasattr(
                self.modal_renderer, "current_ui_config"
            ):

                # FIX: Do NOT call clear_active_area() during modal refresh
                # The modal uses the alternate buffer, and clear_active_area() is
                # designed for the normal buffer. The state manager already handles
                # clearing properly through its own _clear_modal_content_area() method.

                # Re-render the modal with current widget states (preserve widgets!)
                modal_lines = self.modal_renderer._render_modal_box(
                    self.modal_renderer.current_ui_config,
                    preserve_widgets=True,
                )

                # CRITICAL FIX: Update layout height to accommodate expanded content
                # When dropdown expands, content grows beyond original layout height
                if (
                    self.modal_renderer.state_manager
                    and self.modal_renderer.state_manager.current_layout
                ):
                    from kollabor_tui.modals.modal_state_manager import ModalLayout

                    old_layout = self.modal_renderer.state_manager.current_layout
                    new_layout = ModalLayout(
                        width=old_layout.width,
                        height=len(modal_lines)
                        + 2,  # Update height to match new content
                        start_row=old_layout.start_row,
                        start_col=old_layout.start_col,
                        center_horizontal=old_layout.center_horizontal,
                        center_vertical=old_layout.center_vertical,
                        padding=old_layout.padding,
                        border_style=old_layout.border_style,
                    )
                    # Update the layout without re-entering alternate buffer
                    self.modal_renderer.state_manager.current_layout = new_layout
                    logger.debug(
                        f"Updated modal layout height: {old_layout.height} -> {new_layout.height}"
                    )

                # Use state_manager.render_modal_content() instead of _render_modal_lines()
                # to avoid re-calling prepare_modal_display() which causes buffer switching
                if self.modal_renderer.state_manager:
                    self.modal_renderer.state_manager.render_modal_content(modal_lines)
                else:
                    # Fallback to old method if state_manager not available
                    await self.modal_renderer._render_modal_lines(modal_lines)
            else:
                pass
        except Exception as e:
            logger.error(f"Error refreshing modal display: {e}")

    def _has_pending_modal_changes(self) -> bool:
        """Check if there are unsaved changes in modal widgets."""
        if not self.modal_renderer or not self.modal_renderer.widgets:
            return False
        for widget in self.modal_renderer.widgets:
            if hasattr(widget, "_pending_value") and widget._pending_value is not None:
                # Check if pending value differs from current config value
                current = widget.get_value() if hasattr(widget, "get_value") else None
                if widget._pending_value != current:
                    return True
        return False

    async def _show_save_confirmation(self):
        """Show save confirmation prompt in modal."""
        # Update modal footer to show confirmation prompt
        if self.modal_renderer:
            self.modal_renderer._save_confirm_active = True
            await self._refresh_modal_display()

    async def _handle_save_confirmation(self, key_press) -> bool:
        """Handle y/n input for save confirmation."""
        if key_press.char and key_press.char.lower() == "y":
            logger.info("User confirmed save - showing save target prompt")
            self._pending_save_confirm = False
            if self.modal_renderer:
                self.modal_renderer._save_confirm_active = False
            self._pending_save_target = True
            if self.modal_renderer:
                self.modal_renderer._save_target_active = True
            await self._refresh_modal_display()
            return True
        elif key_press.char and key_press.char.lower() == "n":
            logger.info("User declined save")
            self._pending_save_confirm = False
            if self.modal_renderer:
                self.modal_renderer._save_confirm_active = False
            await self._exit_modal_mode()
            return True
        elif key_press.name == "Escape":
            # Cancel confirmation, stay in modal
            logger.info("User cancelled confirmation")
            self._pending_save_confirm = False
            if self.modal_renderer:
                self.modal_renderer._save_confirm_active = False
            await self._refresh_modal_display()
            return True
        return False

    async def _handle_save_target_selection(self, key_press) -> bool:
        """Handle l/g input for save target selection."""
        if key_press.char and key_press.char.lower() == "l":
            logger.info("User selected local save target")
            self._pending_save_target = False
            if self.modal_renderer:
                self.modal_renderer._save_target_active = False
            await self._save_and_exit_modal(save_target="local")
            return True
        elif key_press.char and key_press.char.lower() == "g":
            logger.info("User selected global save target")
            self._pending_save_target = False
            if self.modal_renderer:
                self.modal_renderer._save_target_active = False
            await self._save_and_exit_modal(save_target="global")
            return True
        elif key_press.name == "Enter":
            # Default to local on Enter
            logger.info("User pressed Enter - defaulting to local save")
            self._pending_save_target = False
            if self.modal_renderer:
                self.modal_renderer._save_target_active = False
            await self._save_and_exit_modal(save_target="local")
            return True
        elif key_press.name == "Escape":
            # Cancel, stay in modal
            logger.info("User cancelled save target selection")
            self._pending_save_target = False
            if self.modal_renderer:
                self.modal_renderer._save_target_active = False
            await self._refresh_modal_display()
            return True
        return False

    async def _save_and_exit_modal(self, save_target: str = "local"):
        """Save modal changes and exit modal mode."""
        try:
            if self.modal_renderer:
                # Check if this is a form modal with form_action
                modal_config = getattr(self.modal_renderer, "current_ui_config", None)
                form_action = None
                if modal_config and hasattr(modal_config, "modal_config"):
                    form_action = modal_config.modal_config.get("form_action")

                if form_action and self.modal_renderer.widgets:
                    # Collect form data from widgets
                    form_data = {}
                    for widget in self.modal_renderer.widgets:
                        widget_type = widget.__class__.__name__
                        config_path = getattr(widget, "config_path", None)
                        pending = getattr(widget, "_pending_value", "NO_ATTR")
                        logger.info(
                            f"Widget: {widget_type}, config_path={config_path}, _pending_value={pending}"
                        )

                        if hasattr(widget, "config_path") and widget.config_path:
                            # Use field name (last part of config path)
                            field_name = widget.config_path.split(".")[-1]
                            # Always use get_pending_value() which returns:
                            # - _pending_value if user modified the field
                            # - Original value from config if not modified
                            # This ensures edit forms preserve unmodified values
                            if hasattr(widget, "get_pending_value"):
                                form_data[field_name] = widget.get_pending_value()
                            elif (
                                hasattr(widget, "_pending_value")
                                and widget._pending_value is not None
                            ):
                                form_data[field_name] = widget._pending_value
                            else:
                                form_data[field_name] = ""

                    logger.info(
                        f"Form submission: action={form_action}, data={form_data}"
                    )

                    # Get any extra fields from modal_config (like edit_profile_name)
                    extra_fields = {}
                    if modal_config and hasattr(modal_config, "modal_config"):
                        mc = modal_config.modal_config
                        # Pass through known extra fields for edit operations
                        for field in [
                            "edit_profile_name",
                            "edit_agent_name",
                            "edit_skill_name",
                        ]:
                            if field in mc:
                                extra_fields[field] = mc[field]

                    # Exit modal first
                    await self._exit_modal_mode()

                    # Emit MODAL_COMMAND_SELECTED with form action and data
                    context = {
                        "command": {
                            "action": form_action,
                            "form_data": form_data,
                            **extra_fields,  # Include edit_profile_name etc.
                        },
                        "source": "modal_form",
                    }
                    results = await self.event_bus.emit_with_hooks(
                        EventType.MODAL_COMMAND_SELECTED, context, "input_handler"
                    )

                    # Get modified data from hook results
                    final_data = (
                        results.get("main", {}).get("final_data", {}) if results else {}
                    )

                    # Display messages if returned
                    if final_data.get("display_messages"):
                        if hasattr(self.renderer, "message_coordinator"):
                            self.renderer.message_coordinator.display_message_sequence(
                                final_data["display_messages"]
                            )

                    # Show modal if plugin returned one
                    if final_data.get("show_modal"):
                        from kollabor_events.models import UIConfig

                        modal_config = final_data["show_modal"]
                        ui_config = UIConfig(
                            type="modal",
                            title=modal_config.get("title", ""),
                            modal_config=modal_config,
                        )
                        await self._enter_modal_mode(ui_config)

                    return

                # Fallback: use action handler for config-based modals
                if hasattr(self.modal_renderer, "action_handler"):
                    result = await self.modal_renderer.action_handler.handle_action(
                        "save", self.modal_renderer.widgets, save_target=save_target
                    )
                    if not result.get("success"):
                        logger.warning(
                            f"Failed to save modal changes: {result.get('message', 'Unknown error')}"
                        )

            await self._exit_modal_mode()
        except Exception as e:
            logger.error(f"Error saving and exiting modal: {e}")
            await self._exit_modal_mode()

    async def _exit_modal_mode(self):
        """Exit modal mode using existing patterns.

        Standard exit path. Use this when the user dismisses a modal
        (Esc or similar) and there's nothing special that the caller
        needs to print right after. This is the mirror of
        ``_enter_modal_mode`` and handles the full tear-down:

          1. Close the modal renderer (writes \\033[?1049l to exit
             altbuf -- terminal is now back in the main screen).
          2. Clear ``command_mode`` back to NORMAL.
          3. Call ``coordinator.exit_alternate_buffer(restore_state=False)``
             which:
               - Clears ``_in_alternate_buffer``
               - Calls ``_flush_buffered_output()`` to dump any
                 messages that arrived during the modal (with the
                 "returned" summary card) into the main buffer.
               - Resets render state flags for a clean input render.
          4. Force-render to draw the input box below the flushed
             messages.

        Compare ``_exit_modal_mode_minimal`` for the variant used
        when a command is about to print its own output.
        """
        try:
            # Close modal renderer (handles its own terminal restoration)
            if self.modal_renderer:
                _ = self.modal_renderer.close_modal()
                self.modal_renderer.widgets = []
                self.modal_renderer.focused_widget_index = 0
                self.modal_renderer = None

            # Return to normal mode
            self.command_mode = CommandMode.NORMAL

            # CRITICAL: Reset coordinator's alternate buffer flag to allow navigation mode entry
            # The modal's state_manager.close_modal() already exited the alternate buffer (ANSI \033[?1049l)
            # But we need to reset the coordinator's _in_alternate_buffer flag
            # AND flush any messages that were buffered while the modal
            # was up (exit_alternate_buffer handles both in the right
            # order -- clear flag, then flush, then invalidate cache).
            if hasattr(self.renderer, "message_coordinator"):
                coordinator = self.renderer.message_coordinator
                before_buf = coordinator._in_alternate_buffer
                before_writing = self.renderer.writing_messages
                logger.info(
                    f"[MODAL EXIT] Before reset: _in_alternate_buffer={before_buf}, "
                    f"writing_messages={before_writing}"
                )
                coordinator.exit_alternate_buffer(restore_state=False)
                after_buf = coordinator._in_alternate_buffer
                after_writing = self.renderer.writing_messages
                logger.info(
                    f"[MODAL EXIT] After reset: _in_alternate_buffer={after_buf}, "
                    f"writing_messages={after_writing}"
                )

            # Resume render loop (set by coordinator.exit_alternate_buffer above, but ensure it's False)
            self.renderer.writing_messages = False
            self.renderer.invalidate_render_cache()
            await self._update_display(force_render=True)
            logger.info("[MODAL EXIT] _update_display completed")

        except Exception as e:
            logger.error(f"Error exiting modal mode: {e}")
            self.command_mode = CommandMode.NORMAL
            self.modal_renderer = None
            self.renderer.writing_messages = False
            # Ensure coordinator state is reset even on error
            if hasattr(self.renderer, "message_coordinator"):
                self.renderer.message_coordinator.exit_alternate_buffer(
                    restore_state=False
                )

    async def _exit_modal_mode_minimal(self):
        """Exit modal mode WITHOUT rendering input - for commands that display their own content.

        Use this when a command (like /branch, /resume) will immediately display its own
        content after modal closes. This prevents duplicate input boxes.

        Why this path exists: the normal ``_exit_modal_mode`` flips
        ``writing_messages`` back to False and force-renders an input
        box. If the caller is about to print its own messages via
        ``display_message_sequence()`` right after, that creates a
        visible race: the input box renders, then gets immediately
        cleared and rewritten below the caller's output. Users see a
        flash and sometimes a duplicate box. This variant skips the
        re-render and keeps ``writing_messages=True`` so the caller's
        ``display_message_sequence()`` is atomic with the modal exit.

        The tradeoff: we can't call ``exit_alternate_buffer()`` (it
        flips ``writing_messages`` as a side effect). So we do the
        coordinator tear-down manually, matching what
        ``exit_alternate_buffer(restore_state=False)`` would do minus
        the writing_messages reset:

          1. Clear ``_in_alternate_buffer``
          2. Clear saved main-buffer state
          3. Call ``_flush_buffered_output()`` explicitly to drain
             any messages that arrived during the modal. WITHOUT
             THIS, buffered messages stay orphaned in
             ``_buffered_output`` until the next alt-buffer cycle,
             then print out of order AFTER the caller's own output.
          4. Do NOT touch ``writing_messages`` -- leave it True.

        CRITICAL STATE MANAGEMENT:
        - input_line_written=True: Marks content exists on screen
        - last_line_count=0: Prevents clear_active_area() from clearing stale lines
          (after modal exit, the stale last_line_count could clear into banner)
        """
        try:
            # Close modal renderer (handles its own terminal restoration via alternate buffer).
            # close_modal() writes \033[?1049l, so the terminal is back in
            # the main screen buffer by the time we hit the coordinator
            # tear-down below. That's the precondition for
            # _flush_buffered_output() -- it uses print(), which has to
            # land in the main buffer or the lines vanish.
            if self.modal_renderer:
                _ = self.modal_renderer.close_modal()
                self.modal_renderer.widgets = []
                self.modal_renderer.focused_widget_index = 0
                self.modal_renderer = None

            # Return to normal mode
            self.command_mode = CommandMode.NORMAL

            # CRITICAL: Reset coordinator's _in_alternate_buffer flag so navigation mode can work
            # But keep writing_messages=True for the calling command's display_message_sequence()
            if hasattr(self.renderer, "message_coordinator"):
                coordinator = self.renderer.message_coordinator
                # Manually reset _in_alternate_buffer without affecting writing_messages.
                # This is the "exit_alternate_buffer minus the writing_messages
                # reset" variant -- we deliberately inline the flag clear + flush
                # instead of calling the public method because the public method
                # would flip writing_messages and break atomicity with the
                # follow-up display_message_sequence() in the caller.
                if coordinator._in_alternate_buffer:
                    coordinator._in_alternate_buffer = False
                    coordinator._saved_main_buffer_state = None
                    # Flush any messages that arrived while the modal was up
                    # (e.g. llm responses). Without this, buffered messages are
                    # orphaned until the next alt-buffer cycle and end up
                    # printing AFTER the calling command's own output, out of
                    # order. Must flush AFTER clearing the flag so
                    # _output_rendered doesn't re-buffer the lines we're about
                    # to print.
                    coordinator._flush_buffered_output()

            # KEEP writing_messages=True to block render loop!
            # The calling command's display_message_sequence() will set it False when done.
            # This prevents the race condition where render loop runs before command displays.

            # After modal closes (alternate buffer exit), the OLD input box from before
            # the modal is restored on screen. We need clear_active_area() in
            # display_message_sequence() to clear it.
            #
            # CRITICAL: Set input_line_written=True so clear_active_area() will actually clear!
            # When the modal opened, clear_active_area() set input_line_written=False.
            # Now that we're back to main buffer with old input box, we need this True.
            self.renderer.input_line_written = True
            # last_line_count should still have the correct value from before modal opened
            self.renderer.invalidate_render_cache()
            # NOTE: No _update_display() call here - command will handle display

        except Exception as e:
            logger.error(f"Error exiting modal mode (minimal): {e}")
            self.command_mode = CommandMode.NORMAL
            self.modal_renderer = None
            # Ensure coordinator state is reset even on error
            if hasattr(self.renderer, "message_coordinator"):
                coordinator = self.renderer.message_coordinator
                if coordinator._in_alternate_buffer:
                    coordinator._in_alternate_buffer = False
                    coordinator._saved_main_buffer_state = None
            # Keep render state as-is for clearing
            self.renderer.invalidate_render_cache()
