---
title: "Theming System"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Theming System

Kollab features a comprehensive theming and color system that adapts to your terminal's capabilities while providing a consistent design system for plugin developers.

## Color Modes

The CLI automatically detects and adapts to four color support levels:

| Mode | Colors | Description |
|------|--------|-------------|
| TRUE_COLOR | 16.7 million | 24-bit RGB for full color spectrum |
| EXTENDED | 256 | 8-bit palette for terminals without truecolor |
| BASIC | 16 | Standard ANSI colors (maximum compatibility) |
| NONE | 0 | Monochrome output for pipes and non-interactive use |

### Auto-Detection

Color detection runs on startup in this priority order:

1. **KOLLAB_COLOR_MODE** - Manual override (see below)
2. **COLORTERM** env var - `truecolor` or `24bit` indicates truecolor support
3. **TERM_PROGRAM** - `iTerm.app` and `vscode` indicate truecolor support
4. **TERM** variable - Checks for `256color` in term name
5. Fallback to 16-color ANSI

### Manual Override

Force a specific color mode via environment variable:

```bash
# Force truecolor (24-bit RGB)
KOLLAB_COLOR_MODE=truecolor kollab

# Force 256-color palette
KOLLAB_COLOR_MODE=256 kollab

# Force basic 16-color ANSI
KOLLAB_COLOR_MODE=16 kollab

# Disable colors entirely
KOLLAB_COLOR_MODE=none kollab

# Persist in shell profile
export KOLLAB_COLOR_MODE=256
```

Accepted values: `truecolor`, `24bit`, `true`, `256`, `256color`, `16`, `basic`, `none`

## Terminal Compatibility

| Terminal | Color Support | Notes |
|----------|---------------|-------|
| iTerm2 | TRUE_COLOR | Full 24-bit RGB |
| WezTerm | TRUE_COLOR | Full 24-bit RGB |
| kitty | TRUE_COLOR | Full 24-bit RGB |
| Alacritty | TRUE_COLOR | Full 24-bit RGB |
| VS Code | TRUE_COLOR | Full 24-bit RGB |
| tmux | TRUE_COLOR* | Requires `set -g default-terminal "screen-256color"` or `tmux-256color` |
| Apple Terminal | EXTENDED | 256 colors only |
| GNOME Terminal | EXTENDED | 256 colors only |
| Windows Terminal | TRUE_COLOR | Full 24-bit RGB (Windows 10+) |

*tmux truecolor support requires proper TERM configuration. Add to `.tmux.conf`:
```
set -g default-terminal "tmux-256color"
set -ga terminal-overrides ",xterm-256color:Tc"
```

## Built-in Themes

Five preset themes are included:

| Theme | Style | Primary Colors |
|-------|-------|----------------|
| **lime** | Default (green) | Vibrant green accents |
| **ocean** | Cool (blue) | Cyan and blue gradients |
| **sunset** | Warm (orange) | Orange and pink tones |
| **mono** | Grayscale | Subtle gray gradients |
| **dark** | Minimal | Low-contrast dark mode |

## Design System for Plugin Developers

Plugin developers should use the design system API rather than hardcoding colors. This ensures consistency and allows future theme expansion.

### Core Imports

```python
from kollabor_tui.design_system import T, S, C, Box, TagBox, solid, solid_fg, gradient
```

### Theme Access (T())

The `T()` function returns the active theme object with semantic color roles:

```python
# Gradients (lists of RGB tuples for horizontal gradients)
T().primary          # Primary accent gradient
T().primary_dark     # Darker primary variant
T().secondary        # Secondary accent gradient
T().response_bg      # AI response background
T().input_bg         # Input box background
T().dark             # Dark background gradient

# Semantic colors
T().success          # Success state (green tones)
T().error            # Error state (red tones)
T().warning          # Warning state (yellow tones)

# Solid colors (RGB tuples)
T().user_tag         # User message tag color
T().ai_tag           # AI message tag color
T().tool_tag         # Tool execution tag color
T().thinking_tag     # Thinking indicator color
T().code_bg          # Code block background

# Text colors
T().text             # Primary text (white)
T().text_dim         # Dimmed text (gray)
T().text_dark        # Dark text for bright backgrounds (black)
```

### Style Constants (S)

ANSI escape codes for text formatting:

```python
S.BOLD               # Enable bold
S.RESET_BOLD         # Disable bold
S.DIM                # Enable dim
S.RESET_DIM          # Disable dim
S.ITALIC             # Enable italic
S.RESET_ITALIC       # Disable italic
S.RESET              # Reset all formatting

# Usage
styled = f"{S.BOLD}Important{S.RESET} message"
```

### Box Rendering

Use solid block style (not box-drawing characters) for visual containers:

```python
# Create a gradient box
box = Box.render(
    lines=["Hello World", "Multi-line content"],
    colors=T().primary,           # Gradient colors
    fg=T().text,                  # Text color
    width=50,                     # Box width
)

# Create a solid color box
solid_box = Box.render_solid(
    lines=["Simple message"],
    bg=T().dark[0],               # Single RGB tuple
    fg=T().text,
    width=50,
)
```

### TagBox Pattern

For "tag + content" layouts (like user/AI message headers):

```python
output = TagBox.render(
    lines=["Response content here"],
    tag_bg=T().ai_tag,            # Tag background color
    tag_fg=T().text_dark,         # Tag text color
    tag_width=3,                  # Tag column width
    content_colors=T().response_bg,  # Content gradient
    content_fg=T().text,          # Content text color
    content_width=47,             # Content column width
    tag_chars=[" > "],            # Tag icon/text
)
```

### Solid Colors

```python
# Solid foreground (text) color
colored_text = solid_fg("Hello", T().primary[0])

# Solid background with foreground
solid_line = solid("Text", bg=(30, 30, 30), fg=(255, 255, 255), width=40)
```

### UI Characters (C)

Predefined Unicode characters for consistent UI elements:

```python
C["check_on"]         # ✔
C["check_off"]        # ☐
C["arrow_right"]      # ▶
C["bullet"]           # ●
C["success"]          # ✔
C["error"]            # ✖
C["warning"]          # ⚠
C["bar_full"]         # █ (progress)
C["bar_empty"]        # ░ (progress)
C["spin"]             # ["◐", "◓", "◑", "◒"] (spinner frames)
```

### Progress Bars

```python
from kollabor_tui.design_system import progress_bar

bar = progress_bar(0.75, width=20)  # 75% progress, 20 chars wide
# Returns: "████████████████▌░░░░"
```

## Terminal Size Access

Always use the global terminal state for layout calculations:

```python
from kollabor_tui.terminal_state import get_terminal_size, get_terminal_width

width, height = get_terminal_size()
content_width = min(80, width - 4)  # Leave margin
```

## Status Bar Areas

Plugins can contribute to three status areas:

| Area | Position | Usage |
|------|----------|-------|
| A | Top-left | Session info, profile name |
| B | Top-center | LLM provider, model |
| C | Top-right | Tool status, thinking indicator |

Status items are simple strings with optional color codes.

## Color Fallback

The design system automatically converts RGB colors to the terminal's supported mode:

- RGB tuples are converted to 256-color palette or 16-color ANSI as needed
- Truecolor gradients become dithered in lower color modes
- Themes maintain visual hierarchy even in 16-color mode

## Custom Themes (JSON)

Custom themes can be added as JSON files in `~/.kollab/themes/`:

```json
{
  "name": "mytheme",
  "primary": [[100, 200, 100], [120, 220, 120], [140, 240, 140]],
  "primary_dark": [[60, 120, 60], [50, 100, 50]],
  "secondary": [[100, 100, 200], [120, 120, 220]],
  "response_bg": [[50, 50, 50], [40, 40, 40]],
  "input_bg": [[55, 55, 60], [45, 45, 50]],
  "dark": [[30, 30, 30], [20, 20, 20]],
  "success": [[60, 180, 80], [80, 200, 100]],
  "error": [[180, 60, 60], [200, 80, 80]],
  "warning": [[200, 140, 40], [220, 160, 60]],
  "user_tag": [50, 200, 220],
  "ai_tag": [80, 180, 50],
  "tool_tag": [50, 140, 180],
  "thinking_tag": [200, 140, 40],
  "code_bg": [25, 25, 25],
  "text": [255, 255, 255],
  "text_dim": [120, 120, 120]
}
```

Switch to custom theme via `/theme` command (if implemented) or programmatically:

```python
from kollabor_tui.design_system import set_theme
set_theme('mytheme')
```

## Best Practices

1. **Always use T() for colors** - Never hardcode RGB values
2. **Pair S.BOLD with S.RESET** - Prevents "bleeding" formatting
3. **Use Box/TagBox for containers** - Consistent visual style
4. **Check terminal width** - Gracefully handle narrow terminals
5. **Test in multiple color modes** - Verify 16-color fallback works
6. **Respect user's theme choice** - Don't override without good reason
