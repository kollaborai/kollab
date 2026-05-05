"""Navigation state management for interactive status widgets.

Provides thread-safe state tracking for navigation mode in the status area,
including selection position, widget interaction state, and navigation history.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class NavigationMode(Enum):
    """Navigation mode states.

    Modes:
        INPUT: Default mode, user typing in input box
        STATUS_FOCUS: Status area view-only mode (after Tab)
        EDIT: Full edit mode with slots visible (after 'e' in status_focus)
    """

    INPUT = "input"
    STATUS_FOCUS = "status_focus"
    EDIT = "edit"


class SelectionType(Enum):
    """Type of item currently selected.

    Types:
        WIDGET: An existing widget is selected
        SLOT: An insertion slot is selected
    """

    WIDGET = "widget"
    SLOT = "slot"


@dataclass
class InlineEditState:
    """Tracks inline editor state for a widget.

    Attributes:
        widget_id: ID of the widget being edited
        editor: The inline editor instance (InlineTextEditor/InlineSliderEditor/InlineDropdownEditor)
        editor_output: Current rendered output from the editor
    """

    widget_id: Optional[str] = None
    editor: Optional[Any] = None
    editor_output: Optional[str] = None


@dataclass
class UndoAction:
    """Represents a reversible action in the status widget system.

    Attributes:
        action_type: Type of action (toggle, color, add, delete, reorder, edit)
        data: Action-specific data for restoration
        timestamp: When the action was performed
    """

    action_type: str
    data: Dict[str, Any]
    timestamp: float

    # Action type constants
    TOGGLE = "toggle"
    COLOR = "color"
    ADD = "add"
    DELETE = "delete"
    REORDER = "reorder"
    EDIT = "edit"


@dataclass
class StatusNavigationState:
    """Tracks current navigation state in status area.

    Thread-safe navigation state with asyncio.Lock to prevent
    race conditions during concurrent operations.

    Attributes:
        active: Whether navigation mode is currently active
        mode: Current navigation mode (input/status_focus/edit)
        selected_type: What type of item is selected (widget/slot)
        selected_row: Current selected row index
        selected_widget_index: Current selected widget index within the row
        slot_index: Slot position within row (for edit mode navigation)
        interaction_active: Whether a widget is currently being interacted with
        active_widget_id: ID of the currently active widget (if any)
        interaction_data: Optional data for widget interaction state
        inline_edit_state: State for inline editor (when editing widget values)
        navigation_history: History stack for back navigation (row, widget_index tuples)
        undo_history: History stack for undo actions (UndoAction objects)
        _lock: Asyncio lock for thread-safe state mutations
    """

    # Navigation active? (backward compatibility - equivalent to mode != INPUT)
    active: bool = False

    # Current navigation mode
    mode: NavigationMode = field(default_factory=lambda: NavigationMode.INPUT)

    # Selection type (for edit mode)
    selected_type: SelectionType = field(default_factory=lambda: SelectionType.WIDGET)

    # Current selection position
    selected_row: int = 0
    selected_widget_index: int = 0

    # Slot position within row (for edit mode)
    # Position 0 = before first widget, 1 = between widgets, etc.
    slot_index: int = 0

    # Widget interaction state
    interaction_active: bool = False
    active_widget_id: Optional[str] = None
    interaction_data: Dict[str, Any] = field(default_factory=dict)

    # Inline editor state
    inline_edit_state: InlineEditState = field(default_factory=InlineEditState)

    # History for back navigation
    navigation_history: List[Tuple[int, int]] = field(default_factory=list)

    # History for undo operations
    undo_history: List[UndoAction] = field(default_factory=list)

    # Lock for async-safe state mutations
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def set_active(self, active: bool) -> None:
        """Set navigation active state (thread-safe).

        Args:
            active: Whether navigation mode should be active

        Note:
            When deactivating, resets interaction state and clears
            the active widget ID.
            Backward compatibility: sets mode to INPUT when False,
            STATUS_FOCUS when True.
        """
        async with self._lock:
            self.active = active
            if active:
                self.mode = NavigationMode.STATUS_FOCUS
            else:
                self.mode = NavigationMode.INPUT
                # Reset selection when exiting navigation
                self.interaction_active = False
                self.active_widget_id = None
            logger.debug(f"Navigation active={active}, mode={self.mode.value}")

    def is_active(self) -> bool:
        """Check if navigation is active.

        Returns:
            True if in STATUS_FOCUS or EDIT mode, False if in INPUT mode
        """
        return self.active

    def get_mode(self) -> NavigationMode:
        """Get current navigation mode.

        Returns:
            Current NavigationMode
        """
        return self.mode

    async def set_mode(self, mode: NavigationMode) -> None:
        """Set navigation mode directly (thread-safe).

        Args:
            mode: NavigationMode to set

        Note:
            Updates 'active' for backward compatibility:
            - INPUT -> active=False
            - STATUS_FOCUS/EDIT -> active=True
        """
        async with self._lock:
            self.mode = mode
            self.active = mode != NavigationMode.INPUT
            logger.debug(f"Mode set to {mode.value}, active={self.active}")

    async def transition_to_status_focus(self) -> None:
        """Transition from INPUT to STATUS_FOCUS mode (thread-safe).

        Triggered by Tab key.
        """
        async with self._lock:
            self.mode = NavigationMode.STATUS_FOCUS
            self.active = True
            logger.debug("Transitioned to STATUS_FOCUS mode")

    async def transition_to_edit_mode(self) -> None:
        """Transition from STATUS_FOCUS to EDIT mode (thread-safe).

        Triggered by 'e' key.
        Sets initial selection to first slot (position 0).
        """
        async with self._lock:
            if self.mode == NavigationMode.STATUS_FOCUS:
                self.mode = NavigationMode.EDIT
                # Start with slot selection in edit mode
                self.selected_type = SelectionType.SLOT
                self.slot_index = 0
                logger.debug("Transitioned to EDIT mode, initial selection: slot 0")
            else:
                logger.warning(f"Cannot transition to EDIT mode from {self.mode.value}")

    async def transition_to_input_mode(self) -> None:
        """Transition from any mode to INPUT mode (thread-safe).

        Triggered by Esc key.
        Resets interaction state and selection.
        """
        async with self._lock:
            self.mode = NavigationMode.INPUT
            self.active = False
            self.interaction_active = False
            self.active_widget_id = None
            self.selected_type = SelectionType.WIDGET
            logger.debug("Transitioned to INPUT mode, state reset")

    async def transition_to_status_focus_from_edit(self) -> None:
        """Transition from EDIT to STATUS_FOCUS mode (thread-safe).

        Triggered by first Esc key in edit mode.

        Converts slot position to widget position if currently on a slot:
        - slot_index 0 (before first widget) -> widget 0
        - slot_index N (after widget N-1) -> widget N-1
        """
        async with self._lock:
            if self.mode == NavigationMode.EDIT:
                self.mode = NavigationMode.STATUS_FOCUS
                self.active = True

                # Convert slot position to widget position if needed
                if self.selected_type == SelectionType.SLOT:
                    # Convert slot to widget index:
                    # slot 0 (before first widget) -> widget 0
                    # slot N (after widget N-1) -> widget N-1
                    self.selected_widget_index = max(0, self.slot_index - 1)

                self.selected_type = SelectionType.WIDGET
                logger.debug(
                    "Transitioned from EDIT to STATUS_FOCUS mode, "
                    f"widget_index={self.selected_widget_index}"
                )
            else:
                logger.warning(
                    f"Cannot transition to STATUS_FOCUS from {self.mode.value}"
                )

    def get_selection_type(self) -> SelectionType:
        """Get current selection type.

        Returns:
            Current SelectionType (widget or slot)
        """
        return self.selected_type

    async def set_selection_type(self, selection_type: SelectionType) -> None:
        """Set selection type (thread-safe).

        Args:
            selection_type: SelectionType to set (widget or slot)
        """
        async with self._lock:
            self.selected_type = selection_type
            logger.debug(f"Selection type set to {selection_type.value}")

    async def move_selection(self, delta_row: int, delta_widget: int) -> None:
        """Move selection by delta (thread-safe).

        Args:
            delta_row: Row position change (can be negative)
            delta_widget: Widget index change (can be negative)

        Note:
            Selection values are clamped to minimum of 0 to prevent
            negative indices.
        """
        async with self._lock:
            self.selected_row = max(0, self.selected_row + delta_row)
            self.selected_widget_index = max(
                0, self.selected_widget_index + delta_widget
            )
            logger.debug(
                f"Selection moved: row={self.selected_row}, "
                f"widget={self.selected_widget_index}"
            )

    async def move_selection_with_slot(
        self, delta_row: int, delta_position: int
    ) -> None:
        """Move selection considering slot positions (thread-safe).

        In edit mode, positions alternate: slot-widget-slot-widget-...
        delta_position moves through this sequence.

        Args:
            delta_row: Row position change (can be negative)
            delta_position: Position change including slots (can be negative)

        Note:
            Automatically updates selected_type based on whether
            position is even (slot) or odd (widget).
            Slot index is derived from: (position // 2)
            Widget index is derived from: (position - 1) // 2
        """
        async with self._lock:
            self.selected_row = max(0, self.selected_row + delta_row)

            # Calculate new position (0 = slot 0, 1 = widget 0, 2 = slot 1, etc.)
            current_position = self._get_position()
            new_position = max(0, current_position + delta_position)

            # Update selection type based on position parity
            if new_position % 2 == 0:
                self.selected_type = SelectionType.SLOT
                self.slot_index = new_position // 2
            else:
                self.selected_type = SelectionType.WIDGET
                self.selected_widget_index = (new_position - 1) // 2

            logger.debug(
                f"Selection moved with slot: row={self.selected_row}, "
                f"type={self.selected_type.value}, "
                f"slot_idx={self.slot_index}, "
                f"widget_idx={self.selected_widget_index}"
            )

    def _get_position(self) -> int:
        """Helper to calculate position from current state.

        Returns:
            Position index where even=slot, odd=widget
        """
        if self.selected_type == SelectionType.SLOT:
            return self.slot_index * 2
        else:
            return self.selected_widget_index * 2 + 1

    async def set_slot_selection(self, row: int, slot_index: int) -> None:
        """Set slot selection directly (thread-safe).

        Args:
            row: Row index
            slot_index: Slot position within row (0 = before first widget)
        """
        async with self._lock:
            self.selected_row = max(0, row)
            self.slot_index = max(0, slot_index)
            self.selected_type = SelectionType.SLOT
            logger.debug(
                f"Slot selection set: row={self.selected_row}, "
                f"slot_index={self.slot_index}"
            )

    async def set_widget_selection(self, row: int, widget_index: int) -> None:
        """Set widget selection directly (thread-safe).

        Args:
            row: Row index
            widget_index: Widget index within row
        """
        async with self._lock:
            self.selected_row = max(0, row)
            self.selected_widget_index = max(0, widget_index)
            self.selected_type = SelectionType.WIDGET
            logger.debug(
                f"Widget selection set: row={self.selected_row}, "
                f"widget_index={self.selected_widget_index}"
            )

    def get_slot_index(self) -> int:
        """Get current slot index.

        Returns:
            Current slot index
        """
        return self.slot_index

    async def set_slot_index(self, slot_index: int) -> None:
        """Set slot index directly (thread-safe).

        Args:
            slot_index: Slot position to set
        """
        async with self._lock:
            self.slot_index = max(0, slot_index)
            logger.debug(f"Slot index set to {self.slot_index}")

    async def activate_widget(self, widget_id: str) -> None:
        """Activate a widget for interaction (thread-safe).

        Args:
            widget_id: ID of the widget to activate

        Note:
            Sets interaction_active to True and stores the widget ID.
            The widget can then receive interaction events.
        """
        async with self._lock:
            self.interaction_active = True
            self.active_widget_id = widget_id
            logger.debug(f"Widget activated: {widget_id}")

    async def deactivate_widget(self) -> None:
        """Deactivate current widget interaction (thread-safe).

        Note:
            Sets interaction_active to False and clears active_widget_id.
        """
        async with self._lock:
            self.interaction_active = False
            self.active_widget_id = None
            logger.debug("Widget deactivated")

    async def push_history(self, row: int, widget_index: int) -> None:
        """Push current position to navigation history.

        Args:
            row: Row index to save
            widget_index: Widget index to save
        """
        async with self._lock:
            self.navigation_history.append((row, widget_index))
            logger.debug(
                f"History pushed: ({row}, {widget_index}), "
                f"depth={len(self.navigation_history)}"
            )

    async def pop_history(self) -> Optional[Tuple[int, int]]:
        """Pop last position from navigation history.

        Returns:
            Tuple of (row, widget_index) if history available, None otherwise
        """
        async with self._lock:
            if self.navigation_history:
                row, widget_index = self.navigation_history.pop()
                logger.debug(
                    f"History popped: ({row}, {widget_index}), "
                    f"depth={len(self.navigation_history)}"
                )
                return (row, widget_index)
            return None

    def get_selection(self) -> Tuple[int, int]:
        """Get current selection position.

        Returns:
            Tuple of (selected_row, selected_widget_index)

        Note:
            Backward compatible method. For slot-aware selection,
            use get_full_selection() instead.
        """
        return (self.selected_row, self.selected_widget_index)

    def get_full_selection(self) -> Tuple[int, SelectionType, int]:
        """Get full selection info including type.

        Returns:
            Tuple of (row, selection_type, index)
            - For widget: (row, WIDGET, widget_index)
            - For slot: (row, SLOT, slot_index)
        """
        if self.selected_type == SelectionType.SLOT:
            return (self.selected_row, SelectionType.SLOT, self.slot_index)
        else:
            return (
                self.selected_row,
                SelectionType.WIDGET,
                self.selected_widget_index,
            )

    async def set_selection(self, row: int, widget_index: int) -> None:
        """Set selection position directly (thread-safe).

        Args:
            row: Row index to set
            widget_index: Widget index to set

        Note:
            Backward compatible method. Sets selection_type to WIDGET.
            For slot selection, use set_slot_selection() instead.
        """
        async with self._lock:
            self.selected_row = max(0, row)
            self.selected_widget_index = max(0, widget_index)
            self.selected_type = SelectionType.WIDGET
            logger.debug(
                f"Selection set: row={self.selected_row}, "
                f"widget={self.selected_widget_index}"
            )

    async def clear_history(self) -> None:
        """Clear navigation history (thread-safe)."""
        async with self._lock:
            self.navigation_history.clear()
            logger.debug("Navigation history cleared")

    async def push_undo(self, action: "UndoAction") -> None:
        """Push action to undo history (thread-safe).

        Args:
            action: UndoAction to record

        Note:
            Limits history to 50 actions (removes oldest when full).
        """
        async with self._lock:
            self.undo_history.append(action)
            # Limit history to 50 actions
            if len(self.undo_history) > 50:
                self.undo_history.pop(0)
            logger.debug(
                f"Undo pushed: {action.action_type}, depth={len(self.undo_history)}"
            )

    async def pop_undo(self) -> Optional["UndoAction"]:
        """Pop last action from undo history (thread-safe).

        Returns:
            UndoAction if history available, None otherwise
        """
        async with self._lock:
            if self.undo_history:
                action = self.undo_history.pop()
                logger.debug(
                    f"Undo popped: {action.action_type}, depth={len(self.undo_history)}"
                )
                return action
            return None

    async def clear_undo_history(self) -> None:
        """Clear undo history (thread-safe)."""
        async with self._lock:
            self.undo_history.clear()
            logger.debug("Undo history cleared")

    def is_interacting(self) -> bool:
        """Check if currently interacting with a widget.

        Returns:
            True if widget interaction is active
        """
        return self.interaction_active

    def get_active_widget_id(self) -> Optional[str]:
        """Get active widget ID.

        Returns:
            Active widget ID or None
        """
        return self.active_widget_id

    async def set_inline_edit_state(
        self,
        widget_id: Optional[str] = None,
        editor: Optional[Any] = None,
        editor_output: Optional[str] = None,
    ) -> None:
        """Set inline editor state (thread-safe).

        Args:
            widget_id: ID of the widget being edited
            editor: The inline editor instance
            editor_output: Current rendered output from the editor

        Note:
            When all parameters are None, clears the inline edit state.
        """
        async with self._lock:
            if widget_id is None and editor is None and editor_output is None:
                # Clear the state
                self.inline_edit_state = InlineEditState()
                logger.debug("Inline edit state cleared")
            else:
                self.inline_edit_state = InlineEditState(
                    widget_id=widget_id, editor=editor, editor_output=editor_output
                )
                logger.debug(f"Inline edit state set for widget: {widget_id}")

    def get_inline_edit_state(self) -> InlineEditState:
        """Get inline editor state.

        Returns:
            Current InlineEditState
        """
        return self.inline_edit_state

    async def update_inline_editor_output(self, editor_output: str) -> None:
        """Update inline editor output (thread-safe).

        Args:
            editor_output: New rendered output from the editor
        """
        async with self._lock:
            self.inline_edit_state.editor_output = editor_output

    async def set_interaction_data(self, data: Dict[str, Any]) -> None:
        """Set interaction data for widget activation (thread-safe).

        Args:
            data: Dictionary of interaction data (e.g., direction, position)
        """
        async with self._lock:
            self.interaction_data = data.copy() if data else {}
            logger.debug(f"Interaction data set: {self.interaction_data}")

    def get_interaction_data(self) -> Dict[str, Any]:
        """Get current interaction data.

        Returns:
            Dictionary of interaction data (empty dict if none set)
        """
        return self.interaction_data.copy() if self.interaction_data else {}
