"""Spin box widget for precise numeric input with increment/decrement."""

from typing import Any, List

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette

from .base_widget import BaseWidget


class SpinBoxWidget(BaseWidget):
    """Widget for numeric input with increment/decrement buttons.

    Provides precise numeric entry with arrow keys or direct typing.
    Ideal for temperature, max_tokens, timeout values, and other settings.

    Example config:
    {
        "label": "Temperature",
        "min_value": 0.0,
        "max_value": 2.0,
        "step": 0.1,
        "decimal_places": 1,
        "unit": "°C"
    }
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize spin box widget.

        Args:
            config: Widget configuration with range and display options.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing values.
        """
        super().__init__(config, config_path, config_service)

        # Range constraints
        self.min_value = self.config.get("min_value", 0)
        self.max_value = self.config.get("max_value", 100)
        self.step = self.config.get("step", 1)

        # Display options
        self.decimal_places = self.config.get("decimal_places", 0)
        self.unit = self.config.get("unit", "")
        self.show_buttons = self.config.get("show_buttons", True)
        self.wrap_around = self.config.get("wrap_around", False)

        # Current value
        initial_value = self.get_value()
        if initial_value is None:
            self.current_value = (self.min_value + self.max_value) / 2
        else:
            self.current_value = float(initial_value)

        # Colors
        self.colors = ColorPalette()

    def render(self) -> List[str]:
        """Render spin box widget.

        Returns:
            List of strings representing widget display lines.
        """
        lines = []

        # Label line
        label = self.get_label()
        if label:
            label_color = (
                self.colors.accent_color if self.focused else self.colors.primary_color
            )
            lines.append(f"{label_color}{label}{self.colors.reset}")

        # Value display with buttons
        value_str = self._format_value(self.current_value)

        if self.show_buttons:
            # Decrement button | Value | Increment button
            dec_color = self.colors.muted_color
            inc_color = self.colors.muted_color
            value_color = (
                self.colors.highlight if self.focused else self.colors.primary_color
            )

            # Highlight buttons on focus
            if self.focused:
                dec_color = self.colors.accent_color
                inc_color = self.colors.accent_color

            # Build display
            width = 20  # Total width
            button_width = 3
            value_width = width - 2 * button_width - 4  # Account for borders and spaces

            value_display = f"{value_color}{value_str}{self.colors.reset}"

            if self.unit:
                value_display += (
                    f" {self.colors.muted_color}{self.unit}{self.colors.reset}"
                )

            # Align and pad
            value_display = value_display.center(value_width)

            display = f"{dec_color}[−]{self.colors.reset} {value_display} {inc_color}[+]{self.colors.reset}"
            lines.append(display)
        else:
            # Simple value display
            value_color = (
                self.colors.highlight if self.focused else self.colors.primary_color
            )
            display = f"{value_color}{value_str}{self.colors.reset}"

            if self.unit:
                display += f" {self.colors.muted_color}{self.unit}{self.colors.reset}"

            lines.append(display)

        # Range indicator when focused
        if self.focused:
            min_val = self._format_value(self.min_value)
            max_val = self._format_value(self.max_value)
            step_val = self._format_value(self.step)
            range_info = (
                f"{self.colors.muted_color}Range: {min_val} - {max_val} | "
                f"Step: {step_val}{self.colors.reset}"
            )
            lines.append(range_info)

        return lines

    def render_modern(self, width: int = 50) -> List[str]:  # type: ignore[override]
        """Render spin box with modern design system styling.

        Uses TagBox for label and increment/decrement buttons.

        Args:
            width: Total width of spin box widget (default: 50).

        Returns:
            List containing spin box display lines with modern styling.
        """
        lines = []
        label = self.get_label()
        tag_width = 3
        content_width = width - tag_width

        # Colors based on focus state
        if self.focused:
            tag_bg = T().primary[0]
            content_colors = T().input_bg
            content_fg = T().text
            tag_fg = T().text_dark
            use_gradient = True
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim
            tag_fg = T().text_dim
            use_gradient = False

        # Label line with icon
        if label:
            icon = " 🔢 " if self.focused else "   "
            content = f" {label}"
            label_line = TagBox.render(
                lines=[content],
                tag_bg=tag_bg,
                tag_fg=tag_fg,
                tag_width=tag_width,
                content_colors=content_colors,
                content_fg=content_fg,
                content_width=content_width,
                tag_chars=[icon],
                use_gradient=use_gradient,
            )
            lines.append(label_line)

        # Value display line with buttons
        value_str = self._format_value(self.current_value)
        unit_display = f" {self.unit}" if self.unit else ""

        if self.show_buttons:
            # Decrement button | Value | Increment button
            icon = " ◆ " if self.focused else " ◇ "
            content = f" [−] {value_str}{unit_display} [+]"

            value_line = TagBox.render(
                lines=[content],
                tag_bg=T().secondary[0] if self.focused else T().dark[0],
                tag_fg=T().text if self.focused else T().text_dim,
                tag_width=tag_width,
                content_colors=T().input_bg if self.focused else T().dark[0],
                content_fg=T().text if self.focused else T().text_dim,
                content_width=content_width,
                tag_chars=[icon],
                use_gradient=self.focused,
            )
            lines.append(value_line)
        else:
            # Simple value display
            icon = " ◆ " if self.focused else " ◇ "
            content = f" {value_str}{unit_display}"

            value_line = TagBox.render(
                lines=[content],
                tag_bg=T().primary[0] if self.focused else T().dark[0],
                tag_fg=None if self.focused else T().text_dim,
                tag_width=tag_width,
                content_colors=T().input_bg if self.focused else T().dark[0],
                content_fg=T().text if self.focused else T().text_dim,
                content_width=content_width,
                tag_chars=[icon],
                use_gradient=self.focused,
            )
            lines.append(value_line)

        # Range indicator when focused
        if self.focused:
            min_val = self._format_value(self.min_value)
            max_val = self._format_value(self.max_value)
            step_val = self._format_value(self.step)
            range_info = f"     Range: {min_val} - {max_val} | Step: {step_val}"
            range_line = TagBox.render(
                lines=[range_info],
                tag_bg=T().dim[0],
                tag_fg=T().text_dim,
                tag_width=tag_width,
                content_colors=T().dim[0],
                content_fg=T().text_dim,
                content_width=content_width,
                tag_chars=["   "],
                use_gradient=False,
            )
            lines.append(range_line)

        return lines

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key press was handled by this widget.
        """
        # Increment/decrement with arrows
        if key_press.key == "up" and key_press.is_cursor_key:
            self._increment()
            return True

        if key_press.key == "down" and key_press.is_cursor_key:
            self._decrement()
            return True

        if key_press.key == "right" and key_press.is_cursor_key:
            self._increment()
            return True

        if key_press.key == "left" and key_press.is_cursor_key:
            self._decrement()
            return True

        # Page up/down for larger steps
        if key_press.key == "pageup" and key_press.is_cursor_key:
            self._increment(step=self.step * 10)
            return True

        if key_press.key == "pagedown" and key_press.is_cursor_key:
            self._decrement(step=self.step * 10)
            return True

        # Direct number input
        if key_press.char and key_press.char.isdigit():
            # Start building new number
            self._handle_digit_input(key_press.char)
            return True

        # Decimal point
        if key_press.key == "." and self.decimal_places > 0:
            self._handle_decimal_input()
            return True

        # Minus sign for negative values
        if key_press.key == "-" and self.min_value < 0:
            self._handle_minus_input()
            return True

        # Enter to confirm
        if key_press.key == "enter":
            self.set_value(self.current_value)
            return True

        # Reset to default with Ctrl+R
        if key_press.key == "r" and key_press.ctrl:
            default = (self.min_value + self.max_value) / 2
            self.current_value = default
            return True

        return False

    def get_value(self) -> Any:
        """Get current numeric value.

        Returns:
            Current value as float or int based on decimal_places.
        """
        # Use pending value if available
        if self._pending_value is not None:
            value = self._pending_value
        else:
            value = self.current_value

        # Return int if no decimals
        if self.decimal_places == 0:
            return int(value)
        return value

    def _increment(self, step=None):
        """Increment value by step.

        Args:
            step: Step size (uses default if None).
        """
        if step is None:
            step = self.step

        new_value = self.current_value + step

        # Check max constraint
        if new_value > self.max_value:
            if self.wrap_around:
                new_value = self.min_value
            else:
                new_value = self.max_value

        self.current_value = self._round_to_decimals(new_value)

    def _decrement(self, step=None):
        """Decrement value by step.

        Args:
            step: Step size (uses default if None).
        """
        if step is None:
            step = self.step

        new_value = self.current_value - step

        # Check min constraint
        if new_value < self.min_value:
            if self.wrap_around:
                new_value = self.max_value
            else:
                new_value = self.min_value

        self.current_value = self._round_to_decimals(new_value)

    def _handle_digit_input(self, digit: str):
        """Handle direct digit input for entering numbers.

        Args:
            digit: Digit character to append.
        """
        # For now, just increment by digit * step
        # A full implementation would track input buffer
        multiplier = int(digit)
        self._increment(step=self.step * multiplier)

    def _handle_decimal_input(self):
        """Handle decimal point input."""
        # Simplified: toggle to next decimal precision
        if self.decimal_places > 0:
            self.current_value = round(self.current_value, self.decimal_places)

    def _handle_minus_input(self):
        """Handle minus sign for negative values."""
        # Toggle between negative and positive
        if self.current_value >= 0:
            self.current_value = max(self.min_value, -self.current_value)
        else:
            self.current_value = abs(self.current_value)

    def _format_value(self, value: float) -> str:
        """Format value for display.

        Args:
            value: Value to format.

        Returns:
            Formatted value string.
        """
        if self.decimal_places == 0:
            return str(int(value))
        return f"{value:.{self.decimal_places}f}"

    def _round_to_decimals(self, value: float) -> float:
        """Round value to configured decimal places.

        Args:
            value: Value to round.

        Returns:
            Rounded value.
        """
        if self.decimal_places == 0:
            return float(round(value))
        return float(round(value, self.decimal_places))

    def is_valid_value(self, value: Any) -> bool:
        """Validate if value is within range.

        Args:
            value: Value to validate.

        Returns:
            True if value is numeric and within range.
        """
        try:
            num_value = float(value)
            return float(self.min_value) <= num_value <= float(self.max_value)
        except (ValueError, TypeError):
            return False
