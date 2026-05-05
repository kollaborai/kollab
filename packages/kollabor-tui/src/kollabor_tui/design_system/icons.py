"""Icon collection with semantic colors.

This module provides a comprehensive collection of Unicode icons with
predefined colors for consistent UI elements.
"""

__all__ = ["IconColors", "Icons", "color_icon"]


class IconColors:
    """Color definitions for icons (RGB tuples)."""

    # Bright/accent colors
    LIME = (163, 230, 53)
    GREEN = (34, 197, 94)
    CYAN = (6, 182, 212)
    BLUE = (59, 130, 246)
    PURPLE = (168, 85, 247)
    PINK = (236, 72, 153)
    RED = (239, 68, 68)
    ORANGE = (249, 115, 22)
    YELLOW = (234, 179, 8)

    # Neutral colors
    WHITE = (255, 255, 255)
    GRAY_LIGHT = (156, 163, 175)
    GRAY = (107, 114, 128)
    GRAY_DARK = (75, 85, 99)
    BLACK = (0, 0, 0)


def color_icon(icon, color):
    """Apply foreground color to an icon character.

    Args:
        icon: Unicode character to color
        color: RGB tuple (r, g, b)

    Returns:
        ANSI-colored string

    Example:
        >>> print(color_icon("✔", IconColors.GREEN))
    """
    r, g, b = color
    return f"\033[38;2;{r};{g};{b}m{icon}\033[0m"


class Icons:
    """Collection of Unicode terminal UI icons with semantic colors.

    Each icon is a tuple of (character, default_color).

    Usage:
        >>> icon, color = Icons.CHECK_1
        >>> print(color_icon(icon, color))

    Or access just the character:
        >>> icon, _ = Icons.ARROW_RIGHT_1
        >>> print(icon)  # →
    """

    # Arrows - Right
    ARROW_RIGHT_1 = ("→", IconColors.LIME)
    ARROW_RIGHT_2 = ("❯", IconColors.GREEN)
    ARROW_RIGHT_3 = ("➤", IconColors.CYAN)
    ARROW_RIGHT_4 = ("▻", IconColors.BLUE)
    ARROW_RIGHT_5 = ("►", IconColors.PURPLE)
    ARROW_RIGHT_6 = ("➔", IconColors.PINK)
    ARROW_RIGHT_7 = ("⭢", IconColors.RED)
    ARROW_RIGHT_8 = ("⇒", IconColors.ORANGE)
    ARROW_RIGHT_9 = ("⤏", IconColors.YELLOW)
    ARROW_RIGHT_10 = ("⤳", IconColors.WHITE)

    # Arrows - Left
    ARROW_LEFT_1 = ("←", IconColors.LIME)
    ARROW_LEFT_2 = ("❮", IconColors.GREEN)
    ARROW_LEFT_3 = ("◅", IconColors.CYAN)
    ARROW_LEFT_4 = ("◄", IconColors.BLUE)
    ARROW_LEFT_5 = ("⭠", IconColors.RED)
    ARROW_LEFT_6 = ("⇐", IconColors.ORANGE)

    # Arrows - Up/Down
    ARROW_UP_1 = ("↑", IconColors.CYAN)
    ARROW_UP_2 = ("△", IconColors.BLUE)
    ARROW_UP_3 = ("▲", IconColors.PURPLE)
    ARROW_DOWN_1 = ("↓", IconColors.CYAN)
    ARROW_DOWN_2 = ("▽", IconColors.BLUE)
    ARROW_DOWN_3 = ("▼", IconColors.PURPLE)

    # Arrows - Diagonal
    ARROW_DIAG_NE = ("↗", IconColors.LIME)
    ARROW_DIAG_SE = ("↘", IconColors.GREEN)
    ARROW_DIAG_SW = ("↙", IconColors.CYAN)
    ARROW_DIAG_NW = ("↖", IconColors.BLUE)
    ARROW_SWAP = ("⇄", IconColors.PURPLE)
    ARROW_UPDOWN = ("⇅", IconColors.PINK)

    # Permission/Risk Icons
    RISK_LOW = ("⚡", IconColors.GREEN)
    RISK_MEDIUM = ("⚠", IconColors.ORANGE)
    RISK_HIGH = ("☢", IconColors.RED)
    APPROVED = ("✓", IconColors.GREEN)
    DENIED = ("✗", IconColors.RED)
    PERMISSION = ("🔒", IconColors.BLUE)

    # Checkmarks & Success
    CHECK_1 = ("✓", IconColors.LIME)
    CHECK_2 = ("✔", IconColors.GREEN)
    CHECK_3 = ("√", IconColors.CYAN)
    CHECK_BOX_ON = ("☑", IconColors.BLUE)
    CHECK_BOX_OFF = ("☐", IconColors.GRAY)

    # Cross & Error
    CROSS_1 = ("✕", IconColors.RED)
    CROSS_2 = ("✖", IconColors.PINK)
    CROSS_3 = ("×", IconColors.ORANGE)
    CROSS_4 = ("✗", IconColors.RED)
    CROSS_5 = ("✘", IconColors.PINK)
    CROSS_CIRCLE = ("⊗", IconColors.RED)

    # Bullets & Dots - Large
    BULLET_FILLED = ("●", IconColors.LIME)
    BULLET_EMPTY = ("○", IconColors.GRAY)
    BULLET_RING = ("◉", IconColors.GREEN)
    BULLET_DOUBLE = ("◎", IconColors.CYAN)
    BULLET_HALF_L = ("◐", IconColors.BLUE)
    BULLET_HALF_R = ("◑", IconColors.PURPLE)
    BULLET_HALF_T = ("◒", IconColors.PINK)
    BULLET_BLACK = ("⚫", IconColors.GRAY_DARK)
    BULLET_WHITE = ("⚪", IconColors.GRAY_LIGHT)

    # Bullets & Dots - Small
    DOT_FILLED = ("•", IconColors.LIME)
    DOT_MIDDLE = ("·", IconColors.GREEN)
    DOT_SMALL = ("∙", IconColors.CYAN)
    DOT_HOLLOW = ("∘", IconColors.BLUE)

    # Spinner frames
    SPINNER_1 = ("⠋", IconColors.LIME)
    SPINNER_2 = ("⠙", IconColors.GREEN)
    SPINNER_3 = ("⠹", IconColors.CYAN)
    SPINNER_4 = ("⠸", IconColors.BLUE)
    SPINNER_5 = ("⠼", IconColors.PURPLE)
    SPINNER_6 = ("⠴", IconColors.PINK)
    SPINNER_7 = ("⠦", IconColors.RED)
    SPINNER_8 = ("⠧", IconColors.ORANGE)
    SPINNER_9 = ("⠇", IconColors.YELLOW)
    SPINNER_10 = ("⠏", IconColors.WHITE)

    # Shapes - Boxes
    BOX_FILLED = ("■", IconColors.LIME)
    BOX_EMPTY = ("□", IconColors.GRAY)
    BOX_ROUNDED = ("▢", IconColors.CYAN)
    BOX_DOTTED = ("▣", IconColors.BLUE)

    # Shapes - Diamonds
    DIAMOND_FILLED = ("◆", IconColors.CYAN)
    DIAMOND_EMPTY = ("◇", IconColors.GRAY)

    # Progress Bars
    BAR_FULL = ("█", IconColors.LIME)
    BAR_7_8 = ("▉", IconColors.GREEN)
    BAR_6_8 = ("▊", IconColors.CYAN)
    BAR_5_8 = ("▋", IconColors.BLUE)
    BAR_4_8 = ("▌", IconColors.PURPLE)
    BAR_3_8 = ("▍", IconColors.PINK)
    BAR_2_8 = ("▎", IconColors.RED)
    BAR_1_8 = ("▏", IconColors.ORANGE)
    BAR_EMPTY = ("░", IconColors.GRAY)
    BAR_SHADE = ("▒", IconColors.GRAY_LIGHT)

    # Lines & Separators
    LINE_H = ("─", IconColors.GRAY)
    LINE_V = ("│", IconColors.GRAY)
    LINE_H_LIGHT = ("┄", IconColors.GRAY_LIGHT)
    LINE_V_LIGHT = ("┆", IconColors.GRAY_LIGHT)
    LINE_H_HEAVY = ("━", IconColors.GRAY_DARK)
    LINE_V_HEAVY = ("┃", IconColors.GRAY_DARK)

    # Corners
    CORNER_TL = ("┌", IconColors.GRAY)
    CORNER_TR = ("┐", IconColors.GRAY)
    CORNER_BL = ("└", IconColors.GRAY)
    CORNER_BR = ("┘", IconColors.GRAY)

    # T-junctions
    T_LEFT = ("├", IconColors.GRAY)
    T_RIGHT = ("┤", IconColors.GRAY)
    T_TOP = ("┬", IconColors.GRAY)
    T_BOTTOM = ("┴", IconColors.GRAY)
    T_CROSS = ("┼", IconColors.GRAY)

    # Brackets
    BRACKET_L = ("[", IconColors.GRAY)
    BRACKET_R = ("]", IconColors.GRAY)
    BRACE_L = ("{", IconColors.GRAY_LIGHT)
    BRACE_R = ("}", IconColors.GRAY_LIGHT)
    ANGLE_L = ("«", IconColors.CYAN)
    ANGLE_R = ("»", IconColors.CYAN)

    # Special Characters
    GEAR = ("⚙", IconColors.GRAY)
    LIGHTNING = ("⚡", IconColors.YELLOW)
    STAR_FILLED = ("★", IconColors.ORANGE)
    STAR_EMPTY = ("☆", IconColors.GRAY_LIGHT)
    HEART = ("♥", IconColors.RED)
    HEART_EMPTY = ("♡", IconColors.PINK)
    DIAMOND_SUIT = ("♦", IconColors.RED)
    SPADE = ("♠", IconColors.PURPLE)
    CLUB = ("♣", IconColors.GREEN)

    # Status
    INFO = ("ℹ", IconColors.BLUE)
    WARNING = ("⚠", IconColors.YELLOW)
    ERROR = ("✖", IconColors.RED)
    SUCCESS = ("✔", IconColors.GREEN)

    # Technical
    CMD = ("⌘", IconColors.GRAY)
    OPT = ("⌥", IconColors.GRAY_LIGHT)
    SHIFT = ("⇧", IconColors.GRAY_DARK)
    ESC = ("⎋", IconColors.WHITE)
    DELETE = ("⌫", IconColors.GRAY_LIGHT)
    RETURN = ("⏎", IconColors.GRAY)

    # Math & Logic
    PLUS = ("+", IconColors.LIME)
    MINUS = ("−", IconColors.GREEN)
    MULTIPLY = ("×", IconColors.CYAN)
    DIVIDE = ("÷", IconColors.BLUE)
    PLUS_MINUS = ("±", IconColors.PURPLE)
    INFINITY = ("∞", IconColors.YELLOW)
    DELTA = ("∆", IconColors.WHITE)

    # Miscellaneous
    ELLIPSIS = ("…", IconColors.GRAY)
    SECTION = ("§", IconColors.GRAY)
    PILCROW = ("¶", IconColors.GRAY_LIGHT)
    THEREFORE = ("∴", IconColors.BLUE)
    APPROX = ("≈", IconColors.CYAN)
    NOT_EQUAL = ("≠", IconColors.RED)

    @classmethod
    def get_spinner_frames(cls):
        """Get list of spinner frame characters for animation."""
        return [
            cls.SPINNER_1[0],
            cls.SPINNER_2[0],
            cls.SPINNER_3[0],
            cls.SPINNER_4[0],
            cls.SPINNER_5[0],
            cls.SPINNER_6[0],
            cls.SPINNER_7[0],
            cls.SPINNER_8[0],
            cls.SPINNER_9[0],
            cls.SPINNER_10[0],
        ]

    @classmethod
    def get_progress_blocks(cls):
        """Get list of progress bar block characters (full to empty)."""
        return [
            cls.BAR_FULL[0],
            cls.BAR_7_8[0],
            cls.BAR_6_8[0],
            cls.BAR_5_8[0],
            cls.BAR_4_8[0],
            cls.BAR_3_8[0],
            cls.BAR_2_8[0],
            cls.BAR_1_8[0],
            cls.BAR_EMPTY[0],
        ]
