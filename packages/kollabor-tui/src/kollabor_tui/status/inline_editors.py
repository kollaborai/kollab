"""Inline editors for interactive status widgets.

Provides compact inline editors that can be activated from status widgets
to edit values directly in the status area without opening modals.

Editors:
    InlineSliderEditor: Numeric slider with keyboard controls
    InlineTextEditor: Text input field
    InlineDropdownEditor: Quick selection from options

Example:
    editor = InlineSliderEditor(
        value=0.7,
        min_val=0.0,
        max_val=2.0,
        step=0.1,
        presets=[0.1, 0.5, 0.7, 1.0, 1.5],
    )
    print(editor.render())
    # [====▌░░] 0.7

    editor.handle_keypress(key_press)
    if editor.is_confirmed():
        print(f"New value: {editor.get_value()}")
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

from kollabor_tui.design_system import (
    C,
    T,
    progress_bar,
    solid,
    solid_fg,
)
from kollabor_tui.key_parser import KeyPress, KeyType


@dataclass
class EditorResult:
    """Result from an inline editor operation.

    Attributes:
        confirmed: Whether the user confirmed (True) or cancelled (False)
        value: The final value (only valid if confirmed=True)
    """

    confirmed: bool
    value: Any


class BaseInlineEditor(ABC):
    """Base class for inline editors.

    All editors support:
    - render() method to display the editor
    - handle_keypress() to process keyboard input
    - get_value()/set_value() for state access
    - Enter to confirm, Esc to cancel
    """

    def __init__(self, width: int = 20):
        """Initialize the inline editor.

        Args:
            width: Display width of the editor in characters
        """
        self.width = width
        self._original_value = None
        self._confirmed = False
        self._cancelled = False

    @abstractmethod
    def render(self) -> str:
        """Render the editor to a string.

        Returns:
            String containing the rendered editor with ANSI codes
        """
        pass

    @abstractmethod
    def handle_keypress(self, key: KeyPress) -> bool:
        """Handle a key press event.

        Args:
            key: KeyPress event from key parser

        Returns:
            True if the key was handled, False otherwise
        """
        pass

    @abstractmethod
    def get_value(self) -> Any:
        """Get the current value.

        Returns:
            Current editor value
        """
        pass

    @abstractmethod
    def set_value(self, value: Any) -> None:
        """Set the current value.

        Args:
            value: New value to set
        """
        pass

    def is_confirmed(self) -> bool:
        """Check if editor was confirmed.

        Returns:
            True if Enter was pressed
        """
        return self._confirmed

    def is_cancelled(self) -> bool:
        """Check if editor was cancelled.

        Returns:
            True if Esc was pressed
        """
        return self._cancelled

    def is_done(self) -> bool:
        """Check if editor is done (confirmed or cancelled).

        Returns:
            True if editor session is complete
        """
        return self._confirmed or self._cancelled

    def get_result(self) -> EditorResult:
        """Get the editor result.

        Returns:
            EditorResult with confirmed status and value
        """
        if self._confirmed:
            return EditorResult(confirmed=True, value=self.get_value())
        return EditorResult(confirmed=False, value=self._original_value)

    def _save_original(self, value: Any) -> None:
        """Save original value for cancel reversion."""
        self._original_value = value

    def _handle_common_keys(self, key: KeyPress) -> Optional[bool]:
        """Handle common keys (Enter, Esc).

        Args:
            key: KeyPress event

        Returns:
            True if handled, None if not a common key
        """
        key_name = getattr(key, "name", None)
        if key_name == "Enter":
            self._confirmed = True
            return True
        elif key_name == "Escape":
            self._cancelled = True
            # Revert to original value
            if self._original_value is not None:
                self.set_value(self._original_value)
            return True
        return None


class InlineSliderEditor(BaseInlineEditor):
    """Inline slider editor for numeric values.

    Keyboard controls:
    - Left/Right: Adjust by step
    - Up/Down: Adjust by 10x step
    - 1-9: Jump to preset N
    - Enter: Confirm
    - Esc: Cancel

    Example:
        editor = InlineSliderEditor(
            value=0.7,
            min_val=0.0,
            max_val=2.0,
            step=0.1,
            presets=[0.1, 0.5, 0.7, 1.0, 1.5],
            label="temp",
        )
    """

    def __init__(
        self,
        value: float,
        min_val: float = 0.0,
        max_val: float = 100.0,
        step: float = 1.0,
        presets: Optional[List[float]] = None,
        label: str = "",
        width: int = 20,
        bar_width: int = 10,
    ):
        """Initialize the slider editor.

        Args:
            value: Initial value
            min_val: Minimum allowed value
            max_val: Maximum allowed value
            step: Step size for left/right adjustment (must be positive)
            presets: Optional list of preset values (indexed 1-9)
            label: Optional label to display
            width: Total display width
            bar_width: Width of the progress bar portion

        Raises:
            ValueError: If min_val >= max_val or step <= 0
        """
        super().__init__(width=width)

        # Validate range
        if min_val >= max_val:
            raise ValueError(
                f"min_val ({min_val}) must be less than max_val ({max_val})"
            )

        # Validate step is positive
        if step <= 0:
            raise ValueError(f"step ({step}) must be positive")

        # Clamp initial value to range
        clamped_value = max(min_val, min(max_val, value))

        self._value = clamped_value
        self._min_val = min_val
        self._max_val = max_val
        self._step = step
        self._presets = presets or []
        self._label = label
        self._bar_width = bar_width
        self._save_original(clamped_value)

    def render(self) -> str:
        """Render the slider editor."""
        # Clamp value to range
        clamped = max(self._min_val, min(self._max_val, self._value))

        # Normalize for progress bar
        if self._max_val > self._min_val:
            normalized = (clamped - self._min_val) / (self._max_val - self._min_val)
        else:
            normalized = 0

        # Create progress bar
        bar = progress_bar(normalized, self._bar_width)

        # Color the bar
        result: str = solid_fg("[", T().text_dim)
        for char in bar:
            if char in (
                C["bar_full"],
                C["bar_7_8"],
                C["bar_6_8"],
                C["bar_5_8"],
                C["bar_4_8"],
                C["bar_3_8"],
                C["bar_2_8"],
                C["bar_1_8"],
            ):
                result += solid_fg(char, T().primary[0])
            else:
                result += solid_fg(char, T().dark[0])
        result += solid_fg("]", T().text_dim)

        # Add value display
        if isinstance(clamped, float) and clamped == int(clamped):
            display = str(int(clamped))
        elif isinstance(clamped, float):
            display = f"{clamped:.1f}"
        else:
            display = str(clamped)

        result += solid_fg(f" {display}", T().text)

        # Add label if present
        if self._label:
            result += solid_fg(f" {self._label}", T().text_dim)

        # Add hint for presets
        if self._presets:
            result += solid_fg(" [1-9]", T().text_dim)

        return result

    def handle_keypress(self, key: KeyPress) -> bool:
        """Handle a key press event."""
        # Check common keys first
        common_result = self._handle_common_keys(key)
        if common_result is not None:
            return common_result

        # Get key name safely (handle None case)
        key_name = getattr(key, "name", None)
        if not key_name:
            return False

        # Arrow keys for adjustment
        if key_name == "ArrowLeft":
            self._value = max(self._min_val, self._value - self._step)
            return True
        elif key_name == "ArrowRight":
            self._value = min(self._max_val, self._value + self._step)
            return True
        elif key_name == "ArrowUp":
            self._value = min(self._max_val, self._value + (self._step * 10))
            return True
        elif key_name == "ArrowDown":
            self._value = max(self._min_val, self._value - (self._step * 10))
            return True

        # Number keys for presets
        key_type = getattr(key, "type", None)
        key_char = getattr(key, "char", None)
        if key_type == KeyType.PRINTABLE and key_char and key_char.isdigit():
            preset_index = int(key_char) - 1  # 1-9 -> 0-8
            if 0 <= preset_index < len(self._presets):
                self._value = self._presets[preset_index]
                return True

        return False

    def get_value(self) -> float:
        """Get the current value."""
        return self._value

    def set_value(self, value: float) -> None:
        """Set the current value."""
        self._value = max(self._min_val, min(self._max_val, value))


class InlineTextEditor(BaseInlineEditor):
    """Inline text editor for search/filter input.

    Features:
    - Text input with cursor position
    - Backspace/Delete for editing
    - Home/End for navigation
    - Enter to confirm
    - Esc to cancel

    Example:
        editor = InlineTextEditor(
            value="search",
            placeholder="filter...",
            width=30,
        )
    """

    def __init__(
        self,
        value: str = "",
        placeholder: str = "",
        width: int = 30,
        mask: Optional[str] = None,
    ):
        """Initialize the text editor.

        Args:
            value: Initial text value
            placeholder: Placeholder text when empty
            width: Display width in characters
            mask: Optional character to mask input (e.g., "*" for passwords)
        """
        super().__init__(width=width)
        self._text = value
        self._placeholder = placeholder
        self._cursor_pos = len(value)
        self._mask = mask
        self._save_original(value)
        self._scroll_offset = 0  # For scrolling long text

    def render(self) -> str:
        """Render the text editor."""
        # Display text (masked if needed)
        if self._mask:
            display_text = self._mask * len(self._text)
        else:
            display_text = self._text

        # Use placeholder if empty
        if not display_text and self._placeholder:
            display_text = self._placeholder
            display_cursor_pos = 0
        else:
            display_cursor_pos = self._cursor_pos

        # Calculate visible portion (with scrolling)
        available_width = self.width - 2  # Account for brackets
        text_len = len(display_text)

        # Adjust scroll offset to keep cursor visible
        if display_cursor_pos - self._scroll_offset >= available_width:
            self._scroll_offset = display_cursor_pos - available_width + 1
        elif display_cursor_pos - self._scroll_offset < 0:
            self._scroll_offset = display_cursor_pos

        # Get visible portion
        visible_text = display_text[
            self._scroll_offset : min(self._scroll_offset + available_width, text_len)
        ].ljust(available_width)

        # Insert cursor
        relative_cursor = display_cursor_pos - self._scroll_offset
        if 0 <= relative_cursor < len(visible_text):
            before = visible_text[:relative_cursor]
            after = visible_text[relative_cursor + 1 :]
            cursor_char = visible_text[relative_cursor]
            # Use block cursor
            visible_with_cursor = before + solid_fg(cursor_char, T().primary[0]) + after
        else:
            visible_with_cursor = visible_text

        # Build editor with brackets
        result: str = solid_fg("[", T().ai_tag)
        result += solid(visible_with_cursor, T().dark[0], T().text, available_width)
        result += solid_fg("]", T().ai_tag)

        return result

    def handle_keypress(self, key: KeyPress) -> bool:
        """Handle a key press event."""
        # Check common keys first
        common_result = self._handle_common_keys(key)
        if common_result is not None:
            return common_result

        # Get key name and attributes safely (handle None case)
        key_name = getattr(key, "name", None)
        if not key_name:
            return False

        # Handle editing keys
        if key_name == "Backspace":
            if self._cursor_pos > 0:
                self._text = (
                    self._text[: self._cursor_pos - 1] + self._text[self._cursor_pos :]
                )
                self._cursor_pos -= 1
            return True
        elif key_name == "Delete":
            if self._cursor_pos < len(self._text):
                self._text = (
                    self._text[: self._cursor_pos] + self._text[self._cursor_pos + 1 :]
                )
            return True
        elif key_name == "ArrowLeft":
            if self._cursor_pos > 0:
                self._cursor_pos -= 1
            return True
        elif key_name == "ArrowRight":
            if self._cursor_pos < len(self._text):
                self._cursor_pos += 1
            return True
        elif key_name == "Home":
            self._cursor_pos = 0
            return True
        elif key_name == "End":
            self._cursor_pos = len(self._text)
            return True

        # Handle printable characters
        key_type = getattr(key, "type", None)
        key_char = getattr(key, "char", None)
        if key_type == KeyType.PRINTABLE and key_char:
            # Insert character at cursor
            self._text = (
                self._text[: self._cursor_pos]
                + key_char
                + self._text[self._cursor_pos :]
            )
            self._cursor_pos += 1
            return True

        return False

    def get_value(self) -> str:
        """Get the current text value."""
        return self._text

    def set_value(self, value: str) -> None:
        """Set the current text value."""
        self._text = value
        self._cursor_pos = len(value)


class InlineDropdownEditor(BaseInlineEditor):
    """Inline dropdown editor for quick selection.

    Features:
    - Arrow up/down to cycle through options
    - 1-9 keys: quick select option N
    - Type character: jump to option starting with that letter
    - Enter to confirm
    - Esc to cancel

    Example:
        editor = InlineDropdownEditor(
            options=["ocean", "sunset", "forest"],
            selected_index=0,
            label="theme",
        )
    """

    def __init__(
        self,
        options: List[str],
        selected_index: int = 0,
        label: str = "",
        width: int = 25,
    ):
        """Initialize the dropdown editor.

        Args:
            options: List of options to choose from
            selected_index: Index of initially selected option (will be clamped to valid range)
            label: Optional label to display
            width: Display width in characters
        """
        super().__init__(width=width)
        self._options = options

        # Clamp selected_index to valid range
        if options and 0 <= selected_index < len(options):
            self._selected_index = selected_index
        else:
            self._selected_index = 0

        self._label = label
        self._expanded = False  # Track if dropdown is expanded

        # Save original value safely
        if options and 0 <= self._selected_index < len(options):
            self._save_original(options[self._selected_index])
        else:
            self._save_original("")

    def render(self) -> str:
        """Render the dropdown editor."""
        # Get current selection
        if 0 <= self._selected_index < len(self._options):
            current = self._options[self._selected_index]
        else:
            current = ""

        # Truncate if needed
        max_text_width = self.width - 6  # Account for brackets, space, arrow
        if len(current) > max_text_width:
            display = current[: max_text_width - 1] + "…"
        else:
            display = current

        # Build dropdown display
        result: str = solid_fg("[", T().ai_tag)
        result += solid_fg(display, T().text)
        result += solid_fg(" ▼", T().primary[0])
        result += solid_fg("]", T().ai_tag)

        # Add label if present
        if self._label:
            result += solid_fg(f" {self._label}", T().text_dim)

        # Add counter
        total = len(self._options)
        if total > 1:
            result += solid_fg(f" [{self._selected_index + 1}/{total}]", T().text_dim)

        return result

    def render_expanded(self, max_visible: int = 5) -> List[str]:
        """Render the dropdown in expanded state with options list.

        Args:
            max_visible: Maximum number of options to show at once

        Returns:
            List of strings (rendered lines)
        """
        lines = []

        # First line is the collapsed dropdown (current selection)
        lines.append(self.render())

        # Calculate visible range
        total = len(self._options)
        start_idx = max(0, self._selected_index - max_visible // 2)
        end_idx = min(total, start_idx + max_visible)

        # Adjust if we're near the end
        if end_idx - start_idx < max_visible and end_idx < total:
            start_idx = max(0, end_idx - max_visible)

        # Add options
        for i in range(start_idx, end_idx):
            option = self._options[i]
            is_selected = i == self._selected_index

            # Truncate if needed
            max_option_width = self.width - 2  # Account for brackets
            if len(option) > max_option_width:
                option_display = option[: max_option_width - 1] + "…"
            else:
                option_display = option

            # Selection indicator
            if is_selected:
                indicator = solid_fg("→", T().primary[0])
                option_text = solid(
                    f" {option_display}", T().primary[0], T().text, self.width - 1
                )
            else:
                indicator = " "
                option_text = solid(
                    f" {option_display}", T().dark[0], T().text_dim, self.width - 1
                )

            lines.append(f"{indicator}{option_text}")

        return lines

    def handle_keypress(self, key: KeyPress) -> bool:
        """Handle a key press event."""
        # Check common keys first
        common_result = self._handle_common_keys(key)
        if common_result is not None:
            return common_result

        # Get key name and attributes safely (handle None case)
        key_name = getattr(key, "name", None)
        if not key_name:
            return False

        # Arrow keys for navigation
        if key_name == "ArrowUp":
            if self._options:
                self._selected_index = (self._selected_index - 1) % len(self._options)
            return True
        elif key_name == "ArrowDown":
            if self._options:
                self._selected_index = (self._selected_index + 1) % len(self._options)
            return True

        # Number keys for quick select (1-9)
        key_type = getattr(key, "type", None)
        key_char = getattr(key, "char", None)
        if key_type == KeyType.PRINTABLE and key_char and key_char.isdigit():
            idx = int(key_char) - 1  # 1-9 -> 0-8
            if 0 <= idx < len(self._options):
                self._selected_index = idx
                self._confirmed = True  # Auto-confirm on quick select
            return True

        # Printable characters for jump-to-letter
        if key_type == KeyType.PRINTABLE and key_char and key_char.isalpha():
            char = key_char.lower()
            # Find next option starting with this letter
            for i in range(len(self._options)):
                idx = (self._selected_index + i + 1) % len(self._options)
                if self._options[idx].lower().startswith(char):
                    self._selected_index = idx
                    return True
            # If none found, try from beginning
            for i in range(len(self._options)):
                if self._options[i].lower().startswith(char):
                    self._selected_index = i
                    return True
            return True  # Handled even if no match found

        return False

    def get_value(self) -> str:
        """Get the currently selected value."""
        if 0 <= self._selected_index < len(self._options):
            return self._options[self._selected_index]
        return ""

    def set_value(self, value: str) -> None:
        """Set the current value by finding it in options."""
        if value in self._options:
            self._selected_index = self._options.index(value)


__all__ = [
    "EditorResult",
    "BaseInlineEditor",
    "InlineSliderEditor",
    "InlineTextEditor",
    "InlineDropdownEditor",
]
