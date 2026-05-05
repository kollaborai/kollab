"""Text area widget for multi-line text input."""

from typing import Any, List

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette

from .base_widget import BaseWidget


class TextAreaWidget(BaseWidget):
    """Widget for multi-line text input with scrolling.

    Supports cursor navigation, text editing, and line wrapping.
    Ideal for system prompts, long descriptions, and code snippets.

    Example config:
    {
        "label": "System Prompt",
        "placeholder": "Enter your system prompt...",
        "rows": 10,
        "cols": 60,
        "max_length": 5000,
        "line_wrap": True,
        "show_line_numbers": False
    }
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize text area widget.

        Args:
            config: Widget configuration with size and display options.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing values.
        """
        super().__init__(config, config_path, config_service)

        # Display dimensions
        self.rows = self.config.get("rows", 10)
        self.cols = self.config.get("cols", 60)

        # Content
        initial_text = str(self.get_value()) if self.get_value() else ""
        self.lines = initial_text.split("\n")
        self.cursor_row = 0
        self.cursor_col = 0
        self.scroll_row = 0
        self.scroll_col = 0

        # Display options
        self.placeholder = self.config.get("placeholder", "")
        self.max_length = self.config.get("max_length", 5000)
        self.line_wrap = self.config.get("line_wrap", False)
        self.show_line_numbers = self.config.get("show_line_numbers", False)

        # Colors
        self.colors = ColorPalette()

    def render(self) -> List[str]:
        """Render text area widget.

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
            # Character count
            current_length = self._get_text_length()
            length_info = (
                f" ({current_length}/{self.max_length})" if self.max_length else ""
            )
            lines.append(f"{label_color}{label}{self.colors.reset}{length_info}")

        # Border top
        border_color = (
            self.colors.accent_color if self.focused else self.colors.muted_color
        )
        if self.show_line_numbers:
            line_num_width = len(str(len(self.lines))) + 1
            border = f"{border_color}┌{'─' * line_num_width}┬{'─' * (self.cols - line_num_width)}┐{self.colors.reset}"
        else:
            border = f"{border_color}┌{'─' * self.cols}┐{self.colors.reset}"
        lines.append(border)

        # Content lines
        display_lines = self._get_display_lines()

        for display_row in range(self.rows):
            if display_row < len(display_lines):
                content = display_lines[display_row]

                # Add line numbers if enabled
                if self.show_line_numbers:
                    actual_line_num = display_row + self.scroll_row
                    line_num = f"{border_color}{actual_line_num + 1:>{line_num_width - 1}}│{self.colors.reset}"
                else:
                    line_num = f"{border_color}│{self.colors.reset}"

                # Content with cursor
                if self.focused and display_row == self.cursor_row - self.scroll_row:
                    before = content[: self.cursor_col - self.scroll_col]
                    cursor = (
                        content[self.cursor_col - self.scroll_col]
                        if self.cursor_col - self.scroll_col < len(content)
                        else " "
                    )
                    after = content[self.cursor_col - self.scroll_col + 1 :]

                    # Cursor highlighting
                    cursor_render = f"{self.colors.highlight}{self.colors.background}{cursor}{self.colors.reset}"

                    # Truncate to fit
                    available_width = self.cols - (
                        line_num_width if self.show_line_numbers else 1
                    )
                    before = (
                        before[-(available_width // 2) :]
                        if len(before) > available_width // 2
                        else before
                    )
                    after = after[: available_width - len(before) - 1]

                    content = before + cursor_render + after

                # Wrap or truncate
                if self.line_wrap:
                    content = self._wrap_line(
                        content,
                        self.cols - (line_num_width if self.show_line_numbers else 1),
                    )
                else:
                    available_width = self.cols - (
                        line_num_width if self.show_line_numbers else 1
                    )
                    content = content[:available_width].ljust(available_width)

                lines.append(f"{line_num}{content}")
            else:
                # Empty row
                if self.show_line_numbers:
                    empty_row = " " * (self.cols - line_num_width)
                    lines.append(
                        f"{border_color}{' ' * line_num_width}│{self.colors.reset}{empty_row}"
                    )
                else:
                    lines.append(
                        f"{border_color}│{' ' * self.cols}│{self.colors.reset}"
                    )

        # Border bottom
        if self.show_line_numbers:
            border = f"{border_color}└{'─' * line_num_width}┴{'─' * (self.cols - line_num_width)}┘{self.colors.reset}"
        else:
            border = f"{border_color}└{'─' * self.cols}┘{self.colors.reset}"
        lines.append(border)

        # Help text when focused
        if self.focused:
            help_text = (
                f"{self.colors.muted_color}Arrows: Navigate | Enter: New line | "
                f"Ctrl+D: Delete line{self.colors.reset}"
            )
            lines.append(help_text)

        return lines

    def render_modern(self, width: int = 50) -> List[str]:  # type: ignore[override]
        """Render text area with modern design system styling.

        Uses TagBox for label line and custom border for the text area.

        Args:
            width: Total width of text area widget (default: 50).

        Returns:
            List containing text area display lines with modern styling.
        """
        lines = []
        label = self.get_label()

        # Label line with TagBox styling
        if label:
            tag_width = 3
            content_width = width - tag_width
            icon = " 📝 " if self.focused else "   "
            tag_bg = T().primary[0] if self.focused else T().dark[0]
            tag_fg = T().text_dark if self.focused else T().text_dim
            content_colors = T().input_bg if self.focused else T().dark[0]
            content_fg = T().text if self.focused else T().text_dim

            # Character count
            current_length = self._get_text_length()
            length_info = (
                f" ({current_length}/{self.max_length})" if self.max_length else ""
            )
            content = f" {label}{length_info}"

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

        # Text area with borders
        border_color = T().primary[0] if self.focused else T().dim[0]
        actual_width = min(width, self.cols)

        # Top border
        top_border = f"{border_color}┌{'─' * actual_width}┐"
        lines.append(top_border)

        # Content lines with cursor
        display_lines = self._get_display_lines()
        for display_row in range(self.rows):
            if display_row < len(display_lines):
                content = display_lines[display_row]

                # Add cursor highlighting
                if self.focused and display_row == self.cursor_row - self.scroll_row:
                    before = content[: self.cursor_col - self.scroll_col]
                    cursor = (
                        content[self.cursor_col - self.scroll_col]
                        if self.cursor_col - self.scroll_col < len(content)
                        else " "
                    )
                    after = content[self.cursor_col - self.scroll_col + 1 :]

                    # Cursor highlighting with theme colors
                    cursor_render = (
                        f"{T().highlight[1]}{T().bg_highlight}{cursor}{T().reset}"
                    )
                    available_width = actual_width
                    before = (
                        before[-(available_width // 2) :]
                        if len(before) > available_width // 2
                        else before
                    )
                    after = after[: available_width - len(before) - 1]
                    content = before + cursor_render + after

                # Wrap or truncate
                if self.line_wrap:
                    content = self._wrap_line(content, actual_width)
                else:
                    content = content[:actual_width].ljust(actual_width)

                lines.append(
                    f"{T().fg(border_color)}│{T().reset}{content}{T().fg(border_color)}│{T().reset}"
                )
            else:
                # Empty row
                lines.append(f"{T().fg(border_color)}│{' ' * actual_width}│{T().reset}")

        # Bottom border
        bottom_border = f"{T().fg(border_color)}└{'─' * actual_width}┘{T().reset}"
        lines.append(bottom_border)

        # Help text when focused
        if self.focused:
            help_text = f"{T().fg(T().dim[0])}Arrows: Navigate | Enter: New line | Ctrl+D: Delete line{T().reset}"
            lines.append(help_text)

        return lines

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key press was handled by this widget.
        """
        # Cursor navigation
        if key_press.key == "up" and key_press.is_cursor_key:
            if self.cursor_row > 0:
                self.cursor_row -= 1
                self._adjust_cursor_col()
                self._scroll_to_cursor()
            return True

        if key_press.key == "down" and key_press.is_cursor_key:
            if self.cursor_row < len(self.lines) - 1:
                self.cursor_row += 1
                self._adjust_cursor_col()
                self._scroll_to_cursor()
            return True

        if key_press.key == "left" and key_press.is_cursor_key:
            if self.cursor_col > 0:
                self.cursor_col -= 1
                self._scroll_to_cursor()
            return True

        if key_press.key == "right" and key_press.is_cursor_key:
            if self.cursor_col < len(self.lines[self.cursor_row]):
                self.cursor_col += 1
                self._scroll_to_cursor()
            return True

        # Line operations
        if key_press.key == "home":
            self.cursor_col = 0
            self.scroll_col = 0
            return True

        if key_press.key == "end":
            self.cursor_col = len(self.lines[self.cursor_row])
            self._scroll_to_cursor()
            return True

        if key_press.key == "pageup" and key_press.is_cursor_key:
            self.cursor_row = max(0, self.cursor_row - self.rows)
            self._adjust_cursor_col()
            self._scroll_to_cursor()
            return True

        if key_press.key == "pagedown" and key_press.is_cursor_key:
            self.cursor_row = min(len(self.lines) - 1, self.cursor_row + self.rows)
            self._adjust_cursor_col()
            self._scroll_to_cursor()
            return True

        # Text editing
        if key_press.key == "enter":
            self._insert_newline()
            return True

        if key_press.key == "backspace":
            self._delete_char(backwards=True)
            return True

        if key_press.key == "delete":
            self._delete_char(backwards=False)
            return True

        if key_press.key == "d" and key_press.ctrl:
            self._delete_line()
            return True

        if key_press.key == "k" and key_press.ctrl:
            self._delete_to_end_of_line()
            return True

        if key_press.key == "u" and key_press.ctrl:
            self._delete_to_start_of_line()
            return True

        # Regular character input
        if key_press.char and len(key_press.char) == 1 and ord(key_press.char) >= 32:
            self._insert_char(key_press.char)
            return True

        return False

    def get_value(self) -> Any:
        """Get current text content.

        Returns:
            Multi-line text string.
        """
        return "\n".join(self.lines)

    def _get_display_lines(self) -> List[str]:
        """Get visible lines based on scroll position.

        Returns:
            List of visible lines.
        """
        visible_lines = self.lines[self.scroll_row : self.scroll_row + self.rows]

        # Show placeholder if empty
        if not visible_lines or (len(visible_lines) == 1 and not visible_lines[0]):
            return [self.placeholder] if self.placeholder else [""]

        return visible_lines

    def _insert_char(self, char: str):
        """Insert character at cursor position.

        Args:
            char: Character to insert.
        """
        # Check max length
        if self.max_length and self._get_text_length() >= self.max_length:
            return

        line = self.lines[self.cursor_row]
        self.lines[self.cursor_row] = (
            line[: self.cursor_col] + char + line[self.cursor_col :]
        )
        self.cursor_col += 1
        self._scroll_to_cursor()

    def _insert_newline(self):
        """Insert newline at cursor position."""
        # Check max length
        if self.max_length and self._get_text_length() >= self.max_length:
            return

        line = self.lines[self.cursor_row]
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]

        self.lines[self.cursor_row] = before
        self.lines.insert(self.cursor_row + 1, after)

        self.cursor_row += 1
        self.cursor_col = 0
        self._scroll_to_cursor()

    def _delete_char(self, backwards: bool = True):
        """Delete character at or before cursor.

        Args:
            backwards: True to delete before cursor (backspace), False for after (delete).
        """
        if backwards:
            if self.cursor_col > 0:
                # Delete character in current line
                line = self.lines[self.cursor_row]
                self.lines[self.cursor_row] = (
                    line[: self.cursor_col - 1] + line[self.cursor_col :]
                )
                self.cursor_col -= 1
            elif self.cursor_row > 0:
                # Join with previous line
                prev_line = self.lines[self.cursor_row - 1]
                curr_line = self.lines[self.cursor_row]
                self.cursor_col = len(prev_line)
                self.lines[self.cursor_row - 1] = prev_line + curr_line
                self.lines.pop(self.cursor_row)
                self.cursor_row -= 1
        else:
            if self.cursor_col < len(self.lines[self.cursor_row]):
                # Delete character after cursor
                line = self.lines[self.cursor_row]
                self.lines[self.cursor_row] = (
                    line[: self.cursor_col] + line[self.cursor_col + 1 :]
                )
            elif self.cursor_row < len(self.lines) - 1:
                # Join with next line
                curr_line = self.lines[self.cursor_row]
                next_line = self.lines[self.cursor_row + 1]
                self.lines[self.cursor_row] = curr_line + next_line
                self.lines.pop(self.cursor_row + 1)

        self._scroll_to_cursor()

    def _delete_line(self):
        """Delete current line."""
        if len(self.lines) > 1:
            self.lines.pop(self.cursor_row)
            if self.cursor_row >= len(self.lines):
                self.cursor_row = len(self.lines) - 1
            self.cursor_col = min(self.cursor_col, len(self.lines[self.cursor_row]))

    def _delete_to_end_of_line(self):
        """Delete from cursor to end of line."""
        self.lines[self.cursor_row] = self.lines[self.cursor_row][: self.cursor_col]

    def _delete_to_start_of_line(self):
        """Delete from start of line to cursor."""
        self.lines[self.cursor_row] = self.lines[self.cursor_row][self.cursor_col :]
        self.cursor_col = 0

    def _adjust_cursor_col(self):
        """Adjust cursor column if line is shorter."""
        line_len = len(self.lines[self.cursor_row])
        if self.cursor_col > line_len:
            self.cursor_col = line_len

    def _scroll_to_cursor(self):
        """Scroll view to keep cursor visible."""
        # Vertical scroll
        if self.cursor_row < self.scroll_row:
            self.scroll_row = self.cursor_row
        elif self.cursor_row >= self.scroll_row + self.rows:
            self.scroll_row = self.cursor_row - self.rows + 1

        # Horizontal scroll
        if self.cursor_col < self.scroll_col:
            self.scroll_col = self.cursor_col
        elif self.cursor_col >= self.scroll_col + self.cols:
            self.scroll_col = self.cursor_col - self.cols + 1

    def _wrap_line(self, line: str, width: int) -> str:
        """Wrap line to fit width.

        Args:
            line: Line to wrap.
            width: Maximum width.

        Returns:
            Wrapped line or truncated line.
        """
        if len(line) <= width:
            return line.ljust(width)
        return line[:width]

    def _get_text_length(self) -> int:
        """Get total length of all text.

        Returns:
            Total character count.
        """
        return sum(len(line) for line in self.lines)
