"""Slider widget for modal UI components."""

from typing import List

from kollabor_tui.design_system import S, T, TagBox
from kollabor_tui.key_parser import KeyPress

from .base_widget import BaseWidget

# Widget dimensions
TAG_WIDTH = 3

# Unicode block characters for smooth progress bar
BAR_CHARS = {
    "full": "█",
    "7_8": "▉",
    "6_8": "▊",
    "5_8": "▋",
    "4_8": "▌",
    "3_8": "▍",
    "2_8": "▎",
    "1_8": "▏",
    "empty": "░",
}


class SliderWidget(BaseWidget):
    """Interactive slider widget with █░ visual bar.

    Displays numeric value with visual progress bar and responds to arrow keys.
    Uses design system for styling.
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize slider widget.

        Args:
            config: Widget configuration with min_value, max_value, step values.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing config values.
        """
        super().__init__(config, config_path, config_service)
        # Support both naming conventions: min/max and min_value/max_value
        self.min_value = config.get("min_value") or config.get("min", 0.0)
        self.max_value = config.get("max_value") or config.get("max", 1.0)
        self.step = config.get("step", 0.1)
        self.bar_width = config.get("bar_width", 20)
        self.decimal_places = config.get("decimal_places", 1)

    def render(self) -> List[str]:
        """Render slider with visual progress bar.

        Returns:
            List containing slider display line.
        """
        # Get current value and ensure it's numeric
        current_value = self.get_pending_value()
        try:
            value: float = (
                float(current_value) if current_value is not None else self.min_value
            )
        except (TypeError, ValueError):
            value = self.min_value

        # Clamp value to valid range
        value = max(self.min_value, min(self.max_value, value))

        label = self.get_label()

        # Create visual slider bar using existing characters
        progress = (value - self.min_value) / max(
            0.001, self.max_value - self.min_value
        )
        filled = int(progress * self.bar_width)
        bar = "█" * filled + "░" * (self.bar_width - filled)

        # Format value display
        if self.decimal_places == 0:
            value_display = f"{int(value)}"
        else:
            value_display = f"{value:.{self.decimal_places}f}"

        # Show range info when focused - use design system
        if self.focused:
            range_info = f" ({self.min_value}–{self.max_value})"
            main_line = (
                f"{S.BOLD}  {label}: {value_display} [{bar}]{range_info}{S.RESET_BOLD}"
            )
        else:
            main_line = f"  {label}: {value_display} [{bar}]"

        return [main_line]

    def _progress_bar(self, progress: float, width: int = 15) -> str:
        """Create smooth progress bar using partial block characters.

        Args:
            progress: Progress value from 0.0 to 1.0.
            width: Width of the progress bar in characters.

        Returns:
            String containing the progress bar with smooth partial blocks.
        """
        # Calculate filled portion
        filled_width = progress * width
        full_blocks = int(filled_width)
        partial = filled_width - full_blocks

        # Build bar with smooth transition
        bar = BAR_CHARS["full"] * full_blocks

        # Add partial block for smooth edge
        if full_blocks < width:
            if partial >= 0.875:
                bar += BAR_CHARS["7_8"]
            elif partial >= 0.75:
                bar += BAR_CHARS["6_8"]
            elif partial >= 0.625:
                bar += BAR_CHARS["5_8"]
            elif partial >= 0.5:
                bar += BAR_CHARS["4_8"]
            elif partial >= 0.375:
                bar += BAR_CHARS["3_8"]
            elif partial >= 0.25:
                bar += BAR_CHARS["2_8"]
            elif partial >= 0.125:
                bar += BAR_CHARS["1_8"]
            else:
                bar += BAR_CHARS["empty"]

            # Fill remaining with empty
            remaining = width - len(bar)
            bar += BAR_CHARS["empty"] * remaining

        return bar

    def render_modern(self, width: int = 50, position: str = "only") -> List[str]:  # type: ignore[override]
        """Render slider with modern design system styling.

        Uses TagBox for consistent box styling with gradient backgrounds,
        diamond icon for focus indication, and smooth progress bar.

        Args:
            width: Total width of slider widget (default: 50).
            position: Border position - 'only' (both), 'first' (top only),
                     'middle' (no borders), 'last' (bottom only).

        Returns:
            List containing single slider display line with TagBox styling.
        """
        # Get current value and ensure it's numeric
        current_value = self.get_pending_value()
        try:
            value: float = (
                float(current_value) if current_value is not None else self.min_value
            )
        except (TypeError, ValueError):
            value = self.min_value

        # Clamp value to valid range
        value = max(self.min_value, min(self.max_value, value))

        label = self.get_label()

        # Icon: filled diamond when focused, empty when not
        icon = " ◆ " if self.focused else " ◇ "

        # Calculate colors based on focus state
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

        # Calculate smooth progress bar
        progress = (value - self.min_value) / max(
            0.001, self.max_value - self.min_value
        )
        bar_width = (
            width - TAG_WIDTH - len(label) - 20
        )  # Reserve space for label, value, and range
        bar = self._progress_bar(progress, max(10, bar_width))

        # Format value display
        if self.decimal_places == 0:
            value_display = f"{int(value)}"
        else:
            value_display = f"{value:.{self.decimal_places}f}"

        # Build content line
        content = f" {label}: {value_display} {bar}"
        if self.focused:
            # Show range when focused
            content += f" ({self.min_value}-{self.max_value})"

        # Calculate dimensions
        tag_width = TAG_WIDTH
        content_width = width - tag_width

        # Render using TagBox
        rendered = TagBox.render(
            lines=[content],
            tag_bg=tag_bg,
            tag_fg=tag_fg,
            tag_width=tag_width,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=content_width,
            tag_chars=[icon],
            use_gradient=use_gradient,
            position=position,
        )

        return [rendered]

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle slider input - arrow keys adjust value.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key was handled.
        """
        current_value = self.get_pending_value()
        try:
            value: float = (
                float(current_value) if current_value is not None else self.min_value
            )
        except (TypeError, ValueError):
            value = self.min_value

        # Clamp current value to valid range
        value = max(self.min_value, min(self.max_value, value))

        if key_press.name == "ArrowRight" or key_press.name == "ArrowUp":
            # Increase value
            new_value = min(self.max_value, value + self.step)
            self.set_value(new_value)
            return True

        elif key_press.name == "ArrowLeft" or key_press.name == "ArrowDown":
            # Decrease value
            new_value = max(self.min_value, value - self.step)
            self.set_value(new_value)
            return True

        elif key_press.name == "Home":
            # Set to minimum value
            self.set_value(self.min_value)
            return True

        elif key_press.name == "End":
            # Set to maximum value
            self.set_value(self.max_value)
            return True

        elif key_press.name == "Ctrl+ArrowRight":
            # Large step increase
            large_step = self.step * 10
            new_value = min(self.max_value, value + large_step)
            self.set_value(new_value)
            return True

        elif key_press.name == "Ctrl+ArrowLeft":
            # Large step decrease
            large_step = self.step * 10
            new_value = max(self.min_value, value - large_step)
            self.set_value(new_value)
            return True

        # Handle direct numeric input for precise values
        elif (
            key_press.char
            and isinstance(key_press.char, str)
            and key_press.char.isdigit()
        ):
            # Allow typing numbers (basic implementation)
            try:
                # Convert single digit to step-based movement
                digit = int(key_press.char)
                target_progress = digit / 10.0  # 0-9 maps to 0%-90%
                target_value = self.min_value + target_progress * (
                    self.max_value - self.min_value
                )
                target_value = max(self.min_value, min(self.max_value, target_value))
                self.set_value(target_value)
                return True
            except ValueError:
                pass

        return False

    def set_value(self, value):
        """Set slider value with validation and clamping.

        Args:
            value: New numeric value.
        """
        try:
            numeric_value = float(value)
            # Clamp to valid range
            clamped_value = max(self.min_value, min(self.max_value, numeric_value))
            # Round to step precision
            stepped_value = round(clamped_value / self.step) * self.step
            super().set_value(stepped_value)
        except (TypeError, ValueError):
            # Invalid value, set to minimum
            super().set_value(self.min_value)

    def is_valid_value(self, value) -> bool:
        """Validate slider value - must be numeric and in range.

        Args:
            value: Value to validate.

        Returns:
            True if value is numeric and within min/max range.
        """
        try:
            numeric_value: float = float(value)
            return bool(self.min_value <= numeric_value <= self.max_value)
        except (TypeError, ValueError):
            return False

    def get_progress_percentage(self) -> float:
        """Get current value as percentage of range.

        Returns:
            Progress as percentage (0.0 to 1.0).
        """
        current_value = self.get_pending_value()
        try:
            value: float = (
                float(current_value) if current_value is not None else self.min_value
            )
        except (TypeError, ValueError):
            value = self.min_value

        value = max(self.min_value, min(self.max_value, value))
        return float(
            (value - self.min_value) / max(0.001, self.max_value - self.min_value)
        )
