"""Text input widget for modal UI components."""

from typing import List

from kollabor_tui.design_system import S, T, TagBox
from kollabor_tui.key_parser import KeyPress

from .base_widget import BaseWidget


class TextInputWidget(BaseWidget):
    """Interactive text input widget with cursor ▌.

    Displays as [text▌] with blinking cursor and accepts character input.
    Uses design system for styling.
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize text input widget.

        Args:
            config: Widget configuration with optional placeholder.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing config values.
        """
        super().__init__(config, config_path, config_service)
        self.cursor_position = 0
        self._show_cursor = True

    def render(self) -> List[str]:
        """Render text input with cursor indicator.

        Returns:
            List containing text input display line.
        """
        # Get current text value
        current_value = self.get_pending_value()
        text = str(current_value) if current_value is not None else ""
        label = self.get_label()
        placeholder = self.config.get("placeholder", "")

        # Display text or placeholder
        if text:
            display_text = text
        elif placeholder and not self.focused:
            display_text = placeholder
        else:
            display_text = ""

        # Add cursor when focused
        if self.focused and self._show_cursor:
            cursor_pos = min(self.cursor_position, len(display_text))
            display_with_cursor = (
                display_text[:cursor_pos] + "▌" + display_text[cursor_pos:]
            )
        else:
            display_with_cursor = display_text

        # Apply focus highlighting using design system
        if self.focused:
            return [f"{S.BOLD}  {label}: [{display_with_cursor}]{S.RESET_BOLD}"]
        else:
            if placeholder and not text:
                return [f"  {label}: [{S.DIM}{placeholder}{S.RESET_DIM}]"]
            else:
                return [f"  {label}: [{display_with_cursor}]"]

    def render_modern(self, width: int = 50, position: str = "only") -> List[str]:  # type: ignore[override]
        """Render text input with modern design system styling.

        Args:
            width: Total width of the widget (default 50).
            position: Border position - 'only' (both), 'first' (top only),
                     'middle' (no borders), 'last' (bottom only).

        Returns:
            List containing TagBox rendered string.
        """
        # Get current text value
        current_value = self.get_pending_value()
        text = str(current_value) if current_value is not None else ""
        label = self.get_label()
        placeholder = self.config.get("placeholder", "")

        # Cursor icon for tag
        cursor_icon = " ▌ " if self.focused else "   "

        # Colors based on focus state
        if self.focused:
            tag_bg = T().primary[0]
            content_colors = T().input_bg
            content_fg = T().text
            tag_fg = T().text_dark
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim
            tag_fg = T().text_dim

        # Display text with cursor
        if text:
            display = text
            if self.focused and self._show_cursor:
                cursor_pos = min(self.cursor_position, len(display))
                display = text[:cursor_pos] + "▌" + text[cursor_pos:]
        elif placeholder and not self.focused:
            # Show placeholder in dim when empty and unfocused
            display = f"{S.DIM}{placeholder}{S.RESET_DIM}"
        else:
            # Empty - show cursor if focused
            display = "▌" if (self.focused and self._show_cursor) else ""

        rendered = TagBox.render(
            lines=[f" {label}: {display}"],
            tag_bg=tag_bg,
            tag_fg=tag_fg,
            tag_width=3,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=width - 3,
            tag_chars=[cursor_icon],
            use_gradient=self.focused,
            position=position,
        )
        return [rendered]

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle text input - character insertion and navigation.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key was handled.
        """
        current_text = str(self.get_pending_value() or "")

        # Handle special keys
        if key_press.name == "Backspace":
            if self.cursor_position > 0:
                # Remove character before cursor
                new_text = (
                    current_text[: self.cursor_position - 1]
                    + current_text[self.cursor_position :]
                )
                self.set_value(new_text)
                self.cursor_position = max(0, self.cursor_position - 1)
            return True

        elif key_press.name == "Delete":
            if self.cursor_position < len(current_text):
                # Remove character at cursor
                new_text = (
                    current_text[: self.cursor_position]
                    + current_text[self.cursor_position + 1 :]
                )
                self.set_value(new_text)
            return True

        elif key_press.name == "ArrowLeft":
            # Move cursor left
            self.cursor_position = max(0, self.cursor_position - 1)
            return True

        elif key_press.name == "ArrowRight":
            # Move cursor right
            self.cursor_position = min(len(current_text), self.cursor_position + 1)
            return True

        elif key_press.name == "Home" or key_press.name == "Ctrl+A":
            # Move cursor to beginning
            self.cursor_position = 0
            return True

        elif key_press.name == "End" or key_press.name == "Ctrl+E":
            # Move cursor to end
            self.cursor_position = len(current_text)
            return True

        elif key_press.name == "Ctrl+U":
            # Clear entire line
            self.set_value("")
            self.cursor_position = 0
            return True

        elif key_press.name == "Ctrl+K":
            # Clear from cursor to end
            new_text = current_text[: self.cursor_position]
            self.set_value(new_text)
            return True

        # Handle printable characters
        elif key_press.char and key_press.char.isprintable():
            # CRITICAL FIX: Add input validation for specific field types
            validation_type = self.config.get("validation", "text")

            # For numeric fields, only allow digits and decimal points
            if validation_type == "integer" and not key_press.char.isdigit():
                return True  # Reject non-numeric input
            elif validation_type == "number" and not (
                key_press.char.isdigit() or key_press.char == "."
            ):
                return True  # Reject non-numeric input

            # Insert character at cursor position
            new_text = (
                current_text[: self.cursor_position]
                + key_press.char
                + current_text[self.cursor_position :]
            )

            # Validate the complete value
            if self._validate_complete_value(new_text, validation_type):
                self.set_value(new_text)
                self.cursor_position += 1
            return True

        return False

    def set_focus(self, focused: bool):
        """Set focus and reset cursor position.

        Args:
            focused: Whether widget should be focused.
        """
        super().set_focus(focused)
        if focused:
            # Move cursor to end when gaining focus
            current_text = str(self.get_pending_value() or "")
            self.cursor_position = len(current_text)

    def set_value(self, value):
        """Set text value and adjust cursor position.

        Args:
            value: New text value.
        """
        super().set_value(str(value) if value is not None else "")
        # Ensure cursor position is valid
        new_text = str(value) if value is not None else ""
        self.cursor_position = min(self.cursor_position, len(new_text))

    def _validate_complete_value(self, value: str, validation_type: str) -> bool:
        """Validate complete input value based on validation type.

        Args:
            value: String value to validate.
            validation_type: Type of validation (text, integer, number).

        Returns:
            True if value is valid for the field type.
        """
        if validation_type == "integer":
            if not value:  # Empty is valid (will use default)
                return True
            try:
                int(value)
                return True
            except ValueError:
                return False
        elif validation_type == "number":
            if not value:  # Empty is valid (will use default)
                return True
            try:
                float(value)
                return True
            except ValueError:
                return False
        return True  # Text validation always passes

    def is_valid_value(self, value) -> bool:
        """Validate text input value - must be string-convertible.

        Args:
            value: Value to validate.

        Returns:
            True if value can be converted to string.
        """
        try:
            str(value)
            validation_type = self.config.get("validation", "text")
            return self._validate_complete_value(str(value), validation_type)
        except (TypeError, ValueError):
            return False
