from typing import List

from kollabor_tui.design_system import T, gradient, solid_fg


class ModernInputRenderer:
    """Renders multi-line input box using design system."""

    def __init__(self, config):
        self.config = config

    def render(
        self,
        buffer_content: str,
        cursor_position: int,
        cursor_char: str,
        width: int,
        is_thinking: bool = False,
    ) -> List[str]:
        """Render the input box.

        Args:
            buffer_content: Raw input buffer (may contain newlines)
            cursor_position: Absolute cursor position in buffer
            cursor_char: Cursor character to display
            width: Box width
            is_thinking: Whether LLM is processing (hide cursor)

        Returns:
            List of rendered lines
        """
        lines = []

        # Determine prompt
        is_shell = buffer_content.lstrip().startswith("!")
        prompt = "!" if is_shell else "⠠⠵"
        continuation = "  "  # 2 spaces to match prompt width

        # Process buffer for display
        display_buffer, display_cursor = self._process_buffer(
            buffer_content, cursor_position, is_shell
        )

        # Split into lines
        input_lines = display_buffer.split("\n") if display_buffer else [""]

        # Calculate visible window
        cursor_line, cursor_col = self._get_cursor_line_col(
            display_buffer, display_cursor, input_lines
        )
        visible_start, visible_end = self._get_visible_window(
            cursor_line, len(input_lines)
        )

        visible_lines = input_lines[visible_start:visible_end]
        has_above = visible_start > 0
        has_below = visible_end < len(input_lines)

        # Top border
        lines.append(solid_fg("▄" * width, T().dark[0]))

        # Content lines
        for i, line_text in enumerate(visible_lines):
            actual_line_idx = visible_start + i
            is_first_visible = i == 0
            is_last_visible = i == len(visible_lines) - 1

            # Determine scroll indicator for this line
            scroll_indicator = " "
            if is_first_visible and has_above:
                scroll_indicator = "▲"
            elif is_last_visible and has_below:
                scroll_indicator = "▼"

            line_prompt = prompt if actual_line_idx == 0 else continuation

            # Add cursor if on this line
            if not is_thinking and actual_line_idx == cursor_line:
                col = min(cursor_col, len(line_text))
                text = line_text[:col] + cursor_char + line_text[col:]
            elif not is_thinking and not display_buffer and i == 0:
                # Empty buffer - show placeholder or just cursor
                if self.config.show_placeholder:
                    text = cursor_char + self._dim_text(self.config.placeholder)
                else:
                    text = cursor_char
            else:
                text = line_text

            content = f"{scroll_indicator} {line_prompt} {text}".ljust(width)[:width]
            lines.append(gradient(content, T().input_bg, T().text, width))

        # Bottom border
        lines.append(solid_fg("▀" * width, T().dark[0]))

        return lines

    def _process_buffer(self, buffer: str, cursor_pos: int, is_shell: bool):
        """Process buffer for display (strip shell ! prefix)."""
        if not is_shell or not buffer:
            return buffer, cursor_pos

        stripped = buffer.lstrip()
        leading_ws = len(buffer) - len(stripped)

        if stripped.startswith("!"):
            display = buffer[:leading_ws] + stripped[1:]
            if cursor_pos > leading_ws + 1:
                cursor_pos -= 1
            elif cursor_pos > leading_ws:
                cursor_pos = leading_ws
            return display, cursor_pos
        return buffer, cursor_pos

    def _get_cursor_line_col(self, buffer: str, cursor_pos: int, lines: List[str]):
        """Calculate cursor line and column."""
        chars = 0
        for i, line in enumerate(lines):
            line_end = chars + len(line)
            if cursor_pos <= line_end:
                return i, cursor_pos - chars
            chars = line_end + 1
        return len(lines) - 1, len(lines[-1]) if lines else 0

    def _get_visible_window(self, cursor_line: int, total_lines: int):
        """Calculate visible line window."""
        max_visible = self.config.max_visible_lines

        if total_lines <= max_visible:
            return 0, total_lines

        # Center cursor in visible window when possible
        half = max_visible // 2
        start = max(0, cursor_line - half)
        end = start + max_visible

        if end > total_lines:
            end = total_lines
            start = max(0, end - max_visible)

        return start, end

    def _dim_text(self, text: str) -> str:
        """Apply dim styling to text."""
        return f"\033[2m{text}\033[22m"
