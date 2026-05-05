"""Design System for Kollab.

A comprehensive design system providing theming, gradients, and UI components
for building consistent, visually appealing terminal interfaces.

Modules:
    color_mode: Color mode detection and conversion (truecolor/256/16)
    theme: Themeable color palettes with semantic roles
    gradient: Character-by-character gradient rendering
    components: Box and TagBox rendering components

Quick Start:
    from kollabor_tui.design_system import T, S, Box, TagBox, gradient, solid

    # Use theme colors
    primary = T().primary

    # Create a gradient box
    box = Box.render(["Hello World"], T().primary, T().text, 40)

    # Style text
    styled = f"{S.BOLD}Important{S.RESET}"

Example:
    from kollabor_tui.design_system import (
        set_theme, T, S,
        gradient, gradient_fg,
        solid, solid_fg,
        Box, TagBox,
    )

    # Switch theme
    set_theme('ocean')

    # Render a TagBox
    output = TagBox.render(
        lines=[" Hello World"],
        tag_bg=T().primary[0],

        tag_width=3,
        content_colors=T().response_bg,
        content_fg=T().text,
        content_width=47,
        tag_chars=[" * "],
    )
"""

# Color mode system
from .color_mode import (
    COLOR_16,
    COLOR_256,
    COLOR_TRUECOLOR,
    auto_detect_color_mode,
    get_color_mode,
    rgb_to_16,
    rgb_to_256,
    set_color_mode,
)

# Auto-detect color mode on import (ensures terminal compatibility)
_detected_mode = auto_detect_color_mode()
set_color_mode(_detected_mode)

# Theme system
# Border style system
from .border_style import (  # noqa: E402
    BORDER_STYLES,
    BorderStyle,
    get_border_chars,
    get_border_style,
    list_border_styles,
    set_border_style,
)

# UI components
from .components import (  # noqa: E402
    Box,
    C,
    TagBox,
    progress_bar,
    solid,
    solid_fg,
    wrap_text,
)

# Gradient engine
from .gradient import (  # noqa: E402
    ANSI_RE,
    gradient,
    gradient_fg,
    smooth_gradient,
    smooth_gradient_subtle,
)

# Icons
from .icons import IconColors, Icons, color_icon  # noqa: E402

# Inline widgets for status lines
from .inline_widgets import (  # noqa: E402
    inline_badge,
    inline_checkbox,
    inline_dropdown,
    inline_meter,
    inline_progress,
    inline_slider,
    inline_spinner,
    inline_toggle,
)
from .theme import (  # noqa: E402
    THEMES,
    S,
    T,
    Theme,
    get_theme,
    set_theme,
)

# High-level UI components
from .ui_components import UI  # noqa: E402

__all__ = [
    # Color mode
    "COLOR_TRUECOLOR",
    "COLOR_256",
    "COLOR_16",
    "set_color_mode",
    "get_color_mode",
    "auto_detect_color_mode",
    "rgb_to_256",
    "rgb_to_16",
    # Theme
    "Theme",
    "THEMES",
    "set_theme",
    "get_theme",
    "T",
    "S",
    # Gradient
    "ANSI_RE",
    "gradient",
    "gradient_fg",
    "smooth_gradient",
    "smooth_gradient_subtle",
    # Components
    "solid",
    "solid_fg",
    "Box",
    "TagBox",
    "C",
    "wrap_text",
    "progress_bar",
    # High-level UI
    "UI",
    # Icons
    "IconColors",
    "Icons",
    "color_icon",
    # Inline widgets
    "inline_checkbox",
    "inline_toggle",
    "inline_progress",
    "inline_slider",
    "inline_dropdown",
    "inline_badge",
    "inline_spinner",
    "inline_meter",
    # Border styles
    "BORDER_STYLES",
    "BorderStyle",
    "set_border_style",
    "get_border_style",
    "get_border_chars",
    "list_border_styles",
]
