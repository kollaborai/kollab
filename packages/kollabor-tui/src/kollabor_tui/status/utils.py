"""Shared utility functions for the status widget system.

Common text formatting and ANSI helpers used across status widgets,
layout renderer, and interactive widgets.
"""

import re


def fg(text: str, color: tuple) -> str:
    """Apply foreground color to text.

    Args:
        text: Text to color
        color: RGB tuple (r, g, b)

    Returns:
        Text wrapped in ANSI foreground color codes
    """
    r, g, b = color
    return f"\033[38;2;{r};{g};{b}m{text}\033[39m"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text.

    Args:
        text: Text potentially containing ANSI codes

    Returns:
        Plain text with all ANSI codes removed
    """
    return re.sub(r"\033\[[0-9;]*m", "", text)


def truncate(text: str, width: int, ellipsis: str = "...") -> str:
    """Truncate text to fit within width.

    Args:
        text: Text to truncate
        width: Maximum visible width
        ellipsis: Suffix when truncated

    Returns:
        Text fitting within width
    """
    if len(text) <= width:
        return text
    if width <= len(ellipsis):
        return text[:width]
    return text[: width - len(ellipsis)] + ellipsis


def middle_truncate(text: str, max_width: int) -> str:
    """Truncate text in the middle, keeping start and end visible.

    Examples:
    - "kollab-status-widgets" (20) -> "kollabor..s-widgets"
    - "kollab-status-widgets" (15) -> "kollab..widgets"
    - "default" (5) -> "d..t"
    - "default" (4) -> "d.."

    Args:
        text: Text to truncate
        max_width: Maximum width

    Returns:
        Middle-truncated text (always shows ".." when truncating)
    """
    if len(text) <= max_width:
        return text

    if max_width <= 2:
        return text[:max_width]

    if max_width == 3:
        return f"{text[0]}.."

    if max_width == 4:
        return f"{text[0]}..{text[-1]}"

    # Reserve 2 chars for ".."
    available = max_width - 2
    # Split roughly 40% start, 60% end (end is usually more meaningful)
    start_chars = max(1, available * 2 // 5)
    end_chars = max(1, available - start_chars)

    return f"{text[:start_chars]}..{text[-end_chars:]}"
