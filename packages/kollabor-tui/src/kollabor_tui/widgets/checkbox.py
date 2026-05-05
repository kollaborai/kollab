"""Checkbox widget for modal UI components."""

import logging
from typing import List

from kollabor_tui.design_system import S, T, TagBox
from kollabor_tui.key_parser import KeyPress

from .base_widget import BaseWidget

logger = logging.getLogger(__name__)

# Widget dimensions
TAG_WIDTH = 3
CONTENT_WIDTH = 50 - TAG_WIDTH  # Default content width


class CheckboxWidget(BaseWidget):
    """Interactive checkbox widget with ✓ symbol.

    Displays as [ ] or [✓] and toggles state on Enter or Space key press.
    Uses design system for styling.
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize checkbox widget.

        Args:
            config: Widget configuration dictionary.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing config values.
        """
        super().__init__(config, config_path, config_service)

    def render(self) -> List[str]:
        """Render checkbox with current state.

        Returns:
            List containing single checkbox display line.
        """
        # Get current value (prefer pending value if available)
        current_value = self.get_pending_value()
        check = "✓" if current_value else " "
        label = self.get_label()

        logger.info(
            f"Checkbox render: value={current_value}, check='{check}', focused={self.focused}"
        )

        # Apply focus highlighting using design system
        if self.focused:
            rendered = f"{S.BOLD}  [{check}] {label}{S.RESET_BOLD}"
        else:
            rendered = f"  [{check}] {label}"

        logger.info(f"Checkbox rendered as: '{rendered}'")
        return [rendered]

    def render_modern(self, width: int = 50, position: str = "only") -> List[str]:  # type: ignore[override]
        """Render checkbox with modern design system styling.

        Uses TagBox for consistent box styling with gradient backgrounds.

        Args:
            width: Total width of checkbox widget (default: 50).
            position: Border position - 'only' (both), 'first' (top only),
                     'middle' (no borders), 'last' (bottom only).

        Returns:
            List containing single checkbox display line with TagBox styling.
        """
        # Get current value (prefer pending value if available)
        current_value = self.get_pending_value()
        label = self.get_label()

        # Icon IS the state indicator
        if current_value:
            # Checked: success color with checkmark
            icon = " ✔ "
            tag_bg = T().success[0] if self.focused else T().dark[0]
        else:
            # Unchecked: error color with X
            icon = " ✖ "
            tag_bg = T().error[0] if self.focused else T().dark[0]

        # Content colors based on focus state
        if self.focused:
            content_colors = T().input_bg
            content_fg = T().text
            tag_fg = T().text_dark
            use_gradient = True
        else:
            content_colors = T().dark[0]
            content_fg = T().text_dim
            tag_fg = T().text_dim
            use_gradient = False

        # Calculate dimensions
        tag_width = TAG_WIDTH
        content_width = width - tag_width

        # Render using TagBox
        rendered = TagBox.render(
            lines=[f" {label}"],
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

        logger.info(
            f"Checkbox render_modern: value={current_value}, focused={self.focused}"
        )
        return [rendered]

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle checkbox input - toggle on Enter or Space.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key was handled (Enter or Space).
        """
        # Check for Enter key (name="Enter" or char="\r" or char="\n")
        is_enter = key_press.name == "Enter" or key_press.char in ("\r", "\n")
        # Check for Space key (name="Space" or char=" ")
        is_space = key_press.name == "Space" or key_press.char == " "

        logger.info(
            f"Checkbox handle_input: name={key_press.name}, char={repr(key_press.char)}, "
            f"is_enter={is_enter}, is_space={is_space}"
        )

        if is_enter or is_space:
            current_value = self.get_pending_value()
            new_value = not current_value
            logger.info(f"🔘 Checkbox TOGGLING: {current_value} → {new_value}")
            self.set_value(new_value)
            logger.info(
                f"🔘 Checkbox value after set: {self.get_pending_value()}, _pending={self._pending_value}"
            )
            return True
        return False

    def is_valid_value(self, value) -> bool:
        """Validate checkbox value - must be boolean.

        Args:
            value: Value to validate.

        Returns:
            True if value is boolean.
        """
        return isinstance(value, bool)
