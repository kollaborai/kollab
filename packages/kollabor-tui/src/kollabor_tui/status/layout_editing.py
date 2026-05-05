"""Layout editing operations for status widget navigation.

Extracted from StatusNavigationManager to keep the navigation manager
focused on keyboard routing and state management.
"""

import logging
import time
from typing import Any, Dict

from .constants import WIDGET_BG_COLOR_NAMES
from .navigation_state import NavigationMode, SelectionType, UndoAction

logger = logging.getLogger(__name__)


class LayoutEditingMixin:
    """Mixin providing layout editing operations for StatusNavigationManager.

    Methods here modify the status widget layout: adding/removing widgets,
    managing rows, toggling colors/effects, and undo support.

    Type annotations below declare attributes provided by the host class
    (StatusNavigationManager) at runtime via multi-inheritance. Bare
    annotations do NOT create class variables, so runtime behavior is
    unchanged — they only inform mypy of the expected interface.
    """

    # Instance attributes provided by host class
    layout_manager: Any
    state: Any
    widget_registry: Any
    renderer: Any
    layout: Any

    # Methods provided by host class or sibling mixins
    render_navigation_state: Any
    _get_row_at_index: Any
    _get_widget_at_position: Any
    _get_layout_bounds: Any
    _show_widget_picker_modal: Any
    _show_widget_picker: Any
    _has_widgets_with_effects: Any
    _start_effect_animation: Any
    _stop_effect_animation: Any

    async def handle_add_widget(self) -> bool:
        """Handle 'a' or '+' key - show widget picker with row selection.

        Shows a modal with available widgets and allows Tab to change target row.

        Returns:
            True if widget was added, False otherwise
        """
        if not self.layout_manager:
            logger.warning("Cannot add widget - no layout manager")
            return False

        # Get current row index for initial selection
        current_row_idx, _ = self.state.get_selection()

        # Force reload layout from config for accurate visible rows
        fresh_layout = self.layout_manager.reload()

        # Get all visible rows for Tab cycling
        visible_rows = []
        if fresh_layout and hasattr(fresh_layout, "rows"):
            visible_rows = [r for r in fresh_layout.rows if getattr(r, "visible", True)]

        if not visible_rows:
            logger.warning("No visible rows to add widget to")
            return False

        # Get all available widgets from registry
        available_widgets = []
        if self.widget_registry:
            for widget in self.widget_registry.get_all():
                widget_id = widget.id
                desc = getattr(widget, "description", widget_id)
                available_widgets.append(
                    {
                        "id": widget_id,
                        "label": widget_id,
                        "description": desc,
                    }
                )

        if not available_widgets:
            logger.info("No available widgets to add")
            return False

        # Show widget picker modal with row selection
        result = await self._show_widget_picker_modal(
            available_widgets,
            "Add Widget",
            initial_row_idx=current_row_idx,
            visible_rows=visible_rows,
        )

        if result:
            widget_info = result.get("widget", {})
            widget_id = widget_info.get("id")
            target_row_id = result.get("target_row_id")

            if widget_id and target_row_id:
                # Add widget to target row using layout manager
                success = self.layout_manager.add_widget_to_row(
                    target_row_id, widget_id
                )
                if success:
                    # Save and refresh
                    self.layout_manager.save()
                    self.layout = self.layout_manager.get_layout()
                    await self.render_navigation_state()
                    logger.info(f"Added widget '{widget_id}' to row {target_row_id}")
                    return True

        return False

    async def handle_remove_widget(self) -> bool:
        """Handle 'd' or '-' key - remove currently selected widget.

        Shows confirmation modal if deleting the last widget in a row.

        Returns:
            True if widget was removed, False otherwise
        """
        if not self.layout_manager:
            logger.warning("Cannot remove widget - no layout manager")
            return False

        # Get full selection info including type
        row, selection_type, index = self.state.get_full_selection()
        logger.debug(
            f"handle_remove_widget: row={row}, type={selection_type.value}, index={index}"
        )

        # Can only delete when a widget is selected, not a slot
        if selection_type == SelectionType.SLOT:
            logger.info("Cannot remove - slot is selected, not a widget")
            return False

        widget_idx = index  # Use the index from full selection
        current_row = self._get_row_at_index(row, include_hidden=True)
        if not current_row:
            logger.warning(f"Cannot remove widget - row {row} not found")
            return False

        # Get widget info for confirmation (include hidden rows for edit mode)
        widget_info = self._get_widget_at_position(row, widget_idx, include_hidden=True)
        if not widget_info:
            logger.warning(f"No widget at position ({row}, {widget_idx})")
            return False

        widget_id = widget_info.get("id", "unknown")
        row_id = getattr(current_row, "id", row + 1)

        # Record action for undo BEFORE making the change
        # Get widget config before deletion - DEEP COPY to preserve state
        # Record state snapshot before deletion
        await self._record_action()

        # Remove widget from row
        success = self.layout_manager.remove_widget_from_row(row_id, widget_idx)
        if success:
            # Save and refresh
            self.layout_manager.save()
            self.layout = self.layout_manager.get_layout()

            # Adjust selection if needed
            # Use include_hidden=True for edit mode (hidden rows are visible)
            max_row, max_widget = self._get_layout_bounds(include_hidden=True)

            # Adjust selection if widget index is now out of bounds
            # Use >= to handle case where deleting only widget (0 >= 0 is True)
            if widget_idx >= max_widget and max_widget >= 0:
                # Move selection to last valid widget index
                await self.state.set_selection(row, max(0, max_widget))
            elif max_widget < 0:
                # Row is now empty, move selection to slot instead
                await self.state.set_selection_type(SelectionType.SLOT)
                # Ensure slot index is valid (will be 0 for empty row)
                await self.state.set_slot_index(row, 0)

            await self.render_navigation_state()
            logger.info(f"Removed widget '{widget_id}' from row {row_id}")
            return True

        return False

    async def handle_add_row(self) -> bool:
        """Handle 'A' (Shift+A) key - add a new visible row.

        Returns:
            True if row was added, False otherwise
        """
        logger.info("[NAV] handle_add_row called")
        if not self.layout_manager:
            logger.warning("[NAV] Cannot add row - no layout manager")
            return False

        logger.info(f"[NAV] Current rows: {len(self.layout_manager.get_layout().rows)}")

        # Add new row using layout manager
        new_row_id = self.layout_manager.add_new_row()
        logger.info(f"[NAV] add_new_row returned: {new_row_id}")

        if new_row_id:
            # Make the new row visible
            vis_result = self.layout_manager.set_row_visibility(new_row_id, True)
            logger.info(
                f"[NAV] set_row_visibility({new_row_id}, True) returned: {vis_result}"
            )
            # Save and refresh
            save_result = self.layout_manager.save()
            logger.info(f"[NAV] save() returned: {save_result}")
            self.layout = self.layout_manager.get_layout()
            await self.render_navigation_state()
            logger.info(f"[NAV] Added new row {new_row_id}")
            return True
        else:
            logger.info("[NAV] Cannot add row - at maximum (6)")
            return False

    async def handle_remove_row(self) -> bool:
        """Handle 'R' (Shift+R) key - hide current row (only if empty).

        Returns:
            True if row was hidden, False otherwise
        """
        if not self.layout_manager:
            logger.warning("Cannot remove row - no layout manager")
            return False

        # Get current row
        row, _ = self.state.get_selection()
        current_row = self._get_row_at_index(row)
        if not current_row:
            logger.warning(f"Cannot remove row - row {row} not found")
            return False

        row_id = getattr(current_row, "id", row + 1)

        # Check if row has widgets
        if hasattr(current_row, "widgets") and current_row.widgets:
            logger.info(f"Cannot hide row {row_id} - remove widgets first")
            return False

        # Hide the row
        success = self.layout_manager.set_row_visibility(row_id, False)
        if success:
            # Save and refresh
            self.layout_manager.save()
            self.layout = self.layout_manager.get_layout()

            # Adjust selection if needed
            max_row, _ = self._get_layout_bounds()
            if row > max_row:
                await self.state.set_selection(max(0, max_row), 0)

            await self.render_navigation_state()
            logger.info(f"Hidden row {row_id}")
            return True

        return False

    async def _record_action(self) -> None:
        """Record current state snapshot before any action.

        Uses Memento Pattern - captures full state so no per-action undo logic needed.
        """
        snapshot = self.layout_manager.get_config_snapshot()
        undo_action = UndoAction(
            action_type="snapshot",  # All actions use snapshot approach now
            data={"config": snapshot},
            timestamp=time.time(),
        )
        await self.state.push_undo(undo_action)
        logger.debug("Recorded state snapshot for undo")

    async def handle_undo(self) -> bool:
        """Handle Ctrl+Z - undo last action.

        Restores previous state snapshot (Memento Pattern).
        Uses lock to prevent race conditions.

        Returns:
            True if action was undone, False otherwise
        """
        if not self.layout_manager:
            logger.warning("Cannot undo - no layout manager")
            return False

        # Check if we're in navigation mode before allowing undo
        current_mode = self.state.get_mode()
        if current_mode == NavigationMode.INPUT:
            logger.info("Undo only available in navigation mode")
            return False

        # Acquire lock for entire undo operation
        async with self.state._lock:
            action = await self.state.pop_undo()
            if not action:
                logger.info("Nothing to undo")
                return False

            logger.info("Undoing last action - restoring state snapshot")

            try:
                # Restore layout config from snapshot
                snapshot = action.data.get("config")
                if snapshot:
                    self.layout_manager.restore_config_snapshot(snapshot)
                    self.layout = self.layout_manager.get_layout()

                    # Save and refresh
                    self.layout_manager.save()
                    await self.render_navigation_state()
                    logger.info("Undo completed - state restored")
                    return True
                else:
                    logger.error("Undo snapshot missing config data")
                    return False

            except Exception as e:
                logger.error(f"Error during undo: {e}", exc_info=True)
                return False

    async def _undo_color(self, data: Dict[str, Any]) -> None:
        """Restore widget color.

        Args:
            data: Contains row_id, widget_idx, and previous_color
        """
        row_id = data["row_id"]
        widget_idx = data["widget_idx"]
        previous_color = data["previous_color"]

        row = self.layout_manager.get_row(row_id)
        if row and widget_idx < len(row.widgets):
            row.widgets[widget_idx].color = previous_color
            logger.info(
                f"Restored color for widget {widget_idx} in row {row_id} to {previous_color}"
            )

    async def _undo_delete(self, data: Dict[str, Any]) -> None:
        """Restore deleted widget.

        Args:
            data: Contains row_id, widget_idx, and widget_config (deleted widget)
        """
        row_id = data["row_id"]
        widget_idx = data["widget_idx"]
        widget_config = data["widget_config"]

        row = self.layout_manager.get_row(row_id)
        if row:
            row.widgets.insert(widget_idx, widget_config)
            logger.info(
                f"Restored deleted widget at index {widget_idx} in row {row_id}"
            )

    async def _undo_add(self, data: Dict[str, Any]) -> None:
        """Remove added widget.

        Args:
            data: Contains row_id and widget_idx (position of added widget)
        """
        row_id = data["row_id"]
        widget_idx = data["widget_idx"]

        row = self.layout_manager.get_row(row_id)
        if row and widget_idx < len(row.widgets):
            removed_widget = row.widgets.pop(widget_idx)
            logger.info(
                f"Removed added widget at index {widget_idx} in row {row_id}: {removed_widget.id}"
            )

    async def _undo_toggle(self, data: Dict[str, Any]) -> None:
        """Restore toggle state.

        Args:
            data: Contains row_id, widget_idx, and previous_state
        """
        row_id = data["row_id"]
        widget_idx = data["widget_idx"]
        previous_state = data["previous_state"]

        row = self.layout_manager.get_row(row_id)
        if row and widget_idx < len(row.widgets):
            widget = row.widgets[widget_idx]
            # Use 'toggle_state' key (correct key for toggle widgets)
            if "toggle_state" in widget.config:
                widget.config["toggle_state"] = previous_state
                logger.info(
                    f"Restored toggle state for widget {widget_idx} in row {row_id} to {previous_state}"
                )
            # Fallback to 'state' key for compatibility
            elif "state" in widget.config:
                widget.config["state"] = previous_state
                logger.info(
                    f"Restored state for widget {widget_idx} in row {row_id} to {previous_state}"
                )

    async def _undo_edit(self, data: Dict[str, Any]) -> None:
        """Restore widget config after edit.

        Args:
            data: Contains row_id, widget_idx, and previous_config
        """
        row_id = data["row_id"]
        widget_idx = data["widget_idx"]
        previous_config = data["previous_config"]

        row = self.layout_manager.get_row(row_id)
        if row and widget_idx < len(row.widgets):
            row.widgets[widget_idx].config = previous_config.copy()
            logger.info(f"Restored config for widget {widget_idx} in row {row_id}")

    async def _undo_reorder(self, data: Dict[str, Any]) -> None:
        """Restore widget order after reorder.

        Args:
            data: Contains row_id, old_idx, and new_idx
        """
        row_id = data["row_id"]
        old_idx = data["old_idx"]
        new_idx = data["new_idx"]

        row = self.layout_manager.get_row(row_id)
        if row and 0 <= new_idx < len(row.widgets):
            # Move widget back from new_idx to old_idx
            widget = row.widgets.pop(new_idx)
            row.widgets.insert(old_idx, widget)
            logger.info(
                f"Restored widget order in row {row_id}: moved from {new_idx} back to {old_idx}"
            )

    async def handle_add_widget_at_slot(self) -> bool:
        """Handle 'a' or '+' key in edit mode, or Enter on slot - add widget at selected slot.

        Shows WidgetPickerModal to browse and select from available widgets.

        Returns:
            True if widget was added, False otherwise
        """
        if not self.layout_manager:
            logger.warning("Cannot add widget - no layout manager")
            return False

        if not self.widget_registry:
            logger.warning("Cannot add widget - no widget registry")
            return False

        # Get current selection
        row, selection_type, index = self.state.get_full_selection()

        if selection_type != SelectionType.SLOT:
            logger.info("Must select a slot to add widget (use arrows to move to +)")
            return False

        # Get row ID for insertion (include hidden rows for edit mode)
        current_row = self._get_row_at_index(row, include_hidden=True)
        row_id = getattr(current_row, "id", row + 1)

        # Show widget picker modal using proper WidgetPickerModal class
        from .widget_picker import WidgetPickerModal

        picker = WidgetPickerModal(
            widget_registry=self.widget_registry,
            layout_manager=self.layout_manager,
            row_id=row_id,
            slot_index=index,
            renderer=self.renderer,
        )

        # Show picker using modal system
        selected_widget_id = await self._show_widget_picker(picker)

        if selected_widget_id:
            # Record state snapshot before adding
            await self._record_action()

            # Insert widget at slot position (index = slot position)
            success = self.layout_manager.insert_widget_at_position(
                row_id, selected_widget_id, index
            )
            if success:
                # Save and refresh
                self.layout_manager.save()
                self.layout = self.layout_manager.get_layout()
                await self.render_navigation_state()
                logger.info(
                    f"Added widget '{selected_widget_id}' to row {row_id} at slot {index}"
                )
                return True

        return False

    async def handle_toggle_widget_color(self) -> bool:
        """Handle 'c' key in edit mode - toggle widget background color.

        Returns:
            True if color was toggled, False otherwise
        """
        if not self.layout_manager:
            logger.warning("Cannot toggle color - no layout manager")
            return False

        # Get current selection
        row, selection_type, index = self.state.get_full_selection()

        if selection_type != SelectionType.WIDGET:
            logger.info("Must select a widget to toggle color")
            return False

        # Get row and widget (include hidden rows for edit mode)
        current_row = self._get_row_at_index(row, include_hidden=True)
        if not current_row or not hasattr(current_row, "widgets"):
            return False

        if index >= len(current_row.widgets):
            return False

        widget_config = current_row.widgets[index]
        row_id = getattr(current_row, "id", row + 1)

        # Toggle through color cycle: None -> dark[0] -> dark[1] -> primary[0] -> secondary[0] -> None
        current_color = getattr(widget_config, "color", "none")
        color_cycle = WIDGET_BG_COLOR_NAMES
        try:
            current_idx = color_cycle.index(current_color)
            new_color = color_cycle[(current_idx + 1) % len(color_cycle)]
        except ValueError:
            new_color = "dark0"

        logger.info(
            f"[COLOR TOGGLE] row={row_id}, widget_idx={index}, current='{current_color}', new='{new_color}'"
        )

        # Record state snapshot before color change
        await self._record_action()

        # Update widget color
        success = self.layout_manager.set_widget_color(row_id, index, new_color)
        if success:
            # Verify the color was actually updated
            updated_widget = current_row.widgets[index]
            logger.info(
                f"[COLOR TOGGLE] After update: widget_config.color='{updated_widget.color}'"
            )
            # Save and refresh
            self.layout_manager.save()
            self.layout = self.layout_manager.get_layout()
            await self.render_navigation_state()
            logger.info(f"Toggled widget color to {new_color}")
            return True

        return False

    async def handle_toggle_widget_effect(self) -> bool:
        """Handle 'x' key in edit mode - toggle widget visual effect.

        Cycles through: none -> shimmer -> pulse -> none

        Returns:
            True if effect was toggled, False otherwise
        """
        if not self.layout_manager:
            logger.warning("Cannot toggle effect - no layout manager")
            return False

        # Get current selection
        row, selection_type, index = self.state.get_full_selection()

        if selection_type != SelectionType.WIDGET:
            logger.info("Must select a widget to toggle effect")
            return False

        # Get row and widget (include hidden rows for edit mode)
        current_row = self._get_row_at_index(row, include_hidden=True)
        if not current_row or not hasattr(current_row, "widgets"):
            return False

        if index >= len(current_row.widgets):
            return False

        widget_config = current_row.widgets[index]
        row_id = getattr(current_row, "id", row + 1)

        # Toggle through effect cycle: none -> shimmer -> pulse -> none
        from .constants import WIDGET_EFFECT_NAMES

        current_effect = getattr(widget_config, "effect", "none")
        effect_cycle = WIDGET_EFFECT_NAMES
        try:
            current_idx = effect_cycle.index(current_effect)
            new_effect = effect_cycle[(current_idx + 1) % len(effect_cycle)]
        except ValueError:
            new_effect = "shimmer"

        logger.info(
            f"[EFFECT TOGGLE] row={row_id}, widget_idx={index}, current='{current_effect}', new='{new_effect}'"
        )

        # Record state snapshot before effect change
        await self._record_action()

        # Update widget effect using layout manager
        success = self.layout_manager.cycle_widget_effect(row_id, index)
        if success:
            # Save and refresh
            self.layout_manager.save()
            self.layout = self.layout_manager.get_layout()

            # Start or stop animation based on whether any widgets have effects
            if self._has_widgets_with_effects():
                await self._start_effect_animation()
            else:
                await self._stop_effect_animation()

            await self.render_navigation_state()
            logger.info(f"Toggled widget effect to {new_effect}")
            return True

        return False

    async def _enter_edit_mode(self) -> bool:
        """Enter edit mode from STATUS_FOCUS.

        Shows insertion slots between widgets for precise widget placement.

        Returns:
            True if successfully entered edit mode
        """
        logger.info("[EDIT] Entering edit mode")
        await self.state.transition_to_edit_mode()
        await self.render_navigation_state()
        logger.info("Entered EDIT mode")
        return True
