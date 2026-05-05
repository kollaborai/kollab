"""Core gradient engine for terminal text rendering.

This module provides character-by-character gradient rendering that preserves
ANSI escape sequences. Gradients automatically fall back to solid colors in
256/16-color modes for optimal compatibility.
"""

import re

from .color_mode import COLOR_TRUECOLOR, _bg_code, _color_mode, _fg_code

__all__ = [
    "ANSI_RE",
    "_split_ansi",
    "gradient",
    "gradient_fg",
    "smooth_gradient",
    "smooth_gradient_subtle",
]


# =============================================================================
# CORE GRADIENT ENGINE
# =============================================================================

# Regex to find ANSI escape sequences
ANSI_RE = re.compile(r"(\033\[[0-9;]*m)")


def _split_ansi(text):
    """Split text into (visible_chars, ansi_positions) preserving ANSI codes."""
    parts = ANSI_RE.split(text)
    visible = []
    ansi_at = {}  # position -> list of ansi codes to insert before this char
    pos = 0
    for part in parts:
        if ANSI_RE.match(part):
            # It's an ANSI code - attach to current position
            if pos not in ansi_at:
                ansi_at[pos] = []
            ansi_at[pos].append(part)
        else:
            # Visible characters
            for char in part:
                visible.append(char)
                pos += 1
    return visible, ansi_at


def gradient(text, colors, fg=(255, 255, 255), width=None):
    """Character-by-character background gradient, preserving ANSI codes.

    In 256/16 color modes, uses solid color (middle of gradient) instead.
    """
    if not text or not colors or len(colors) < 2:
        return text

    visible, ansi_at = _split_ansi(text)

    # Pad if needed (add spaces to visible)
    if width and len(visible) < width:
        visible.extend([" "] * (width - len(visible)))

    # Pre-compute fg code once
    fg_code = _fg_code(fg[0], fg[1], fg[2])

    # In limited color modes, use solid color (middle of gradient)
    if _color_mode != COLOR_TRUECOLOR:
        mid_idx = len(colors) // 2
        mid_color = colors[mid_idx]
        bg_code = _bg_code(mid_color[0], mid_color[1], mid_color[2])

        result = []
        for i, char in enumerate(visible):
            if i in ansi_at:
                result.extend(ansi_at[i])
            result.append(f"\033[0;{bg_code};{fg_code}m{char}")

        if len(visible) in ansi_at:
            result.extend(ansi_at[len(visible)])
        return "".join(result) + "\033[0m"

    # Truecolor mode: full gradient
    result = []
    for i, char in enumerate(visible):
        if i in ansi_at:
            result.extend(ansi_at[i])

        t = i / max(1, len(visible) - 1)
        idx = min(int(t * (len(colors) - 1)), len(colors) - 2)
        f = (t * (len(colors) - 1)) - idx

        c1, c2 = colors[idx], colors[idx + 1]
        bg_r = int(c1[0] + (c2[0] - c1[0]) * f)
        bg_g = int(c1[1] + (c2[1] - c1[1]) * f)
        bg_b = int(c1[2] + (c2[2] - c1[2]) * f)

        bg_code = _bg_code(bg_r, bg_g, bg_b)
        result.append(f"\033[0;{bg_code};{fg_code}m{char}")

    if len(visible) in ansi_at:
        result.extend(ansi_at[len(visible)])

    return "".join(result) + "\033[0m"


def gradient_fg(text, colors, width=None):
    """Foreground-only gradient, preserving ANSI codes.

    In 256/16 color modes, uses solid color (middle of gradient) instead.
    """
    if not text or not colors or len(colors) < 2:
        return text

    visible, ansi_at = _split_ansi(text)

    if width and len(visible) < width:
        visible.extend([" "] * (width - len(visible)))

    # In limited color modes, use solid color (middle of gradient)
    if _color_mode != COLOR_TRUECOLOR:
        mid_idx = len(colors) // 2
        mid_color = colors[mid_idx]
        fg_code = _fg_code(mid_color[0], mid_color[1], mid_color[2])

        result = []
        for i, char in enumerate(visible):
            if i in ansi_at:
                result.extend(ansi_at[i])
            result.append(f"\033[{fg_code}m{char}")

        if len(visible) in ansi_at:
            result.extend(ansi_at[len(visible)])
        return "".join(result) + "\033[0m"

    # Truecolor mode: full gradient
    result = []
    for i, char in enumerate(visible):
        if i in ansi_at:
            result.extend(ansi_at[i])

        t = i / max(1, len(visible) - 1)
        idx = min(int(t * (len(colors) - 1)), len(colors) - 2)
        f = (t * (len(colors) - 1)) - idx

        c1, c2 = colors[idx], colors[idx + 1]
        r = int(c1[0] + (c2[0] - c1[0]) * f)
        g = int(c1[1] + (c2[1] - c1[1]) * f)
        b = int(c1[2] + (c2[2] - c1[2]) * f)

        fg_code = _fg_code(r, g, b)
        result.append(f"\033[{fg_code}m{char}")

    if len(visible) in ansi_at:
        result.extend(ansi_at[len(visible)])

    return "".join(result) + "\033[0m"


# =============================================================================
# SMOOTH GRADIENTS (simpler API, always truecolor)
# =============================================================================


def smooth_gradient(text, bg_colors, fg_color=(255, 255, 255), width=None):
    """Create smooth character-by-character background gradient.

    Simpler API than gradient() - always uses truecolor for smooth rendering.
    Best for banners, headers, and visual elements.

    Args:
        text: Text to colorize
        bg_colors: List of RGB tuples for background gradient
        fg_color: Single RGB tuple for foreground (default white)
        width: Width to pad text to (optional)

    Returns:
        Formatted string with smooth background gradient

    Example:
        >>> smooth_gradient("Hello", [(80, 200, 50), (120, 240, 90)], (0, 0, 0))
    """
    if not text or not bg_colors or len(bg_colors) < 2:
        return text

    # Pad text if width specified
    if width and len(text) < width:
        text = text.ljust(width)

    result = []
    text_length = len(text)

    for i, char in enumerate(text):
        # Calculate position in gradient (0.0 to 1.0)
        position = i / max(1, text_length - 1)

        # Scale to color array indices
        scaled_pos = position * (len(bg_colors) - 1)
        color_index = int(scaled_pos)

        # Interpolation factor between colors
        t = scaled_pos - color_index

        # Get current and next background colors
        if color_index >= len(bg_colors) - 1:
            br, bg, bb = bg_colors[-1]
        else:
            curr_bg = bg_colors[color_index]
            next_bg = bg_colors[color_index + 1]

            # Linear interpolation for background
            br = curr_bg[0] + (next_bg[0] - curr_bg[0]) * t
            bg = curr_bg[1] + (next_bg[1] - curr_bg[1]) * t
            bb = curr_bg[2] + (next_bg[2] - curr_bg[2]) * t

        br, bg_int, bb = int(br), int(bg), int(bb)

        # Background gradient with interpolation
        bg_code = f"\033[48;2;{br};{bg_int};{bb}m"

        # Solid foreground color
        fg_code = f"\033[38;2;{fg_color[0]};{fg_color[1]};{fg_color[2]}m"

        result.append(f"{bg_code}{fg_code}{char}")

    result.append("\033[0m")  # Reset
    return "".join(result)


def smooth_gradient_subtle(
    text, bg_colors, width=None, lighten=20, no_background=False
):
    """Create subtle gradient with slightly lighter foreground.

    The foreground is slightly lighter than background, making half-block
    characters visible but very subtle - creates a "half-padding" effect.

    Args:
        text: Text to colorize
        bg_colors: List of RGB tuples for gradient
        width: Width to pad text to (optional)
        lighten: How much to lighten foreground (default 20)
        no_background: If True, only set foreground (for half-block edges)

    Returns:
        Formatted string with subtle fg/bg gradient

    Example:
        >>> # Create subtle top edge
        >>> smooth_gradient_subtle("▄" * 50, colors, no_background=True)
    """
    if not text or not bg_colors or len(bg_colors) < 2:
        return text

    # Pad text if width specified
    if width and len(text) < width:
        text = text.ljust(width)

    result = []
    text_length = len(text)

    for i, char in enumerate(text):
        # Calculate position in gradient (0.0 to 1.0)
        position = i / max(1, text_length - 1)

        # Scale to color array indices
        scaled_pos = position * (len(bg_colors) - 1)
        color_index = int(scaled_pos)

        # Interpolation factor between colors
        t = scaled_pos - color_index

        # Get current and next colors
        if color_index >= len(bg_colors) - 1:
            r, g, b = bg_colors[-1]
        else:
            curr = bg_colors[color_index]
            next_c = bg_colors[color_index + 1]

            # Linear interpolation
            r = curr[0] + (next_c[0] - curr[0]) * t
            g = curr[1] + (next_c[1] - curr[1]) * t
            b = curr[2] + (next_c[2] - curr[2]) * t

        br, bg_int, bb = int(r), int(g), int(b)

        if no_background:
            # Foreground uses background gradient color directly
            fg_code = f"\033[38;2;{br};{bg_int};{bb}m"
            result.append(f"{fg_code}{char}")
        else:
            # Foreground is slightly lighter for subtle visibility
            fr = min(255, br + lighten)
            fg = min(255, bg_int + lighten)
            fb = min(255, bb + lighten)

            # Background uses base gradient color
            bg_code = f"\033[48;2;{br};{bg_int};{bb}m"
            # Foreground is lighter version
            fg_code = f"\033[38;2;{fr};{fg};{fb}m"
            result.append(f"{bg_code}{fg_code}{char}")

    result.append("\033[0m")  # Reset
    return "".join(result)
