#!/usr/bin/env python3
"""Modern terminal UI - DRY, smart, sweet."""

# =============================================================================
# COLOR MODE SYSTEM (POSIX Compatible)
# =============================================================================

import os
import re

# Color modes
COLOR_TRUECOLOR = "truecolor"  # 24-bit RGB (16M colors)
COLOR_256 = "256"  # 256-color palette (POSIX compatible)
COLOR_16 = "16"  # Basic 16 ANSI colors (most compatible)

# Active color mode (can be changed at runtime)
_color_mode = COLOR_TRUECOLOR


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
    """Auto-detect best color mode from environment."""
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
    # Grayscale ramp (232-255) for neutral and near-neutral colors.
    if max(r, g, b) - min(r, g, b) <= 16:
        gray = round((r + g + b) / 3)
        if gray < 8:
            return 16
        if gray > 248:
            return 231
        return round((gray - 8) / 247 * 24) + 232

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
# THEME SYSTEM
# =============================================================================


class Theme:
    """Themeable color palette with semantic color roles."""

    def __init__(self, name, **colors):
        self.name = name
        # Gradients (list of RGB tuples)
        self.primary = colors.get(
            "primary", [(80, 200, 50), (100, 220, 70), (120, 240, 90)]
        )
        self.secondary = colors.get(
            "secondary", [(20, 160, 180), (30, 180, 200), (40, 200, 220)]
        )
        self.response_bg = colors.get(
            "response_bg", [(55, 55, 55), (45, 45, 45), (35, 35, 35)]
        )
        self.input_bg = colors.get(
            "input_bg", [(55, 60, 68), (46, 50, 58), (38, 42, 48)]
        )
        self.dark = colors.get("dark", [(35, 35, 35), (25, 25, 25), (18, 18, 18)])
        # Semantic colors
        self.success = colors.get(
            "success", [(60, 180, 80), (80, 200, 100), (100, 220, 120)]
        )
        self.error = colors.get(
            "error", [(180, 60, 60), (200, 80, 80), (220, 100, 100)]
        )
        self.warning = colors.get(
            "warning", [(200, 120, 40), (220, 140, 60), (240, 160, 80)]
        )
        # Solid colors (RGB tuples)
        self.user_tag = colors.get("user_tag", (50, 200, 220))
        self.ai_tag = colors.get("ai_tag", (80, 180, 50))
        self.tool_tag = colors.get("tool_tag", (50, 140, 180))
        self.thinking_tag = colors.get("thinking_tag", (200, 140, 40))
        self.code_bg = colors.get("code_bg", (25, 25, 25))
        # Text colors
        self.text = colors.get("text", (255, 255, 255))
        self.text_dim = colors.get("text_dim", (120, 120, 120))
        self.text_dark = colors.get("text_dark", (0, 0, 0))


# Preset themes
THEMES = {
    "lime": Theme(
        "lime",
        primary=[(80, 200, 50), (100, 220, 70), (120, 240, 90), (140, 255, 110)],
        secondary=[(20, 160, 180), (30, 180, 200), (40, 200, 220)],
        user_tag=(50, 200, 220),
        ai_tag=(80, 180, 50),
        success=[(60, 180, 80), (80, 200, 100), (100, 220, 120)],
        error=[(180, 60, 60), (200, 80, 80), (220, 100, 100)],
        warning=[(200, 140, 40), (220, 160, 60), (240, 180, 80)],
    ),
    "ocean": Theme(
        "ocean",
        primary=[(40, 180, 200), (50, 195, 215), (60, 210, 230), (70, 225, 245)],
        secondary=[(50, 120, 180), (60, 140, 200), (70, 160, 220)],
        response_bg=[(50, 55, 60), (42, 46, 50), (34, 37, 40)],
        input_bg=[(55, 60, 68), (46, 50, 58), (38, 42, 48)],
        dark=[(32, 36, 42), (24, 27, 32), (16, 18, 22)],
        user_tag=(40, 180, 200),
        ai_tag=(60, 200, 180),
        tool_tag=(50, 120, 180),
        thinking_tag=(60, 140, 180),
        success=[(40, 160, 140), (50, 180, 160), (60, 200, 180)],
        error=[(160, 80, 100), (180, 100, 120), (200, 120, 140)],
        warning=[(180, 160, 80), (200, 180, 100), (220, 200, 120)],
        text=(240, 245, 255),
        text_dim=(110, 120, 130),
    ),
    "sunset": Theme(
        "sunset",
        primary=[(220, 100, 80), (240, 120, 100), (255, 140, 120)],
        secondary=[(180, 80, 140), (200, 100, 160), (220, 120, 180)],
        response_bg=[(50, 45, 45), (40, 36, 36), (30, 28, 28)],
        input_bg=[(55, 48, 50), (46, 40, 42), (38, 32, 35)],
        dark=[(40, 35, 35), (30, 26, 26), (20, 18, 18)],
        user_tag=(220, 120, 80),
        ai_tag=(180, 100, 160),
        tool_tag=(200, 80, 120),
        thinking_tag=(240, 160, 80),
        success=[(120, 180, 100), (140, 200, 120), (160, 220, 140)],
        error=[(200, 80, 80), (220, 100, 100), (240, 120, 120)],
        warning=[(240, 180, 80), (250, 200, 100), (255, 220, 120)],
    ),
    "mono": Theme(
        "mono",
        primary=[(180, 180, 180), (200, 200, 200), (220, 220, 220)],
        secondary=[(140, 140, 140), (160, 160, 160), (180, 180, 180)],
        response_bg=[(45, 45, 45), (35, 35, 35), (25, 25, 25)],
        input_bg=[(50, 50, 50), (40, 40, 40), (30, 30, 30)],
        dark=[(35, 35, 35), (25, 25, 25), (15, 15, 15)],
        user_tag=(200, 200, 200),
        ai_tag=(160, 160, 160),
        tool_tag=(140, 140, 140),
        thinking_tag=(180, 180, 180),
        success=[(140, 180, 140), (160, 200, 160), (180, 220, 180)],
        error=[(180, 140, 140), (200, 160, 160), (220, 180, 180)],
        warning=[(180, 180, 140), (200, 200, 160), (220, 220, 180)],
    ),
}

# Active theme (mutable)
_active_theme = THEMES["lime"]


def set_theme(name):
    """Switch active theme by name."""
    global _active_theme
    if name in THEMES:
        _active_theme = THEMES[name]
    else:
        raise ValueError(f"Unknown theme: {name}. Available: {list(THEMES.keys())}")


def get_theme():
    """Get active theme."""
    return _active_theme


# Singleton instance for static-like access
C = type(
    "C",
    (),
    {
        "GRAY": property(lambda s: get_theme().response_bg),
        "GRAY_COOL": property(lambda s: get_theme().input_bg),
        "DARK": property(lambda s: get_theme().dark),
        "LIME": property(lambda s: get_theme().primary),
        "CYAN": property(lambda s: get_theme().secondary),
        "RED": property(lambda s: get_theme().error),
        "ORANGE": property(lambda s: get_theme().warning),
        "WHITE": property(lambda s: get_theme().text),
        "BLACK": property(lambda s: get_theme().text_dark),
        "DIM": property(lambda s: get_theme().text_dim),
    },
)()


def T():
    """Shorthand to get active theme."""
    return get_theme()


class S:
    """ANSI style codes."""

    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    RESET = "\033[0m"
    RESET_BOLD = "\033[22m"
    RESET_DIM = "\033[22m"
    RESET_ITALIC = "\033[23m"


# =============================================================================
# UI COMPONENTS (DRY)
# =============================================================================


def solid(text, bg, fg, width=None):
    """Solid background color with consistent foreground per character."""
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
    """Solid foreground color (no gradient)."""
    fg_code = _fg_code(color[0], color[1], color[2])
    return f"\033[{fg_code}m{text}\033[0m"


class Box:
    """Smart box with correct half-block edges."""

    @staticmethod
    def top(colors, width):
        """Top edge - lower half connects to content below."""
        return gradient_fg("▄" * width, colors, width)

    @staticmethod
    def bottom(colors, width):
        """Bottom edge - upper half connects to content above."""
        return gradient_fg("▀" * width, colors, width)

    @staticmethod
    def top_solid(color, width):
        """Top edge - solid color."""
        return solid_fg("▄" * width, color)

    @staticmethod
    def bottom_solid(color, width):
        """Bottom edge - solid color."""
        return solid_fg("▀" * width, color)

    @staticmethod
    def content(text, colors, fg, width):
        """Content line with gradient background."""
        return gradient(text, colors, fg, width)

    @classmethod
    def render(cls, lines, colors, fg, width):
        """Render complete box with gradient edges and content."""
        output = [cls.top(colors, width)]
        for line in lines:
            output.append(cls.content(line, colors, fg, width))
        output.append(cls.bottom(colors, width))
        return "\n".join(output)

    @classmethod
    def render_solid(cls, lines, bg, fg, width):
        """Render complete box with solid color (no gradient)."""
        output = [cls.top_solid(bg, width)]
        for line in lines:
            padded = line.ljust(width) if len(line) < width else line
            output.append(solid(padded, bg, fg, width))
        output.append(cls.bottom_solid(bg, width))
        return "\n".join(output)


class TagBox:
    """DRY helper for tag + content box pattern."""

    @staticmethod
    def render(
        lines,
        tag_bg,
        tag_fg,
        tag_width,
        content_colors,
        content_fg,
        content_width,
        tag_chars=None,
        use_gradient=True,
        indent="",
    ):
        """
        Render a box with colored tag on left + content on right.

        Args:
            lines: List of content text lines
            tag_bg: RGB tuple for tag background
            tag_fg: RGB tuple for tag foreground
            tag_width: Width of tag column
            content_colors: List of RGB tuples for gradient, or single RGB for solid
            content_fg: RGB tuple for content foreground
            content_width: Width of content column
            tag_chars: List of tag characters per line (default: first line gets icon, rest blank)
            use_gradient: True for gradient content bg, False for solid
            indent: String to prepend to each output line
        """
        output = []

        # Default tag chars: empty for all lines
        if tag_chars is None:
            tag_chars = ["   "] * len(lines)

        # Top edge
        tag_top = solid_fg("▄" * tag_width, tag_bg)
        if use_gradient:
            content_top = gradient_fg("▄" * content_width, content_colors)
        else:
            content_top = solid_fg("▄" * content_width, content_colors)
        output.append(tag_top + content_top)

        # Content lines
        for i, line in enumerate(lines):
            tc = tag_chars[i] if i < len(tag_chars) else "   "
            padded = line.ljust(content_width)
            tag_part = solid(tc, tag_bg, tag_fg, tag_width)
            if use_gradient:
                content_part = gradient(
                    padded, content_colors, content_fg, content_width
                )
            else:
                content_part = solid(padded, content_colors, content_fg, content_width)
            output.append(tag_part + content_part)

        # Bottom edge
        tag_bot = solid_fg("▀" * tag_width, tag_bg)
        if use_gradient:
            content_bot = gradient_fg("▀" * content_width, content_colors)
        else:
            content_bot = solid_fg("▀" * content_width, content_colors)
        output.append(tag_bot + content_bot)

        if indent:
            return "\n".join(indent + line for line in output)
        return "\n".join(output)


class UI:
    """High-level UI components."""

    WIDTH = 76

    @classmethod
    def banner(cls):
        """App banner."""
        logo = [
            "                                                                            ",
            "   ▄█▀▀▀█▄  █ ▄▀ █▀▀█ █   █   █▀▀█ █▀▀▄ █▀▀█ █▀▀█                          ",
            "   ██   ██  █▀▄  █  █ █   █   █▄▄█ █▀▀▄ █  █ █▄▄▀                          ",
            "   ▀█▄▄▄█▀  █  █ █▄▄█ █▄▄ █▄▄ █  █ █▄▄▀ █▄▄█ █ █▄   v0.5.0                 ",
            "                                                                            ",
        ]
        return Box.render(logo, C.LIME, C.BLACK, cls.WIDTH)

    @classmethod
    def status(cls, left, mid, right):
        """Three-section status bar with flair."""
        left_bar = gradient(f" ⚡ {left} ", C.LIME, C.BLACK, 20)
        mid_bar = gradient(f" ◐ {mid} ", C.GRAY, C.WHITE, 36)
        right_bar = gradient(f" ◆ {right} ", C.DARK, C.DIM, 20)
        line = left_bar + mid_bar + right_bar
        # Composite gradient for edges (lime -> gray -> dark)
        edge_colors = C.LIME[:2] + C.GRAY[:2] + C.DARK[:2]
        return f"{Box.top(edge_colors, cls.WIDTH)}\n{line}\n{Box.bottom(edge_colors, cls.WIDTH)}"

    @classmethod
    def user_input(cls, text):
        """User input line with prompt - themed tag + input background."""
        return TagBox.render(
            lines=[f" {text}"],
            tag_bg=T().user_tag,
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().input_bg,
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=[" ❯ "],
        )

    @classmethod
    def thinking(cls, seconds, tokens=None):
        """Thinking indicator - animated spinner style."""
        lime_fg = f"\033[38;2;{C.LIME[0][0]};{C.LIME[0][1]};{C.LIME[0][2]}m"
        reset_fg = f"\033[38;2;{C.DIM[0]};{C.DIM[1]};{C.DIM[2]}m"
        if tokens:
            tok_info = f"{S.DIM}({tokens} tokens){S.RESET_DIM}"
            text = (
                f"  {lime_fg}{S.BOLD}◐{S.RESET_BOLD}"
                f"{reset_fg} thinking {seconds}s  {tok_info}"
            )
        else:
            text = (
                f"  {lime_fg}{S.BOLD}◐{S.RESET_BOLD}"
                f"{reset_fg} thinking {seconds}s{S.DIM}...{S.RESET_DIM}"
            )
        return Box.render([text], C.DARK, C.DIM, cls.WIDTH)

    @classmethod
    def response(cls, text):
        """Single LLM response line - themed AI tag + response background."""
        return TagBox.render(
            lines=[f" {text}"],
            tag_bg=T().ai_tag,
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().response_bg,
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=[" ◆ "],
        )

    @classmethod
    def response_block(cls, lines):
        """Multi-line LLM response - themed AI tag bar + response background."""
        content_lines = [f" {line}" for line in lines]
        tag_chars = [" ◆ "] + ["   "] * (len(lines) - 1)

        return TagBox.render(
            lines=content_lines,
            tag_bg=T().ai_tag,
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().response_bg,
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=tag_chars,
        )

    # Nested pane settings
    INDENT = "  "
    NESTED_WIDTH = 50

    @classmethod
    def tool_call(cls, name, args, status="running"):
        """Tool call block - themed tag + colored gradient content."""
        # All use white text for readability against colored backgrounds
        configs = {
            "running": (" * ", T().tool_tag, T().text, T().secondary, T().text),
            "success": (" + ", T().ai_tag, T().text_dark, T().success, T().text),
            "error": (" x ", T().error[0], T().text, T().error, T().text),
        }
        icon, tag_bg, tag_fg, content_colors, content_fg = configs.get(
            status, configs["running"]
        )

        return TagBox.render(
            lines=[f" {S.BOLD}{name}({args}){S.RESET_BOLD}", f" {status}..."],
            tag_bg=tag_bg,
            tag_fg=tag_fg,
            tag_width=3,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[icon, "   "],
            indent=cls.INDENT,
        )

    @classmethod
    def tool_result(cls, lines):
        """Tool result block - nested, solid (no gradient for code/text)."""
        padded = [f"  {line}" for line in lines]
        box = Box.render_solid(padded, (25, 25, 25), C.DIM, cls.NESTED_WIDTH)
        return "\n".join(cls.INDENT + line for line in box.split("\n"))

    @classmethod
    def code_block(cls, code_lines, lang="python"):
        """Code block - nested, solid, bold language."""
        header = f"  ─── {S.BOLD}{lang}{S.RESET_BOLD} "
        # Account for ANSI codes in length calc
        visible_len = len(f"  ─── {lang} ")
        header = header + "─" * (cls.NESTED_WIDTH - visible_len)
        lines = [header] + [f"  {line}" for line in code_lines]
        box = Box.render_solid(lines, (25, 25, 25), C.WHITE, cls.NESTED_WIDTH)
        return "\n".join(cls.INDENT + line for line in box.split("\n"))

    @classmethod
    def error(cls, title, message):
        """Error block - nested, bold icon and title."""
        lines = [
            f"  {S.BOLD}✖ {title}{S.RESET_BOLD}",
            f"    ⤷ {S.DIM}{message}{S.RESET_DIM}",
        ]
        box = Box.render(lines, C.RED, C.WHITE, cls.NESTED_WIDTH)
        return "\n".join(cls.INDENT + line for line in box.split("\n"))

    @classmethod
    def warning(cls, message):
        """Warning pane - nested, solid, colored icon."""
        icon_c = "\033[38;2;120;40;0m"
        icon_reset = "\033[38;2;0;0;0m"
        box = Box.render_solid(
            [f"  {S.BOLD}{icon_c}⚠{icon_reset} {message}{S.RESET_BOLD}"],
            (220, 140, 50),
            C.BLACK,
            cls.NESTED_WIDTH,
        )
        return "\n".join(cls.INDENT + line for line in box.split("\n"))

    # =========================================================================
    # V2 COMPONENTS - Tag Style (using TagBox)
    # =========================================================================

    @classmethod
    def thinking_v2(cls, seconds, tokens=None):
        """Thinking indicator - themed thinking tag style."""
        text = (
            f" thinking {seconds}s  ({tokens} tokens)"
            if tokens
            else f" thinking {seconds}s..."
        )
        return TagBox.render(
            lines=[text],
            tag_bg=T().thinking_tag,
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().dark,
            content_fg=T().text_dim,
            content_width=cls.WIDTH - 3,
            tag_chars=[" ~ "],
        )

    @classmethod
    def error_v2(cls, title, message):
        """Error block - themed error tag style."""
        return TagBox.render(
            lines=[f" {S.BOLD}{title}{S.RESET_BOLD}", f" {message}"],
            tag_bg=T().error[0],
            tag_fg=T().text,
            tag_width=3,
            content_colors=T().error,
            content_fg=T().text,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[" x ", "   "],
            indent=cls.INDENT,
        )

    @classmethod
    def warning_v2(cls, message):
        """Warning block - themed warning tag style."""
        return TagBox.render(
            lines=[f" {message}"],
            tag_bg=T().warning[0],
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().warning,
            content_fg=T().text,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[" ! "],
            indent=cls.INDENT,
        )

    @classmethod
    def code_block_v2(cls, code_lines, lang="python"):
        """Code block - language-colored tag style."""
        lang_colors = {
            "python": (130, 80, 180),
            "javascript": (240, 220, 80),
            "typescript": (50, 120, 200),
            "rust": (220, 100, 50),
            "go": (80, 180, 220),
        }
        lang_bg = lang_colors.get(lang, (100, 100, 100))
        lang_fg = T().text_dark if lang == "javascript" else T().text

        lines = [f" {S.BOLD}{lang}{S.RESET_BOLD}"] + [f" {line}" for line in code_lines]
        tag_chars = [" # "] + ["   "] * len(code_lines)

        return TagBox.render(
            lines=lines,
            tag_bg=lang_bg,
            tag_fg=lang_fg,
            tag_width=3,
            content_colors=T().code_bg,
            content_fg=T().text,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=tag_chars,
            use_gradient=False,
            indent=cls.INDENT,
        )

    @classmethod
    def tool_result_v2(cls, lines):
        """Tool result - subtle gray tag style."""
        content_lines = [f" {line}" for line in lines]
        tag_chars = [" > "] + ["   "] * (len(lines) - 1)

        return TagBox.render(
            lines=content_lines,
            tag_bg=T().response_bg[0],
            tag_fg=T().text_dim,
            tag_width=3,
            content_colors=T().code_bg,
            content_fg=T().text_dim,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=tag_chars,
            use_gradient=False,
            indent=cls.INDENT,
        )

    @classmethod
    def banner_v2(cls):
        """Banner - themed primary gradient tag style."""
        logo_lines = [
            "                                                                         ",
            "  ▄█▀▀▀█▄  █ ▄▀ █▀▀█ █   █   █▀▀█ █▀▀▄ █▀▀█ █▀▀█                        ",
            "  ██   ██  █▀▄  █  █ █   █   █▄▄█ █▀▀▄ █  █ █▄▄▀                        ",
            "  ▀█▄▄▄█▀  █  █ █▄▄█ █▄▄ █▄▄ █  █ █▄▄▀ █▄▄█ █ █▄   v0.5.0              ",
            "                                                                         ",
        ]
        tag_chars = ["   ", "   ", " K ", "   ", "   "]

        return TagBox.render(
            lines=logo_lines,
            tag_bg=T().ai_tag,
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().primary,
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=tag_chars,
        )

    @classmethod
    def status_v2(cls, left, mid, right):
        """Status bar - themed segmented style."""
        seg_width = cls.WIDTH // 3
        remaining = cls.WIDTH - (seg_width * 2)

        segments = [
            (T().ai_tag, T().text_dark, f" * {left}", seg_width),
            (T().response_bg[0], T().text, f" ~ {mid}", seg_width),
            (T().dark[0], T().text_dim, f" # {right}", remaining),
        ]

        top = "".join(solid_fg("▄" * w, bg) for bg, _, _, w in segments)
        mid_line = "".join(
            solid(txt.ljust(w), bg, fg, w) for bg, fg, txt, w in segments
        )
        bot = "".join(solid_fg("▀" * w, bg) for bg, _, _, w in segments)

        return f"{top}\n{mid_line}\n{bot}"

    # =========================================================================
    # Original Components
    # =========================================================================

    @classmethod
    def divider(cls, label=""):
        """Section divider - with flair."""
        dim = "\033[38;2;60;60;60m"
        lime_fg = "\033[38;2;100;180;60m"
        reset = "\033[0m"
        if label:
            left = "─" * 2
            right = "─" * (cls.WIDTH - len(label) - 8)
            return f"{dim} {left}┤ {lime_fg}{label}{reset}{dim} ├{right}{reset}"
        return f"{dim}{'─' * cls.WIDTH}{reset}"

    @classmethod
    def update_block(cls, filename, added, removed, diff_lines):
        """File update/diff block - cyan tag + dark content."""
        cyan_bg = (50, 160, 180)
        tag_width = 3
        content_width = cls.NESTED_WIDTH - tag_width

        # Color codes for diff content
        green_fg = "\033[38;2;80;200;80m"
        red_fg = "\033[38;2;200;80;80m"
        dim_fg = "\033[38;2;100;100;100m"
        white_fg = "\033[38;2;220;220;220m"
        reset = "\033[0m"

        # Build content lines
        lines_text = [
            f" {S.BOLD}Update{S.RESET_BOLD}({white_fg}{filename}{reset})",
            f" {green_fg}+{added}{reset} {red_fg}-{removed}{reset}",
        ]
        for line in diff_lines:
            if line.startswith("+"):
                # Lime green background for added lines
                lime_bg = "\033[48;2;60;120;40m"
                lines_text.append(f" {lime_bg}{white_fg}{line}{reset}")
            elif line.startswith("-"):
                # Red background for deleted lines
                red_bg = "\033[48;2;140;50;50m"
                lines_text.append(f" {red_bg}{white_fg}{line}{reset}")
            else:
                lines_text.append(f" {dim_fg}{line}{reset}")

        output = []

        # Top edge
        tag_top = solid_fg("▄" * tag_width, cyan_bg)
        dark_top = gradient_fg("▄" * content_width, C.DARK)
        output.append(tag_top + dark_top)

        # Content lines
        for i, line_text in enumerate(lines_text):
            tag_char = " ~ " if i == 0 else "   "
            tag_part = solid(tag_char, cyan_bg, C.BLACK, tag_width)

            # Check if this is a diff line that needs special bg
            raw_line = diff_lines[i - 2] if i >= 2 else ""
            if raw_line.startswith("+"):
                # Lime green background for added lines
                padded = f" {raw_line}".ljust(content_width)
                content_part = solid(padded, (60, 120, 40), C.WHITE, content_width)
            elif raw_line.startswith("-"):
                # Red background for deleted lines
                padded = f" {raw_line}".ljust(content_width)
                content_part = solid(padded, (140, 50, 50), C.WHITE, content_width)
            else:
                # Normal gradient for other lines
                padded = line_text.ljust(content_width)
                content_part = gradient(padded, C.DARK, C.DIM, content_width)

            output.append(tag_part + content_part)

        # Bottom edge
        tag_bot = solid_fg("▀" * tag_width, cyan_bg)
        dark_bot = gradient_fg("▀" * content_width, C.DARK)
        output.append(tag_bot + dark_bot)

        return "\n".join(cls.INDENT + line for line in output)

    @classmethod
    def input_area(cls, placeholder="Type your message here...", spinner="⠹"):
        """Full input area as gradient pane with flair."""

        # Colors for status text
        lime_fg = "\033[38;2;140;200;80m"
        cyan_fg = "\033[38;2;100;180;200m"
        white_fg = "\033[38;2;220;220;220m"
        gray_fg = "\033[38;2;120;120;120m"
        dark_fg = "\033[38;2;70;70;70m"
        reset = "\033[0m"

        lines = []

        # Input pane with gradient
        input_line = f" {lime_fg}{spinner}{reset} {S.BOLD}█{S.RESET_BOLD}{gray_fg}{placeholder}{reset}"
        input_pane = Box.render([input_line], C.GRAY_COOL, C.WHITE, cls.WIDTH)
        lines.append(input_pane)

        # Status pane below with fancy icons
        status_content = (
            f" {cyan_fg}⌁{reset} {cyan_fg}~/dev/kollab{reset}  "
            f"{dark_fg}│{reset} \033[38;2;255;255;255m◆{reset} {white_fg}default{reset}  "
            f"{dark_fg}│{reset} \033[38;2;255;220;60m⚡{reset} {lime_fg}gpt-4{reset}  "
            f"{dark_fg}│{reset} {lime_fg}● Ready{reset}  "
            f"{dark_fg}│{reset} {gray_fg}0 msg ∙ 0 tok{reset}"
        )
        status_pane = Box.render([status_content], C.DARK, C.DIM, cls.WIDTH)
        lines.append(status_pane)

        # Agent/hint line with flair
        agent_line = (
            f" \033[38;2;255;220;60m★{reset} {lime_fg}default{reset}"
            f" {dark_fg}→{reset} {gray_fg}code-review{reset}"
            f" {dark_fg}→{reset} {gray_fg}debugging{reset}"
            f" {dark_fg}→{reset} {gray_fg}+3 more{reset}"
        )
        lines.append(agent_line)

        return "\n".join(lines)


# =============================================================================
# CONVERSATION MOCKUP
# =============================================================================


def demo_conversation():
    """Realistic LLM conversation flow."""

    print()
    print(UI.banner())
    print()
    print(UI.status("gpt-4", "ready", "3 plugins"))
    print()
    print(UI.divider("session start"))
    print()

    # Turn 1: Simple greeting
    print(UI.user_input("hey"))
    print(UI.thinking("1.2"))
    print(UI.response("Hey! What can I help you with?"))

    # Turn 2: Code request
    print(
        UI.user_input(
            "can you read the config file and tell me what plugins are enabled?"
        )
    )
    print(UI.thinking("3.4", "847"))
    print(UI.response("Sure, let me check the config file."))
    print(UI.tool_call("file_read", "~/.kollab/config.json", "success"))
    print(
        UI.tool_result(
            [
                '{ "plugins": {',
                '    "modern_input": { "enabled": true },',
                '    "hook_monitor": { "enabled": true },',
                '    "tmux": { "enabled": false }',
                "  }",
                "}",
            ]
        )
    )
    print(
        UI.response_block(
            [
                "You have 3 plugins configured:",
                "- modern_input: enabled",
                "- hook_monitor: enabled",
                "- tmux: disabled",
            ]
        )
    )
    print()

    # Turn 3: Edit request
    print(UI.user_input("enable the tmux plugin"))
    print(UI.thinking("2.1", "312"))
    print(UI.response("I'll update the config to enable tmux."))
    print()
    print(
        UI.update_block(
            "~/.kollab/config.json",
            1,
            1,
            [
                '461       "hook_monitor": { "enabled": true },',
                '-462       "tmux": { "enabled": false }',
                '+462       "tmux": { "enabled": true }',
                "463     }",
            ],
        )
    )
    print()
    print(UI.response("Done! The tmux plugin is now enabled. Restart to apply."))

    # Turn 4: Error case
    print(UI.user_input("read /etc/shadow"))
    print(UI.thinking("0.8"))
    print(UI.response("I'll try to read that file."))
    print(UI.tool_call("file_read", "/etc/shadow", "error"))
    print(UI.error("Permission denied", "/etc/shadow requires root access"))
    print(
        UI.response_block(
            [
                "I don't have permission to read that file. It's a system",
                "file that stores password hashes and requires root access.",
            ]
        )
    )

    # Turn 5: Code generation
    print(UI.user_input("write a quick python function to reverse a string"))
    print(UI.thinking("1.8", "156"))
    print(UI.response("Here's a simple string reversal function:"))
    print(
        UI.code_block(
            [
                "def reverse(s: str) -> str:",
                '    """Reverse a string."""',
                "    return s[::-1]",
                "",
                "# usage",
                'print(reverse("hello"))  # olleh',
            ]
        )
    )
    print(UI.response("Uses Python's slice notation - clean and efficient."))

    print()
    print(UI.input_area())
    print()


def demo_components():
    """Show all UI components."""

    print(UI.divider("component showcase"))

    print("status bar variants:")
    print(UI.status("claude-3", "streaming...", "2.4k tokens"))
    print(UI.status("gpt-5.4", "idle", "local"))

    print("tool states:")
    print(UI.tool_call("bash", "npm install", "running"))
    print(UI.tool_call("file_write", "src/app.py", "success"))
    print(UI.tool_call("http_get", "api.example.com", "error"))

    print("messages:")
    print(UI.warning("Rate limit approaching (80%)"))
    print(UI.error("Connection failed", "Could not reach api.openai.com"))

    print("thinking states:")
    print(UI.thinking("0.4"))
    print(UI.thinking("12.7", "3,847"))


def demo_v2_components():
    """Show all V2 tag-style components."""

    print()
    print(UI.divider("V2 TAG STYLE COMPONENTS"))
    print()

    print("banner_v2:")
    print(UI.banner_v2())
    print()

    print("status_v2:")
    print(UI.status_v2("claude-3", "streaming...", "2.4k tokens"))
    print()

    print("thinking_v2:")
    print(UI.thinking_v2("3.2", "1,247"))
    print()

    print("error_v2:")
    print(UI.error_v2("Connection failed", "Could not reach api.openai.com"))
    print()

    print("warning_v2:")
    print(UI.warning_v2("Rate limit approaching (80%)"))
    print()

    print("code_block_v2 (python):")
    print(
        UI.code_block_v2(
            [
                "def reverse(s: str) -> str:",
                '    """Reverse a string."""',
                "    return s[::-1]",
            ],
            "python",
        )
    )
    print()

    print("code_block_v2 (javascript):")
    print(
        UI.code_block_v2(
            [
                "const reverse = (s) => {",
                "  return s.split('').reverse().join('');",
                "};",
            ],
            "javascript",
        )
    )
    print()

    print("code_block_v2 (rust):")
    print(
        UI.code_block_v2(
            [
                "fn reverse(s: &str) -> String {",
                "    s.chars().rev().collect()",
                "}",
            ],
            "rust",
        )
    )
    print()

    print("tool_result_v2:")
    print(
        UI.tool_result_v2(
            [
                '{ "plugins": {',
                '    "modern_input": { "enabled": true },',
                '    "hook_monitor": { "enabled": true }',
                "  }",
                "}",
            ]
        )
    )
    print()


def demo_themes():
    """Show all themes side by side."""

    for theme_name in THEMES:
        set_theme(theme_name)
        print()
        print(f"{'=' * 76}")
        print(f"  THEME: {theme_name.upper()}")
        print(f"{'=' * 76}")
        print()
        print(UI.banner_v2())
        print()
        print(UI.user_input("hey, how are you?"))
        print(UI.thinking_v2("2.1", "523"))
        print(UI.response("I'm doing great! How can I help you today?"))
        print()
        print(UI.tool_call("bash", "npm install", "running"))
        print(UI.tool_call("file_write", "src/app.py", "success"))
        print(UI.tool_call("http_get", "api.example.com", "error"))
        print()
        print(UI.warning_v2("Rate limit approaching (80%)"))
        print(UI.error_v2("Connection failed", "Could not reach api.openai.com"))
        print()
        print(UI.code_block_v2(["def hello():", '    return "world"'], "python"))
        print()

    # Reset to default
    set_theme("lime")


# =============================================================================
# WIDGET COMPONENTS
# =============================================================================


class Widgets:
    """Widget components for modal dialogs - preview of design system."""

    WIDTH = 50  # Standard widget width
    TAG_WIDTH = 3

    # Unicode characters for rich UI
    CHARS = {
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
        # Spinner frames
        "spin": ["◐", "◓", "◑", "◒"],
    }

    @classmethod
    def _progress_bar(cls, progress, width=15):
        """Create smooth progress bar using partial block characters."""
        # Calculate filled portion
        filled_width = progress * width
        full_blocks = int(filled_width)
        partial = filled_width - full_blocks

        # Build bar with smooth transition
        bar = cls.CHARS["bar_full"] * full_blocks

        # Add partial block for smooth edge
        if full_blocks < width:
            if partial >= 0.875:
                bar += cls.CHARS["bar_7_8"]
            elif partial >= 0.75:
                bar += cls.CHARS["bar_6_8"]
            elif partial >= 0.625:
                bar += cls.CHARS["bar_5_8"]
            elif partial >= 0.5:
                bar += cls.CHARS["bar_4_8"]
            elif partial >= 0.375:
                bar += cls.CHARS["bar_3_8"]
            elif partial >= 0.25:
                bar += cls.CHARS["bar_2_8"]
            elif partial >= 0.125:
                bar += cls.CHARS["bar_1_8"]
            else:
                bar += cls.CHARS["bar_empty"]

            # Fill remaining with empty
            remaining = width - len(bar)
            bar += cls.CHARS["bar_empty"] * remaining

        return bar

    @classmethod
    def checkbox(cls, label, checked=False, focused=False):
        """Checkbox widget - icon IS the indicator."""
        C = cls.CHARS
        if checked:
            icon = f" {C['success']} "
            tag_bg = T().success[0] if focused else T().dark[0]
        else:
            icon = f" {C['error']} "
            tag_bg = T().error[0] if focused else T().dark[0]

        if focused:
            content_colors = T().input_bg
            content_fg = T().text
        else:
            content_colors = T().dark[0]
            content_fg = T().text_dim

        return TagBox.render(
            lines=[f" {label}"],
            tag_bg=tag_bg,
            tag_fg=T().text_dark if focused else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=[icon],
            use_gradient=focused,
        )

    @classmethod
    def dropdown(cls, label, value, options=None, focused=False, expanded=False):
        """Dropdown widget - tag icon shows expand state."""
        C = cls.CHARS
        # Icon IS the state indicator
        icon = f" {C['arrow_down']} " if expanded else f" {C['triangle_right']} "

        if focused:
            tag_bg = T().secondary[0]
            content_colors = T().input_bg
            content_fg = T().text
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim

        # Clean display - no redundant arrow
        lines = [f" {label}: {value}"]
        tag_chars = [icon]

        # Show options if expanded
        if expanded and options:
            for opt in options:
                if opt == value:
                    prefix = C["bullet"]
                else:
                    prefix = C["bullet_empty"]
                lines.append(f"     {prefix} {opt}")
                tag_chars.append("   ")

        return TagBox.render(
            lines=lines,
            tag_bg=tag_bg,
            tag_fg=T().text_dark if focused else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=tag_chars,
            use_gradient=focused,
        )

    @classmethod
    def text_input(
        cls, label, value="", placeholder="", focused=False, cursor_pos=None
    ):
        """Text input widget - clean display with cursor."""
        C = cls.CHARS
        icon = f" {C['cursor']} " if focused else "   "

        if focused:
            tag_bg = T().primary[0]
            content_colors = T().input_bg
            content_fg = T().text
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim

        # Show value or placeholder - no brackets
        if value:
            display = value
            if focused and cursor_pos is not None:
                display = value[:cursor_pos] + C["cursor"] + value[cursor_pos:]
            elif focused:
                display = value + C["cursor"]
        elif placeholder and not focused:
            display = f"{S.DIM}{placeholder}{S.RESET_DIM}"
        else:
            display = C["cursor"] if focused else ""

        return TagBox.render(
            lines=[f" {label}: {display}"],
            tag_bg=tag_bg,
            tag_fg=T().text_dark if focused else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=[icon],
            use_gradient=focused,
        )

    @classmethod
    def slider(cls, label, value, min_val=0, max_val=100, focused=False, bar_width=15):
        """Slider widget with smooth visual bar."""
        diamond = cls.CHARS["diamond"] if focused else cls.CHARS["diamond_empty"]
        icon = f" {diamond} "

        if focused:
            tag_bg = T().primary[0]
            content_colors = T().input_bg
            content_fg = T().text
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim

        # Calculate smooth progress bar
        progress = (value - min_val) / max(0.001, max_val - min_val)
        bar = cls._progress_bar(progress, bar_width)

        # Format value
        if isinstance(value, float):
            val_display = f"{value:.1f}"
        else:
            val_display = str(value)

        content = f" {label}: {val_display} {bar}"
        if focused:
            content += f" ({min_val}-{max_val})"

        return TagBox.render(
            lines=[content],
            tag_bg=tag_bg,
            tag_fg=T().text_dark if focused else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors if focused else content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=[icon],
            use_gradient=focused,
        )

    @classmethod
    def label(cls, text, style="normal"):
        """Static label - semantic styles use full colored box (no tag column)."""
        C = cls.CHARS

        # Normal/header use tag style
        if style in ("normal", "header"):
            if style == "header":
                return TagBox.render(
                    lines=[f" {S.BOLD}{text}{S.RESET_BOLD}"],
                    tag_bg=T().primary[0],
                    tag_fg=T().text_dark,
                    tag_width=cls.TAG_WIDTH,
                    content_colors=T().dark[0],
                    content_fg=T().text,
                    content_width=cls.WIDTH - cls.TAG_WIDTH,
                    tag_chars=[f" {C['square']} "],
                    use_gradient=False,
                )
            else:
                return TagBox.render(
                    lines=[f" {text}"],
                    tag_bg=T().dark[0],
                    tag_fg=T().text_dim,
                    tag_width=cls.TAG_WIDTH,
                    content_colors=T().dark[0],
                    content_fg=T().text_dim,
                    content_width=cls.WIDTH - cls.TAG_WIDTH,
                    tag_chars=["   "],
                    use_gradient=False,
                )

        # Semantic styles use full colored box (like UI.warning)
        style_config = {
            "info": (T().secondary, T().text, C["info"]),
            "success": (T().success, T().text_dark, C["success"]),
            "warning": (T().warning, T().text_dark, C["warning"]),
            "error": (T().error, T().text, C["error"]),
        }
        colors, fg, icon = style_config.get(style, (T().dark, T().text_dim, " "))

        return Box.render([f"  {icon} {text}"], colors, fg, cls.WIDTH)

    @classmethod
    def progress(cls, label, value, total, show_percent=True):
        """Progress bar widget with smooth bar."""
        C = cls.CHARS
        progress = value / max(1, total)
        bar = cls._progress_bar(progress, width=20)

        if show_percent:
            pct = f" {int(progress * 100)}%"
        else:
            pct = f" {value}/{total}"

        # Color and icon based on progress
        if progress >= 1.0:
            tag_bg = T().success[0]
            icon = f" {C['success']} "
        elif progress >= 0.5:
            tag_bg = T().primary[0]
            icon = f" {C['bullet']} "
        else:
            tag_bg = T().warning[0]
            icon = " ~ "  # Simple tilde for in-progress

        return TagBox.render(
            lines=[f" {label}: {bar}{pct}"],
            tag_bg=tag_bg,
            tag_fg=T().text_dark,
            tag_width=cls.TAG_WIDTH,
            content_colors=T().dark,
            content_fg=T().text,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=[icon],
            use_gradient=True,
        )

    @classmethod
    def multi_select(cls, label, options, selected, focused=False, focused_index=0):
        """Multi-select widget - icons show state directly."""
        C = cls.CHARS
        icon = f" {C['square']} "

        if focused:
            tag_bg = T().secondary[0]
            content_colors = T().input_bg
            content_fg = T().text
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim

        lines = [f" {label}:"]
        tag_chars = [icon]

        for i, opt in enumerate(options):
            # Icon IS the state
            if opt in selected:
                state = C["success"]
            else:
                state = C["error"]
            # Pointer for focused item
            if focused and i == focused_index:
                prefix = C["triangle_right"]
            else:
                prefix = " "
            lines.append(f"   {prefix} {state} {opt}")
            tag_chars.append("   ")

        return TagBox.render(
            lines=lines,
            tag_bg=tag_bg,
            tag_fg=T().text_dark if focused else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=tag_chars,
            use_gradient=focused,
        )

    @classmethod
    def spin_box(cls, label, value, min_val=0, max_val=100, step=1, focused=False):
        """Spin box - clean numeric input with range."""
        # Use hash symbol for numeric input
        icon = " # " if focused else "   "

        if focused:
            tag_bg = T().primary[0]
            content_colors = T().input_bg
            content_fg = T().text
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim

        # Format value
        if isinstance(value, float):
            val_display = f"{value:.2f}"
        else:
            val_display = str(value)

        # Clean display - just value and range when focused
        if focused:
            content = f" {label}: {val_display}  ({min_val}–{max_val}, step {step})"
        else:
            content = f" {label}: {val_display}"

        return TagBox.render(
            lines=[content],
            tag_bg=tag_bg,
            tag_fg=T().text_dark if focused else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=[icon],
            use_gradient=focused,
        )

    @classmethod
    def toggle(cls, label, on=False, focused=False):
        """Toggle switch widget - visual on/off switch."""
        C = cls.CHARS
        if on:
            switch = f"{C['bullet']}{C['bar_full']}{C['bar_full']}"
            icon = f" {C['success']} "
            tag_bg = T().success[0] if focused else T().dark[0]
        else:
            switch = f"{C['bar_empty']}{C['bar_empty']}{C['bullet_empty']}"
            icon = f" {C['bullet_empty']} "
            tag_bg = T().dark[0]

        if focused:
            content_colors = T().input_bg
            content_fg = T().text
        else:
            content_colors = T().dark[0]
            content_fg = T().text_dim

        state_text = "ON" if on else "OFF"
        return TagBox.render(
            lines=[f" {label}: {switch} {state_text}"],
            tag_bg=tag_bg,
            tag_fg=T().text_dark if (focused and on) else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=[icon],
            use_gradient=focused,
        )

    @classmethod
    def radio_group(cls, label, options, selected, focused=False, focused_index=0):
        """Radio button group - single selection."""
        C = cls.CHARS
        icon = f" {C['bullet']} "

        if focused:
            tag_bg = T().secondary[0]
            content_colors = T().input_bg
            content_fg = T().text
        else:
            tag_bg = T().dark[0]
            content_colors = T().dark[0]
            content_fg = T().text_dim

        lines = [f" {label}:"]
        tag_chars = [icon]

        for i, opt in enumerate(options):
            # Radio: filled = selected, empty = not selected
            if opt == selected:
                state = C["bullet"]
            else:
                state = C["bullet_empty"]
            # Pointer for focused item
            if focused and i == focused_index:
                prefix = C["triangle_right"]
            else:
                prefix = " "
            lines.append(f"     {prefix} {state} {opt}")
            tag_chars.append("   ")

        return TagBox.render(
            lines=lines,
            tag_bg=tag_bg,
            tag_fg=T().text_dark if focused else T().text_dim,
            tag_width=cls.TAG_WIDTH,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=tag_chars,
            use_gradient=focused,
        )

    @classmethod
    def separator(cls, label=""):
        """Visual separator/divider."""
        if label:
            # Labeled separator
            line_char = "─"
            left = line_char * 3
            right = line_char * (cls.WIDTH - len(label) - 8)
            content = f" {left} {label} {right}"
        else:
            content = " " + "─" * (cls.WIDTH - 2)

        return solid_fg(content, T().text_dim)

    @classmethod
    def button(cls, label, style="primary", focused=False):
        """Clickable button - full colored box."""
        C = cls.CHARS

        styles = {
            "primary": (T().primary, T().text_dark, C["triangle_right"]),
            "danger": (T().error, T().text, C["error"]),
            "success": (T().success, T().text_dark, C["success"]),
            "secondary": (T().input_bg, T().text, C["bullet_empty"]),
        }
        bg, fg, icon = styles.get(style, styles["secondary"])

        # Full box style
        content = f"  {icon} {S.BOLD}{label}{S.RESET_BOLD}"
        return Box.render([content], bg, fg, cls.WIDTH)

    @classmethod
    def form_modal(cls, title, widgets_output):
        """Render a complete form modal with widgets."""
        C = cls.CHARS
        # Title bar
        title_bar = TagBox.render(
            lines=[f" {S.BOLD}{title}{S.RESET_BOLD}"],
            tag_bg=T().primary[0],
            tag_fg=T().text_dark,
            tag_width=cls.TAG_WIDTH,
            content_colors=T().primary,
            content_fg=T().text,
            content_width=cls.WIDTH - cls.TAG_WIDTH,
            tag_chars=[f" {C['diamond']} "],
            use_gradient=True,
        )

        # Footer - segmented status_v2 style with key hints
        seg_width = cls.WIDTH // 3
        remaining = cls.WIDTH - (seg_width * 2)

        segments = [
            (T().success[0], T().text_dark, f" {C['success']} Enter: Save", seg_width),
            (T().error[0], T().text, f" {C['error']} Esc: Cancel", seg_width),
            (
                T().secondary[0],
                T().text_dark,
                f" {C['triangle_right']} Tab: Next",
                remaining,
            ),
        ]

        top = "".join(solid_fg("▄" * w, bg) for bg, _, _, w in segments)
        mid_line = "".join(
            solid(txt.ljust(w), bg, fg, w) for bg, fg, txt, w in segments
        )
        bot = "".join(solid_fg("▀" * w, bg) for bg, _, _, w in segments)

        footer = f"{top}\n{mid_line}\n{bot}"

        return f"{title_bar}\n{widgets_output}\n{footer}"


def demo_widgets():
    """Show all widget components."""

    print()
    print(UI.divider("WIDGET COMPONENTS"))
    print()

    # Checkbox
    print("checkbox:")
    print(Widgets.checkbox("Enable dark mode", checked=False, focused=False))
    print(Widgets.checkbox("Enable dark mode", checked=True, focused=True))
    print()

    # Toggle
    print("toggle:")
    print(Widgets.toggle("Stream responses", on=False, focused=False))
    print(Widgets.toggle("Stream responses", on=True, focused=True))
    print()

    # Dropdown
    print("dropdown:")
    print(Widgets.dropdown("Model", "gpt-4", focused=False))
    print(
        Widgets.dropdown(
            "Model",
            "gpt-4",
            options=["gpt-4", "gpt-4-turbo", "claude-3", "llama-3"],
            focused=True,
            expanded=True,
        )
    )
    print()

    # Text Input
    print("text_input:")
    print(Widgets.text_input("API Key", placeholder="sk-...", focused=False))
    print(Widgets.text_input("API Key", value="sk-abc123xyz", focused=True))
    print()

    # Slider
    print("slider:")
    print(Widgets.slider("Temperature", 0.7, min_val=0.0, max_val=2.0, focused=False))
    print(Widgets.slider("Temperature", 0.7, min_val=0.0, max_val=2.0, focused=True))
    print()

    # Progress
    print("progress:")
    print(Widgets.progress("Download", 25, 100))
    print(Widgets.progress("Processing", 75, 100))
    print(Widgets.progress("Complete", 100, 100))
    print()

    # Spin Box
    print("spin_box:")
    print(
        Widgets.spin_box(
            "Max Tokens", 4096, min_val=1, max_val=128000, step=256, focused=False
        )
    )
    print(
        Widgets.spin_box(
            "Max Tokens", 4096, min_val=1, max_val=128000, step=256, focused=True
        )
    )
    print()

    # Radio Group
    print("radio_group:")
    print(
        Widgets.radio_group(
            "Response Format",
            options=["text", "json", "markdown"],
            selected="json",
            focused=True,
            focused_index=1,
        )
    )
    print()

    # Multi-select
    print("multi_select:")
    print(
        Widgets.multi_select(
            "Plugins",
            options=["modern_input", "hook_monitor", "tmux", "matrix"],
            selected=["modern_input", "hook_monitor"],
            focused=True,
            focused_index=2,
        )
    )
    print()

    # Separator
    print("separator:")
    print(Widgets.separator())
    print(Widgets.separator("Advanced Options"))
    print()

    # Buttons
    print("buttons:")
    print(Widgets.button("Save Changes", style="primary", focused=True))
    print(Widgets.button("Cancel", style="secondary", focused=False))
    print(Widgets.button("Delete", style="danger", focused=False))
    print(Widgets.button("Confirm", style="success", focused=False))
    print()

    # Labels
    print("labels:")
    print(Widgets.label("This is a normal label", style="normal"))
    print(Widgets.label("Section Header", style="header"))
    print(Widgets.label("Informational message", style="info"))
    print(Widgets.label("Operation successful", style="success"))
    print(Widgets.label("Warning: Rate limit near", style="warning"))
    print(Widgets.label("Error: Connection failed", style="error"))
    print()


def demo_form_modal():
    """Show a complete form modal with multiple widgets."""

    print()
    print(UI.divider("FORM MODAL EXAMPLE"))
    print()

    # Build widgets (simulating focused state on Temperature)
    widgets = "\n".join(
        [
            Widgets.label("LLM Configuration", style="header"),
            Widgets.dropdown("Model", "gpt-4", focused=False),
            Widgets.slider("Temperature", 0.7, min_val=0.0, max_val=2.0, focused=True),
            Widgets.spin_box(
                "Max Tokens", 4096, min_val=1, max_val=128000, step=256, focused=False
            ),
            Widgets.checkbox("Stream responses", checked=True, focused=False),
            Widgets.text_input(
                "System Prompt", value="You are a helpful assistant.", focused=False
            ),
        ]
    )

    print(Widgets.form_modal("Settings", widgets))
    print()


def demo_widgets_themes():
    """Show widgets in all themes."""

    for theme_name in THEMES:
        set_theme(theme_name)
        print()
        print(f"{'=' * 56}")
        print(f"  WIDGETS - THEME: {theme_name.upper()}")
        print(f"{'=' * 56}")
        print()

        # Sample widgets
        print(Widgets.checkbox("Dark mode", checked=True, focused=True))
        print(
            Widgets.dropdown(
                "Model", "gpt-4", options=["gpt-4", "claude-3"], focused=False
            )
        )
        print(
            Widgets.slider("Temperature", 0.7, min_val=0.0, max_val=2.0, focused=True)
        )
        print(Widgets.text_input("Name", value="Kollab", focused=False))
        print(Widgets.progress("Loading", 65, 100))
        print()

    set_theme("lime")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import sys

    # Parse arguments
    args = sys.argv[1:]
    theme_arg = None
    color_mode_arg = None

    demo_mode = None

    for arg in args:
        if arg == "--themes":
            demo_mode = "themes"
        elif arg == "--widgets":
            demo_mode = "widgets"
        elif arg == "--widgets-themes":
            demo_mode = "widgets-themes"
        elif arg == "--form":
            demo_mode = "form"
        elif arg == "--all":
            demo_mode = "all"
        elif arg == "--auto":
            color_mode_arg = auto_detect_color_mode()
        elif arg in ("--256", "--posix", "-p"):
            color_mode_arg = COLOR_256
        elif arg in ("--16", "--basic"):
            color_mode_arg = COLOR_16
        elif arg in ("--truecolor", "--24bit", "-t"):
            color_mode_arg = COLOR_TRUECOLOR
        elif arg in THEMES:
            theme_arg = arg
        elif arg in ("--help", "-h"):
            print(f"""Usage: {sys.argv[0]} [options] [theme]

Color modes (POSIX compatible):
  --truecolor, --24bit, -t   24-bit RGB (default, 16M colors)
  --256, --posix, -p         256-color palette (POSIX compatible)
  --16, --basic              Basic 16 ANSI colors (most compatible)
  --auto                     Auto-detect from environment

Themes:
  lime, ocean, sunset, mono

Demos:
  --widgets                  Show widget components (NEW)
  --widgets-themes           Show widgets in all themes (NEW)
  --form                     Show form modal example (NEW)
  --themes                   Show all themes side by side
  --all                      Show everything

Other:
  --help, -h                 Show this help

Examples:
  {sys.argv[0]}              Default (truecolor, lime theme)
  {sys.argv[0]} --widgets    Preview widget design system
  {sys.argv[0]} --form       Preview form modal
  {sys.argv[0]} --256        POSIX-compatible 256-color mode
  {sys.argv[0]} --256 ocean  256-color mode with ocean theme
  {sys.argv[0]} --auto       Auto-detect color support
""")
            sys.exit(0)

    # Apply color mode
    if color_mode_arg:
        set_color_mode(color_mode_arg)
        print(f"[Color mode: {color_mode_arg}]")
        print()

    # Apply theme
    if theme_arg in THEMES:
        set_theme(theme_arg)

    # Run selected demo
    if demo_mode == "themes":
        demo_themes()
    elif demo_mode == "widgets":
        demo_widgets()
    elif demo_mode == "widgets-themes":
        demo_widgets_themes()
    elif demo_mode == "form":
        demo_form_modal()
    elif demo_mode == "all":
        demo_conversation()
        demo_components()
        demo_v2_components()
        demo_widgets()
        demo_form_modal()
    elif theme_arg in THEMES:
        demo_conversation()
        demo_v2_components()
    else:
        demo_conversation()
        demo_components()
        demo_v2_components()
