"""Multi-select widget for choosing multiple options from a list."""

from typing import Any, List

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette

from .base_widget import BaseWidget


class MultiSelectWidget(BaseWidget):
    """Widget for selecting multiple items from a list.

    Displays options with checkboxes that can be toggled independently.
    Supports arrow key navigation and space to toggle selection.

    Example config:
    {
        "label": "Select Plugins",
        "options": ["plugin1", "plugin2", "plugin3"],
        "selected_indices": [0, 2],  # Pre-selected items
        "min_selections": 1,
        "max_selections": 2
    }
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize multi-select widget.

        Args:
            config: Widget configuration with options list and selection constraints.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing values.
        """
        super().__init__(config, config_path, config_service)

        self.options = self.config.get("options", [])
        self.cursor_index = 0
        self.colors = ColorPalette()

        # Get initial selection from config or defaults
        initial_selection = self.config.get("selected_indices", [])
        self.selected_indices = set(initial_selection)

        # Constraints
        self.min_selections = self.config.get("min_selections", 0)
        self.max_selections = self.config.get("max_selections", len(self.options))

        # Display options
        self.show_cursor = self.config.get("show_cursor", True)
        self.check_char = self.config.get("check_char", "✓")
        self.uncheck_char = self.config.get("uncheck_char", " ")

    def render(self) -> List[str]:
        """Render multi-select widget.

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

        # Options
        for i, option in enumerate(self.options):
            # Determine checkbox state
            is_selected = i in self.selected_indices
            check = self.check_char if is_selected else self.uncheck_char

            # Build option line
            cursor = (
                "▶ "
                if self.focused and self.show_cursor and i == self.cursor_index
                else "  "
            )

            # Checkbox with color
            check_color = (
                self.colors.success_color if is_selected else self.colors.muted_color
            )
            checkbox = f"[{check_color}{check}{self.colors.reset}]"

            # Option text
            option_text = f" {option}"

            # Highlight focused option
            if self.focused and i == self.cursor_index:
                option_text = f"{self.colors.highlight}{option_text}{self.colors.reset}"

            lines.append(f"{cursor}{checkbox}{option_text}")

        # Selection count footer
        if self.focused:
            count = len(self.selected_indices)
            if self.min_selections > 0 or self.max_selections < len(self.options):
                constraint_text = (
                    f" ({self.min_selections}-{self.max_selections} required)"
                )
            else:
                constraint_text = ""

            count_color = (
                self.colors.success_color
                if count >= self.min_selections
                else self.colors.error_color
            )
            footer = (
                f"{self.colors.muted_color}Selected:{self.colors.reset} "
                f"{count_color}{count}{self.colors.reset}/{len(self.options)}{constraint_text}"
            )
            lines.append(footer)

        return lines

    def render_modern(self, width: int = 50) -> List[str]:  # type: ignore[override]
        """Render multi-select with modern design system styling.

        Uses TagBox for label and checkbox items with selection indicators.

        Args:
            width: Total width of multi-select widget (default: 50).

        Returns:
            List containing multi-select display lines with modern styling.
        """
        lines = []
        label = self.get_label()
        tag_width = 3
        content_width = width - tag_width

        # Label line with checkbox icon
        if label:
            icon = " ☑️ " if self.focused else "   "
            tag_bg = T().primary[0] if self.focused else T().dark[0]
            tag_fg = T().text_dark if self.focused else T().text_dim
            content_colors = T().input_bg if self.focused else T().dark[0]
            content_fg = T().text if self.focused else T().text_dim

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
                use_gradient=self.focused,
            )
            lines.append(label_line)

        # Options with TagBox styling
        for i, option in enumerate(self.options):
            # Determine checkbox state
            is_selected = i in self.selected_indices
            is_cursor = self.focused and i == self.cursor_index

            if is_cursor:
                tag_icon = " ▶ "
                tag_bg = T().primary[0]
                tag_fg = T().text_dark
                content_colors = T().input_bg
                content_fg = T().text
            else:
                tag_icon = "   "
                tag_bg = T().dark[0]
                tag_fg = T().text_dim
                content_colors = T().success if is_selected else T().dark[0]
                content_fg = T().text_dark if is_selected else T().text_dim

            # Checkbox character
            check = self.check_char if is_selected else self.uncheck_char

            # Build content with checkbox
            content = f" [{check}] {option}"

            option_line = TagBox.render(
                lines=[content],
                tag_bg=tag_bg,
                tag_fg=tag_fg,
                tag_width=tag_width,
                content_colors=content_colors,
                content_fg=content_fg,
                content_width=content_width,
                tag_chars=[tag_icon],
                use_gradient=is_cursor,
            )
            lines.append(option_line)

        # Selection count footer
        if self.focused:
            count = len(self.selected_indices)
            if self.min_selections > 0 or self.max_selections < len(self.options):
                constraint_text = (
                    f" ({self.min_selections}-{self.max_selections} required)"
                )
            else:
                constraint_text = ""

            count_color = (
                T().success[0] if count >= self.min_selections else T().error[0]
            )
            content = f" Selected:{T().fg(count_color)} {count}{T().reset}/{len(self.options)}{constraint_text}"

            footer_line = TagBox.render(
                lines=[content],
                tag_bg=T().dim[0],
                tag_fg=T().text_dim,
                tag_width=tag_width,
                content_colors=T().dark[0],
                content_fg=T().text_dim,
                content_width=content_width,
                tag_chars=["   "],
                use_gradient=False,
            )
            lines.append(footer_line)

        return lines

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key press was handled by this widget.
        """
        # Arrow key navigation
        if key_press.key == "up" and key_press.is_cursor_key:
            if self.cursor_index > 0:
                self.cursor_index -= 1
            return True

        if key_press.key == "down" and key_press.is_cursor_key:
            if self.cursor_index < len(self.options) - 1:
                self.cursor_index += 1
            return True

        # Toggle selection with space
        if key_press.key == " ":
            self._toggle_selection(self.cursor_index)
            return True

        # Select all with Ctrl+A
        if key_press.key == "a" and key_press.ctrl:
            for i in range(len(self.options)):
                self.selected_indices.add(i)
            return True

        # Deselect all with Ctrl+D
        if key_press.key == "d" and key_press.ctrl:
            self.selected_indices.clear()
            return True

        # Enter confirms selection
        if key_press.key == "enter":
            self._save_selection()
            return True

        return False

    def _toggle_selection(self, index: int):
        """Toggle selection at index.

        Args:
            index: Option index to toggle.
        """
        if index in self.selected_indices:
            # Check minimum constraint before deselecting
            if len(self.selected_indices) > self.min_selections:
                self.selected_indices.remove(index)
        else:
            # Check maximum constraint before selecting
            if len(self.selected_indices) < self.max_selections:
                self.selected_indices.add(index)

    def _save_selection(self):
        """Save selected indices to config."""
        selected_list = sorted(list(self.selected_indices))
        self.set_value(selected_list)

    def get_value(self) -> Any:
        """Get selected values.

        Returns:
            List of selected option values (or indices based on config).
        """
        # If we have pending values, use those
        if self._pending_value is not None:
            selected_list = self._pending_value
        else:
            selected_list = sorted(list(self.selected_indices))

        # Return indices or actual values based on config
        return_mode = self.config.get("return_values", False)

        if return_mode:
            # Return actual option values
            return [self.options[i] for i in selected_list]
        else:
            # Return indices
            return selected_list

    def is_valid_value(self, value: Any) -> bool:
        """Validate selection meets constraints.

        Args:
            value: List of selected indices or values.

        Returns:
            True if selection is within min/max constraints.
        """
        if not isinstance(value, list):
            return False

        count = len(value)
        return int(self.min_selections) <= count <= int(self.max_selections)

    def set_focus(self, focused: bool):
        """Set focus state and reset cursor to first selected item.

        Args:
            focused: Whether widget should be focused.
        """
        super().set_focus(focused)

        if focused and self.selected_indices:
            # Move cursor to first selected item
            self.cursor_index = min(self.selected_indices)
        elif focused:
            # No selection, start at top
            self.cursor_index = 0
