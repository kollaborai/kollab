"""
Theme system for Kollab UI.

This module provides a themable color palette system with semantic color roles
for building consistent terminal UIs. It includes preset themes (lime, ocean, sunset, mono, dark)
and utilities for managing the active theme.

Custom themes can be added as JSON files in ~/.kollab/themes/.

Example:
    from kollabor_tui.design_system.theme import Theme, set_theme, T, S

    # Switch to ocean theme
    set_theme('ocean')

    # Access active theme
    theme = T()
    print(theme.primary)

    # Use style codes
    bold_text = f"{S.BOLD}Hello{S.RESET}"
"""

__all__ = [
    "Theme",
    "THEMES",
    "set_theme",
    "get_theme",
    "T",
    "S",
]


class Theme:
    """Themeable color palette with semantic color roles.

    Attributes:
        name: Theme name identifier
        primary: Primary gradient colors (list of RGB tuples)
        primary_dark: Dark primary gradient colors (list of RGB tuples)
        secondary: Secondary gradient colors (list of RGB tuples)
        response_bg: Response background gradient (list of RGB tuples)
        input_bg: Input background gradient (list of RGB tuples)
        dark: Dark background gradient (list of RGB tuples)
        success: Success color gradient (list of RGB tuples)
        error: Error color gradient (list of RGB tuples)
        warning: Warning color gradient (list of RGB tuples)
        user_tag: User tag color (RGB tuple)
        ai_tag: AI tag color (RGB tuple)
        tool_tag: Tool tag color (RGB tuple)
        thinking_tag: Thinking tag color (RGB tuple)
        code_bg: Code block background color (RGB tuple)
        text: Primary text color (RGB tuple)
        text_dim: Dimmed text color (RGB tuple)
        text_dark: Dark text color (RGB tuple)
    """

    def __init__(self, name, **colors):
        """Initialize a theme with a name and color overrides.

        Args:
            name: Theme name identifier
            **colors: Color attribute overrides (gradients as lists, solids as tuples)
        """
        self.name = name
        # Gradients (list of RGB tuples)
        self.primary = colors.get(
            "primary", [(80, 200, 50), (100, 220, 70), (120, 240, 90)]
        )
        self.primary_dark = colors.get(
            "primary_dark", [(50, 120, 30), (40, 100, 25), (30, 80, 20)]
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

    @staticmethod
    def text_on(bg: tuple) -> tuple:
        """Return readable text color (light or dark) for the given background.

        Uses relative luminance (BT.601) to pick white or near-black text.
        """
        r, g, b = bg[0], bg[1], bg[2]
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        return (20, 20, 20) if luminance > 140 else (240, 240, 240)


# Preset themes
THEMES = {
    "lime": Theme(
        "lime",
        primary=[(248, 108, 43), (236, 130, 70), (214, 92, 34)],
        primary_dark=[(92, 42, 23), (68, 30, 18), (42, 20, 14)],
        secondary=[(44, 50, 58), (36, 41, 48), (28, 32, 38)],
        response_bg=[(16, 18, 21), (13, 15, 18), (10, 12, 15)],
        input_bg=[(20, 23, 27), (16, 19, 23), (12, 15, 19)],
        dark=[(18, 20, 22), (12, 14, 17), (8, 10, 12)],
        user_tag=(248, 108, 43),
        ai_tag=(116, 210, 137),
        tool_tag=(236, 130, 70),
        thinking_tag=(190, 135, 74),
        code_bg=(9, 11, 14),
        success=[(96, 190, 120), (112, 210, 140), (132, 228, 158)],
        error=[(210, 76, 72), (230, 92, 88), (244, 114, 106)],
        warning=[(224, 152, 62), (240, 172, 82), (248, 190, 100)],
        text=(236, 240, 244),
        text_dim=(145, 153, 164),
        text_dark=(10, 12, 15),
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
    "dark": Theme(
        "dark",
        primary=[(248, 108, 43), (236, 130, 70), (214, 92, 34)],
        primary_dark=[(92, 42, 23), (68, 30, 18), (42, 20, 14)],
        secondary=[(44, 50, 58), (36, 41, 48), (28, 32, 38)],
        response_bg=[(16, 18, 21), (13, 15, 18), (10, 12, 15)],
        input_bg=[(20, 23, 27), (16, 19, 23), (12, 15, 19)],
        dark=[(18, 20, 22), (12, 14, 17), (8, 10, 12)],
        user_tag=(248, 108, 43),
        ai_tag=(116, 210, 137),
        tool_tag=(236, 130, 70),
        thinking_tag=(190, 135, 74),
        code_bg=(9, 11, 14),
        success=[(96, 190, 120), (112, 210, 140), (132, 228, 158)],
        error=[(210, 76, 72), (230, 92, 88), (244, 114, 106)],
        warning=[(224, 152, 62), (240, 172, 82), (248, 190, 100)],
        text=(236, 240, 244),
        text_dim=(145, 153, 164),
        text_dark=(10, 12, 15),
    ),
}

# Custom theme cache
_custom_themes: dict[str, "Theme"] = {}


def _load_custom_themes():
    """Load custom themes from ~/.kollab/themes/*.json."""
    global _custom_themes
    if _custom_themes:
        return _custom_themes

    try:
        import json

        from kollabor_config.config_utils import resolve_global_path

        themes_dir = resolve_global_path("themes")
        if not themes_dir.exists():
            themes_dir.mkdir(parents=True, exist_ok=True)

        for theme_file in themes_dir.glob("*.json"):
            try:
                with open(theme_file, "r") as f:
                    theme_data = json.load(f)

                theme_name = theme_data.get("name")
                if not theme_name:
                    continue

                # Create Theme from JSON data
                theme = Theme(
                    name=theme_name,
                    primary=theme_data.get(
                        "primary", [(80, 200, 50), (100, 220, 70), (120, 240, 90)]
                    ),
                    primary_dark=theme_data.get(
                        "primary_dark", [(50, 120, 30), (40, 100, 25), (30, 80, 20)]
                    ),
                    secondary=theme_data.get(
                        "secondary", [(20, 160, 180), (30, 180, 200), (40, 200, 220)]
                    ),
                    response_bg=theme_data.get(
                        "response_bg", [(55, 55, 55), (45, 45, 45), (35, 35, 35)]
                    ),
                    input_bg=theme_data.get(
                        "input_bg", [(55, 60, 68), (46, 50, 58), (38, 42, 48)]
                    ),
                    dark=theme_data.get(
                        "dark", [(35, 35, 35), (25, 25, 25), (18, 18, 18)]
                    ),
                    success=theme_data.get(
                        "success", [(60, 180, 80), (80, 200, 100), (100, 220, 120)]
                    ),
                    error=theme_data.get(
                        "error", [(180, 60, 60), (200, 80, 80), (220, 100, 100)]
                    ),
                    warning=theme_data.get(
                        "warning", [(200, 120, 40), (220, 140, 60), (240, 160, 80)]
                    ),
                    user_tag=tuple(theme_data.get("user_tag", [50, 200, 220])),
                    ai_tag=tuple(theme_data.get("ai_tag", [80, 180, 50])),
                    tool_tag=tuple(theme_data.get("tool_tag", [50, 140, 180])),
                    thinking_tag=tuple(theme_data.get("thinking_tag", [200, 140, 40])),
                    code_bg=tuple(theme_data.get("code_bg", [25, 25, 25])),
                    text=tuple(theme_data.get("text", [255, 255, 255])),
                    text_dim=tuple(theme_data.get("text_dim", [120, 120, 120])),
                )
                _custom_themes[theme_name] = theme
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    f"Error loading theme {theme_file}: {e}"
                )
    except Exception:
        pass

    return _custom_themes


# Active theme (mutable)
_active_theme = THEMES["dark"]


def set_theme(name):
    """Switch active theme by name.

    Args:
        name: Theme name ('lime', 'ocean', 'sunset', 'mono', 'dark', or custom theme)

    Raises:
        ValueError: If theme name is not found in THEMES or custom themes

    Example:
        set_theme('ocean')
    """
    global _active_theme

    # Check built-in themes first
    if name in THEMES:
        _active_theme = THEMES[name]
        return

    # Check custom themes
    custom_themes = _load_custom_themes()
    if name in custom_themes:
        _active_theme = custom_themes[name]
        return

    # Not found
    available = list(THEMES.keys()) + list(custom_themes.keys())
    raise ValueError(f"Unknown theme: {name}. Available: {sorted(available)}")


def get_theme():
    """Get the active theme.

    Returns:
        Theme: The currently active theme object

    Example:
        theme = get_theme()
        print(theme.primary)
    """
    return _active_theme


def T():
    """Shorthand to get active theme.

    Returns:
        Theme: The currently active theme object

    Example:
        primary = T().primary
    """
    return get_theme()


class S:
    """ANSI style codes for terminal text formatting.

    These ANSI escape codes control text appearance in terminal output.
    Always pair style codes with S.RESET to avoid bleeding formatting.

    Example:
        bold_text = f"{S.BOLD}Important{S.RESET}"
        dim_text = f"{S.DIM}Secondary info{S.RESET}"
    """

    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    RESET = "\033[0m"
    RESET_BOLD = "\033[22m"
    RESET_DIM = "\033[22m"
    RESET_ITALIC = "\033[23m"
