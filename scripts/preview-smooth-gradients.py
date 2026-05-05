#!/usr/bin/env python3
"""Smooth background gradients - subtle monochromatic only.

Character-by-character smooth gradients on backgrounds only.
Text stays solid color (white on gray, black on lime).
"""


def smooth_gradient(text, bg_colors, fg_color=(255, 255, 255), width=None):
    """Create smooth character-by-character background gradient.

    Args:
        text: Text to colorize
        bg_colors: List of RGB tuples for background gradient
        fg_color: Foreground color RGB tuple (default white)
        width: Width to pad text to (optional)

    Returns:
        Formatted string with smooth background gradient
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
    characters (▀▄) visible but very subtle - creates a "half-padding" effect.

    Args:
        text: Text to colorize
        bg_colors: List of RGB tuples for gradient
        width: Width to pad text to (optional)
        lighten: How much to lighten foreground (default 20)
        no_background: If True, don't set background color (default False)

    Returns:
        Formatted string with subtle fg/bg gradient
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


# Visible monochromatic color palettes

# Dark gray to black (dark range)
GRAY_BLACK = [
    (40, 40, 40),
    (30, 30, 30),
    (20, 20, 20),
    (15, 15, 15),
    (10, 10, 10),
]

# Dark gray to medium-dark gray
GRAY_LIGHT = [
    (55, 55, 55),
    (50, 50, 50),
    (45, 45, 45),
    (40, 40, 40),
]

# Vibrant lime gradients
LIME_BRIGHT = [
    (80, 205, 50),  # Vibrant dark lime
    (90, 215, 70),
    (100, 225, 80),
    (110, 235, 90),
    (120, 245, 100),  # Bright lime
]

LIME_RANGE = [
    (160, 255, 140),  # Bright lime
    (140, 235, 115),
    (120, 215, 90),
    (100, 195, 65),  # Dark lime
]

# Text colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def print_header(title):
    """Print gradient header."""
    line = smooth_gradient(title.center(70), LIME_BRIGHT, BLACK, 70)
    top = smooth_gradient(
        "▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀".center(
            70
        ),
        LIME_BRIGHT,
        BLACK,
        70,
    )
    bottom = smooth_gradient(
        "▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄".center(
            70
        ),
        LIME_BRIGHT,
        BLACK,
        70,
    )
    print(f"{top}")
    print(f"{line}")
    print(f"{bottom}")


def demo_gradients():
    """Show gradient families."""
    print_header("Subtle Monochromatic Gradients")

    print("Dark Gray → Black (white text):")
    line = smooth_gradient("Dark Gray → Black Gradient", GRAY_BLACK, WHITE)
    print(line)
    print()

    print("Light Gray → Dark Gray (white text):")
    line = smooth_gradient("Light Gray → Dark Gray", GRAY_LIGHT, WHITE)
    print(line)
    print()

    print("Lime → Brighter Lime (black text):")
    line = smooth_gradient("Lime → Brighter Lime", LIME_BRIGHT, BLACK)
    print(line)
    print()

    print("Bright → Dark Lime (black text):")
    line = smooth_gradient("Bright → Dark Lime", LIME_RANGE, BLACK)
    print(line)
    print()


def demo_multiselect():
    """MultiSelect with smooth gradients."""
    print_header("MultiSelect Widget")

    # Lime gradient title
    title = smooth_gradient("Select Plugins", LIME_BRIGHT, BLACK)
    print(title)
    print()

    # Gray gradient options
    options = ["  ● enhanced-input", "  ● hook-monitor", "  ○ tmux-integration"]

    for opt in options:
        line = smooth_gradient(opt, GRAY_LIGHT, WHITE)
        print(line)

    print()

    # Lime gradient status
    status = smooth_gradient("  2 of 3 selected", LIME_RANGE, BLACK)
    print(status)
    print()


def demo_textarea():
    """TextArea with smooth gradients and half-block padding."""
    print_header("TextArea Widget")

    # Pane width
    width = 60

    # Gray gradient header with character count
    header = smooth_gradient("System Prompt    234 / 5000", GRAY_BLACK, WHITE, width)
    print(header)
    print()

    # Top half-block padding (no background - just the visible colored tops)
    top_padding = smooth_gradient_subtle(
        "▄" * width, GRAY_LIGHT, width, no_background=True
    )
    print(top_padding)

    # Content lines with gradient
    content_lines = [
        "  This is a multi-line text input",
        "  with smooth background gradients",
        "  subtle monochromatic transitions",
        "  classy and professional",
    ]

    for line in content_lines:
        colored = smooth_gradient(line, GRAY_LIGHT, WHITE, width)
        print(colored)

    # Bottom half-block padding (no background - just the visible colored bottoms)
    bottom_padding = smooth_gradient_subtle(
        "▀" * width, GRAY_LIGHT, width, no_background=True
    )
    print(bottom_padding)
    print()


def demo_spinbox():
    """SpinBox with smooth gradients."""
    print_header("SpinBox Widget")

    # Lime gradient title
    title = smooth_gradient("Temperature", LIME_BRIGHT, BLACK)
    print(title)
    print()

    # Gray gradient controls
    controls = smooth_gradient("  [ − ]      0.7 °C      [ + ]", GRAY_LIGHT, WHITE)
    print(controls)
    print()

    # Slider with gray gradient
    slider = smooth_gradient("  0.0  ─────────●────────  2.0", GRAY_BLACK, WHITE)
    print(slider)
    print()

    # Info in lighter gray
    info = smooth_gradient("  Range: 0.0 to 2.0  •  Step: 0.1", GRAY_LIGHT, WHITE)
    print(info)
    print()


def demo_progress():
    """Progress with smooth gradients."""
    print_header("Progress Widget")

    # Lime gradient title
    title = smooth_gradient("Processing Files", LIME_BRIGHT, BLACK)
    print(title)
    print()

    # Lime gradient progress bar
    bar = smooth_gradient("  ──────────────●──────────", LIME_BRIGHT, BLACK)
    print(bar)
    print()

    # Lime gradient percentage
    pct = smooth_gradient("  45 %  ", LIME_RANGE, BLACK)
    print(pct)
    print()

    # Gray gradient details
    details = ["  45 of 100", "  Processing...", "  ETA: 2m"]
    for detail in details:
        colored = smooth_gradient(detail, GRAY_LIGHT, WHITE)
        print(colored)

    print()


def demo_treeview():
    """TreeView with smooth gradients."""
    print_header("TreeView Widget")

    # Lime gradient title
    title = smooth_gradient("Project Structure", LIME_BRIGHT, BLACK)
    print(title)
    print()

    # Gray gradient tree items
    items = [
        "  core",
        "    llm",
        "    ui",
        "    application.py",
        "",
        "  plugins",
        "    enhanced-input",
        "    hook-monitor",
    ]

    for item in items:
        if item.strip():
            colored = smooth_gradient(item, GRAY_LIGHT, WHITE)
            print(colored)
        else:
            print()

    print()


def demo_form():
    """Complete form with smooth gradients."""
    print_header("Complete Form")

    # Lime gradient title
    title = smooth_gradient("⚙  CONFIGURATION  ", LIME_BRIGHT, BLACK)
    print(title)
    print()

    fields = [
        ("Model", "  gpt-4"),
        ("Temperature", "  0.0  ─────●────  2.0"),
        ("Max Tokens", "  4000"),
        ("Enable Streaming", "  ● Yes"),
    ]

    for label, value in fields:
        # Gray gradient label
        label_line = smooth_gradient(label, GRAY_BLACK, WHITE)
        print(label_line)

        # Light gray gradient value
        value_line = smooth_gradient(value, GRAY_LIGHT, WHITE)
        print(value_line)
        print()

    # Lime gradient button
    button = smooth_gradient("  Save Configuration  ", LIME_BRIGHT, BLACK)
    print(button)
    print()


def demo_buttons():
    """Button states with smooth gradients."""
    print_header("Button States")

    print("Primary (lime gradient, black text):")
    btn = smooth_gradient("  Save  ", LIME_BRIGHT, BLACK)
    print(btn)
    print()

    print("Secondary (gray gradient, white text):")
    btn = smooth_gradient("  Cancel  ", GRAY_LIGHT, WHITE)
    print(btn)
    print()

    print("Tertiary (dark gray gradient, white text):")
    btn = smooth_gradient("  Disabled  ", GRAY_BLACK, WHITE)
    print(btn)
    print()


def demo_checkbox():
    """Checkbox widget."""
    print_header("Checkbox Widget")

    label = smooth_gradient("Enable Debug Mode", GRAY_BLACK, WHITE)
    print(label)
    print()

    # Checked - lime gradient
    checked = smooth_gradient("  ● Enabled", LIME_BRIGHT, BLACK)
    print(checked)
    print()


def demo_slider():
    """Slider widget."""
    print_header("Slider Widget")

    label = smooth_gradient("Temperature", GRAY_BLACK, WHITE)
    print(label)
    print()

    # Slider with gradient
    slider = smooth_gradient("  0.0  ─────●────  1.0", GRAY_LIGHT, WHITE)
    print(slider)
    print()

    # Value in lime gradient
    value = smooth_gradient("  0.7", LIME_RANGE, BLACK)
    print(value)
    print()


def demo_quality():
    """Demonstrate gradient quality."""
    print_header("Gradient Quality")

    print("60 characters of smooth gray gradient:")
    demo = smooth_gradient("█" * 60, GRAY_BLACK, WHITE)
    print(demo)
    print()

    print("60 characters of smooth lime gradient:")
    demo = smooth_gradient("█" * 60, LIME_BRIGHT, BLACK)
    print(demo)
    print()


DEMOS = {
    "quality": ("demo_quality", "gradient quality bars"),
    "gradients": ("demo_gradients", "gradient color families"),
    "buttons": ("demo_buttons", "button states"),
    "multiselect": ("demo_multiselect", "multi-select widget"),
    "textarea": ("demo_textarea", "textarea widget"),
    "spinbox": ("demo_spinbox", "spin box widget"),
    "progress": ("demo_progress", "progress widget"),
    "treeview": ("demo_treeview", "tree view widget"),
    "checkbox": ("demo_checkbox", "checkbox widget"),
    "slider": ("demo_slider", "slider widget"),
    "form": ("demo_form", "complete form"),
    "icons": ("demo_icons", "icon collection (200 icons)"),
    "showcase": ("demo_terminal_showcase", "terminal UI showcase / LLM chat mockup"),
}


def main():
    """Display smooth gradient previews."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Preview smooth background gradient UI components",
        epilog="\n".join([f"  {name:<14} {desc}" for name, (_, desc) in DEMOS.items()]),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "demo",
        nargs="?",
        choices=list(DEMOS.keys()),
        help="specific demo to run (default: all)",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="list available demos",
    )
    args = parser.parse_args()

    if args.list:
        print("available demos:")
        for name, (_, desc) in DEMOS.items():
            print(f"  {name:<14} {desc}")
        return

    current_module = sys.modules[__name__]

    if args.demo:
        fn_name, desc = DEMOS[args.demo]
        fn = getattr(current_module, fn_name)
        fn()
        return

    # run all
    print("\n" + "=" * 70)
    title = smooth_gradient("SMOOTH BACKGROUND GRADIENTS", LIME_BRIGHT, BLACK)
    print(title)
    print("=" * 70)

    for name, (fn_name, _) in DEMOS.items():
        fn = getattr(current_module, fn_name)
        fn()

    print_header("Design Principles")
    print("✓ Background gradients only (text is solid color)")
    print("✓ Character-by-character smooth interpolation")
    print("✓ Vibrant monochromatic palettes:")
    print("  • Gray: 70-10 RGB range (dark)")
    print("  • Lime: 80-160/255 RGB range (vibrant)")
    print("✓ White text on dark backgrounds")
    print("✓ Black text on bright lime backgrounds")
    print("✓ Left to right only")
    print("✓ Classy and professional")
    print()

    ready = smooth_gradient("Ready to implement?", LIME_BRIGHT, BLACK)
    print(ready)
    print()


# ============================================================================
# ICON COLLECTION - 100 ASCII/Unicode symbols with colors
# ============================================================================


class IconColors:
    """Color definitions for icons."""

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
    """Apply color to an icon character."""
    r, g, b = color
    return f"\033[38;2;{r};{g};{b}m{icon}\033[0m"


class Icons:
    """Collection of 100 terminal UI icons."""

    # 1-10: Arrows - Right
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

    # 11-20: Arrows - Left
    ARROW_LEFT_1 = ("←", IconColors.LIME)
    ARROW_LEFT_2 = ("❮", IconColors.GREEN)
    ARROW_LEFT_3 = ("◅", IconColors.CYAN)
    ARROW_LEFT_4 = ("◄", IconColors.BLUE)
    ARROW_LEFT_5 = ("➲", IconColors.PURPLE)
    ARROW_LEFT_6 = ("⤜", IconColors.PINK)
    ARROW_LEFT_7 = ("⭠", IconColors.RED)
    ARROW_LEFT_8 = ("⇐", IconColors.ORANGE)
    ARROW_LEFT_9 = ("⤍", IconColors.YELLOW)
    ARROW_LEFT_10 = ("⤛", IconColors.WHITE)

    # 21-30: Arrows - Up
    ARROW_UP_1 = ("↑", IconColors.CYAN)
    ARROW_UP_2 = ("△", IconColors.BLUE)
    ARROW_UP_3 = ("▲", IconColors.PURPLE)
    ARROW_UP_4 = ("⭡", IconColors.PINK)
    ARROW_UP_5 = ("⇑", IconColors.RED)
    ARROW_UP_6 = ("⤒", IconColors.ORANGE)
    ARROW_UP_7 = ("⤖", IconColors.YELLOW)
    ARROW_UP_8 = ("↟", IconColors.WHITE)
    ARROW_UP_9 = ("⇧", IconColors.GRAY)
    ARROW_UP_10 = ("⤒", IconColors.GRAY_DARK)

    # 31-40: Arrows - Down
    ARROW_DOWN_1 = ("↓", IconColors.CYAN)
    ARROW_DOWN_2 = ("▽", IconColors.BLUE)
    ARROW_DOWN_3 = ("▼", IconColors.PURPLE)
    ARROW_DOWN_4 = ("⭣", IconColors.PINK)
    ARROW_DOWN_5 = ("⇓", IconColors.RED)
    ARROW_DOWN_6 = ("⤓", IconColors.ORANGE)
    ARROW_DOWN_7 = ("⤵", IconColors.YELLOW)
    ARROW_DOWN_8 = ("⬇", IconColors.WHITE)
    ARROW_DOWN_9 = ("↧", IconColors.GRAY)
    ARROW_DOWN_10 = ("⇩", IconColors.GRAY_DARK)

    # 41-50: Arrows - Diagonal
    ARROW_DIAG_1 = ("↗", IconColors.LIME)
    ARROW_DIAG_2 = ("↘", IconColors.GREEN)
    ARROW_DIAG_3 = ("↙", IconColors.CYAN)
    ARROW_DIAG_4 = ("↖", IconColors.BLUE)
    ARROW_DIAG_5 = ("⇄", IconColors.PURPLE)
    ARROW_DIAG_6 = ("⇅", IconColors.PINK)
    ARROW_DIAG_7 = ("⇆", IconColors.RED)
    ARROW_DIAG_8 = ("⇋", IconColors.ORANGE)
    ARROW_DIAG_9 = ("⇌", IconColors.YELLOW)
    ARROW_DIAG_10 = ("↔", IconColors.WHITE)

    # 51-60: Checkmarks & Success
    CHECK_1 = ("✓", IconColors.LIME)
    CHECK_2 = ("✔", IconColors.GREEN)
    CHECK_3 = ("√", IconColors.CYAN)
    CHECK_4 = ("☑", IconColors.BLUE)
    CHECK_5 = ("☒", IconColors.PURPLE)
    CHECK_6 = ("✓", IconColors.PINK)
    CHECK_7 = ("✔", IconColors.RED)
    CHECK_8 = ("√", IconColors.ORANGE)
    CHECK_9 = ("☑", IconColors.YELLOW)
    CHECK_10 = ("✔", IconColors.WHITE)

    # 61-70: Cross & Error
    CROSS_1 = ("✕", IconColors.RED)
    CROSS_2 = ("✖", IconColors.PINK)
    CROSS_3 = ("×", IconColors.ORANGE)
    CROSS_4 = ("☓", IconColors.YELLOW)
    CROSS_5 = ("✗", IconColors.RED)
    CROSS_6 = ("✘", IconColors.PINK)
    CROSS_7 = ("⤫", IconColors.ORANGE)
    CROSS_8 = ("⨯", IconColors.YELLOW)
    CROSS_9 = ("✕", IconColors.RED)
    CROSS_10 = ("⊗", IconColors.PINK)

    # 71-80: Bullets & Dots - Large
    BULLET_BIG_1 = ("●", IconColors.LIME)
    BULLET_BIG_2 = ("◉", IconColors.GREEN)
    BULLET_BIG_3 = ("◎", IconColors.CYAN)
    BULLET_BIG_4 = ("◐", IconColors.BLUE)
    BULLET_BIG_5 = ("◑", IconColors.PURPLE)
    BULLET_BIG_6 = ("◒", IconColors.PINK)
    BULLET_BIG_7 = ("⊚", IconColors.RED)
    BULLET_BIG_8 = ("⊛", IconColors.ORANGE)
    BULLET_BIG_9 = ("⚫", IconColors.GRAY_DARK)
    BULLET_BIG_10 = ("⚪", IconColors.GRAY_LIGHT)

    # 81-90: Bullets & Dots - Small
    BULLET_SMALL_1 = ("•", IconColors.LIME)
    BULLET_SMALL_2 = ("·", IconColors.GREEN)
    BULLET_SMALL_3 = ("∙", IconColors.CYAN)
    BULLET_SMALL_4 = ("∘", IconColors.BLUE)
    BULLET_SMALL_5 = ("◌", IconColors.PURPLE)
    BULLET_SMALL_6 = ("◍", IconColors.PINK)
    BULLET_SMALL_7 = ("⊡", IconColors.RED)
    BULLET_SMALL_8 = ("⊙", IconColors.ORANGE)
    BULLET_SMALL_9 = ("⊝", IconColors.YELLOW)
    BULLET_SMALL_10 = ("⊘", IconColors.WHITE)

    # 91-100: Loading & Spinner
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

    # 101-110: Shapes - Boxes
    BOX_1 = ("■", IconColors.LIME)
    BOX_2 = ("□", IconColors.GREEN)
    BOX_3 = ("▢", IconColors.CYAN)
    BOX_4 = ("▣", IconColors.BLUE)
    BOX_5 = ("▤", IconColors.PURPLE)
    BOX_6 = ("▥", IconColors.PINK)
    BOX_7 = ("▦", IconColors.RED)
    BOX_8 = ("▧", IconColors.ORANGE)
    BOX_9 = ("▨", IconColors.YELLOW)
    BOX_10 = ("▩", IconColors.WHITE)

    # 111-120: Shapes - Circles
    CIRCLE_1 = ("●", IconColors.LIME)
    CIRCLE_2 = ("○", IconColors.GREEN)
    CIRCLE_3 = ("◯", IconColors.CYAN)
    CIRCLE_4 = ("◕", IconColors.BLUE)
    CIRCLE_5 = ("◔", IconColors.PURPLE)
    CIRCLE_6 = ("◴", IconColors.PINK)
    CIRCLE_7 = ("◷", IconColors.RED)
    CIRCLE_8 = ("◵", IconColors.ORANGE)
    CIRCLE_9 = ("◶", IconColors.YELLOW)
    CIRCLE_10 = ("⬤", IconColors.WHITE)

    # 121-130: Progress Bars
    PROGRESS_1 = ("█", IconColors.LIME)
    PROGRESS_2 = ("▉", IconColors.GREEN)
    PROGRESS_3 = ("▊", IconColors.CYAN)
    PROGRESS_4 = ("▋", IconColors.BLUE)
    PROGRESS_5 = ("▌", IconColors.PURPLE)
    PROGRESS_6 = ("▍", IconColors.PINK)
    PROGRESS_7 = ("▎", IconColors.RED)
    PROGRESS_8 = ("▏", IconColors.ORANGE)
    PROGRESS_9 = ("░", IconColors.YELLOW)
    PROGRESS_10 = ("▒", IconColors.GRAY)

    # 131-140: Brackets & Delimiters
    BRACKET_1 = ("[", IconColors.GRAY)
    BRACKET_2 = ("]", IconColors.GRAY)
    BRACKET_3 = ("{", IconColors.GRAY_LIGHT)
    BRACKET_4 = ("}", IconColors.GRAY_LIGHT)
    BRACKET_5 = ("(", IconColors.GRAY_DARK)
    BRACKET_6 = (")", IconColors.GRAY_DARK)
    BRACKET_7 = ("<", IconColors.WHITE)
    BRACKET_8 = (">", IconColors.WHITE)
    BRACKET_9 = ("«", IconColors.CYAN)
    BRACKET_10 = ("»", IconColors.CYAN)

    # 141-150: Lines & Separators
    LINE_1 = ("─", IconColors.GRAY)
    LINE_2 = ("│", IconColors.GRAY)
    LINE_3 = ("┄", IconColors.GRAY_LIGHT)
    LINE_4 = ("┅", IconColors.GRAY_LIGHT)
    LINE_5 = ("┆", IconColors.GRAY_DARK)
    LINE_6 = ("┇", IconColors.GRAY_DARK)
    LINE_7 = ("┈", IconColors.WHITE)
    LINE_8 = ("┉", IconColors.WHITE)
    LINE_9 = ("┊", IconColors.GRAY)
    LINE_10 = ("┋", IconColors.GRAY)

    # 151-160: Special Characters
    SPECIAL_1 = ("⚙", IconColors.GRAY)
    SPECIAL_2 = ("⚡", IconColors.YELLOW)
    SPECIAL_3 = ("◆", IconColors.CYAN)
    SPECIAL_4 = ("◇", IconColors.BLUE)
    SPECIAL_5 = ("★", IconColors.ORANGE)
    SPECIAL_6 = ("☆", IconColors.GRAY_LIGHT)
    SPECIAL_7 = ("♦", IconColors.RED)
    SPECIAL_8 = ("♢", IconColors.PINK)
    SPECIAL_9 = ("♠", IconColors.PURPLE)
    SPECIAL_10 = ("♤", IconColors.CYAN)

    # 161-170: Technical
    TECH_1 = ("⌘", IconColors.GRAY)
    TECH_2 = ("⌥", IconColors.GRAY_LIGHT)
    TECH_3 = ("⇧", IconColors.GRAY_DARK)
    TECH_4 = ("⎋", IconColors.WHITE)
    TECH_5 = ("⎊", IconColors.GRAY)
    TECH_6 = ("⎈", IconColors.GRAY_LIGHT)
    TECH_7 = ("⌗", IconColors.GRAY_DARK)
    TECH_8 = ("⌦", IconColors.WHITE)
    TECH_9 = ("⎇", IconColors.GRAY)
    TECH_10 = ("⌫", IconColors.GRAY_LIGHT)

    # 171-180: Math & Logic
    MATH_1 = ("+", IconColors.LIME)
    MATH_2 = ("−", IconColors.GREEN)
    MATH_3 = ("×", IconColors.CYAN)
    MATH_4 = ("÷", IconColors.BLUE)
    MATH_5 = ("±", IconColors.PURPLE)
    MATH_6 = ("∓", IconColors.PINK)
    MATH_7 = ("∔", IconColors.RED)
    MATH_8 = ("∝", IconColors.ORANGE)
    MATH_9 = ("∞", IconColors.YELLOW)
    MATH_10 = ("∆", IconColors.WHITE)

    # 181-190: Status & Indicators
    STATUS_1 = ("•", IconColors.LIME)
    STATUS_2 = ("◦", IconColors.GREEN)
    STATUS_3 = ("☰", IconColors.CYAN)
    STATUS_4 = ("☱", IconColors.BLUE)
    STATUS_5 = ("☲", IconColors.PURPLE)
    STATUS_6 = ("☳", IconColors.PINK)
    STATUS_7 = ("⚊", IconColors.RED)
    STATUS_8 = ("⚋", IconColors.ORANGE)
    STATUS_9 = ("⚌", IconColors.YELLOW)
    STATUS_10 = ("⚍", IconColors.WHITE)

    # 191-200: Miscellaneous
    MISC_1 = ("§", IconColors.GRAY)
    MISC_2 = ("¶", IconColors.GRAY_LIGHT)
    MISC_3 = ("†", IconColors.GRAY_DARK)
    MISC_4 = ("‡", IconColors.WHITE)
    MISC_5 = ("…", IconColors.GRAY)
    MISC_6 = ("∴", IconColors.BLUE)
    MISC_7 = ("∵", IconColors.PURPLE)
    MISC_8 = ("∷", IconColors.PINK)
    MISC_9 = ("≈", IconColors.CYAN)
    MISC_10 = ("≠", IconColors.RED)


def demo_icons():
    """Display icon collection preview."""
    print_header("Icon Collection - 200 Icons")

    print("\nArrows (50 icons):")
    print("  Right:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"ARROW_RIGHT_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Left:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"ARROW_LEFT_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Up:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"ARROW_UP_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Down:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"ARROW_DOWN_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Diagonal:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"ARROW_DIAG_{i}")
        print(color_icon(icon, color), end=" ")
    print()

    print("\nCheckmarks & Crosses (20 icons):")
    print("  Checks:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"CHECK_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Crosses:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"CROSS_{i}")
        print(color_icon(icon, color), end=" ")
    print()

    print("\nBullets (20 icons):")
    print("  Large:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"BULLET_BIG_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Small:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"BULLET_SMALL_{i}")
        print(color_icon(icon, color), end=" ")
    print()

    print("\nSpinners (10 icons):")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"SPINNER_{i}")
        print(f"  {color_icon(icon, color)}", end=" ")
    print()

    print("\nShapes (20 icons):")
    print("  Boxes:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"BOX_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Circles:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"CIRCLE_{i}")
        print(color_icon(icon, color), end=" ")
    print()

    print("\nProgress Bars (10 icons):")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"PROGRESS_{i}")
        print(f"  {color_icon(icon, color)}", end=" ")
    print()

    print("\nBrackets & Lines (20 icons):")
    print("  Brackets:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"BRACKET_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Lines:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"LINE_{i}")
        print(color_icon(icon, color), end=" ")
    print()

    print("\nSpecial Characters (30 icons):")
    print("  Special:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"SPECIAL_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Technical:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"TECH_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Math:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"MATH_{i}")
        print(color_icon(icon, color), end=" ")
    print()

    print("\nStatus & Misc (20 icons):")
    print("  Status:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"STATUS_{i}")
        print(color_icon(icon, color), end=" ")
    print("\n  Misc:", end=" ")
    for i in range(1, 11):
        icon, color = getattr(Icons, f"MISC_{i}")
        print(color_icon(icon, color), end=" ")
    print()
    print()

    total_icons = len([attr for attr in dir(Icons) if not attr.startswith("_")])
    print(f"  Total: {total_icons} icons")
    print()


def demo_terminal_showcase():
    """Mock LLM chat with tool usage - modern gradient design."""
    print_header("Terminal UI Showcase - LLM Chat")

    width = 70

    # ===== BANNER =====
    print("\nBanner with logo:")
    banner_logo = smooth_gradient(
        " ▄█─●─●─█▄  █ ▄▀ █▀▀█ █   █   █▀▀█ █▀▀▄ █▀▀█ █▀▀█ ", LIME_BRIGHT, BLACK, width
    )
    banner_line2 = smooth_gradient(
        " ●──███──●  █▀▄  █  █ █   █   █▄▄█ █▀▀▄ █  █ █▄▄▀ ", LIME_BRIGHT, BLACK, width
    )
    banner_line3 = smooth_gradient(
        " ▀█─●─●─█▀  █  █ █▄▄█ █▄▄ █▄▄ █  █ █▄▄▀ █▄▄█ █ █▄ ", LIME_BRIGHT, BLACK, width
    )

    banner_top = smooth_gradient_subtle(
        "▄" * width, LIME_BRIGHT, width, no_background=True
    )
    banner_bottom = smooth_gradient_subtle(
        "▀" * width, LIME_BRIGHT, width, no_background=True
    )

    print(banner_top)
    print(banner_logo)
    print(banner_line2)
    print(banner_line3)
    print(banner_bottom)
    print()

    # ===== STATUS LINE =====
    print("Status bar:")
    status_left = smooth_gradient("  v0.4.15  ", LIME_BRIGHT, WHITE, 20)
    status_mid = smooth_gradient("  Ready to chat  ", GRAY_LIGHT, WHITE, 30)
    status_right = smooth_gradient("  3 plugins  ", GRAY_BLACK, BLACK, 20)
    print(status_left + status_mid + status_right)
    print()

    # ===== USER INPUT =====
    print("User input with gradient:")
    prompt_label = smooth_gradient("  >  ", GRAY_BLACK, WHITE)
    prompt_input = smooth_gradient("hi".ljust(width - 4), GRAY_LIGHT, WHITE, width - 4)
    print(prompt_label + prompt_input)
    print()

    # ===== THINKING STATE =====
    print("Thinking state:")
    thinking_bg = smooth_gradient_subtle(
        "▄" * width, GRAY_BLACK, width, no_background=True
    )
    print(thinking_bg)
    thinking_text = smooth_gradient(
        "  ● Thinking for 7.6 seconds...  ", GRAY_BLACK, WHITE, width
    )
    print(thinking_text)
    print()

    # ===== LLM RESPONSE =====
    print("LLM response:")
    response_text = smooth_gradient(
        "  ∴ Hi! How can I assist you today? Let me know what you need help with!  ",
        GRAY_LIGHT,
        WHITE,
        width,
    )
    print(response_text)
    print()

    # ===== SECOND USER INPUT =====
    print("Second user input:")
    prompt_label2 = smooth_gradient("  >  ", GRAY_BLACK, WHITE)
    prompt_input2 = smooth_gradient(
        "can you read a file".ljust(width - 4), GRAY_LIGHT, WHITE, width - 4
    )
    print(prompt_label2 + prompt_input2)
    print()

    # ===== SECOND THINKING =====
    print("Second thinking:")
    thinking_bg2 = smooth_gradient_subtle(
        "▄" * width, GRAY_BLACK, width, no_background=True
    )
    print(thinking_bg2)
    thinking_text2 = smooth_gradient(
        "  ● Thinking for 2.5 seconds...  ", GRAY_BLACK, WHITE, width
    )
    print(thinking_text2)
    print()

    # ===== LLM RESPONSE WITH TOOL =====
    print("LLM response with tool call:")

    # Response text
    response_part1 = smooth_gradient(
        "  ∴ Yes, I can read files using the file_read tool.  ",
        GRAY_LIGHT,
        WHITE,
        width,
    )
    print(response_part1)

    # Tool call block with gradient
    tool_top = smooth_gradient_subtle(
        "▀" * width, LIME_BRIGHT, width, no_background=True
    )
    tool_header = smooth_gradient(
        "  ⚙ file_read(path/to/file.py)  ", LIME_BRIGHT, BLACK, width
    )
    tool_content = smooth_gradient("  Reading file...  ", LIME_RANGE, BLACK, width)
    tool_bottom = smooth_gradient_subtle(
        "▄" * width, LIME_BRIGHT, width, no_background=True
    )

    print(tool_top)
    print(tool_header)
    print(tool_content)
    print(tool_bottom)
    print()

    # ===== TOOL ERROR =====
    print("Tool error state:")
    error_top = smooth_gradient_subtle(
        "▀" * width, GRAY_BLACK, width, no_background=True
    )
    error_header = smooth_gradient(
        "  ✕ Error: File not found  ", GRAY_BLACK, WHITE, width
    )
    error_msg = smooth_gradient(
        "  path/to/file.py does not exist  ", GRAY_LIGHT, WHITE, width
    )
    error_bottom = smooth_gradient_subtle(
        "▄" * width, GRAY_BLACK, width, no_background=True
    )

    print(error_top)
    print(error_header)
    print(error_msg)
    print(error_bottom)
    print()

    # ===== FINAL LLM RESPONSE =====
    print("Final LLM response:")
    final_response = smooth_gradient(
        "  ∴ The file doesn't exist. Let me know the correct path!  ",
        GRAY_LIGHT,
        WHITE,
        width,
    )
    print(final_response)
    print()


if __name__ == "__main__":
    main()
