"""Color mode system for terminal color management.

This module provides POSIX-compatible color mode detection and conversion,
supporting 24-bit truecolor, 256-color palette, and basic 16-color ANSI modes.
"""

import os

# Color modes
COLOR_TRUECOLOR = "truecolor"  # 24-bit RGB (16M colors)
COLOR_256 = "256"  # 256-color palette (POSIX compatible)
COLOR_16 = "16"  # Basic 16 ANSI colors (most compatible)

# Active color mode (can be changed at runtime)
_color_mode = COLOR_TRUECOLOR


__all__ = [
    "COLOR_TRUECOLOR",
    "COLOR_256",
    "COLOR_16",
    "set_color_mode",
    "get_color_mode",
    "auto_detect_color_mode",
    "rgb_to_256",
    "rgb_to_16",
    "_fg_code",
    "_bg_code",
]


def set_color_mode(mode):
    """Set the color mode: 'truecolor', '256', or '16'."""
    global _color_mode
    if mode in (COLOR_TRUECOLOR, COLOR_256, COLOR_16):
        _color_mode = mode
    else:
        raise ValueError(f"Unknown color mode: {mode}. Use 'truecolor', '256', or '16'")


def get_color_mode():
    """Get the current color mode."""
    return _color_mode


def auto_detect_color_mode():
    """Auto-detect best color mode from environment.

    Priority:
    1. KOLLAB_COLOR_MODE env var (explicit override)
    2. COLORTERM env var (truecolor/24bit)
    3. TERM env var (256color detection)
    4. Fallback to 16-color

    KOLLAB_COLOR_MODE values:
    - truecolor, 24bit, true -> truecolor
    - 256, 256color -> 256-color palette
    - 16, basic -> 16 ANSI colors
    """
    # Check for explicit override first
    kollabor_mode = os.environ.get("KOLLAB_COLOR_MODE", "").lower()
    if kollabor_mode:
        if kollabor_mode in ("truecolor", "24bit", "true"):
            return COLOR_TRUECOLOR
        elif kollabor_mode in ("256", "256color"):
            return COLOR_256
        elif kollabor_mode in ("16", "basic"):
            return COLOR_16

    # Auto-detect from terminal environment
    colorterm = os.environ.get("COLORTERM", "").lower()
    term = os.environ.get("TERM", "").lower()

    if colorterm in ("truecolor", "24bit"):
        return COLOR_TRUECOLOR
    elif "256color" in term:
        return COLOR_256
    else:
        return COLOR_16


def rgb_to_256(r, g, b):
    """Convert RGB (0-255) to 256-color palette index."""
    # Grayscale ramp (232-255) for near-gray colors
    if r == g == b:
        if r < 8:
            return 16
        if r > 248:
            return 231
        return round((r - 8) / 247 * 24) + 232

    # Color cube (16-231): 6x6x6 = 216 colors
    # Each component maps to 0-5
    r_idx = round(r / 255 * 5)
    g_idx = round(g / 255 * 5)
    b_idx = round(b / 255 * 5)
    return 16 + (36 * r_idx) + (6 * g_idx) + b_idx


def rgb_to_16(r, g, b):
    """Convert RGB (0-255) to basic 16-color ANSI index."""
    # Calculate brightness
    brightness = (r + g + b) / 3

    # Determine if bright variant
    bright = brightness > 127

    # Find closest base color
    # Basic colors: black, red, green, yellow, blue, magenta, cyan, white
    max_c = max(r, g, b)
    if max_c < 50:
        return 8 if bright else 0  # black / bright black (gray)

    # Normalize to find dominant color
    r_dom = r > (max_c * 0.6)
    g_dom = g > (max_c * 0.6)
    b_dom = b > (max_c * 0.6)

    if r_dom and g_dom and b_dom:
        return 15 if bright else 7  # white
    elif r_dom and g_dom:
        return 11 if bright else 3  # yellow
    elif r_dom and b_dom:
        return 13 if bright else 5  # magenta
    elif g_dom and b_dom:
        return 14 if bright else 6  # cyan
    elif r_dom:
        return 9 if bright else 1  # red
    elif g_dom:
        return 10 if bright else 2  # green
    elif b_dom:
        return 12 if bright else 4  # blue
    else:
        return 15 if bright else 7  # white (fallback)


def _fg_code(r, g, b):
    """Generate foreground color code based on current mode."""
    mode = _color_mode
    if mode == COLOR_TRUECOLOR:
        return f"38;2;{r};{g};{b}"
    elif mode == COLOR_256:
        return f"38;5;{rgb_to_256(r, g, b)}"
    else:  # COLOR_16
        return f"38;5;{rgb_to_16(r, g, b)}"


def _bg_code(r, g, b):
    """Generate background color code based on current mode."""
    mode = _color_mode
    if mode == COLOR_TRUECOLOR:
        return f"48;2;{r};{g};{b}"
    elif mode == COLOR_256:
        return f"48;5;{rgb_to_256(r, g, b)}"
    else:  # COLOR_16
        return f"48;5;{rgb_to_16(r, g, b)}"
