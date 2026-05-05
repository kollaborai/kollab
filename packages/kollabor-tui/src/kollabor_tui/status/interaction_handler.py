"""Widget interaction handler for interactive status widgets.

This module provides the WidgetInteractionHandler class that manages
widget activation and modal lifecycle for interactive status widgets.

It integrates with:
- MessageDisplayCoordinator for modal lifecycle management
- ModalRenderer for displaying modal overlays
- StatusNavigationManager for navigation coordination
- Key parser for keyboard input handling

Supported interaction types:
- modal: Opens a fullscreen modal overlay with options
- toggle: Toggles between states (boolean or cyclic)
- inline_edit: Edits value directly in status area
- action: Executes command or function directly
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

from kollabor_events.models import UIConfig
from kollabor_tui.key_parser import KeyParser
from kollabor_tui.message_coordinator import MessageDisplayCoordinator
from kollabor_tui.modals.modal_renderer import ModalRenderer

logger = logging.getLogger(__name__)


class InteractionType(Enum):
    """Types of widget interactions."""

    MODAL = "modal"
    TOGGLE = "toggle"
    INLINE_EDIT = "inline_edit"
    ACTION = "action"


@dataclass
class InteractionState:
    """Tracks current widget interaction state.

    Attributes:
        active: Whether an interaction is currently active
        interaction_type: Type of active interaction
        widget_id: ID of the widget being interacted with
        modal_result: Result from modal interaction
        edit_value: Current value for inline editing
        action_result: Result from action execution
    """

    active: bool = False
    interaction_type: Optional[InteractionType] = None
    widget_id: Optional[str] = None
    modal_result: Optional[Dict[str, Any]] = None
    edit_value: Any = None
    action_result: Optional[Dict[str, Any]] = None


class WidgetInteractionHandler:
    """Handles widget interactions with modal lifecycle management.

    This class coordinates widget activation based on interaction type,
    manages modal state using MessageDisplayCoordinator, and routes
    keyboard input during active interactions.

    Attributes:
        renderer: Terminal renderer for display
        modal_renderer: ModalRenderer for showing modals
        coordinator: MessageDisplayCoordinator for buffer management
        navigation_manager: StatusNavigationManager for coordination
        key_parser: KeyParser for key handling
        state: Current interaction state
    """

    def __init__(
        self,
        renderer,
        modal_renderer: ModalRenderer,
        coordinator: MessageDisplayCoordinator,
        navigation_manager,
        key_parser: Optional[KeyParser] = None,
    ):
        """Initialize the widget interaction handler.

        Args:
            renderer: Terminal renderer instance
            modal_renderer: ModalRenderer instance for modals
            coordinator: MessageDisplayCoordinator for lifecycle
            navigation_manager: StatusNavigationManager instance
            key_parser: Optional KeyParser instance (created if not provided)
        """
        self.renderer = renderer
        self.modal_renderer = modal_renderer
        self.coordinator = coordinator
        self.navigation_manager = navigation_manager
        self.key_parser = key_parser or KeyParser()
        self.state = InteractionState()

        # Modal keyboard loop control
        self._modal_keyboard_loop_running = False
        self._modal_keyboard_loop_task: Optional[asyncio.Task] = None

        logger.debug("WidgetInteractionHandler initialized")

    async def handle_enter_key(
        self,
        widget_id: str,
        interaction_type: str,
        on_activate: Optional[Callable],
        context: Any,
    ) -> bool:
        """Route Enter key to appropriate widget activation.

        Args:
            widget_id: ID of the widget to activate
            interaction_type: Type of interaction ("modal", "toggle", "inline_edit", "action")
            on_activate: Optional async activation handler
            context: Widget context object

        Returns:
            True if activation was handled, False otherwise
        """
        # Check if an interaction is already active
        if self.state.active:
            logger.warning(f"Interaction already active for {self.state.widget_id}")
            return False

        # Parse interaction type
        try:
            type_enum = InteractionType(interaction_type)
        except ValueError:
            logger.error(f"Unknown interaction type: {interaction_type}")
            return False

        # Route to appropriate activation method
        logger.info(f"Activating widget {widget_id} with type {interaction_type}")

        if type_enum == InteractionType.MODAL:
            return await self.activate_modal(widget_id, on_activate, context)
        elif type_enum == InteractionType.TOGGLE:
            return await self.activate_toggle(widget_id, on_activate, context)
        elif type_enum == InteractionType.INLINE_EDIT:
            return await self.activate_inline_edit(widget_id, on_activate, context)
        elif type_enum == InteractionType.ACTION:
            return await self.activate_action(widget_id, on_activate, context)
        else:
            logger.error(f"Unhandled interaction type: {interaction_type}")
            return False

    async def activate_modal(
        self,
        widget_id: str,
        on_activate: Optional[Callable],
        context: Any,
    ) -> bool:
        """Activate a modal widget.

        Opens a fullscreen modal overlay with options. Uses
        MessageDisplayCoordinator for proper buffer lifecycle.

        Args:
            widget_id: ID of the widget to activate
            on_activate: Async handler that returns modal config
            context: Widget context object

        Returns:
            True if modal was activated successfully
        """
        logger.info(f"Activating modal widget: {widget_id}")

        # Set interaction state
        self.state.active = True
        self.state.interaction_type = InteractionType.MODAL
        self.state.widget_id = widget_id

        # Enter alternate buffer via coordinator
        await self.coordinator.enter_alternate_buffer()  # type: ignore[func-returns-value]

        try:
            # Call widget activation handler to get modal config
            if on_activate:
                modal_config = await on_activate(widget_id, context)

                # Build UIConfig from modal config
                ui_config = self._build_modal_ui_config(modal_config)

                # Show modal and get result
                result = await self._show_modal_with_keyboard_loop(ui_config)
                self.state.modal_result = result

                logger.info(f"Modal closed with result: {result}")
            else:
                logger.warning(f"No activation handler for modal widget {widget_id}")

        finally:
            # Exit alternate buffer, restore state cleanly
            await self.coordinator.exit_alternate_buffer(restore_state=True)  # type: ignore[func-returns-value]

            # Reset interaction state
            self.state.active = False
            self.state.interaction_type = None
            self.state.widget_id = None

        return True

    async def activate_toggle(
        self,
        widget_id: str,
        on_activate: Optional[Callable],
        context: Any,
    ) -> bool:
        """Activate a toggle widget.

        Toggles between states (boolean or cyclic). Executes inline
        without opening a modal.

        Args:
            widget_id: ID of the widget to activate
            on_activate: Async handler that performs toggle
            context: Widget context object

        Returns:
            True if toggle was successful
        """
        logger.info(f"Activating toggle widget: {widget_id}")

        # Set interaction state (short-lived for toggle)
        self.state.active = True
        self.state.interaction_type = InteractionType.TOGGLE
        self.state.widget_id = widget_id

        try:
            # Call widget activation handler
            if on_activate:
                result = await on_activate(widget_id, context)
                logger.info(f"Toggle result: {result}")
            else:
                logger.warning(f"No activation handler for toggle widget {widget_id}")

        finally:
            # Reset interaction state immediately (toggle is instant)
            self.state.active = False
            self.state.interaction_type = None
            self.state.widget_id = None

        return True

    async def activate_inline_edit(
        self,
        widget_id: str,
        on_activate: Optional[Callable],
        context: Any,
    ) -> bool:
        """Activate an inline edit widget.

        Edits value directly in status area (no modal). Shows
        inline editor controls.

        Args:
            widget_id: ID of the widget to activate
            on_activate: Async handler that returns edit config
            context: Widget context object

        Returns:
            True if inline edit was successful
        """
        logger.info(f"Activating inline edit widget: {widget_id}")

        # Set interaction state
        self.state.active = True
        self.state.interaction_type = InteractionType.INLINE_EDIT
        self.state.widget_id = widget_id

        try:
            # Call widget activation handler to get edit config
            if on_activate:
                edit_config = await on_activate(widget_id, context)

                # Show inline editor
                result = await self._show_inline_editor(edit_config)
                self.state.edit_value = result

                logger.info(f"Inline edit result: {result}")
            else:
                logger.warning(
                    f"No activation handler for inline edit widget {widget_id}"
                )

        finally:
            # Reset interaction state
            self.state.active = False
            self.state.interaction_type = None
            self.state.widget_id = None

        return True

    async def activate_action(
        self,
        widget_id: str,
        on_activate: Optional[Callable],
        context: Any,
    ) -> bool:
        """Activate an action widget.

        Executes command or function directly. May show
        action menu or execute immediately.

        Args:
            widget_id: ID of the widget to activate
            on_activate: Async handler that executes action
            context: Widget context object

        Returns:
            True if action was executed successfully
        """
        logger.info(f"Activating action widget: {widget_id}")

        # Set interaction state (short-lived for action)
        self.state.active = True
        self.state.interaction_type = InteractionType.ACTION
        self.state.widget_id = widget_id

        try:
            # Call widget activation handler
            if on_activate:
                result = await on_activate(widget_id, context)
                self.state.action_result = result
                logger.info(f"Action result: {result}")
            else:
                logger.warning(f"No activation handler for action widget {widget_id}")

        finally:
            # Reset interaction state
            self.state.active = False
            self.state.interaction_type = None
            self.state.widget_id = None

        return True

    async def handle_modal_keyboard_input(self) -> Dict[str, Any]:
        """Handle modal keyboard input loop.

        This method runs a keyboard input loop for modal navigation,
        handling up/down/Enter/Esc keys.

        Returns:
            Modal completion result when user exits
        """
        result = {"success": False, "action": "cancelled"}

        # Get terminal state for raw input
        terminal_state = self.renderer.terminal_state

        # Start keyboard loop
        self._modal_keyboard_loop_running = True

        try:
            while self._modal_keyboard_loop_running:
                # Read single keypress
                key_data = await asyncio.to_thread(terminal_state.read_key_raw)
                if not key_data:
                    continue

                # Parse key
                key_press = self.key_parser.parse(key_data)  # type: ignore[attr-defined]

                # Handle navigation keys
                if key_press.name == "Escape":
                    # Close modal
                    result = {"success": True, "action": "cancelled"}
                    break
                elif key_press.name == "Enter":
                    # Confirm selection
                    result = {"success": True, "action": "confirmed"}
                    break
                elif key_press.name in ("ArrowUp", "ArrowDown", "PageUp", "PageDown"):
                    # Refresh modal for navigation
                    self.modal_renderer.refresh_modal_display()
                elif key_press.name == "Tab":
                    # Move to next widget/option
                    self.modal_renderer.refresh_modal_display()

        except Exception as e:
            logger.error(f"Error in modal keyboard loop: {e}")
            result = {"success": False, "error": str(e)}

        finally:
            self._modal_keyboard_loop_running = False

        return result

    async def _show_modal_with_keyboard_loop(
        self, ui_config: UIConfig
    ) -> Dict[str, Any]:
        """Show modal and manage keyboard input loop.

        Args:
            ui_config: Modal UI configuration

        Returns:
            Modal interaction result
        """
        # Show modal (starts in background, handles keyboard internally)
        result = await self.modal_renderer.show_modal(ui_config)

        return result

    async def _show_inline_editor(self, edit_config: Dict[str, Any]) -> Any:
        """Show inline editor for inline edit widgets.

        Uses InlineEditorService to show editor directly in status area.
        Handles slider, text, and dropdown editor types with full
        keyboard input and callback execution.

        Args:
            edit_config: Editor configuration dict with keys:
                - type: "slider", "text", or "dropdown"
                - current: Current value
                - For slider: min, max, step, presets
                - For text: placeholder, max_length
                - For dropdown: options, selected (index)
                - on_save: Callback when confirmed (optional)
                - on_change: Callback on each change (optional)
                - on_select: Callback for dropdown selection (optional)
                - config_key: Config key for auto-save (optional)

        Returns:
            Edited value if confirmed, None if cancelled
        """
        if not edit_config or not isinstance(edit_config, dict):
            logger.warning(f"Invalid inline editor config: {edit_config}")
            return None

        from .inline_editor_service import InlineEditorService

        # Create service and show editor
        service = InlineEditorService(self.renderer, self.navigation_manager.state)
        result = await service.show_editor(edit_config)

        return result

    def _build_modal_ui_config(self, modal_config: Dict[str, Any]) -> UIConfig:
        """Build UIConfig from modal widget configuration.

        Args:
            modal_config: Modal configuration from widget activation handler

        Returns:
            UIConfig for modal renderer
        """
        # Extract modal properties
        title = modal_config.get("title", "Modal")
        options = modal_config.get("options", [])
        footer = modal_config.get("footer", "enter to select • esc to close")

        # Build sections for modal
        sections = []

        if options:
            # Convert options to command sections
            commands = []
            for i, option in enumerate(options):
                commands.append(
                    {
                        "name": option.get("label", f"Option {i+1}"),
                        "description": option.get("description", ""),
                        "action": option.get("action", f"option_{i}"),
                        "selectable": True,
                    }
                )

            sections.append(
                {
                    "title": title,
                    "commands": commands,
                }
            )

        # Create UIConfig
        ui_config = UIConfig(
            type="modal",
            title=title,
            footer=footer,
            modal_config=(
                {
                    "sections": sections,
                }
                if sections
                else None
            ),
        )

        return ui_config

    async def get_interaction_state(self) -> Dict[str, Any]:
        """Get current interaction state for debugging.

        Returns:
            Dictionary with interaction state information
        """
        return {
            "active": self.state.active,
            "interaction_type": (
                self.state.interaction_type.value
                if self.state.interaction_type
                else None
            ),
            "widget_id": self.state.widget_id,
            "has_modal_result": self.state.modal_result is not None,
            "modal_keyboard_loop_running": self._modal_keyboard_loop_running,
        }
