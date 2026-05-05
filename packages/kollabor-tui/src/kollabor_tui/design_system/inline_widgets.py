"""Inline widgets for embedding in status lines.

Compact widget representations that can be embedded in any text line.
These return short colored strings, not full TagBox layouts.

Example usage:
    from kollabor_tui.design_system import inline_checkbox, inline_progress, inline_dropdown

    # In a status line:
    line = f"cwd ~/dev  {inline_checkbox(True, 'stream')}  {inline_progress(0.67, 8)}  {inline_dropdown('lime')}"
    # Result: cwd ~/dev  [x] stream  [▓▓▓▓▓░░░] 67%  [lime v]
"""

from .components import C, progress_bar
from .theme import T

__all__ = [
    "inline_checkbox",
    "inline_progress",
    "inline_slider",
    "inline_dropdown",
    "inline_toggle",
    "inline_badge",
    "inline_spinner",
    "inline_meter",
]


def _fg(text: str, color: tuple) -> str:
    """Apply foreground color."""
    r, g, b = color
    return f"\033[38;2;{r};{g};{b}m{text}\033[39m"


def inline_checkbox(checked: bool, label: str = "", dim_when_off: bool = True) -> str:
    """Inline checkbox: [x] label or [ ] label.

    Args:
        checked: Whether checkbox is checked
        label: Optional label text
        dim_when_off: Dim the widget when unchecked

    Returns:
        Colored inline checkbox string
    """
    if checked:
        box = _fg("[", T().text_dim) + _fg("x", T().ai_tag) + _fg("]", T().text_dim)
        text = _fg(f" {label}", T().text) if label else ""
    else:
        color = T().text_dim if dim_when_off else T().text
        box = _fg("[ ]", T().text_dim)
        text = _fg(f" {label}", color) if label else ""
    return box + text


def inline_toggle(on: bool, on_label: str = "on", off_label: str = "off") -> str:
    """Inline toggle switch: (on) or (off).

    Args:
        on: Toggle state
        on_label: Label when on
        off_label: Label when off

    Returns:
        Colored inline toggle string
    """
    if on:
        return (
            _fg("(", T().text_dim) + _fg(on_label, T().ai_tag) + _fg(")", T().text_dim)
        )
    else:
        return (
            _fg("(", T().text_dim)
            + _fg(off_label, T().text_dim)
            + _fg(")", T().text_dim)
        )


def inline_progress(value: float, width: int = 8, show_percent: bool = True) -> str:
    """Inline progress bar: [▓▓▓▓░░░░] 67%.

    Args:
        value: Progress value 0.0 to 1.0
        width: Bar width in characters (default 8)
        show_percent: Show percentage after bar

    Returns:
        Colored inline progress bar string
    """
    value = max(0.0, min(1.0, value))
    bar = progress_bar(value, width)

    result = _fg("[", T().text_dim)

    for char in bar:
        if char in (
            C["bar_full"],
            C["bar_7_8"],
            C["bar_6_8"],
            C["bar_5_8"],
            C["bar_4_8"],
            C["bar_3_8"],
            C["bar_2_8"],
            C["bar_1_8"],
        ):
            result += _fg(char, T().ai_tag)
        else:
            result += _fg(char, T().text_dim)

    result += _fg("]", T().text_dim)

    if show_percent:
        pct = int(value * 100)
        result += _fg(f" {pct}%", T().text_dim)

    return result


def inline_slider(
    value: float,
    min_val: float = 0,
    max_val: float = 100,
    width: int = 6,
    show_value: bool = True,
) -> str:
    """Inline slider: [▓▓▓░░░] 50.

    Args:
        value: Current value
        min_val: Minimum value
        max_val: Maximum value
        width: Bar width in characters
        show_value: Show numeric value after bar

    Returns:
        Colored inline slider string
    """
    # Normalize to 0-1
    if max_val > min_val:
        normalized = (value - min_val) / (max_val - min_val)
    else:
        normalized = 0

    normalized = max(0.0, min(1.0, normalized))
    bar = progress_bar(normalized, width)

    result = _fg("[", T().text_dim)
    for char in bar:
        if char in (
            C["bar_full"],
            C["bar_7_8"],
            C["bar_6_8"],
            C["bar_5_8"],
            C["bar_4_8"],
            C["bar_3_8"],
            C["bar_2_8"],
            C["bar_1_8"],
        ):
            result += _fg(char, T().ai_tag)
        else:
            result += _fg(char, T().text_dim)
    result += _fg("]", T().text_dim)

    if show_value:
        # Format value nicely
        if isinstance(value, float) and value == int(value):
            display = str(int(value))
        elif isinstance(value, float):
            display = f"{value:.1f}"
        else:
            display = str(value)
        result += _fg(f" {display}", T().text)

    return result


def inline_dropdown(value: str, max_width: int = 12) -> str:
    """Inline dropdown display: [value v].

    Args:
        value: Current selected value
        max_width: Maximum width for value display

    Returns:
        Colored inline dropdown string
    """
    # Truncate if needed
    display = value[: max_width - 1] + "…" if len(value) > max_width else value

    return (
        _fg("[", T().text_dim)
        + _fg(display, T().text)
        + _fg(" " + str(C["arrow_down"]), T().ai_tag)
        + _fg("]", T().text_dim)
    )


def inline_badge(text: str, style: str = "default") -> str:
    """Inline badge/tag: (text).

    Args:
        text: Badge text
        style: 'default', 'success', 'warning', 'error', 'info'

    Returns:
        Colored inline badge string
    """
    colors = {
        "default": T().text_dim,
        "success": T().ai_tag,
        "warning": T().warning[0] if hasattr(T(), "warning") else (200, 150, 50),
        "error": T().error[0] if hasattr(T(), "error") else (200, 80, 80),
        "info": T().secondary[0] if hasattr(T(), "secondary") else (80, 150, 200),
    }
    color = colors.get(style, colors["default"])

    return _fg("(", T().text_dim) + _fg(text, color) + _fg(")", T().text_dim)


def inline_spinner(frame: int = 0, style: str = "braille") -> str:
    """Inline spinner character.

    Args:
        frame: Animation frame index
        style: 'braille' or 'dots'

    Returns:
        Colored spinner character
    """
    if style == "braille":
        frames = C["spin_braille"]
    else:
        frames = C["spin"]

    char = frames[frame % len(frames)]
    return _fg(char, T().ai_tag)


def inline_meter(
    value: float, width: int = 5, char_full: str = "▮", char_empty: str = "▯"
) -> str:
    """Inline meter without brackets: ▮▮▮▯▯.

    Args:
        value: Value 0.0 to 1.0
        width: Total width in characters
        char_full: Character for filled portion
        char_empty: Character for empty portion

    Returns:
        Colored inline meter string
    """
    value = max(0.0, min(1.0, value))
    filled = int(value * width)
    empty = width - filled

    return _fg(char_full * filled, T().ai_tag) + _fg(char_empty * empty, T().text_dim)
