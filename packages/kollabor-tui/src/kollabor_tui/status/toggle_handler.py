"""Toggle handler for interactive status widgets.

This module provides the ToggleHandler class for managing toggle widgets
that cycle through multiple states (e.g., hidden/collapsed/expanded).

Based on spec: docs/specs/interactive-status-widgets-spec.md lines 269-299
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToggleHandler:
    """Handler for toggle widgets that cycle through states.

    Manages state cycling for toggle widgets. Toggle widgets cycle through
    a list of states when Space is pressed (quick toggle) or Left/Right
    arrow keys when activated.

    Attributes:
        widget_id: ID of the widget
        states: List of possible states (e.g., ["hidden", "collapsed", "expanded"])
        current_state: Current state index
        on_change: Optional callback when state changes
    """

    def __init__(
        self,
        widget_id: str,
        states: List[str],
        initial_state: Optional[str] = None,
        on_change: Optional[Callable[[str, str], Any]] = None,
    ):
        """Initialize the toggle handler.

        Args:
            widget_id: ID of the widget
            states: List of possible states
            initial_state: Initial state (defaults to first state)
            on_change: Optional callback(widget_id, new_state) -> Any
        """
        if not states or len(states) < 2:
            logger.warning(f"ToggleHandler for {widget_id} needs at least 2 states")

        self.widget_id = widget_id
        self.states = states
        self.on_change = on_change

        # Set initial state
        if initial_state and initial_state in states:
            self.current_state_idx = states.index(initial_state)
        else:
            self.current_state_idx = 0

        # Log initialization (handle empty states case)
        if states:
            logger.debug(
                f"ToggleHandler initialized for {widget_id} "
                f"with {len(states)} states, starting at: {states[self.current_state_idx]}"
            )
        else:
            logger.debug(
                f"ToggleHandler initialized for {widget_id} "
                f"with no states (invalid configuration)"
            )

    def cycle_next(self) -> str:
        """Cycle to the next state.

        Returns:
            The new state
        """
        if not self.states:
            return ""

        self.current_state_idx = (self.current_state_idx + 1) % len(self.states)
        new_state = self.states[self.current_state_idx]

        logger.debug(f"Toggle {self.widget_id} cycled to: {new_state}")

        # Call on_change callback if provided
        if self.on_change:
            try:
                result = self.on_change(self.widget_id, new_state)
                logger.debug(f"Toggle callback returned: {result}")
            except Exception as e:
                logger.error(f"Error in toggle on_change callback: {e}", exc_info=True)

        return new_state

    def cycle_prev(self) -> str:
        """Cycle to the previous state.

        Returns:
            The new state
        """
        if not self.states:
            return ""

        self.current_state_idx = (self.current_state_idx - 1) % len(self.states)
        new_state = self.states[self.current_state_idx]

        logger.debug(f"Toggle {self.widget_id} cycled (prev) to: {new_state}")

        # Call on_change callback if provided
        if self.on_change:
            try:
                result = self.on_change(self.widget_id, new_state)
                logger.debug(f"Toggle callback returned: {result}")
            except Exception as e:
                logger.error(f"Error in toggle on_change callback: {e}", exc_info=True)

        return new_state

    def set_state(self, state: str) -> bool:
        """Set to a specific state.

        Args:
            state: The state to set

        Returns:
            True if state was found and set, False otherwise
        """
        if state in self.states:
            self.current_state_idx = self.states.index(state)
            logger.debug(f"Toggle {self.widget_id} set to: {state}")
            return True
        logger.warning(f"State '{state}' not found in toggle {self.widget_id}")
        return False

    def get_current_state(self) -> str:
        """Get the current state.

        Returns:
            Current state string, or empty string if no states
        """
        if not self.states:
            return ""
        return self.states[self.current_state_idx]

    def get_current_state_index(self) -> int:
        """Get the current state index.

        Returns:
            Current state index
        """
        return self.current_state_idx

    def get_state_index(self, state: str) -> int:
        """Get the index of a state.

        Args:
            state: State string to look up

        Returns:
            Index of state, or -1 if not found
        """
        try:
            return self.states.index(state)
        except ValueError:
            return -1

    def get_all_states(self) -> List[str]:
        """Get all possible states.

        Returns:
            List of all states
        """
        return self.states.copy()

    def get_state_count(self) -> int:
        """Get the number of states.

        Returns:
            Number of states
        """
        return len(self.states)

    def is_first_state(self) -> bool:
        """Check if currently at the first state.

        Returns:
            True if at first state
        """
        return self.current_state_idx == 0

    def is_last_state(self) -> bool:
        """Check if currently at the last state.

        Returns:
            True if at last state
        """
        return self.current_state_idx == len(self.states) - 1

    def can_cycle_next(self) -> bool:
        """Check if can cycle to next state.

        Returns:
            True if there are states to cycle through (always True for toggles)
        """
        return len(self.states) > 1

    def can_cycle_prev(self) -> bool:
        """Check if can cycle to previous state.

        Returns:
            True if there are states to cycle through (always True for toggles)
        """
        return len(self.states) > 1


class ToggleWidgetContext:
    """Context manager for toggle widget interactions.

    Manages the lifecycle of a toggle widget interaction, handling
    state persistence and cleanup.

    Attributes:
        handler: The ToggleHandler instance
        original_state: Original state before interaction (for revert)
    """

    def __init__(self, handler: ToggleHandler):
        """Initialize the context.

        Args:
            handler: ToggleHandler instance
        """
        self.handler = handler
        self.original_state = handler.get_current_state()
        self.modified = False

    def __enter__(self) -> "ToggleWidgetContext":
        """Enter the toggle context."""
        logger.debug(f"Entered toggle context for {self.handler.widget_id}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the toggle context."""
        if exc_type is not None:
            # Exception occurred, consider reverting
            logger.debug(
                f"Toggle context exited with exception, reverting to {self.original_state}"
            )
            self.handler.set_state(self.original_state)
        else:
            logger.debug(
                f"Toggle context exited normally, state: {self.handler.get_current_state()}"
            )

    def cycle_next(self) -> str:
        """Cycle to next state and mark as modified."""
        self.modified = True
        return self.handler.cycle_next()

    def cycle_prev(self) -> str:
        """Cycle to previous state and mark as modified."""
        self.modified = True
        return self.handler.cycle_prev()

    def is_modified(self) -> bool:
        """Check if state was modified during interaction."""
        return self.modified


def create_toggle_handler_from_widget(
    widget_info: Dict[str, Any],
    current_state: Optional[str] = None,
    on_change: Optional[Callable[[str, str], Any]] = None,
) -> Optional[ToggleHandler]:
    """Create a ToggleHandler from widget info.

    Factory function to create a ToggleHandler from widget registry info.

    Args:
        widget_info: Dictionary containing widget information with:
            - id: Widget ID
            - states: List of states
        current_state: Current state (if known)
        on_change: Optional callback when state changes

    Returns:
        ToggleHandler instance, or None if widget is not toggle type
    """
    if not widget_info or widget_info.get("interaction_type") != "toggle":
        return None

    widget_id = widget_info.get("id", "")
    states = widget_info.get("states", [])

    if not states:
        logger.warning(f"Toggle widget {widget_id} has no states defined")
        return None

    return ToggleHandler(
        widget_id=widget_id,
        states=states,
        initial_state=current_state,
        on_change=on_change,
    )
