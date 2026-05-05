"""
Border style system for Kollab UI.

This module provides configurable border styles to handle different terminal
rendering behaviors. Some terminals have line spacing issues with half-block
characters, so alternative styles are provided.

Available styles:
    half_blocks: Uses ▄▀ characters (default, best visual quality)
    lines: Uses Unicode box-drawing ─│┌┐└┘ (most compatible)
    ascii: Uses simple ASCII +--+ (universal fallback)
    none: No visible borders (solid color blocks only)

Example:
    from kollabor_tui.design_system.border_style import set_border_style, get_border_chars

    # Switch to line-drawing style
    set_border_style('lines')

    # Get border characters for current style
    chars = get_border_chars()
    print(chars['top'])  # '─'
"""

__all__ = [
    "BORDER_STYLES",
    "set_border_style",
    "get_border_style",
    "get_border_chars",
    "BorderStyle",
]


class BorderStyle:
    """Border style definition with character mappings.

    Attributes:
        name: Style name identifier
        top: Character for top edge
        bottom: Character for bottom edge
        left: Character for left edge
        right: Character for right edge
        corner_tl: Top-left corner
        corner_tr: Top-right corner
        corner_bl: Bottom-left corner
        corner_br: Bottom-right corner
        uses_half_blocks: Whether style uses half-block rendering
        description: Human-readable description
    """

    def __init__(
        self,
        name,
        top,
        bottom,
        left,
        right,
        corner_tl,
        corner_tr,
        corner_bl,
        corner_br,
        uses_half_blocks=False,
        description="",
    ):
        self.name = name
        self.top = top
        self.bottom = bottom
        self.left = left
        self.right = right
        self.corner_tl = corner_tl
        self.corner_tr = corner_tr
        self.corner_bl = corner_bl
        self.corner_br = corner_br
        self.uses_half_blocks = uses_half_blocks
        self.description = description

    def to_dict(self):
        """Convert to dictionary for easy access."""
        return {
            "name": self.name,
            "top": self.top,
            "bottom": self.bottom,
            "left": self.left,
            "right": self.right,
            "corner_tl": self.corner_tl,
            "corner_tr": self.corner_tr,
            "corner_bl": self.corner_bl,
            "corner_br": self.corner_br,
            "uses_half_blocks": self.uses_half_blocks,
        }


# Preset border styles
BORDER_STYLES = {
    "half_blocks": BorderStyle(
        name="half_blocks",
        top="▄",  # Lower half block for top edge
        bottom="▀",  # Upper half block for bottom edge
        left="█",  # Full block for left edge (if needed)
        right="█",  # Full block for right edge (if needed)
        corner_tl="▄",
        corner_tr="▄",
        corner_bl="▀",
        corner_br="▀",
        uses_half_blocks=True,
        description="Half-block characters (best visual quality, may have gaps in some terminals)",
    ),
    "lines": BorderStyle(
        name="lines",
        top="─",
        bottom="─",
        left="│",
        right="│",
        corner_tl="┌",
        corner_tr="┐",
        corner_bl="└",
        corner_br="┘",
        uses_half_blocks=False,
        description="Unicode box-drawing lines (most compatible)",
    ),
    "lines_rounded": BorderStyle(
        name="lines_rounded",
        top="─",
        bottom="─",
        left="│",
        right="│",
        corner_tl="╭",
        corner_tr="╮",
        corner_bl="╰",
        corner_br="╯",
        uses_half_blocks=False,
        description="Unicode box-drawing with rounded corners",
    ),
    "lines_double": BorderStyle(
        name="lines_double",
        top="═",
        bottom="═",
        left="║",
        right="║",
        corner_tl="╔",
        corner_tr="╗",
        corner_bl="╚",
        corner_br="╝",
        uses_half_blocks=False,
        description="Double-line box-drawing characters",
    ),
    "lines_heavy": BorderStyle(
        name="lines_heavy",
        top="━",
        bottom="━",
        left="┃",
        right="┃",
        corner_tl="┏",
        corner_tr="┓",
        corner_bl="┗",
        corner_br="┛",
        uses_half_blocks=False,
        description="Heavy/thick box-drawing lines",
    ),
    "ascii": BorderStyle(
        name="ascii",
        top="-",
        bottom="-",
        left="|",
        right="|",
        corner_tl="+",
        corner_tr="+",
        corner_bl="+",
        corner_br="+",
        uses_half_blocks=False,
        description="Simple ASCII characters (universal fallback)",
    ),
    "none": BorderStyle(
        name="none",
        top=" ",
        bottom=" ",
        left=" ",
        right=" ",
        corner_tl=" ",
        corner_tr=" ",
        corner_bl=" ",
        corner_br=" ",
        uses_half_blocks=False,
        description="No visible borders (padding only)",
    ),
}

# Active border style (mutable)
_active_style = BORDER_STYLES["half_blocks"]


def set_border_style(name):
    """Switch active border style by name.

    Args:
        name: Style name ('half_blocks', 'lines', 'lines_rounded', 'ascii', 'none')

    Raises:
        ValueError: If style name is not found in BORDER_STYLES

    Example:
        set_border_style('lines')
    """
    global _active_style
    if name in BORDER_STYLES:
        _active_style = BORDER_STYLES[name]
    else:
        available = list(BORDER_STYLES.keys())
        raise ValueError(f"Unknown border style: {name}. Available: {available}")


def get_border_style():
    """Get the active border style.

    Returns:
        BorderStyle: The currently active border style object

    Example:
        style = get_border_style()
        print(style.name)
    """
    return _active_style


def get_border_chars():
    """Get border characters as dictionary for current style.

    Returns:
        dict: Dictionary with border character mappings

    Example:
        chars = get_border_chars()
        top_edge = chars['top'] * width
    """
    return _active_style.to_dict()


def list_border_styles():
    """List all available border styles with descriptions.

    Returns:
        list: List of (name, description) tuples
    """
    return [(name, style.description) for name, style in BORDER_STYLES.items()]
