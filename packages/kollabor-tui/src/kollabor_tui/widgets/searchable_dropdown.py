"""Searchable dropdown widget for filtering and selecting from long lists."""

from typing import Any, List

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette

from .base_widget import BaseWidget


class SearchableDropdownWidget(BaseWidget):
    """Dropdown widget with real-time search filtering.

    Displays a text input that filters options as you type.
    Ideal for selecting from long lists (models, commands, agents, plugins).

    Example config:
    {
        "label": "Select Model",
        "options": ["gpt-4", "gpt-3.5-turbo", "claude-3-opus", ...],
        "case_sensitive": False,
        "min_chars_to_filter": 1,
        "max_visible": 8
    }
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize searchable dropdown widget.

        Args:
            config: Widget configuration with options and filter settings.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing values.
        """
        super().__init__(config, config_path, config_service)

        # Options and filtering
        self.options = self.config.get("options", [])
        self.case_sensitive = self.config.get("case_sensitive", False)
        self.min_chars_to_filter = self.config.get("min_chars_to_filter", 1)
        self.max_visible = self.config.get("max_visible", 8)

        # State
        self.search_query = ""
        self.filtered_options = self.options.copy()
        self.cursor_index = 0
        self.dropdown_open = False
        self.matching_chars: List[str] = []  # Track which chars match for highlighting

        # Colors
        self.colors = ColorPalette()

        # Get initial selected value
        current_value = self.get_value()
        if current_value in self.options:
            self.cursor_index = self.options.index(current_value)
            self.search_query = current_value

    def render(self) -> List[str]:
        """Render searchable dropdown widget.

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

        # Search input line
        if self.focused or self.search_query:
            # Input box with cursor
            cursor_pos = len(self.search_query)
            display_text = self.search_query

            # Highlight matching characters in filtered options
            if self.dropdown_open and self.search_query:
                display_text = self._highlight_search_text(display_text)

            # Add cursor indicator
            if self.focused:
                cursor_render = f"{self.colors.highlight}{self.colors.background}▌{self.colors.reset}"
                if cursor_pos < len(display_text):
                    display_text = (
                        display_text[:cursor_pos]
                        + cursor_render
                        + display_text[cursor_pos + 1 :]
                    )
                else:
                    display_text = display_text + cursor_render

            # Border around input
            border_color = (
                self.colors.accent_color if self.focused else self.colors.muted_color
            )
            padding = " " * max(0, 40 - len(display_text))
            input_display = (
                f"{border_color}┌─▸{self.colors.reset} {display_text}{padding}"
                f"{border_color}┐{self.colors.reset}"
            )
            lines.append(input_display)
        else:
            # Closed state - show current selection
            current_value = self.get_value()
            border_color = (
                self.colors.accent_color if self.focused else self.colors.muted_color
            )
            display = (
                f"{current_value}"
                if current_value
                else f"{self.colors.muted_color}Select...{self.colors.reset}"
            )
            lines.append(f"{border_color}▸ {self.colors.reset}{display}")

        # Dropdown options (when open)
        if self.dropdown_open:
            visible_options = self.filtered_options[: self.max_visible]

            for i, option in enumerate(visible_options):
                # Highlight selected option
                if i == self.cursor_index:
                    prefix = f"{self.colors.highlight}{self.colors.background}▶ "
                else:
                    prefix = "  "

                # Highlight matching characters
                if (
                    self.search_query
                    and len(self.search_query) >= self.min_chars_to_filter
                ):
                    option_display = self._highlight_match(option)
                else:
                    option_display = option

                # Reset color after highlight
                if i == self.cursor_index:
                    option_display = f"{option_display}{self.colors.reset}"

                lines.append(f"{prefix}{option_display}")

            # Show match count
            if self.search_query and self.case_sensitive is False:
                total_matches = len(self.filtered_options)
                if total_matches < len(self.options):
                    match_info = f"{self.colors.muted_color}({total_matches} matches){self.colors.reset}"
                    lines.append(match_info)

        # Help text when focused
        if self.focused and not self.dropdown_open:
            help_text = f"{self.colors.muted_color}Type to search, Enter to open dropdown{self.colors.reset}"
            lines.append(help_text)
        elif self.focused and self.dropdown_open:
            help_text = f"{self.colors.muted_color}↑↓: Navigate | Enter: Select | Esc: Close{self.colors.reset}"
            lines.append(help_text)

        return lines

    def render_modern(self, width: int = 50) -> List[str]:  # type: ignore[override]
        """Render searchable dropdown with modern design system styling.

        Uses TagBox for label and search input, with bullet indicators for options.

        Args:
            width: Total width of searchable dropdown widget (default: 50).

        Returns:
            List containing searchable dropdown display lines with modern styling.
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

        # Label line with search icon
        if label:
            icon = " 🔍 " if self.focused else "   "
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

        # Search input line
        display_text = self.search_query
        cursor_pos = len(self.search_query)

        if self.focused:
            cursor_render = f"{T().highlight[1]}{T().bg_highlight}▌{T().reset}"
            if cursor_pos < len(display_text):
                display_text = (
                    display_text[:cursor_pos]
                    + cursor_render
                    + display_text[cursor_pos + 1 :]
                )
            else:
                display_text = display_text + cursor_render

        # Search input with TagBox
        icon = " ▸ " if (self.focused or self.search_query) else "   "
        content = f" {display_text}"

        input_line = TagBox.render(
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
        lines.append(input_line)

        # Dropdown options when open
        if self.dropdown_open:
            visible_options = self.filtered_options[: self.max_visible]

            for i, option in enumerate(visible_options):
                # Bullet for selected, empty bullet for others
                if i == self.cursor_index:
                    prefix = "●"  # Filled bullet
                else:
                    prefix = "○"  # Empty bullet

                # Highlight matching characters
                if (
                    self.search_query
                    and len(self.search_query) >= self.min_chars_to_filter
                ):
                    option_display = self._highlight_match(option)
                else:
                    option_display = option

                content = f"     {prefix} {option_display}"
                lines.append(content)

            # Show match count
            if self.search_query and len(self.filtered_options) < len(self.options):
                total_matches = len(self.filtered_options)
                match_info = (
                    f"     {T().fg(T().dim[0])}({total_matches} matches){T().reset}"
                )
                lines.append(match_info)

        # Help text when focused
        if self.focused and not self.dropdown_open:
            help_text = (
                f"{T().fg(T().dim[0])}Type to search, Enter to open dropdown{T().reset}"
            )
            lines.append(help_text)
        elif self.focused and self.dropdown_open:
            help_text = f"{T().fg(T().dim[0])}↑↓: Navigate | Enter: Select | Esc: Close{T().reset}"
            lines.append(help_text)

        return lines

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key press was handled by this widget.
        """
        # Toggle dropdown with Enter
        if key_press.key == "enter":
            if self.dropdown_open:
                # Confirm selection
                if self.filtered_options:
                    selected = self.filtered_options[self.cursor_index]
                    self.set_value(selected)
                    self.search_query = selected
                self.dropdown_open = False
            else:
                # Open dropdown
                self.dropdown_open = True
                self._filter_options()
            return True

        # Close dropdown with Esc
        if key_press.key == "escape":
            self.dropdown_open = False
            return True

        # Navigate filtered options
        if self.dropdown_open:
            if key_press.key == "up" and key_press.is_cursor_key:
                if self.cursor_index > 0:
                    self.cursor_index -= 1
                return True

            if key_press.key == "down" and key_press.is_cursor_key:
                if self.cursor_index < len(self.filtered_options) - 1:
                    self.cursor_index += 1
                return True

        # Text input for search
        if (
            self.focused
            and key_press.char
            and len(key_press.char) == 1
            and ord(key_press.char) >= 32
        ):
            self.search_query += key_press.char
            if not self.dropdown_open:
                self.dropdown_open = True
            self._filter_options()
            return True

        # Backspace to delete
        if key_press.key == "backspace":
            if self.search_query:
                self.search_query = self.search_query[:-1]
                self._filter_options()
            return True

        # Ctrl+C to clear search
        if key_press.key == "c" and key_press.ctrl:
            self.search_query = ""
            self._filter_options()
            return True

        return False

    def _filter_options(self):
        """Filter options based on search query."""
        if not self.search_query or len(self.search_query) < self.min_chars_to_filter:
            self.filtered_options = self.options.copy()
            self.cursor_index = 0
            return

        query = self.search_query if self.case_sensitive else self.search_query.lower()

        # Filter and find matches
        self.filtered_options = []
        for option in self.options:
            search_text = option if self.case_sensitive else option.lower()
            if query in search_text:
                self.filtered_options.append(option)

        # Reset cursor to first match
        self.cursor_index = 0

    def _highlight_match(self, option: str) -> str:
        """Highlight matching characters in option.

        Args:
            option: Option text to highlight.

        Returns:
            Option with matching characters highlighted.
        """
        if not self.search_query:
            return option

        query = self.search_query if self.case_sensitive else self.search_query.lower()
        search_text = option if self.case_sensitive else option.lower()

        # Find match position
        match_pos = search_text.find(query)
        if match_pos == -1:
            return option

        # Highlight match
        before = option[:match_pos]
        match = option[match_pos : match_pos + len(self.search_query)]
        after = option[match_pos + len(self.search_query) :]

        return f"{before}{self.colors.accent_color}{match}{self.colors.reset}{after}"

    def _highlight_search_text(self, text: str) -> str:
        """Highlight search query in input text.

        Args:
            text: Input text to highlight.

        Returns:
            Text with query highlighted.
        """
        if not self.search_query:
            return text

        # Just color the entire search query
        return f"{self.colors.primary_color}{text}{self.colors.reset}"

    def is_valid_value(self, value: Any) -> bool:
        """Validate if value is in options.

        Args:
            value: Value to validate.

        Returns:
            True if value is in options list.
        """
        return value in self.options
