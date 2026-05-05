"""Status navigation manager for interactive widget navigation.

This module provides navigation state management and keyboard routing for
interactive status widgets. It coordinates with MessageDisplayCoordinator to
prevent render conflicts during navigation.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from kollabor_events.models import EventType
from kollabor_tui.visual_effects import ShimmerEffect

from .layout_editing import LayoutEditingMixin
from .modal_presenter import ModalPresenterMixin
from .navigation_state import (
    NavigationMode,
    SelectionType,
    StatusNavigationState,
)

logger = logging.getLogger(__name__)


class StatusNavigationManager(LayoutEditingMixin, ModalPresenterMixin):
    """Manages navigation state with MessageDisplayCoordinator awareness.

    This manager handles keyboard routing for status widget navigation,
    coordinates with the message display system to prevent render conflicts,
    and manages the navigation lifecycle.

    Attributes:
        renderer: TerminalRenderer instance for rendering
        coordinator: MessageDisplayCoordinator for render coordination
        event_bus: EventBus for emitting widget interaction events
        state: StatusNavigationState for tracking navigation state
        layout: Current status layout for navigation bounds
    """

    def __init__(
        self,
        renderer,
        coordinator,
        event_bus,
        widget_registry=None,
        layout: Optional[Any] = None,
        layout_manager: Optional[Any] = None,
        config: Optional[Any] = None,
    ):
        """Initialize the navigation manager.

        Args:
            renderer: TerminalRenderer instance
            coordinator: MessageDisplayCoordinator instance
            event_bus: EventBus instance for emitting widget events
            widget_registry: StatusWidgetRegistry for widget info lookup
            layout: Optional status layout for bounds calculation
            layout_manager: Optional layout manager for layout editing
            config: Optional config for first-run help check
        """
        self.renderer = renderer
        self.coordinator = coordinator
        self.event_bus = event_bus
        self.widget_registry = widget_registry
        # Use the new StatusNavigationState with mode support
        self.state = StatusNavigationState()
        self.layout = layout
        self.layout_manager = layout_manager
        self.config = config
        self.app: Optional[Any] = None  # Set by application for command execution
        self._first_run_help_checked = False

        # Shimmer effect for navigation mode
        self._shimmer = ShimmerEffect(speed=1, wave_width=6)
        self._shimmer_task: Optional[asyncio.Task] = None
        self._shimmer_running = False

        logger.debug("StatusNavigationManager initialized")

    def set_layout(self, layout: Any) -> None:
        """Set the current status layout for navigation bounds.

        Args:
            layout: Status layout configuration
        """
        self.layout = layout
        logger.debug("Navigation layout updated")

    def set_widget_registry(self, widget_registry: Any) -> None:
        """Set the widget registry for widget info lookup.

        Args:
            widget_registry: StatusWidgetRegistry instance
        """
        self.widget_registry = widget_registry
        logger.debug("Widget registry set for navigation")

    def set_layout_manager(self, layout_manager: Any) -> None:
        """Set the layout manager for layout editing.

        Args:
            layout_manager: StatusLayoutManager instance
        """
        self.layout_manager = layout_manager
        logger.debug("Layout manager set for navigation")

    @property
    def active(self) -> bool:
        """Check if navigation mode is currently active.

        Returns:
            True if navigation mode is active, False otherwise
        """
        return bool(self.state.active)

    async def toggle_navigation_mode(self) -> bool:
        """Toggle navigation mode on/off (Tab key handler).

        Returns:
            True if navigation mode is now active, False if inactive
        """
        is_active = self.state.is_active()

        if is_active:
            # ESC behavior: always exit to Input Mode
            await self.exit_navigation_mode()
            return False
        else:
            # Check first-run help BEFORE entering navigation mode
            # (modal shows while still in INPUT mode, avoiding state conflicts)
            await self._check_first_run_help()

            # Enter navigation mode
            result = await self.enter_navigation_mode()
            logger.info(
                f"[NAV] toggle_navigation_mode: enter_navigation_mode returned {result}"
            )
            if not result:
                logger.warning(
                    "[NAV] toggle_navigation_mode: enter_navigation_mode failed!"
                )
            return result

    async def enter_navigation_mode(self) -> bool:
        """Enter navigation mode (transitions to STATUS_FOCUS).

        Returns:
            True if successfully entered navigation mode
        """
        logger.info("[NAV] enter_navigation_mode called")

        # Check if already active
        current_mode = self.state.get_mode()
        if current_mode != NavigationMode.INPUT:
            logger.debug(f"Navigation mode already active (mode={current_mode.value})")
            return True

        logger.info("[NAV] Checking coordinator state")
        writing = self.coordinator.is_writing_messages()
        alt_buf = self.coordinator._in_alternate_buffer
        logger.info(
            f"[NAV] is_writing_messages={writing}, _in_alternate_buffer={alt_buf}"
        )
        # Check if LLM is streaming or messages are queued
        if self.coordinator.is_writing_messages():
            logger.warning("Cannot enter navigation during message display")
            return False

        # Check if in alternate buffer (modal/fullscreen active)
        if self.coordinator._in_alternate_buffer:
            logger.warning("Cannot enter navigation during modal/fullscreen mode")
            return False

        logger.info("[NAV] Transitioning to STATUS_FOCUS mode")
        # Use the new mode transition method
        await self.state.transition_to_status_focus()
        logger.info("[NAV] Status focus mode set")

        # Signal coordinator: navigation active, pause message renders
        logger.info("[NAV] Signaling coordinator")
        self.coordinator.set_navigation_active(True)

        # Render initial selection
        logger.info("[NAV] About to render navigation state")
        await self.render_navigation_state()
        logger.info("[NAV] Render navigation state complete")

        # Start shimmer animation loop if any widgets have effects
        await self._start_effect_animation()

        logger.info("Entered navigation mode (STATUS_FOCUS)")
        return True

    async def exit_navigation_mode(self) -> None:
        """Exit navigation mode (restore message display).

        ESC key behavior:
        - EDIT mode -> STATUS_FOCUS (first Esc)
        - STATUS_FOCUS -> INPUT (second Esc)
        """
        current_mode = self.state.get_mode()
        active_widget_id = self.state.active_widget_id

        # Deactivate any active widget interaction
        if self.state.interaction_active:
            await self.state.deactivate_widget()

        # Mode-based exit behavior
        if current_mode == NavigationMode.EDIT:
            # First Esc: EDIT -> STATUS_FOCUS
            await self.state.transition_to_status_focus_from_edit()
            await self.render_navigation_state()
            logger.info("Exited EDIT mode, now in STATUS_FOCUS")
        else:
            # STATUS_FOCUS -> INPUT (full exit)
            await self.state.transition_to_input_mode()

            # Stop effect animation
            await self._stop_effect_animation()

            # Signal coordinator: navigation done, resume message renders
            self.coordinator.set_navigation_active(False)

            # Emit deactivation event if there was an active widget
            if active_widget_id:
                await self.event_bus.emit_with_hooks(
                    EventType.WIDGET_DEACTIVATED,
                    {"widget_id": active_widget_id, "reason": "navigation_exit"},
                    "StatusNavigationManager.exit_navigation_mode",
                )

            # Restore normal rendering
            await self.renderer.render_active_area()
            logger.info("Exited navigation mode to INPUT")

    # Aliases for KeyPressHandler compatibility
    async def activate_navigation(self) -> bool:
        """Alias for transition_to_status_focus (for Tab key)."""
        return await self.transition_to_status_focus()

    async def deactivate_navigation(self) -> None:
        """Alias for exit_navigation_mode (direct exit to INPUT)."""
        await self.exit_navigation_mode()

    async def handle_keypress(self, key_press) -> bool:
        """Handle keypress during navigation mode.

        Routes keypresses to appropriate handlers:
        - Arrow keys -> handle_arrow_key
        - Enter -> handle_enter_key
        - Escape -> exit_navigation_mode
        - Space -> handle_space_key (for toggle widgets)

        Args:
            key_press: KeyPress object from key parser

        Returns:
            True if keypress was handled, False otherwise
        """
        if not self.active:
            return False

        key_name = getattr(key_press, "name", None) or getattr(key_press, "key", None)
        if not key_name:
            return False

        # Map arrow key names to our format
        if key_name in ("ArrowUp", "Up"):
            return await self.handle_arrow_key("up")
        elif key_name in ("ArrowDown", "Down"):
            return await self.handle_arrow_key("down")
        elif key_name in ("ArrowLeft", "Left"):
            return await self.handle_arrow_key("left")
        elif key_name in ("ArrowRight", "Right"):
            return await self.handle_arrow_key("right")
        elif key_name == "Home":
            return await self.handle_arrow_key("home")
        elif key_name == "End":
            return await self.handle_arrow_key("end")
        elif key_name == "Enter":
            await self.handle_enter_key()
            return True
        elif key_name == "Escape":
            # Cascading mode exit: EDIT -> STATUS_FOCUS -> INPUT
            await self.transition_to_input_mode()
            return True
        elif key_name == "Space" or (
            hasattr(key_press, "char") and key_press.char == " "
        ):
            await self.handle_space_key()
            return True
        elif key_name == "F1":
            # Show help overlay from navigation mode
            await self._show_help_from_navigation()
            return True
        elif key_name == "?" or (hasattr(key_press, "char") and key_press.char == "?"):
            # Show help overlay from navigation mode
            await self._show_help_from_navigation()
            return True
        elif key_name == "Ctrl+Z":
            # Undo last action
            await self.handle_undo()
            return True

        # Digit keys 1-9: Quick jump to widget in current row
        char = getattr(key_press, "char", None)
        if char and char.isdigit() and "1" <= char <= "9":
            digit = int(char)
            if await self._handle_digit_key(digit):
                return True

        # Layout editing shortcuts
        char = getattr(key_press, "char", None)
        name = getattr(key_press, "name", None)
        logger.info(
            f"[NAV] Layout editing check: char='{char}', name='{name}', key_name='{key_name}'"
        )

        # Get current mode for edit mode checks
        current_mode = self.state.get_mode()

        # 'e' key: Enter edit mode from STATUS_FOCUS
        if char == "e" or name == "e":
            if current_mode == NavigationMode.STATUS_FOCUS:
                logger.info("[NAV] 'e' pressed - entering EDIT mode")
                await self._enter_edit_mode()
                return True
            else:
                logger.info(
                    f"[NAV] 'e' pressed but not in STATUS_FOCUS (mode={current_mode.value})"
                )

        # Edit mode only shortcuts
        if current_mode == NavigationMode.EDIT:
            # Check both char and name for flexibility
            if char == "a" or char == "+" or name == "a":
                # Add widget at selected slot
                logger.info("[NAV] Matched 'a' or '+' -> handle_add_widget_at_slot")
                await self.handle_add_widget_at_slot()
                return True
            elif char == "d" or char == "-" or name == "d":
                # Remove selected widget (only in edit mode)
                logger.info("[NAV] Matched 'd' or '-' -> handle_remove_widget")
                await self.handle_remove_widget()
                return True
            elif char == "c" or name == "c":
                # Toggle widget color (only in edit mode)
                logger.info("[NAV] Matched 'c' -> handle_toggle_widget_color")
                await self.handle_toggle_widget_color()
                return True
            elif char == "x" or name == "x":
                # Toggle widget effect (only in edit mode)
                logger.info("[NAV] Matched 'x' -> handle_toggle_widget_effect")
                await self.handle_toggle_widget_effect()
                return True
            elif char == "A" or name == "A":
                # Add new row (Shift+A)
                logger.info("[NAV] Matched 'A' -> handle_add_row")
                await self.handle_add_row()
                return True
            elif char == "R" or name == "R":
                # Remove/hide current row (Shift+R) - only if empty
                logger.info("[NAV] Matched 'R' -> handle_remove_row")
                await self.handle_remove_row()
                return True
        else:
            # Not in edit mode - log why we're not handling these keys
            if char in ("d", "-", "c", "x") or name in ("d", "c", "x"):
                logger.info(f"[NAV] '{char}' pressed but not in EDIT mode - ignoring")

        # Unhandled key
        return False

    async def handle_arrow_key(self, key: str) -> bool:
        """Handle arrow key navigation.

        For toggle widgets that are activated:
        - Left/Right cycles through states
        - Up/Down exits toggle interaction and resumes normal navigation

        For edit mode navigation:
        - Left/Right moves through slot-widget-slot pattern
        - Up/Down moves between rows

        For normal navigation (STATUS_FOCUS):
        - Arrows move selection between widgets
        - Home/End jump to first/last widget

        Args:
            key: Key identifier ("up", "down", "left", "right", "home", "end")

        Returns:
            True if navigation was handled, False otherwise
        """
        if not self.state.is_active():
            return False

        # Check if a toggle widget is currently active for state cycling
        if self.state.is_interacting():
            active_widget_id = self.state.active_widget_id
            if active_widget_id:
                widget_info = await self._get_active_widget_info(active_widget_id)
                if widget_info and widget_info.get("interaction_type") == "toggle":
                    return await self._handle_toggle_arrow_key(key, widget_info)

        # Check if in EDIT mode for slot navigation
        current_mode = self.state.get_mode()
        is_edit_mode = current_mode == NavigationMode.EDIT

        # Get layout bounds (include hidden rows in edit mode)
        max_row, max_widget = self._get_layout_bounds(include_hidden=is_edit_mode)

        if key == "up":
            delta_row = -1
            if is_edit_mode:
                # In edit mode: move with slot consideration
                await self.state.move_selection_with_slot(delta_row, 0)
                await self._clamp_selection_to_bounds(max_row, max_widget)
            else:
                # In status focus: normal widget navigation
                await self.state.move_selection(-1, 0)
                await self._clamp_selection_to_bounds(max_row, max_widget)
        elif key == "down":
            delta_row = 1
            if is_edit_mode:
                # In edit mode: move with slot consideration
                await self.state.move_selection_with_slot(delta_row, 0)
                await self._clamp_selection_to_bounds(max_row, max_widget)
            else:
                # In status focus: normal widget navigation
                await self.state.move_selection(1, 0)
                await self._clamp_selection_to_bounds(max_row, max_widget)
        elif key == "left":
            if is_edit_mode:
                # In edit mode: move to previous slot or widget
                await self.state.move_selection_with_slot(0, -1)
                await self._clamp_selection_to_bounds(max_row, max_widget)
            else:
                # In status focus: move to previous widget
                await self.state.move_selection(0, -1)
                await self._clamp_selection_to_bounds(max_row, max_widget)
        elif key == "right":
            if is_edit_mode:
                # In edit mode: move to next slot or widget
                await self.state.move_selection_with_slot(0, 1)
                await self._clamp_selection_to_bounds(max_row, max_widget)
            else:
                # In status focus: move to next widget
                await self.state.move_selection(0, 1)
                await self._clamp_selection_to_bounds(max_row, max_widget)
        elif key == "home":
            # Jump to first position
            old_row, old_widget = self.state.get_selection()
            if is_edit_mode:
                # In edit mode: go to first slot
                await self.state.set_slot_selection(0, 0)
            else:
                # In status focus: go to first widget
                await self.state.set_selection(0, 0)
            await self._emit_widget_selected(old_row, old_widget, 0, 0, key)
            await self.render_navigation_state()
            return True
        elif key == "end":
            # Jump to last position
            old_row, old_widget = self.state.get_selection()
            if is_edit_mode:
                # In edit mode: go to last slot
                # Slot index = number of widgets + 1 (slot after last widget)
                row, _ = self.state.get_selection()
                row_obj = self._get_row_at_index(row, include_hidden=True)
                if row_obj and hasattr(row_obj, "widgets"):
                    num_widgets = len(row_obj.widgets)
                    await self.state.set_slot_selection(row, num_widgets)
                else:
                    # Fallback: go to last row, first slot
                    await self.state.set_slot_selection(max_row, 0)
            else:
                # In status focus: go to last widget
                await self.state.set_selection(max_row, max_widget)
            await self._emit_widget_selected(
                old_row, old_widget, max_row, max_widget, key
            )
            await self.render_navigation_state()
            return True
        else:
            return False

        # Render updated selection
        await self.render_navigation_state()

        return True

    async def _get_active_widget_info(self, widget_id: str) -> Optional[Dict[str, Any]]:
        """Get widget info for an active widget by ID.

        Args:
            widget_id: Widget ID to look up

        Returns:
            Widget info dict or None if not found
        """
        if not self.widget_registry:
            return None

        status_widget = self.widget_registry.get(widget_id)
        if status_widget:
            return {
                "id": widget_id,
                "interaction_type": getattr(status_widget, "interaction_type", "none"),
                "command": getattr(status_widget, "command", None),
                "on_activate": getattr(status_widget, "on_activate", None),
                "states": getattr(status_widget, "states", None),
            }
        return None

    async def _handle_toggle_arrow_key(
        self, key: str, widget_info: Dict[str, Any]
    ) -> bool:
        """Handle arrow key when toggle widget is active.

        Args:
            key: Key identifier ("up", "down", "left", "right", "home", "end")
            widget_info: Widget information dictionary

        Returns:
            True if key was handled, False otherwise
        """
        widget_id = widget_info.get("id", "")

        # Left/Right: cycle states
        if key == "left":
            logger.info(f"Toggle {widget_id}: cycling to previous state")
            # Record state snapshot before toggle
            await self._record_action()
            await self._cycle_toggle_state(widget_info, direction="prev")
            await self.render_navigation_state()
            return True
        elif key == "right":
            logger.info(f"Toggle {widget_id}: cycling to next state")
            # Record state snapshot before toggle
            await self._record_action()
            await self._cycle_toggle_state(widget_info, direction="next")
            await self.render_navigation_state()
            return True
        # Up/Down: exit toggle interaction, resume normal navigation
        elif key in ("up", "down"):
            logger.info(f"Exiting toggle interaction for {widget_id}")
            await self.state.deactivate_widget()
            await self.render_navigation_state()
            return True
        # Home/End: exit toggle interaction and jump
        elif key in ("home", "end"):
            await self.state.deactivate_widget()
            # Let the normal handler process home/end after deactivation
            return False

        return False

    async def _cycle_toggle_state(
        self, widget_info: Dict[str, Any], direction: str = "next"
    ) -> Optional[str]:
        """Cycle toggle widget state.

        Args:
            widget_info: Widget information dictionary
            direction: "next" or "prev"

        Returns:
            New state, or None if error
        """
        widget_id = widget_info.get("id", "")
        handler = widget_info.get("on_activate")

        if not handler:
            logger.warning(f"Toggle widget {widget_id} has no activation handler")
            return None

        try:
            # Get current widget position for config persistence
            row, widget_index = self.state.get_selection()

            # Store direction and position in interaction_data for the handler to use
            await self.state.set_interaction_data(
                {"direction": direction, "row_id": row, "widget_index": widget_index}
            )

            context = self._get_widget_context()
            result = await handler(widget_id, context)

            # Emit action executed event
            new_state = result.get("new_state") if isinstance(result, dict) else None
            await self._emit_action_executed(widget_id, "toggle", result)

            return new_state

        except Exception as e:
            logger.error(
                f"Error cycling toggle state for {widget_id}: {e}", exc_info=True
            )
            await self._emit_action_executed(widget_id, "toggle", None, error=str(e))
            return None

    async def handle_enter_key(self) -> bool:
        """Handle Enter key to activate selected widget or add widget at slot.

        In EDIT mode:
        - On slot: Show widget picker to insert at that slot position
        - On widget: Activate widget (for testing)

        In STATUS_FOCUS mode:
        - On widget: Activate widget (modal, toggle, etc.)

        Returns:
            True if action was handled, False otherwise
        """
        if not self.state.is_active():
            return False

        # Don't activate if already interacting
        if self.state.is_interacting():
            logger.debug("Widget interaction already active")
            return False

        # Check current mode and selection type
        current_mode = self.state.get_mode()
        row, selection_type, index = self.state.get_full_selection()

        # In edit mode, handle slots specially
        if current_mode == NavigationMode.EDIT:
            if selection_type == SelectionType.SLOT:
                # On slot: show widget picker to insert at slot position
                logger.info(f"Enter on slot at row={row}, slot_index={index}")
                return await self.handle_add_widget_at_slot()
            # Fall through to widget activation for widgets in edit mode

        # Get selected widget info (for widget selection)
        if selection_type == SelectionType.WIDGET:
            widget_idx = index
            widget_info = self._get_widget_at_position(row, widget_idx)

            if not widget_info:
                logger.warning(f"No widget at position ({row}, {widget_idx})")
                return False

            widget_id = widget_info.get("id")
            logger.info(f"Activating widget: {widget_id}")

            # Emit activation event
            await self.event_bus.emit_with_hooks(
                EventType.WIDGET_ACTIVATED,
                {
                    "widget_id": widget_id,
                    "row": row,
                    "widget_index": widget_idx,
                    "interaction_type": widget_info.get("interaction_type", "none"),
                },
                "StatusNavigationManager.handle_enter_key",
            )

            # Mark widget as active
            await self.state.activate_widget(widget_id)

            # Trigger widget activation
            await self.activate_widget(widget_info)

            return True

        return False

    async def handle_space_key(self) -> bool:
        """Handle Space key for quick toggle (toggle widgets only).

        Returns:
            True if toggle was handled, False otherwise
        """
        if not self.state.is_active():
            return False

        # Get selected widget info
        row, widget_idx = self.state.get_selection()
        widget_info = self._get_widget_at_position(row, widget_idx)

        if not widget_info:
            return False

        # Only toggle if widget is toggle-type
        if widget_info.get("interaction_type") == "toggle":
            widget_id = widget_info.get("id")
            logger.info(f"Quick toggle widget: {widget_id}")

            # Record state snapshot before toggle
            await self._record_action()

            # For Space quick-toggle: cycle state directly (no interactive mode)
            # This is different from Enter which puts widget in interactive mode for arrows
            await self._cycle_toggle_state(widget_info, direction="next")

            # Re-render to show updated state
            await self.render_navigation_state()

            return True

        return False

    async def handle_escape_key(self) -> bool:
        """Handle ESC key - always exits to Input Mode.

        This implements single-level exit: ESC from Navigation Mode
        returns to Input Mode (not to a parent navigation state).

        Returns:
            True if navigation was exited
        """
        if self.state.is_active():
            await self.exit_navigation_mode()
            return True
        return False

    async def _handle_digit_key(self, digit: int) -> bool:
        """Quick jump to widget at position (digit-1) in current row.

        Handles digit keys 1-9 for quick navigation to widgets within the
        currently selected row. Invalid widget positions are handled
        gracefully - the key is consumed but no jump occurs.

        Args:
            digit: Digit key pressed (1-9)

        Returns:
            True if key was handled (always returns True to consume the key)
        """
        if not self.state.is_active():
            return False

        # Calculate widget index (1-9 maps to 0-8)
        widget_idx = digit - 1

        # Get current row
        current_row, _ = self.state.get_selection()
        row_obj = self._get_row_at_index(current_row, include_hidden=False)

        if not row_obj or not hasattr(row_obj, "widgets"):
            logger.debug(
                f"[NAV] Cannot jump to widget {digit}: row {current_row} not found"
            )
            # Consume the key anyway to prevent exiting navigation mode
            return True

        # Check if widget index is valid for this row
        if widget_idx < 0 or widget_idx >= len(row_obj.widgets):
            logger.debug(
                f"[NAV] Cannot jump to widget {digit}: "
                f"row {current_row} has {len(row_obj.widgets)} widgets"
            )
            # Consume the key anyway to prevent exiting navigation mode
            return True

        # Jump to widget
        old_row, old_widget = self.state.get_selection()
        await self.state.set_widget_selection(current_row, widget_idx)

        # Ensure selection type is WIDGET
        await self.state.set_selection_type(SelectionType.WIDGET)

        logger.info(
            f"[NAV] Quick jump to widget {digit}: "
            f"row={current_row}, widget={widget_idx}"
        )

        # Emit event and render
        await self._emit_widget_selected(
            old_row, old_widget, current_row, widget_idx, f"digit_{digit}"
        )
        await self.render_navigation_state()

        return True

    # =========================================================================
    # =========================================================================
    # MODE TRANSITION METHODS (for Edit Mode UI spec)
    # =========================================================================

    async def transition_to_status_focus(self) -> bool:
        """Transition from INPUT to STATUS_FOCUS mode (Tab key handler).

        Returns:
            True if successfully transitioned to STATUS_FOCUS mode
        """
        logger.info("[NAV] transition_to_status_focus called")

        # Check if already in STATUS_FOCUS or EDIT mode
        current_mode = self.state.get_mode()
        if current_mode != NavigationMode.INPUT:
            logger.debug(
                f"Already in {current_mode.value} mode, not transitioning to STATUS_FOCUS"
            )
            return False

        # Check if LLM is streaming or messages are queued
        if self.coordinator.is_writing_messages():
            logger.warning("Cannot enter navigation during message display")
            return False

        # Check if in alternate buffer (modal/fullscreen active)
        if self.coordinator._in_alternate_buffer:
            logger.warning("Cannot enter navigation during modal/fullscreen mode")
            return False

        # Transition to STATUS_FOCUS mode
        await self.state.transition_to_status_focus()

        # Signal coordinator: navigation active, pause message renders
        self.coordinator.set_navigation_active(True)

        # Render initial selection
        await self.render_navigation_state()

        logger.info("Transitioned to STATUS_FOCUS mode")
        return True

    async def transition_to_edit_mode(self) -> bool:
        """Transition from STATUS_FOCUS to EDIT mode ('e' key handler).

        Returns:
            True if successfully transitioned to EDIT mode
        """
        logger.info("[NAV] transition_to_edit_mode called")

        # Check if in STATUS_FOCUS mode
        current_mode = self.state.get_mode()
        if current_mode != NavigationMode.STATUS_FOCUS:
            logger.warning(f"Cannot transition to EDIT mode from {current_mode.value}")
            return False

        # Transition to EDIT mode
        await self.state.transition_to_edit_mode()

        # Render edit mode state
        await self.render_navigation_state()

        logger.info("Transitioned to EDIT mode")
        return True

    async def transition_to_input_mode(self) -> None:
        """Transition from any mode to INPUT mode (Esc key handler).

        Implements cascading exit:
        - EDIT -> STATUS_FOCUS (first Esc)
        - STATUS_FOCUS -> INPUT (second Esc)

        For single-level exit from any mode directly to INPUT, use
        exit_navigation_mode() instead.
        """
        current_mode = self.state.get_mode()

        if current_mode == NavigationMode.EDIT:
            # First Esc: EDIT -> STATUS_FOCUS
            logger.info("[NAV] Esc in EDIT mode -> transitioning to STATUS_FOCUS")
            await self.state.transition_to_status_focus_from_edit()
            await self.render_navigation_state()
        elif current_mode == NavigationMode.STATUS_FOCUS:
            # Second Esc: STATUS_FOCUS -> INPUT
            logger.info("[NAV] Esc in STATUS_FOCUS mode -> transitioning to INPUT")
            await self.state.transition_to_input_mode()

            # Signal coordinator: navigation done, resume message renders
            self.coordinator.set_navigation_active(False)

            # Restore normal rendering
            await self.renderer.render_active_area()

            logger.info("Transitioned to INPUT mode")
        else:
            logger.debug("Already in INPUT mode, ignoring Esc")

    # =========================================================================
    def _get_row_at_index(
        self, row_idx: int, include_hidden: bool = False
    ) -> Optional[Any]:
        """Get row object at the given index.

        Args:
            row_idx: Row index (0-based)
            include_hidden: If True, include hidden rows (for edit mode)

        Returns:
            Row object or None
        """
        if not self.layout:
            return None

        try:
            if hasattr(self.layout, "rows"):
                if include_hidden:
                    # In edit mode, use all rows
                    rows = self.layout.rows
                else:
                    # Normal mode, only visible rows
                    rows = [r for r in self.layout.rows if getattr(r, "visible", True)]
                if 0 <= row_idx < len(rows):
                    return rows[row_idx]
        except Exception as e:
            logger.warning(f"Error getting row at index {row_idx}: {e}")

        return None

    async def activate_widget(self, widget_info: Dict[str, Any]) -> None:
        """Activate widget (modal/toggle/edit) with coordinator awareness.

        Args:
            widget_info: Dictionary containing widget information including:
                - id: Widget ID
                - interaction_type: Type of interaction ("modal", "toggle", "inline_edit", "action")
                - on_activate: Optional async activation handler
        """
        widget_id = widget_info.get("id")
        interaction_type = widget_info.get("interaction_type", "none")
        command = widget_info.get("command")

        # For command widgets: execute the slash command
        if interaction_type == "command" and command:
            logger.info(f"[CMD] Executing command for widget {widget_id}: {command}")
            try:
                # Exit navigation mode first
                await self.exit_navigation_mode()

                # Get the app through the renderer chain to access command execution
                app = self._get_app()
                if app and hasattr(app, "input_handler") and app.input_handler:
                    # Note: attribute is _command_mode_handler (with underscore prefix)
                    cmd_handler = getattr(
                        app.input_handler, "_command_mode_handler", None
                    )
                    if cmd_handler and hasattr(cmd_handler, "execute_command_string"):
                        success = await cmd_handler.execute_command_string(command)
                        logger.info(f"[CMD] Command execution result: {success}")
                    else:
                        logger.warning(
                            "[CMD] Cannot execute command - _command_mode_handler not available"
                        )
                else:
                    logger.warning(
                        "[CMD] Cannot execute command - app.input_handler not available"
                    )

                # Emit action executed event
                await self._emit_action_executed(
                    widget_id, interaction_type, {"command": command}
                )
            except Exception as e:
                logger.error(
                    f"[CMD] Error executing command {command}: {e}", exc_info=True
                )
            return

        # For modals: _show_modal_result owns its own coordinator lifecycle
        elif interaction_type == "modal":
            try:
                # Call widget activation handler if present
                handler = widget_info.get("on_activate")
                result = None
                if handler:
                    context = self._get_widget_context()
                    logger.debug(f"[MODAL] Got context: {type(context)}")
                    result = await handler(widget_id, context)
                    logger.debug(f"[MODAL] Handler returned: {type(result)}")
                    await self._show_modal_result(result)
                    logger.debug("[MODAL] Modal result shown")

                # Emit action executed event
                await self._emit_action_executed(widget_id, interaction_type, result)
            except Exception as e:
                logger.error(
                    f"[MODAL] Error activating widget {widget_id}: {e}", exc_info=True
                )
            finally:
                await self.exit_navigation_mode()
                logger.debug("[MODAL] Cleanup complete")

        elif interaction_type == "toggle":
            # Toggle widgets: Enter puts into interactive mode for arrow key cycling
            # DON'T call handler on Enter - arrows will trigger state changes
            await self.state.activate_widget(widget_id)
            logger.info(
                f"[TOGGLE] Widget {widget_id} entered toggle interaction mode (awaiting arrow keys)"
            )

            # Re-render to show interactive state indicator
            await self.render_navigation_state()

            # Note: handler is NOT called here - it's called when arrow keys cycle the state
            # This allows Enter to "activate" the toggle for fine-grained control with arrows

        elif interaction_type == "inline_edit":
            # Inline edit widgets edit in place
            handler = widget_info.get("on_activate")
            result = None
            if handler:
                context = self._get_widget_context()
                result = await handler(widget_id, context)
                await self._show_inline_editor(result)

            # Emit action executed event
            await self._emit_action_executed(widget_id, interaction_type, result)

        elif interaction_type == "action":
            # Action widgets execute commands
            handler = widget_info.get("on_activate")
            result = None
            if handler:
                context = self._get_widget_context()
                result = await handler(widget_id, context)

            # Emit action executed event
            await self._emit_action_executed(widget_id, interaction_type, result)

        elif interaction_type == "none" or interaction_type is None:
            # Non-interactive widget - do nothing silently
            # This is expected for widgets like clock, stats, status that are display-only
            logger.debug(f"Widget {widget_id} has no interaction type (display-only)")

        else:
            logger.warning(f"Unknown interaction type: {interaction_type}")
            await self._emit_action_executed(
                widget_id,
                interaction_type,
                None,
                error=f"Unknown interaction type: {interaction_type}",
            )

    def _has_widgets_with_effects(self) -> bool:
        """Check if any widget in the layout has an effect configured.

        Returns:
            True if at least one widget has an effect other than 'none'
        """
        if not self.layout:
            return False

        for row in self.layout.rows:
            if not row.visible:
                continue
            for widget in row.widgets:
                effect = getattr(widget, "effect", "none")
                if effect != "none":
                    return True
        return False

    async def _start_effect_animation(self) -> None:
        """Start background task for animating widget effects."""
        if self._shimmer_task is not None:
            return  # Already running

        if not self._has_widgets_with_effects():
            return  # No effects to animate

        self._shimmer_running = True
        self._shimmer_task = asyncio.create_task(self._effect_animation_loop())
        logger.debug("Started effect animation loop")

    async def _stop_effect_animation(self) -> None:
        """Stop the effect animation background task."""
        self._shimmer_running = False
        if self._shimmer_task is not None:
            self._shimmer_task.cancel()
            try:
                await self._shimmer_task
            except asyncio.CancelledError:
                logger.debug("Effect animation task cancelled")
            self._shimmer_task = None
            logger.debug("Stopped effect animation loop")

    async def _effect_animation_loop(self) -> None:
        """Background loop that periodically re-renders for effect animation."""
        try:
            while self._shimmer_running:
                # Re-render to update effect animation
                await self.render_navigation_state()
                # ~10 FPS for subtle animation
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.debug("Effect animation loop cancelled")
        except Exception as e:
            logger.error(f"Effect animation loop error: {e}")

    async def render_navigation_state(self) -> None:
        """Render navigation state with selection highlight.

        This triggers a status re-render with the current selection.
        """
        logger.info("[NAV] render_navigation_state: invalidating cache")
        # Invalidate renderer cache to force fresh render
        self.renderer.invalidate_render_cache()

        # Trigger render with navigation state
        logger.info("[NAV] render_navigation_state: calling render_active_area()")
        await self.renderer.render_active_area()
        logger.info("[NAV] render_navigation_state: render_active_area() complete")

    def _get_layout_bounds(self, include_hidden: bool = False) -> Tuple[int, int]:
        """Get maximum row and widget indices from layout.

        Args:
            include_hidden: If True, include hidden rows in bounds (for edit mode).
                           If False, only count visible rows (for normal navigation).

        Returns:
            Tuple of (max_row, max_widget_index)
        """
        if not self.layout:
            return (0, 0)

        try:
            if hasattr(self.layout, "rows"):
                # Get rows based on mode
                if include_hidden:
                    # Edit mode: all rows are visible
                    rows = self.layout.rows
                else:
                    # Normal mode: only visible rows
                    rows = [r for r in self.layout.rows if getattr(r, "visible", True)]

                max_row = max(0, len(rows) - 1)

                # Get max widgets in current row (for horizontal navigation)
                current_row, _ = (
                    self.state.selected_row,
                    self.state.selected_widget_index,
                )
                max_widget = 0
                if 0 <= current_row < len(rows):
                    row = rows[current_row]
                    if hasattr(row, "widgets"):
                        max_widget = max(0, len(row.widgets) - 1)
                return (max_row, max_widget)
            elif hasattr(self.layout, "__len__"):
                max_row = len(self.layout) - 1
                return (max_row, 0)
        except Exception as e:
            logger.warning(f"Error getting layout bounds: {e}")

        return (0, 0)

    async def _clamp_selection_to_bounds(self, max_row: int, max_widget: int) -> None:
        """Clamp current selection to be within bounds.

        Preserves selection type (WIDGET vs SLOT) when clamping in EDIT mode.

        Args:
            max_row: Maximum row index
            max_widget: Maximum widget index for current row
        """
        # Get full selection info including type
        row, selection_type, index = self.state.get_full_selection()
        clamped_row = min(row, max_row)

        if selection_type == SelectionType.SLOT:
            # For slots: max slot index = num widgets in row (slots are 0 to N for N widgets)
            # max_widget is the max widget index, so max slot = max_widget + 1
            max_slot = max_widget + 1 if max_widget >= 0 else 0
            clamped_index = min(index, max_slot)
            if clamped_row != row or clamped_index != index:
                await self.state.set_slot_selection(clamped_row, clamped_index)
                logger.debug(
                    f"Clamped slot selection: ({row}, {index}) -> ({clamped_row}, {clamped_index})"
                )
        else:
            # For widgets: clamp to max widget index
            clamped_index = min(index, max_widget)
            if clamped_row != row or clamped_index != index:
                await self.state.set_widget_selection(clamped_row, clamped_index)
                logger.debug(
                    f"Clamped widget selection: ({row}, {index}) -> ({clamped_row}, {clamped_index})"
                )

    def _get_widget_at_position(
        self, row: int, widget_index: int, include_hidden: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Get widget information at the specified position.

        Args:
            row: Row index (0-based)
            widget_index: Widget index within row
            include_hidden: If True, include hidden rows (for edit mode)

        Returns:
            Widget info dict with id, interaction_type, on_activate or None if not found
        """
        if not self.layout:
            return None

        try:
            # Navigate layout structure to find widget_id from WidgetConfig
            if hasattr(self.layout, "rows"):
                # Get rows based on mode
                if include_hidden:
                    rows = self.layout.rows
                else:
                    rows = [r for r in self.layout.rows if getattr(r, "visible", True)]
                if 0 <= row < len(rows):
                    row_obj = rows[row]
                    if hasattr(row_obj, "widgets"):
                        if 0 <= widget_index < len(row_obj.widgets):
                            widget_config = row_obj.widgets[widget_index]
                            # WidgetConfig has {id, width}
                            widget_id = getattr(widget_config, "id", None) or getattr(
                                widget_config, "widget_id", None
                            )

                            if not widget_id:
                                return None

                            # Look up actual StatusWidget from registry for interactive properties
                            if self.widget_registry:
                                status_widget = self.widget_registry.get(widget_id)
                                if status_widget:
                                    return {
                                        "id": widget_id,
                                        "interaction_type": getattr(
                                            status_widget, "interaction_type", "none"
                                        ),
                                        "command": getattr(
                                            status_widget, "command", None
                                        ),
                                        "on_activate": getattr(
                                            status_widget, "on_activate", None
                                        ),
                                    }
                                else:
                                    logger.debug(
                                        f"Widget {widget_id} not found in registry"
                                    )
                            else:
                                logger.warning(
                                    "Widget registry not set, cannot get widget info"
                                )

                            # Fallback: return minimal info if registry not available
                            return {
                                "id": widget_id,
                                "interaction_type": "none",
                                "command": None,
                                "on_activate": None,
                            }
        except Exception as e:
            logger.warning(f"Error getting widget at position: {e}")

        return None

    def _get_app(self) -> Any:
        """Get the application instance.

        Returns:
            TerminalLLMChat app instance or None
        """
        # Try direct app reference first (wired in application.py)
        if hasattr(self, "app") and self.app is not None:
            return self.app

        # Fallback: Try renderer.terminal_renderer.app chain
        if hasattr(self.renderer, "terminal_renderer"):
            term_renderer = self.renderer.terminal_renderer
            if hasattr(term_renderer, "app"):
                return term_renderer.app

        # Fallback: Try renderer.app directly
        if hasattr(self.renderer, "app"):
            return self.renderer.app

        return None

    def _get_widget_context(self) -> Any:
        """Get context object for widget activation.

        Returns:
            WidgetContext object with services (llm_service, profile_manager, etc.)
            Also includes navigation_state for toggle widgets to access direction info.
        """
        # The layout_renderer has the WidgetContext set by the application
        # See: application.py lines 284-291 where WidgetContext is created
        # and self.layout_renderer.set_context(widget_context) is called
        if hasattr(self.renderer, "layout_renderer"):
            layout_renderer = self.renderer.layout_renderer
            if layout_renderer and hasattr(layout_renderer, "_context"):
                ctx = layout_renderer._context
                if ctx:
                    # Add navigation_state reference for toggle/inline edit widgets
                    ctx.navigation_state = self.state
                    # Ensure layout_manager is accessible (for widget config persistence)
                    if not hasattr(ctx, "layout_manager") or ctx.layout_manager is None:
                        ctx.layout_manager = self.layout_manager
                    return ctx

        # Fallback: check if renderer has direct context
        if hasattr(self.renderer, "context"):
            ctx = self.renderer.context
            if ctx:
                ctx.navigation_state = self.state
                if not hasattr(ctx, "layout_manager") or ctx.layout_manager is None:
                    ctx.layout_manager = self.layout_manager
            return ctx

        return None

    async def get_status(self) -> Dict[str, Any]:
        """Get current navigation status for debugging.

        Returns:
            Dictionary with navigation state information
        """
        active = self.state.is_active()
        interacting = self.state.is_interacting()
        row, widget_idx = self.state.get_selection()

        return {
            "navigation_active": active,
            "interaction_active": interacting,
            "selected_row": row,
            "selected_widget_index": widget_idx,
            "active_widget_id": self.state.active_widget_id,
            "history_length": len(self.state.navigation_history),
        }

    async def _emit_widget_selected(
        self, old_row: int, old_widget: int, new_row: int, new_widget: int, key: str
    ) -> None:
        """Emit WIDGET_SELECTED event when selection changes.

        Args:
            old_row: Previous row index
            old_widget: Previous widget index
            new_row: New row index
            new_widget: New widget index
            key: Key that triggered the selection change
        """
        # Only emit if selection actually changed
        if old_row == new_row and old_widget == new_widget:
            return

        # Get widget info for old and new selections
        old_widget_info = self._get_widget_at_position(old_row, old_widget)
        new_widget_info = self._get_widget_at_position(new_row, new_widget)

        await self.event_bus.emit_with_hooks(
            EventType.WIDGET_SELECTED,
            {
                "old_widget_id": old_widget_info.get("id") if old_widget_info else None,
                "new_widget_id": new_widget_info.get("id") if new_widget_info else None,
                "old_position": {"row": old_row, "widget_index": old_widget},
                "new_position": {"row": new_row, "widget_index": new_widget},
                "key": key,
            },
            "StatusNavigationManager._emit_widget_selected",
        )

    async def _emit_action_executed(
        self,
        widget_id: Optional[str],
        interaction_type: str,
        result: Any,
        error: Optional[str] = None,
    ) -> None:
        """Emit WIDGET_ACTION_EXECUTED event when widget action completes.

        Args:
            widget_id: ID of the widget that executed the action
            interaction_type: Type of interaction that was executed
            result: Result from the action handler
            error: Optional error message if action failed
        """
        await self.event_bus.emit_with_hooks(
            EventType.WIDGET_ACTION_EXECUTED,
            {
                "widget_id": widget_id,
                "interaction_type": interaction_type,
                "result": result,
                "error": error,
                "success": error is None,
            },
            "StatusNavigationManager._emit_action_executed",
        )

    async def _check_first_run_help(self) -> None:
        """Mark first-run help as shown.

        The navigation mode indicator is self-explanatory:
        'NAVIGATE   ←→↑↓:move  Enter:act  e:edit  Esc:exit'

        No modal or additional help message needed - the indicator
        provides all necessary information inline.
        """
        logger.debug(
            f"_check_first_run_help called: config={self.config is not None}, checked={self._first_run_help_checked}"
        )

        if not self.config:
            logger.warning("Cannot mark first-run help: config not available")
            return

        if self._first_run_help_checked:
            logger.debug("First-run help already checked this session, skipping")
            return

        try:
            # Just mark the flag - no modal/message needed
            # Navigation mode indicator is self-explanatory
            self.config.set("status.navigation.help_shown", True)
            self.config.save()
            logger.info(
                "First-run help flag set (navigation mode indicator is self-explanatory)"
            )
            self._first_run_help_checked = True

        except Exception as e:
            logger.error(f"Error setting first-run help flag: {e}", exc_info=True)
