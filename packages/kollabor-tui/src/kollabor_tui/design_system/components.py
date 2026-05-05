"""
UI Components - Solid colors and box components.

This module provides UI components for rendering solid-colored backgrounds
and box components with configurable border styles for visual containers.

Border styles can be configured to handle different terminal rendering:
    - half_blocks: Uses ▄▀ characters (default, best visual quality)
    - lines: Uses Unicode box-drawing ─│┌┐└┘ (most compatible)
    - ascii: Uses simple ASCII +--+ (universal fallback)
    - none: No visible borders (solid color blocks only)
"""

import re
from typing import Any, List, Optional, Tuple

from .border_style import get_border_style
from .color_mode import _bg_code, _fg_code
from .gradient import _split_ansi, gradient, gradient_fg

__all__ = [
    "solid",
    "solid_fg",
    "Box",
    "TagBox",
    "C",
    "wrap_text",
    "progress_bar",
]


# ANSI escape code pattern for stripping
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(text):
    """Get visible length of text (excluding ANSI codes)."""
    return len(_ANSI_RE.sub("", text))


def wrap_text(text, width, word_wrap=True, continuation_indent=0):
    """Wrap text to fit within width, preserving ANSI codes.

    Args:
        text: Text string (may contain ANSI codes)
        width: Maximum visible width per line
        word_wrap: If True, try to wrap at word boundaries
        continuation_indent: Number of spaces to add to continuation lines (default: 0)

    Returns:
        List of wrapped lines (always at least one line)
    """
    # Handle edge cases
    if not text:
        return [""]
    if width <= 0:
        return [text]

    # Strip ANSI to get visible text
    visible = _ANSI_RE.sub("", text)

    # If it fits, return as-is
    if len(visible) <= width:
        return [text]

    # Simple word-wrap approach: split into words, rebuild lines
    if word_wrap:
        lines = _wrap_words(text, width)
    else:
        lines = _wrap_chars(text, width)

    # Apply continuation indentation to all lines except the first
    if continuation_indent > 0 and len(lines) > 1:
        indent_str = " " * continuation_indent
        lines[1:] = [indent_str + line for line in lines[1:]]

    return lines


def _wrap_words(text, width):
    """Wrap text at word boundaries, preserving ANSI codes."""
    # Build a list of (word, ansi_prefix) tuples
    words = []
    current_word = ""
    current_ansi = ""
    active_codes = []

    i = 0
    while i < len(text):
        # Check for ANSI escape sequence
        match = _ANSI_RE.match(text[i:])
        if match:
            code = match.group()
            current_ansi += code
            if code == "\033[0m":
                active_codes = []
            else:
                active_codes.append(code)
            i += len(code)
            continue

        char = text[i]
        if char == " ":
            # End of word
            if current_word:
                words.append((current_word, current_ansi, list(active_codes)))
                current_word = ""
                current_ansi = ""
            words.append((" ", "", list(active_codes)))  # Space as separate "word"
        else:
            current_word += char
        i += 1

    # Don't forget last word
    if current_word:
        words.append((current_word, current_ansi, list(active_codes)))

    # Build lines from words
    lines = []
    current_line = ""
    current_len = 0
    line_codes = []

    for word, ansi_prefix, codes_after in words:
        word_len = len(word)

        # Handle words longer than width by breaking them
        if word_len > width and current_len == 0:
            # Word is too long even for an empty line - break it
            broken = _wrap_chars(ansi_prefix + word, width)
            lines.extend(broken[:-1])
            if broken:
                last_broken = broken[-1]
                current_line = last_broken
                current_len = _visible_len(last_broken)
                line_codes = codes_after
            continue

        # Would this word exceed the width?
        if current_len + word_len > width and current_len > 0:
            # Start new line
            if line_codes:
                current_line += "\033[0m"
            lines.append(current_line)
            current_line = "".join(codes_after) + ansi_prefix
            current_len = 0
            line_codes = list(codes_after)

            # Skip leading spaces on new line
            if word == " ":
                continue

        current_line += ansi_prefix + word
        current_len += word_len
        line_codes = list(codes_after)

    # Add final line
    if current_line.strip() or not lines:
        lines.append(current_line)

    return lines if lines else [""]


def _wrap_chars(text, width):
    """Wrap text at character boundaries, preserving ANSI codes."""
    lines = []
    current_line = ""
    current_visible_len = 0
    active_codes = []

    i = 0
    while i < len(text):
        match = _ANSI_RE.match(text[i:])
        if match:
            code = match.group()
            current_line += code
            if code == "\033[0m":
                active_codes = []
            else:
                active_codes.append(code)
            i += len(code)
            continue

        char = text[i]
        if current_visible_len >= width:
            if active_codes:
                current_line += "\033[0m"
            lines.append(current_line)
            current_line = "".join(active_codes)
            current_visible_len = 0

        current_line += char
        current_visible_len += 1
        i += 1

    if current_line or not lines:
        lines.append(current_line)

    return lines


# Unicode characters for UI elements
C = {
    # Checkboxes
    "check_on": "✔",
    "check_off": "☐",
    "check_box_on": "☑",
    "check_box_off": "☐",
    # Progress bars - smooth gradient blocks
    "bar_full": "█",
    "bar_7_8": "▉",
    "bar_6_8": "▊",
    "bar_5_8": "▋",
    "bar_4_8": "▌",
    "bar_3_8": "▍",
    "bar_2_8": "▎",
    "bar_1_8": "▏",
    "bar_empty": "░",
    "bar_shade": "▒",
    # Arrows and indicators
    "arrow_right": "▶",
    "arrow_down": "▼",
    "arrow_up": "▲",
    "triangle_right": "▸",
    "triangle_down": "▾",
    "triangle_up": "▴",
    # Bullets and dots
    "bullet": "●",
    "bullet_empty": "○",
    "diamond": "◆",
    "diamond_empty": "◇",
    "square": "■",
    "square_empty": "□",
    # Editing
    "cursor": "▌",
    "cursor_block": "█",
    # Status
    "success": "✔",
    "error": "✖",
    "warning": "⚠",
    "info": "ℹ",
    # Tool call status
    "tool_running": "▶",
    "tool_success": "✔",
    "tool_error": "✖",
    # Spinner frames
    "spin": ["◐", "◓", "◑", "◒"],
    # Braille spinners (smoother animation)
    "spin_braille": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
    # Lines and separators
    "line_h": "─",
    "line_v": "│",
    "line_h_light": "┄",
    "line_v_light": "┆",
    # Corners (for custom boxes)
    "corner_tl": "┌",
    "corner_tr": "┐",
    "corner_bl": "└",
    "corner_br": "┘",
    # T-junctions
    "t_left": "├",
    "t_right": "┤",
    "t_top": "┬",
    "t_bottom": "┴",
    "t_cross": "┼",
    # Special characters
    "gear": "⚙",
    "lightning": "⚡",
    "star": "★",
    "star_empty": "☆",
    "heart": "♥",
    "prompt": "❯",
    "ellipsis": "…",
    # Half blocks (already used in Box/TagBox, exposed here too)
    "half_top": "▀",
    "half_bottom": "▄",
    "half_left": "▌",
    "half_right": "▐",
}


def progress_bar(progress, width=15):
    """Create smooth progress bar using partial block characters.

    Args:
        progress: Float from 0.0 to 1.0 representing completion
        width: Total width of the bar in characters

    Returns:
        String containing the progress bar characters

    Example:
        >>> progress_bar(0.5, 10)
        '█████░░░░░'
        >>> progress_bar(0.75, 8)
        '██████▌░'
    """
    # Clamp progress to valid range
    progress = max(0.0, min(1.0, progress))

    # Calculate filled portion
    filled_width = progress * width
    full_blocks = int(filled_width)
    partial = filled_width - full_blocks

    # Build bar with full blocks
    bar = C["bar_full"] * full_blocks

    # Add partial block for smooth edge
    if full_blocks < width:
        if partial >= 0.875:
            bar += C["bar_7_8"]
        elif partial >= 0.75:
            bar += C["bar_6_8"]
        elif partial >= 0.625:
            bar += C["bar_5_8"]
        elif partial >= 0.5:
            bar += C["bar_4_8"]
        elif partial >= 0.375:
            bar += C["bar_3_8"]
        elif partial >= 0.25:
            bar += C["bar_2_8"]
        elif partial >= 0.125:
            bar += C["bar_1_8"]
        else:
            bar += C["bar_empty"]

        # Fill remaining with empty
        remaining = width - len(bar)
        bar += C["bar_empty"] * remaining

    return bar


def solid(text, bg, fg, width=None):
    """Solid background color with consistent foreground per character.

    Args:
        text: Text string to render (may contain ANSI codes)
        bg: RGB tuple for background (r, g, b)
        fg: RGB tuple for foreground (r, g, b)
        width: Optional width to pad text to

    Returns:
        String with ANSI color codes for solid background
    """
    visible, ansi_at = _split_ansi(text)

    # Pad visible chars if needed
    if width and len(visible) < width:
        visible.extend([" "] * (width - len(visible)))

    # Pre-compute color codes once
    bg_code = _bg_code(bg[0], bg[1], bg[2])
    fg_code = _fg_code(fg[0], fg[1], fg[2])

    result = []
    for i, char in enumerate(visible):
        if i in ansi_at:
            result.extend(ansi_at[i])
        result.append(f"\033[0;{bg_code};{fg_code}m{char}")

    if len(visible) in ansi_at:
        result.extend(ansi_at[len(visible)])

    return "".join(result) + "\033[0m"


def solid_fg(text, color):
    """Solid foreground color (no gradient).

    Args:
        text: Text string to color
        color: RGB tuple (r, g, b)

    Returns:
        String with ANSI foreground color codes
    """
    fg_code = _fg_code(color[0], color[1], color[2])
    return f"\033[{fg_code}m{text}\033[0m"


class Box:
    """Smart box with configurable border styles.

    Supports multiple border styles:
    - half_blocks: Uses ▄▀ characters (default, best visual quality)
    - lines: Uses Unicode box-drawing ─│┌┐└┘ (most compatible)
    - ascii: Uses simple ASCII +--+ (universal fallback)
    - none: No visible borders (solid color blocks only)

    Use set_border_style() to change the active style globally.
    """

    @staticmethod
    def top(colors, width):
        """Top edge with current border style.

        Args:
            colors: List of RGB tuples for gradient
            width: Width of the edge in characters

        Returns:
            String with gradient-colored top edge
        """
        style = get_border_style()
        if style.uses_half_blocks:
            return gradient_fg(style.top * width, colors, width)
        else:
            # Line-drawing style: corner + horizontal line + corner
            if width >= 2:
                inner = style.top * (width - 2)
                edge = style.corner_tl + inner + style.corner_tr
            else:
                edge = style.top * width
            return gradient_fg(edge, colors, width)

    @staticmethod
    def bottom(colors, width):
        """Bottom edge with current border style.

        Args:
            colors: List of RGB tuples for gradient
            width: Width of the edge in characters

        Returns:
            String with gradient-colored bottom edge
        """
        style = get_border_style()
        if style.uses_half_blocks:
            return gradient_fg(style.bottom * width, colors, width)
        else:
            # Line-drawing style: corner + horizontal line + corner
            if width >= 2:
                inner = style.bottom * (width - 2)
                edge = style.corner_bl + inner + style.corner_br
            else:
                edge = style.bottom * width
            return gradient_fg(edge, colors, width)

    @staticmethod
    def top_solid(color, width):
        """Top edge with solid color.

        Args:
            color: RGB tuple for solid color
            width: Width of the edge in characters

        Returns:
            String with solid-colored top edge
        """
        style = get_border_style()
        if style.uses_half_blocks:
            return solid_fg(style.top * width, color)
        else:
            if width >= 2:
                inner = style.top * (width - 2)
                edge = style.corner_tl + inner + style.corner_tr
            else:
                edge = style.top * width
            return solid_fg(edge, color)

    @staticmethod
    def bottom_solid(color, width):
        """Bottom edge with solid color.

        Args:
            color: RGB tuple for solid color
            width: Width of the edge in characters

        Returns:
            String with solid-colored bottom edge
        """
        style = get_border_style()
        if style.uses_half_blocks:
            return solid_fg(style.bottom * width, color)
        else:
            if width >= 2:
                inner = style.bottom * (width - 2)
                edge = style.corner_bl + inner + style.corner_br
            else:
                edge = style.bottom * width
            return solid_fg(edge, color)

    @staticmethod
    def content(text, colors, fg, width):
        """Content line with gradient background.

        Args:
            text: Content text to render
            colors: List of RGB tuples for gradient
            fg: RGB tuple for foreground color
            width: Width of the content area

        Returns:
            String with gradient background and text
        """
        style = get_border_style()
        if style.uses_half_blocks:
            visible = _visible_len(text)
            # Pad if too short, truncate if too long
            if visible < width:
                padded = text + " " * (width - visible)
            else:
                # Truncate to fit within width
                padded = text[:width] if visible > width else text
            return gradient(padded, colors, fg, width)
        else:
            # Line-drawing style: add left/right borders
            if width >= 2:
                inner_width = width - 2
                visible = _visible_len(text)
                # Truncate if too long
                if visible > inner_width:
                    text = text[:inner_width]
                elif visible < inner_width:
                    # Pad if too short
                    text = text + " " * (inner_width - visible)
                inner = gradient(text, colors, fg, inner_width)
                return (
                    solid_fg(style.left, colors[0])
                    + inner
                    + solid_fg(style.right, colors[-1])
                )
            else:
                return gradient(text, colors, fg, width)

    @staticmethod
    def content_solid(text, bg, fg, width):
        """Content line with solid background.

        Args:
            text: Content text to render
            bg: RGB tuple for background color
            fg: RGB tuple for foreground color
            width: Width of the content area

        Returns:
            String with solid background and text
        """
        style = get_border_style()
        if style.uses_half_blocks:
            visible = _visible_len(text)
            padded = text + " " * (width - visible) if visible < width else text
            return solid(padded, bg, fg, width)
        else:
            # Line-drawing style: add left/right borders
            if width >= 2:
                inner_width = width - 2
                visible = _visible_len(text)
                if visible < inner_width:
                    padded = text + " " * (inner_width - visible)
                else:
                    padded = text[:inner_width] if visible > inner_width else text
                inner = solid(padded, bg, fg, inner_width)
                return solid_fg(style.left, bg) + inner + solid_fg(style.right, bg)
            else:
                return solid(text, bg, fg, width)

    @classmethod
    def render(
        cls,
        lines: List[str],
        colors: Any,
        fg: Tuple[int, int, int],
        width: int,
        disable_wrapping: bool = False,
    ) -> str:
        """Render complete box with gradient edges and content.

        Args:
            lines: List of content lines
            colors: List of RGB tuples for gradient
            fg: RGB tuple for foreground color
            width: Width of the box
            disable_wrapping: If True, preserve original formatting (for code/structured content)

        Returns:
            Multi-line string with complete rendered box
        """
        output = [cls.top(colors, width)]

        for line in lines:
            if disable_wrapping:
                # Preserve original formatting
                output.append(cls.content(line, colors, fg, width))
            else:
                # Wrap text for prose content (no continuation indent for Box - simple container)
                wrapped = wrap_text(line, width, continuation_indent=0)
                for wrapped_line in wrapped:
                    output.append(cls.content(wrapped_line, colors, fg, width))

        output.append(cls.bottom(colors, width))
        return "\n".join(output)

    @classmethod
    def render_solid(
        cls,
        lines: List[str],
        bg: Tuple[int, int, int],
        fg: Tuple[int, int, int],
        width: int,
        disable_wrapping: bool = False,
    ) -> str:
        """Render complete box with solid color (no gradient).

        Args:
            lines: List of content lines
            bg: RGB tuple for solid background color
            fg: RGB tuple for foreground color
            width: Width of the box
            disable_wrapping: If True, preserve original formatting (for code/structured content)

        Returns:
            Multi-line string with complete rendered box
        """
        output = [cls.top_solid(bg, width)]

        for line in lines:
            if disable_wrapping:
                # Preserve original formatting - truncate if too long
                visible_length = _visible_len(line)
                if visible_length > width:
                    # Truncate with ellipsis indicator
                    truncated = line[: width - 3] + "..."
                    output.append(cls.content_solid(truncated, bg, fg, width))
                else:
                    # Use line as-is, pad to width
                    output.append(cls.content_solid(line, bg, fg, width))
            else:
                # Wrap text for prose content (no continuation indent for Box - simple container)
                # Use word_wrap=False to break long words/URLs that exceed width
                wrapped = wrap_text(line, width, word_wrap=False, continuation_indent=0)
                for wrapped_line in wrapped:
                    output.append(cls.content_solid(wrapped_line, bg, fg, width))

        output.append(cls.bottom_solid(bg, width))
        return "\n".join(output)


class TagBox:
    """DRY helper for tag + content box pattern.

    Creates a two-column box with a colored tag section on the left
    and a content section on the right. Supports configurable border styles.

    Border styles:
    - half_blocks: Uses ▄▀ characters (default, best visual quality)
    - lines: Uses Unicode box-drawing (most compatible)
    - ascii: Simple ASCII (universal fallback)
    - none: No visible borders
    """

    @staticmethod
    def render(
        lines: List[str],
        tag_bg: Tuple[int, int, int],
        tag_fg: Optional[Tuple[int, int, int]] = None,
        tag_width: int = 3,
        content_colors: Any = None,
        content_fg: Optional[Tuple[int, int, int]] = None,
        content_width: int = 80,
        tag_chars: Optional[List[str]] = None,
        use_gradient: bool = True,
        indent: str = "",
        position: str = "only",
        disable_wrapping: bool = False,
    ) -> str:
        """Render a box with colored tag on left + content on right.

        Args:
            lines: List of content text lines
            tag_bg: RGB tuple for tag background
            tag_fg: RGB tuple for tag foreground (auto-computed from tag_bg if None)
            tag_width: Width of tag column
            content_colors: List of RGB tuples for gradient, or single RGB for solid
            content_fg: RGB tuple for content foreground
            content_width: Width of content column
            tag_chars: List of tag characters per line (default: first line gets icon, rest blank)
            use_gradient: True for gradient content bg, False for solid
            indent: String to prepend to each output line
            position: Border position - 'only' (both), 'first' (top only),
                     'middle' (no borders), 'last' (bottom only)
            disable_wrapping: If True, preserve original formatting (for code/structured content)

        Returns:
            Multi-line string with complete rendered tag box
        """
        from kollabor_tui.design_system.theme import Theme

        if tag_fg is None:
            tag_fg = Theme.text_on(tag_bg)

        style = get_border_style()
        output = []

        # We handle spacing by adding leading space to each line in the render loop
        # So continuation_indent is 0 - wrap_text doesn't add any spaces
        continuation_indent = 0

        # Total width for line-drawing borders
        total_width = tag_width + content_width

        # Wrap all lines first to know total line count (unless wrapping is disabled)
        wrapped_lines = []
        original_indices = []  # Track which original line each wrapped line came from
        for i, line in enumerate(lines):
            if disable_wrapping:
                # Preserve original formatting - don't wrap
                wrapped_lines.append(line)
                original_indices.append(i)
            else:
                # Wrap text for prose content
                wrapped = wrap_text(
                    line, content_width, continuation_indent=continuation_indent
                )
                for w in wrapped:
                    wrapped_lines.append(w)
                    original_indices.append(i)

        # Default tag chars: empty for all lines
        if tag_chars is None:
            tag_chars = ["   "] * len(lines)

        # Determine which borders to show based on position
        show_top = position in ("only", "first")
        show_bottom = position in ("only", "last")

        # Top edge (only if position is 'only' or 'first')
        if show_top:
            if style.uses_half_blocks:
                tag_top = solid_fg(style.top * tag_width, tag_bg)
                if use_gradient:
                    content_top = gradient_fg(style.top * content_width, content_colors)
                else:
                    content_top = solid_fg(style.top * content_width, content_colors)
                output.append(tag_top + content_top)
            else:
                # Line-drawing: single border spanning full width
                if total_width >= 2:
                    inner = style.top * (total_width - 2)
                    edge = style.corner_tl + inner + style.corner_tr
                else:
                    edge = style.top * total_width
                output.append(solid_fg(edge, tag_bg))

        # Content lines
        for i, line in enumerate(wrapped_lines):
            orig_idx = original_indices[i]
            # Only show tag char on first line of each original line
            is_first_of_original = i == 0 or original_indices[i - 1] != orig_idx
            if is_first_of_original and orig_idx < len(tag_chars):
                tc = tag_chars[orig_idx]
            else:
                tc = "   "

            # Add leading space: on first line for separation, on continuation
            # lines use 1 space to match first line's total spacing (tag_width + 1)
            if is_first_of_original:
                display_line = f" {line}"
            else:
                # Match first line's total spacing: tag_width (3) + space (1) = 4
                # Tag provides 3, content needs 1
                display_line = " " + line

            if style.uses_half_blocks:
                padded = (
                    display_line.ljust(content_width)
                    if _visible_len(display_line) < content_width
                    else display_line
                )
                tag_part = solid(tc, tag_bg, tag_fg, tag_width)
                if use_gradient:
                    content_part = gradient(
                        padded, content_colors, content_fg, content_width
                    )
                else:
                    content_part = solid(
                        padded, content_colors, content_fg, content_width
                    )
                output.append(tag_part + content_part)
            else:
                # Line-drawing: left border + tag + content + right border
                inner_width = total_width - 2
                tag_inner_width = tag_width
                content_inner_width = inner_width - tag_inner_width

                # Adjust content width for borders
                padded = (
                    display_line.ljust(content_inner_width)
                    if _visible_len(display_line) < content_inner_width
                    else display_line
                )
                if _visible_len(padded) > content_inner_width:
                    padded = padded[:content_inner_width]

                tag_part = solid(tc, tag_bg, tag_fg, tag_inner_width)
                if use_gradient:
                    content_part = gradient(
                        padded, content_colors, content_fg, content_inner_width
                    )
                else:
                    content_part = solid(
                        padded, content_colors, content_fg, content_inner_width
                    )
                output.append(
                    solid_fg(style.left, tag_bg)
                    + tag_part
                    + content_part
                    + solid_fg(
                        style.right,
                        content_colors[-1] if use_gradient else content_colors,
                    )
                )

        # Bottom edge (only if position is 'only' or 'last')
        if show_bottom:
            if style.uses_half_blocks:
                tag_bot = solid_fg(style.bottom * tag_width, tag_bg)
                if use_gradient:
                    content_bot = gradient_fg(
                        style.bottom * content_width, content_colors
                    )
                else:
                    content_bot = solid_fg(style.bottom * content_width, content_colors)
                output.append(tag_bot + content_bot)
            else:
                # Line-drawing: single border spanning full width
                if total_width >= 2:
                    inner = style.bottom * (total_width - 2)
                    edge = style.corner_bl + inner + style.corner_br
                else:
                    edge = style.bottom * total_width
                output.append(solid_fg(edge, tag_bg))

        if indent:
            return "\n".join(indent + line for line in output)
        return "\n".join(output)
