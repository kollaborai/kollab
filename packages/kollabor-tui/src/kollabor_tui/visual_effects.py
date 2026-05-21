"""Visual effects system for terminal rendering.

This module provides comprehensive visual effects for terminal rendering,
including gradient effects, shimmer animations, color palettes,
and startup header generation.
"""

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

if TYPE_CHECKING:
    from .terminal_state import TerminalState


# Injected terminal state for color detection (dependency injection)
_terminal_state: "TerminalState | None" = None


class ColorSupport(Enum):
    """Terminal color support levels."""

    NONE = 0  # No color support
    BASIC = 1  # 16 colors (4-bit)
    EXTENDED = 2  # 256 colors (8-bit)
    TRUE_COLOR = 3  # 16 million colors (24-bit RGB)


def set_terminal_state(state: "TerminalState | None") -> None:
    """Set the terminal state instance for color detection.

    This enables dependency injection - the terminal state becomes
    the single source of truth for terminal capabilities.

    Args:
        state: TerminalState instance, or None to clear it.
    """
    global _terminal_state
    _terminal_state = state


def _map_color_level_to_enum(level: str) -> ColorSupport:
    """Map terminal color level string to ColorSupport enum.

    Args:
        level: Color level string ("monochrome", "basic", "256color", "truecolor").

    Returns:
        ColorSupport enum value.
    """
    mapping = {
        "truecolor": ColorSupport.TRUE_COLOR,
        "256color": ColorSupport.EXTENDED,
        "basic": ColorSupport.BASIC,
        "monochrome": ColorSupport.NONE,
    }
    return mapping.get(level, ColorSupport.BASIC)


def detect_color_support() -> ColorSupport:
    """Detect terminal color support level.

    This is a fallback implementation used when no TerminalState
    is injected. Checks environment variables and terminal type to
    determine the maximum color depth supported.

    Returns:
        ColorSupport level for current terminal.
    """
    # Check for explicit no-color request
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return ColorSupport.NONE

    # Check for explicit true color support
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return ColorSupport.TRUE_COLOR

    # Check terminal type for known true color support
    term = os.environ.get("TERM", "").lower()
    term_program = os.environ.get("TERM_PROGRAM", "").lower()

    # Terminals known to support true color
    true_color_terms = (
        "iterm.app",
        "iterm2",
        "vscode",
        "hyper",
        "alacritty",
        "kitty",
        "wezterm",
        "rio",
    )
    if term_program in true_color_terms:
        return ColorSupport.TRUE_COLOR

    # Check TERM for true color indicators
    if "truecolor" in term or "24bit" in term or "direct" in term:
        return ColorSupport.TRUE_COLOR

    # Modern terminal emulators with 256+ color support in TERM
    if "256color" in term or "256" in term:
        return ColorSupport.EXTENDED

    # xterm and similar usually support 256 colors
    if term.startswith(("xterm", "screen", "tmux", "rxvt")):
        return ColorSupport.EXTENDED

    # Apple Terminal.app - only 256 color, NOT true color
    if term_program == "apple_terminal":
        return ColorSupport.EXTENDED

    # Basic color support for other terminals
    if term:
        return ColorSupport.BASIC

    return ColorSupport.NONE


# Global color support level - detected once at import
_COLOR_SUPPORT: ColorSupport | None = None


def get_color_support() -> ColorSupport:
    """Get color support level.

    Priority order:
      1. Manual override via KOLLAB_COLOR_MODE env var
      2. Injected TerminalState.get_color_support_level() (if available)
      3. Fallback detection via environment variables

    Env var override values:
      - "truecolor" or "24bit" -> TRUE_COLOR
      - "256" or "256color"    -> EXTENDED
      - "16" or "basic"        -> BASIC
      - "none" or "off"        -> NONE

    Returns:
        ColorSupport level for current terminal.
    """
    global _COLOR_SUPPORT

    if _COLOR_SUPPORT is None:
        # Check for manual override
        override = os.environ.get("KOLLAB_COLOR_MODE", "").lower()
        if override in ("truecolor", "24bit", "true"):
            _COLOR_SUPPORT = ColorSupport.TRUE_COLOR
        elif override in ("256", "256color", "extended"):
            _COLOR_SUPPORT = ColorSupport.EXTENDED
        elif override in ("16", "basic"):
            _COLOR_SUPPORT = ColorSupport.BASIC
        elif override in ("none", "off", "no"):
            _COLOR_SUPPORT = ColorSupport.NONE
        else:
            # Use injected TerminalState if available
            global _terminal_state
            if _terminal_state is not None:
                color_level = _terminal_state.get_color_support_level()
                _COLOR_SUPPORT = _map_color_level_to_enum(color_level)
            else:
                # Fallback to environment detection
                _COLOR_SUPPORT = detect_color_support()
    return _COLOR_SUPPORT


def set_color_support(level: ColorSupport) -> None:
    """Manually set color support level.

    Args:
        level: ColorSupport level to use.
    """
    global _COLOR_SUPPORT
    _COLOR_SUPPORT = level


def reset_color_support() -> None:
    """Reset color support to re-detect on next call."""
    global _COLOR_SUPPORT
    _COLOR_SUPPORT = None


def rgb_to_256(r: int, g: int, b: int) -> int:
    """Convert RGB color to nearest 256-color palette index.

    Uses the 6x6x6 color cube (indices 16-231) for colored values,
    or grayscale ramp (indices 232-255) for near-gray colors.

    Args:
        r: Red component (0-255)
        g: Green component (0-255)
        b: Blue component (0-255)

    Returns:
        256-color palette index (0-255)
    """
    # Check if color is near grayscale
    if abs(r - g) < 10 and abs(g - b) < 10 and abs(r - b) < 10:
        # Use grayscale ramp (232-255, 24 shades)
        gray = (r + g + b) // 3
        if gray < 8:
            return 16  # black
        if gray > 248:
            return 231  # white
        return 232 + ((gray - 8) * 24) // 240

    # Use 6x6x6 color cube (indices 16-231)
    # Each component maps to 0-5
    r_idx = (r * 6) // 256
    g_idx = (g * 6) // 256
    b_idx = (b * 6) // 256
    return 16 + (36 * r_idx) + (6 * g_idx) + b_idx


def color_code(r: int, g: int, b: int, bold: bool = False, dim: bool = False) -> str:
    """Generate a foreground color escape code with automatic fallback.

    Uses true color (24-bit) when supported, otherwise falls back
    to 256-color palette.

    Args:
        r: Red component (0-255)
        g: Green component (0-255)
        b: Blue component (0-255)
        bold: Add bold attribute
        dim: Add dim attribute

    Returns:
        ANSI escape sequence for the color.
    """
    prefix = ""
    if bold:
        prefix = "\033[1m"
    elif dim:
        prefix = "\033[2m"

    if get_color_support() == ColorSupport.TRUE_COLOR:
        return f"{prefix}\033[38;2;{r};{g};{b}m"
    else:
        idx = rgb_to_256(r, g, b)
        return f"{prefix}\033[38;5;{idx}m"


class EffectType(Enum):
    """Types of visual effects."""

    GRADIENT = "gradient"
    SHIMMER = "shimmer"
    DIM = "dim"
    ANIMATION = "animation"
    COLOR = "color"


@dataclass
class EffectConfig:
    """Configuration for visual effects."""

    effect_type: EffectType
    enabled: bool = True
    intensity: float = 1.0
    speed: int = 3
    width: int = 4
    colors: List[str] = field(default_factory=list)


# Color definitions as (r, g, b, modifier) tuples
# modifier: None=normal, 'bold'=bright, 'dim'=dim
_COLOR_DEFINITIONS = {
    # Basic colors
    "WHITE": (220, 220, 220, None),
    "BRIGHT_WHITE": (255, 255, 255, "bold"),
    "BLACK": (0, 0, 0, None),
    # Red variants
    "DIM_RED": (205, 49, 49, "dim"),
    "RED": (205, 49, 49, None),
    "BRIGHT_RED": (241, 76, 76, "bold"),
    # Green variants
    "DIM_GREEN": (13, 188, 121, "dim"),
    "GREEN": (13, 188, 121, None),
    "BRIGHT_GREEN": (35, 209, 139, "bold"),
    # Yellow variants
    "DIM_YELLOW": (229, 192, 123, "dim"),
    "YELLOW": (229, 192, 123, None),
    "BRIGHT_YELLOW": (245, 223, 77, "bold"),
    # Blue variants
    "DIM_BLUE": (36, 114, 200, "dim"),
    "BLUE": (36, 114, 200, None),
    "BRIGHT_BLUE": (59, 142, 234, "bold"),
    "NORMAL_BLUE": (100, 149, 237, None),
    # Magenta variants
    "DIM_MAGENTA": (188, 63, 188, "dim"),
    "MAGENTA": (188, 63, 188, None),
    "BRIGHT_MAGENTA": (214, 112, 214, "bold"),
    # Cyan variants
    "DIM_CYAN": (17, 168, 205, "dim"),
    "CYAN": (17, 168, 205, None),
    "BRIGHT_CYAN": (41, 184, 219, "bold"),
    # Grey variants
    "DIM_GREY": (128, 128, 128, "dim"),
    "GREY": (128, 128, 128, None),
    "BRIGHT_GREY": (169, 169, 169, "bold"),
    # Extended bright colors
    "BRIGHT_CYAN_256": (0, 255, 255, "bold"),
    "BRIGHT_BLUE_256": (94, 156, 255, "bold"),
    "BRIGHT_GREEN_256": (90, 247, 142, "bold"),
    "BRIGHT_YELLOW_256": (255, 231, 76, "bold"),
    "BRIGHT_MAGENTA_256": (255, 92, 205, "bold"),
    "BRIGHT_RED_256": (255, 85, 85, "bold"),
    # Neon Minimal Palette - Lime
    "LIME": (163, 230, 53, None),
    "BRIGHT_LIME": (163, 230, 53, "bold"),
    "LIME_LIGHT": (190, 242, 100, None),
    "LIME_DARK": (132, 204, 22, None),
    # Info: Cyan
    "INFO_CYAN": (6, 182, 212, None),
    "INFO_CYAN_LIGHT": (34, 211, 238, None),
    "INFO_CYAN_DARK": (8, 145, 178, None),
    # Warning: Gold
    "WARNING_GOLD": (234, 179, 8, None),
    "WARNING_GOLD_LIGHT": (253, 224, 71, None),
    "WARNING_GOLD_DARK": (202, 138, 4, None),
    # Error: Red
    "ERROR_RED": (239, 68, 68, None),
    "ERROR_RED_LIGHT": (248, 113, 113, None),
    "ERROR_RED_DARK": (220, 38, 38, None),
    # Muted: Steel
    "MUTED_STEEL": (113, 113, 122, None),
    "DIM_STEEL": (113, 113, 122, "dim"),
}


def _make_color_code(r: int, g: int, b: int, modifier: str | None = None) -> str:
    """Generate escape code for a color with automatic fallback.

    Args:
        r, g, b: RGB components (0-255)
        modifier: 'bold', 'dim', or None

    Returns:
        ANSI escape sequence appropriate for terminal capability.
    """
    prefix = ""
    if modifier == "bold":
        prefix = "\033[1m"
    elif modifier == "dim":
        prefix = "\033[2m"

    support = get_color_support()

    if support == ColorSupport.NONE:
        return prefix if prefix else ""

    if support == ColorSupport.TRUE_COLOR:
        return f"{prefix}\033[38;2;{r};{g};{b}m"
    else:
        # Use 256-color fallback
        idx = rgb_to_256(r, g, b)
        return f"{prefix}\033[38;5;{idx}m"


class _ColorPaletteMeta(type):
    """Metaclass that generates color codes dynamically based on terminal support."""

    def __getattr__(cls, name: str) -> str:
        if name in _COLOR_DEFINITIONS:
            r, g, b, modifier = _COLOR_DEFINITIONS[name]
            return _make_color_code(r, g, b, modifier)
        raise AttributeError(f"ColorPalette has no color '{name}'")


class ColorPalette(metaclass=_ColorPaletteMeta):
    """Color palette with automatic terminal capability detection.

    Colors are generated dynamically based on terminal support:
    - TRUE_COLOR: Uses 24-bit RGB escape codes
    - EXTENDED: Falls back to 256-color palette
    - BASIC: Uses 16-color approximations
    - NONE: Returns empty strings or just modifiers
    """

    if TYPE_CHECKING:
        # Type stubs for dynamically-generated color attributes (metaclass __getattr__)
        WHITE: str
        BRIGHT_WHITE: str
        BLACK: str
        DIM_RED: str
        RED: str
        BRIGHT_RED: str
        DIM_GREEN: str
        GREEN: str
        BRIGHT_GREEN: str
        DIM_YELLOW: str
        YELLOW: str
        BRIGHT_YELLOW: str
        DIM_BLUE: str
        BLUE: str
        BRIGHT_BLUE: str
        NORMAL_BLUE: str
        DIM_MAGENTA: str
        MAGENTA: str
        BRIGHT_MAGENTA: str
        DIM_CYAN: str
        CYAN: str
        BRIGHT_CYAN: str
        DIM_GREY: str
        GREY: str
        BRIGHT_GREY: str
        BRIGHT_CYAN_256: str
        BRIGHT_BLUE_256: str
        BRIGHT_GREEN_256: str
        BRIGHT_YELLOW_256: str
        BRIGHT_MAGENTA_256: str
        BRIGHT_RED_256: str
        LIME: str
        BRIGHT_LIME: str
        LIME_LIGHT: str
        LIME_DARK: str
        INFO_CYAN: str
        INFO_CYAN_LIGHT: str
        INFO_CYAN_DARK: str
        WARNING_GOLD: str
        WARNING_GOLD_LIGHT: str
        WARNING_GOLD_DARK: str
        ERROR_RED: str
        ERROR_RED_LIGHT: str
        ERROR_RED_DARK: str
        MUTED_STEEL: str
        DIM_STEEL: str

        # Lowercase aliases used by widget code
        accent_color: str
        primary_color: str
        muted_color: str
        success_color: str
        error_color: str
        warning_color: str
        info_color: str
        border_color: str
        text_color: str
        dim_text_color: str
        highlight: str
        background: str
        reset: str

    # Standard modifiers (not affected by color support)
    RESET = "\033[0m"
    DIM = "\033[2m"
    BRIGHT = "\033[1m"

    # Grey gradient levels (256-color palette indices)
    GREY_LEVELS = [255, 254, 253, 252, 251, 250]

    # Dim white gradient levels (bright white to subtle dim white)
    DIM_WHITE_LEVELS = [255, 254, 253, 252, 251, 250]

    # Lime green gradient scheme RGB values for ultra-smooth gradients
    DIM_SCHEME_COLORS = [
        (190, 242, 100),  # Bright lime (#bef264)
        (175, 235, 80),  # Light lime
        (163, 230, 53),  # Primary lime (#a3e635) - hero color!
        (145, 210, 45),  # Medium lime
        (132, 204, 22),  # Darker lime (#84cc16)
        (115, 180, 18),  # Deep lime
        (100, 160, 15),  # Strong lime
        (115, 180, 18),  # Deep lime (return)
        (132, 204, 22),  # Darker lime (return)
        (163, 230, 53),  # Primary lime (return)
        (190, 242, 100),  # Bright lime
    ]


# Powerline separator characters
class Powerline:
    """Powerline/Agnoster style separator characters."""

    # Solid arrows
    RIGHT = "\ue0b0"  #
    LEFT = "\ue0b2"  #

    # Thin arrows (for sub-segments)
    RIGHT_THIN = "\ue0b1"  #
    LEFT_THIN = "\ue0b3"  #

    # Rounded
    RIGHT_ROUND = "\ue0b4"  #
    LEFT_ROUND = "\ue0b6"  #

    # Flame/fire style
    RIGHT_FLAME = "\ue0c0"  #
    LEFT_FLAME = "\ue0c2"  #

    # Pixelated
    RIGHT_PIXEL = "\ue0c4"  #
    LEFT_PIXEL = "\ue0c6"  #

    # Ice/diagonal
    RIGHT_ICE = "\ue0c8"  #
    LEFT_ICE = "\ue0ca"  #


def make_bg_color(r: int, g: int, b: int) -> str:
    """Create background color escape code.

    Args:
        r, g, b: RGB values (0-255).

    Returns:
        ANSI escape code for background color.
    """
    support = get_color_support()

    if support == ColorSupport.NONE:
        return ""

    if support == ColorSupport.TRUE_COLOR:
        return f"\033[48;2;{r};{g};{b}m"
    else:
        # Use 256-color fallback
        idx = rgb_to_256(r, g, b)
        return f"\033[48;5;{idx}m"


def make_fg_color(r: int, g: int, b: int) -> str:
    """Create foreground color escape code.

    Args:
        r, g, b: RGB values (0-255).

    Returns:
        ANSI escape code for foreground color.
    """
    support = get_color_support()

    if support == ColorSupport.NONE:
        return ""

    if support == ColorSupport.TRUE_COLOR:
        return f"\033[38;2;{r};{g};{b}m"
    else:
        idx = rgb_to_256(r, g, b)
        return f"\033[38;5;{idx}m"


class AgnosterColors:
    """Signature color scheme for agnoster segments - lime and cyan."""

    # Lime palette (RGB tuples)
    LIME = (163, 230, 53)
    LIME_DARK = (132, 204, 22)
    LIME_DARKER = (100, 160, 15)

    # Cyan palette
    CYAN = (6, 182, 212)
    CYAN_DARK = (8, 145, 178)
    CYAN_LIGHT = (34, 211, 238)

    # Neutral backgrounds
    BG_DARK = (30, 30, 30)
    BG_MID = (50, 50, 50)
    BG_LIGHT = (70, 70, 70)

    # Text colors
    TEXT_DARK = (20, 20, 20)
    TEXT_LIGHT = (240, 240, 240)


class ShimmerEffect:
    """Handles shimmer animation effects."""

    def __init__(self, speed: int = 3, wave_width: int = 4):
        """Initialize shimmer effect.

        Args:
            speed: Animation speed (frames between updates).
            wave_width: Width of shimmer wave in characters.
        """
        self.speed = speed
        self.wave_width = wave_width
        self.frame_counter = 0
        self.position = 0

    def configure(self, speed: int, wave_width: int) -> None:
        """Configure shimmer parameters.

        Args:
            speed: Animation speed.
            wave_width: Wave width.
        """
        self.speed = speed
        self.wave_width = wave_width

    def apply_shimmer(self, text: str) -> str:
        """Apply elegant wave shimmer effect to text.

        Args:
            text: Text to apply shimmer to.

        Returns:
            Text with shimmer effect applied.
        """
        if not text:
            return text

        # Update shimmer position
        self.frame_counter = (self.frame_counter + 1) % self.speed
        if self.frame_counter == 0:
            self.position = (self.position + 1) % (len(text) + self.wave_width * 2)

        result = []
        for i, char in enumerate(text):
            distance = abs(i - self.position)

            if distance == 0:
                # Center - bright cyan
                result.append(f"{ColorPalette.BRIGHT_CYAN}{char}{ColorPalette.RESET}")
            elif distance == 1:
                # Adjacent - bright blue
                result.append(f"{ColorPalette.BRIGHT_BLUE}{char}{ColorPalette.RESET}")
            elif distance == 2:
                # Second ring - normal blue
                result.append(f"{ColorPalette.NORMAL_BLUE}{char}{ColorPalette.RESET}")
            elif distance <= self.wave_width:
                # Edge - dim blue
                result.append(f"{ColorPalette.DIM_BLUE}{char}{ColorPalette.RESET}")
            else:
                # Base - darker dim blue
                result.append(f"\033[2;94m{char}{ColorPalette.RESET}")

        return "".join(result)

    def apply_shimmer_ansi(self, text: str) -> str:
        """Apply shimmer effect to text that already contains ANSI escape codes.

        Parses the text to identify ANSI sequences vs visible characters,
        and only applies shimmer brightness to visible characters while
        preserving the original escape codes.

        Args:
            text: Text with ANSI escape codes to apply shimmer to.

        Returns:
            Text with shimmer effect applied (preserving original codes).
        """
        import re

        if not text:
            return text

        # Update shimmer position based on visible character count
        # First, count visible characters
        ansi_pattern = re.compile(r"\033\[[0-9;]*m")
        visible_text = ansi_pattern.sub("", text)
        visible_len = len(visible_text)

        if visible_len == 0:
            return text

        # Update position
        self.frame_counter = (self.frame_counter + 1) % self.speed
        if self.frame_counter == 0:
            self.position = (self.position + 1) % (visible_len + self.wave_width * 2)

        # Parse text into segments: (is_ansi, content)
        segments = []
        last_end = 0
        for match in ansi_pattern.finditer(text):
            # Add text before this match
            if match.start() > last_end:
                segments.append((False, text[last_end : match.start()]))
            # Add the ANSI sequence
            segments.append((True, match.group()))
            last_end = match.end()
        # Add remaining text
        if last_end < len(text):
            segments.append((False, text[last_end:]))

        # Build result, applying shimmer only to visible characters
        result = []
        visible_idx = 0

        for is_ansi, content in segments:
            if is_ansi:
                # Pass through ANSI codes unchanged
                result.append(content)
            else:
                # Apply shimmer to each visible character
                for char in content:
                    distance = abs(visible_idx - self.position)

                    if distance == 0:
                        # Center - brightest (add bold)
                        result.append(f"\033[1m{char}\033[22m")
                    elif distance == 1:
                        # Adjacent - bright
                        result.append(f"\033[1m{char}\033[22m")
                    elif distance == 2:
                        # Second ring - slightly bright
                        result.append(char)
                    elif distance <= self.wave_width:
                        # Edge - normal
                        result.append(char)
                    else:
                        # Base - slightly dim
                        result.append(f"\033[2m{char}\033[22m")

                    visible_idx += 1

        return "".join(result)


class UltraShimmerEffect:
    """Advanced shimmer effect with dynamic wave, pause, and return animation.

    Features:
    - Forward movement at normal speed
    - Wave width grows then shrinks as it travels
    - Pauses briefly at the end
    - Returns faster (reverse direction)
    - Subtle background pulse on all text
    """

    def __init__(self):
        """Initialize ultra shimmer effect."""
        self.position = 0
        self.frame_counter = 0
        self.direction = 1  # 1 = forward, -1 = backward
        self.pause_frames = 0  # Pause counter at ends
        self.pulse_phase = 0  # For subtle background pulse
        self.text_length = 0

    def apply_shimmer_ansi(self, text: str) -> str:
        """Apply ultra shimmer effect to text with ANSI codes.

        Args:
            text: Text with ANSI escape codes.

        Returns:
            Text with ultra shimmer effect applied.
        """
        import math
        import re

        if not text:
            return text

        # Parse ANSI and count visible characters
        ansi_pattern = re.compile(r"\033\[[0-9;]*m")
        visible_text = ansi_pattern.sub("", text)
        visible_len = len(visible_text)

        if visible_len == 0:
            return text

        self.text_length = visible_len

        # Update pulse phase (always running for subtle background effect)
        self.pulse_phase = (self.pulse_phase + 1) % 20

        # Handle pause at ends
        if self.pause_frames > 0:
            self.pause_frames -= 1
        else:
            # Update position
            self.frame_counter += 1

            # Speed: forward = every 2 frames, backward = every frame
            speed = 2 if self.direction == 1 else 1

            if self.frame_counter >= speed:
                self.frame_counter = 0
                self.position += self.direction

                # Check bounds and reverse
                if self.position >= visible_len + 2:
                    self.direction = -1
                    self.pause_frames = 5  # Pause at end
                    self.position = visible_len + 1
                elif self.position < -2:
                    self.direction = 1
                    self.pause_frames = 8  # Longer pause at start
                    self.position = -1

        # Calculate dynamic wave width based on position
        # Grows in the middle, shrinks at edges
        progress = self.position / max(visible_len, 1)
        # Sine curve: small at edges, large in middle
        wave_width = int(1 + 3 * math.sin(progress * math.pi))
        wave_width = max(1, min(4, wave_width))

        # Calculate subtle pulse brightness (0.0 to 0.3)
        pulse_brightness = 0.15 * (1 + math.sin(self.pulse_phase * math.pi / 10))

        # Parse text into segments
        segments = []
        last_end = 0
        for match in ansi_pattern.finditer(text):
            if match.start() > last_end:
                segments.append((False, text[last_end : match.start()]))
            segments.append((True, match.group()))
            last_end = match.end()
        if last_end < len(text):
            segments.append((False, text[last_end:]))

        # Build result
        result = []
        visible_idx = 0

        for is_ansi, content in segments:
            if is_ansi:
                result.append(content)
            else:
                for char in content:
                    distance = abs(visible_idx - self.position)

                    if distance == 0:
                        # Center - brightest (bold only, no fg override)
                        result.append(f"\033[1m{char}\033[22m")
                    elif distance == 1:
                        # Adjacent - bright
                        result.append(f"\033[1m{char}\033[22m")
                    elif distance <= wave_width:
                        # Wave area - slight brightness
                        result.append(f"\033[1m{char}\033[22m")
                    else:
                        # Background - subtle pulse
                        if pulse_brightness > 0.1:
                            result.append(f"\033[2m{char}\033[22m")
                        else:
                            result.append(char)

                    visible_idx += 1

        return "".join(result)


class PulseEffect:
    """Handles pulsing brightness animation effects."""

    def __init__(self, speed: int = 3, pulse_width: int = 2):
        """Initialize pulse effect.

        Args:
            speed: Animation speed (frames between updates).
            pulse_width: Number of brightness levels in pulse.
        """
        self.speed = speed
        self.pulse_width = pulse_width
        self.frame_counter = 0
        self.brightness_level = 0
        self.direction = 1  # 1 for getting brighter, -1 for getting dimmer

    def configure(self, speed: int, pulse_width: int) -> None:
        """Configure pulse parameters.

        Args:
            speed: Animation speed.
            pulse_width: Pulse width.
        """
        self.speed = speed
        self.pulse_width = pulse_width

    def apply_pulse(self, text: str) -> str:
        """Apply pulsing brightness effect to text.

        Args:
            text: Text to apply pulse to.

        Returns:
            Text with pulse effect applied.
        """
        if not text:
            return text

        # Update pulse brightness
        self.frame_counter = (self.frame_counter + 1) % self.speed
        if self.frame_counter == 0:
            # Move brightness level
            self.brightness_level += self.direction

            # Reverse direction at bounds
            if self.brightness_level >= self.pulse_width:
                self.direction = -1
                self.brightness_level = self.pulse_width
            elif self.brightness_level <= 0:
                self.direction = 1
                self.brightness_level = 0

        # Determine color based on brightness level
        if self.brightness_level == self.pulse_width:
            # Peak brightness - bright yellow
            color = ColorPalette.BRIGHT_YELLOW
        elif self.brightness_level >= self.pulse_width * 2 // 3:
            # Bright - yellow
            color = ColorPalette.YELLOW
        elif self.brightness_level >= self.pulse_width // 3:
            # Medium - dim yellow
            color = ColorPalette.DIM_YELLOW
        else:
            # Dim - dim grey
            color = ColorPalette.DIM_GREY

        result = []
        for char in text:
            result.append(f"{color}{char}{ColorPalette.RESET}")

        return "".join(result)

    def apply_pulse_ansi(self, text: str) -> str:
        """Apply pulse effect to text that already contains ANSI escape codes.

        Uses bold/dim modifiers instead of color replacement to preserve
        existing colors while adding the pulse brightness effect.

        Args:
            text: Text with ANSI escape codes to apply pulse to.

        Returns:
            Text with pulse effect applied (preserving original codes).
        """
        import re

        if not text:
            return text

        # Update pulse brightness (same logic as apply_pulse)
        self.frame_counter = (self.frame_counter + 1) % self.speed
        if self.frame_counter == 0:
            self.brightness_level += self.direction
            if self.brightness_level >= self.pulse_width:
                self.direction = -1
                self.brightness_level = self.pulse_width
            elif self.brightness_level <= 0:
                self.direction = 1
                self.brightness_level = 0

        # Determine brightness modifier based on level
        if self.brightness_level == self.pulse_width:
            # Peak brightness - bold
            prefix = "\033[1m"
            suffix = "\033[22m"
        elif self.brightness_level >= self.pulse_width * 2 // 3:
            # Bright - bold
            prefix = "\033[1m"
            suffix = "\033[22m"
        elif self.brightness_level >= self.pulse_width // 3:
            # Medium - normal (no modifier)
            prefix = ""
            suffix = ""
        else:
            # Dim
            prefix = "\033[2m"
            suffix = "\033[22m"

        # Parse ANSI codes and apply brightness to visible chars
        ansi_pattern = re.compile(r"\033\[[0-9;]*m")
        segments = []
        last_end = 0
        for match in ansi_pattern.finditer(text):
            if match.start() > last_end:
                segments.append((False, text[last_end : match.start()]))
            segments.append((True, match.group()))
            last_end = match.end()
        if last_end < len(text):
            segments.append((False, text[last_end:]))

        result = []
        for is_ansi, content in segments:
            if is_ansi:
                result.append(content)
            else:
                # Apply brightness modifier to visible text
                if prefix:
                    result.append(f"{prefix}{content}{suffix}")
                else:
                    result.append(content)

        return "".join(result)


class ScrambleEffect:
    """Handles text scramble shimmer animation effects."""

    # Special characters for scramble effect (matrix-style)
    SCRAMBLE_CHARS = "!@#$%^&*()_+-=[]{}|;:,.<>?/~`0123456789abcdefghijklmnopqrstuvwxyz"

    def __init__(self, speed: int = 2, window_size: int = 6):
        """Initialize scramble effect.

        Args:
            speed: Animation speed (frames between updates).
            window_size: Size of scrambling window in characters.
        """
        self.speed = speed
        self.window_size = window_size
        self.frame_counter = 0
        self.position = 0

    def configure(self, speed: int, window_size: int) -> None:
        """Configure scramble parameters.

        Args:
            speed: Animation speed.
            window_size: Scramble window size.
        """
        self.speed = speed
        self.window_size = window_size

    def _get_scramble_char(self, index: int) -> str:
        """Get a random scramble character.

        Args:
            index: Character position for deterministic randomness.

        Returns:
            Random scramble character.
        """
        import random

        # Use index + frame for more chaotic scrambling
        random.seed(index + self.position + self.frame_counter)
        return random.choice(self.SCRAMBLE_CHARS)

    def apply_scramble(self, text: str) -> str:
        """Apply text scramble shimmer effect.

        Creates a moving window of scrambled characters that flows
        through the text like a shimmer.

        Args:
            text: Text to apply effect to.

        Returns:
            Text with scramble shimmer effect applied.
        """
        if not text:
            return text

        # Update position like shimmer
        self.frame_counter = (self.frame_counter + 1) % self.speed
        if self.frame_counter == 0:
            self.position = (self.position + 1) % (len(text) + self.window_size * 2)

        result = []
        for i, char in enumerate(text):
            distance = abs(i - self.position)

            if distance < self.window_size:
                # Inside scramble window - show random character
                scramble = self._get_scramble_char(i)
                # More chaotic at center of window
                if distance == 0:
                    # Center - bright cyan
                    result.append(
                        f"{ColorPalette.BRIGHT_CYAN}{scramble}{ColorPalette.RESET}"
                    )
                elif distance < self.window_size // 2:
                    # Near center - cyan
                    result.append(f"{ColorPalette.CYAN}{scramble}{ColorPalette.RESET}")
                else:
                    # Edge - dim cyan
                    result.append(
                        f"{ColorPalette.DIM_CYAN}{scramble}{ColorPalette.RESET}"
                    )
            else:
                # Outside window - show actual character with green color
                result.append(f"{ColorPalette.BRIGHT_GREEN}{char}{ColorPalette.RESET}")

        return "".join(result)


class AgnosterSegment:
    """Builder for powerline/agnoster style segments."""

    def __init__(self):
        """Initialize empty segment list."""
        self.segments: List[Tuple[Tuple[int, int, int], Tuple[int, int, int], str]] = []

    def add(
        self,
        text: str,
        bg: Tuple[int, int, int],
        fg: Tuple[int, int, int] = AgnosterColors.TEXT_DARK,
    ) -> "AgnosterSegment":
        """Add a segment.

        Args:
            text: Segment text content.
            bg: Background color RGB tuple.
            fg: Foreground (text) color RGB tuple.

        Returns:
            Self for chaining.
        """
        self.segments.append((bg, fg, text))
        return self

    def add_lime(self, text: str, variant: str = "normal") -> "AgnosterSegment":
        """Add a lime-colored segment.

        Args:
            text: Segment text.
            variant: "normal", "dark", or "darker".

        Returns:
            Self for chaining.
        """
        bg_map = {
            "normal": AgnosterColors.LIME,
            "dark": AgnosterColors.LIME_DARK,
            "darker": AgnosterColors.LIME_DARKER,
        }
        return self.add(text, bg_map.get(variant, AgnosterColors.LIME))

    def add_cyan(self, text: str, variant: str = "normal") -> "AgnosterSegment":
        """Add a cyan-colored segment.

        Args:
            text: Segment text.
            variant: "normal", "dark", or "light".

        Returns:
            Self for chaining.
        """
        bg_map = {
            "normal": AgnosterColors.CYAN,
            "dark": AgnosterColors.CYAN_DARK,
            "light": AgnosterColors.CYAN_LIGHT,
        }
        return self.add(text, bg_map.get(variant, AgnosterColors.CYAN))

    def add_neutral(self, text: str, variant: str = "mid") -> "AgnosterSegment":
        """Add a neutral gray segment.

        Args:
            text: Segment text.
            variant: "dark", "mid", or "light".

        Returns:
            Self for chaining.
        """
        bg_map = {
            "dark": AgnosterColors.BG_DARK,
            "mid": AgnosterColors.BG_MID,
            "light": AgnosterColors.BG_LIGHT,
        }
        fg = AgnosterColors.TEXT_LIGHT
        return self.add(text, bg_map.get(variant, AgnosterColors.BG_MID), fg)

    def render(self, separator: str = Powerline.RIGHT) -> str:
        """Render all segments with powerline separators.

        Args:
            separator: Powerline separator character to use.

        Returns:
            Fully formatted powerline string.
        """
        if not self.segments:
            return ""

        result = []
        reset = ColorPalette.RESET

        for i, (bg, fg, text) in enumerate(self.segments):
            bg_code = make_bg_color(*bg)
            fg_code = make_fg_color(*fg)

            # Segment content with padding
            result.append(f"{bg_code}{fg_code} {text} ")

            # Add separator (arrow colored: fg=current_bg, bg=next_bg or transparent)
            if i < len(self.segments) - 1:
                next_bg = self.segments[i + 1][0]
                sep_fg = make_fg_color(*bg)  # Arrow color = current segment bg
                sep_bg = make_bg_color(*next_bg)  # Arrow bg = next segment bg
                result.append(f"{sep_bg}{sep_fg}{separator}")
            else:
                # Last segment - arrow fades to transparent
                sep_fg = make_fg_color(*bg)
                result.append(f"{reset}{sep_fg}{separator}{reset}")

        return "".join(result)

    def render_minimal(self) -> str:
        """Render segments with thin separators (less prominent).

        Returns:
            Formatted string with thin separators.
        """
        return self.render(Powerline.RIGHT_THIN)


class GradientRenderer:
    """Handles various gradient effects."""

    @staticmethod
    def apply_white_to_grey(text: str) -> str:
        """Apply smooth white-to-grey gradient effect.

        Args:
            text: Text to apply gradient to.

        Returns:
            Text with gradient effect applied.
        """
        if not text or "\033[" in text:
            return text

        result = []
        text_length = len(text)
        grey_levels = ColorPalette.GREY_LEVELS

        for i, char in enumerate(text):
            # Calculate position in gradient (0.0 to 1.0)
            position = i / max(1, text_length - 1)

            # Map to grey level with smooth interpolation
            level_index = position * (len(grey_levels) - 1)
            level_index = min(int(level_index), len(grey_levels) - 1)

            grey_level = grey_levels[level_index]
            color_code = f"\033[38;5;{grey_level}m"
            result.append(f"{color_code}{char}")

        result.append(ColorPalette.RESET)
        return "".join(result)

    @staticmethod
    def apply_dim_white_gradient(text: str) -> str:
        """Apply subtle dim white to dimmer white gradient.

        Args:
            text: Text to apply gradient to.

        Returns:
            Text with dim white gradient applied.
        """
        if not text or "\033[" in text:
            return text

        result = []
        text_length = len(text)
        dim_levels = ColorPalette.DIM_WHITE_LEVELS

        for i, char in enumerate(text):
            # Calculate position in gradient (0.0 to 1.0)
            position = i / max(1, text_length - 1)

            # Map to dim white level with smooth interpolation
            level_index = position * (len(dim_levels) - 1)
            level_index = min(int(level_index), len(dim_levels) - 1)

            dim_level = dim_levels[level_index]
            color_code = f"\033[38;5;{dim_level}m"
            result.append(f"{color_code}{char}")

        result.append(ColorPalette.RESET)
        return "".join(result)

    @staticmethod
    def apply_dim_scheme_gradient(text: str) -> str:
        """Apply ultra-smooth gradient using dim color scheme.

        Automatically uses 256-color fallback when true color
        is not supported by the terminal.

        Args:
            text: Text to apply gradient to.

        Returns:
            Text with dim scheme gradient applied.
        """
        if not text:
            return text

        result = []
        text_length = len(text)
        color_rgb = ColorPalette.DIM_SCHEME_COLORS
        use_true_color = get_color_support() == ColorSupport.TRUE_COLOR

        for i, char in enumerate(text):
            position = i / max(1, text_length - 1)
            scaled_pos = position * (len(color_rgb) - 1)
            color_index = int(scaled_pos)
            t = scaled_pos - color_index

            if color_index >= len(color_rgb) - 1:
                r, g, b = color_rgb[-1]
            else:
                curr_rgb = color_rgb[color_index]
                next_rgb = color_rgb[color_index + 1]

                r = int(curr_rgb[0] + (next_rgb[0] - curr_rgb[0]) * t)
                g = int(curr_rgb[1] + (next_rgb[1] - curr_rgb[1]) * t)
                b = int(curr_rgb[2] + (next_rgb[2] - curr_rgb[2]) * t)

            r, g, b = int(r), int(g), int(b)

            if use_true_color:
                color_code = f"\033[38;2;{r};{g};{b}m"
            else:
                # Fallback to 256-color palette
                color_idx = rgb_to_256(r, g, b)
                color_code = f"\033[38;5;{color_idx}m"

            result.append(f"{color_code}{char}")

        result.append(ColorPalette.RESET)
        return "".join(result)

    @staticmethod
    def apply_custom_gradient(text: str, colors: List[Tuple[int, int, int]]) -> str:
        """Apply custom RGB gradient to text.

        Automatically uses 256-color fallback when true color
        is not supported by the terminal.

        Args:
            text: Text to apply gradient to.
            colors: List of RGB color tuples for gradient stops.

        Returns:
            Text with custom gradient applied.
        """
        if not text or len(colors) < 2:
            return text

        result = []
        text_length = len(text)
        use_true_color = get_color_support() == ColorSupport.TRUE_COLOR

        for i, char in enumerate(text):
            position = i / max(1, text_length - 1)
            scaled_pos = position * (len(colors) - 1)
            color_index = int(scaled_pos)
            t = scaled_pos - color_index

            if color_index >= len(colors) - 1:
                r, g, b = colors[-1]
            else:
                curr_rgb = colors[color_index]
                next_rgb = colors[color_index + 1]

                r = int(curr_rgb[0] + (next_rgb[0] - curr_rgb[0]) * t)
                g = int(curr_rgb[1] + (next_rgb[1] - curr_rgb[1]) * t)
                b = int(curr_rgb[2] + (next_rgb[2] - curr_rgb[2]) * t)

            r, g, b = int(r), int(g), int(b)

            if use_true_color:
                color_code = f"\033[38;2;{r};{g};{b}m"
            else:
                # Fallback to 256-color palette
                color_idx = rgb_to_256(r, g, b)
                color_code = f"\033[38;5;{color_idx}m"

            result.append(f"{color_code}{char}")

        result.append(ColorPalette.RESET)
        return "".join(result)


# Pre-compiled regex patterns for StatusColorizer (performance optimization)
# These patterns are used in apply_status_colors() which is called frequently
_STATUS_PATTERNS = None  # Lazy-initialized to avoid circular import issues


def _get_status_patterns():
    """Get pre-compiled status colorization patterns (lazy initialization)."""
    global _STATUS_PATTERNS
    if _STATUS_PATTERNS is None:
        _STATUS_PATTERNS = {
            # Number/count highlighting
            "numbers": re.compile(r"\b(\d{1,3}(?:,\d{3})*)\b"),
            # ASCII icon patterns
            "checkmark": re.compile(r"\b(✓)\s*"),
            "error_x": re.compile(r"\b(×)\s*"),
            "processing": re.compile(r"\b(\*)\s*"),
            "active": re.compile(r"\b(\+)\s*"),
            "inactive": re.compile(r"(^|\s)(-)\s+"),
            # Status indicators
            "processing_yes": re.compile(r"\b(Processing: Yes)\b"),
            "processing_no": re.compile(r"\b(Processing: No)\b"),
            "ready": re.compile(r"\b(Ready)\b"),
            "active_status": re.compile(r"\b(Active)\b"),
            "on": re.compile(r"\b(On)\b"),
            "off": re.compile(r"\b(Off)\b"),
            # Queue states
            "queue_zero": re.compile(r"\b(Queue: 0)\b"),
            "queue_nonzero": re.compile(r"\b(Queue: [1-9][0-9]*)\b"),
            # Time measurements
            "time_seconds": re.compile(r"\b(\d+\.\d+s)\b"),
            # Ratio highlighting
            "ratio": re.compile(r"\b(\d+):(\d+)\b"),
            "enhanced_ratio": re.compile(r"\b(Enhanced: \d+/\d+)"),
            # Percentage
            "percentage": re.compile(r"\b(\d+\.\d+%)\b"),
            # Tokens
            "tokens": re.compile(r"\b(\d+\s*tok)\b"),
            "tokens_k": re.compile(r"\b(\d+K\s*tok)\b"),
        }
    return _STATUS_PATTERNS


class StatusColorizer:
    """Handles semantic coloring of status text with ASCII icons."""

    # ASCII icon mapping (no emojis)
    ASCII_ICONS = {
        "checkmark": "√",
        "error": "×",
        "processing": "*",
        "active": "+",
        "inactive": "-",
        "ratio": "::",
        "arrow_right": ">",
        "separator": "|",
        "loading": "...",
        "count": "#",
        "circle_filled": "●",
        "circle_empty": "○",
        "circle_dot": "•",
    }

    @staticmethod
    def get_ascii_icon(icon_type: str) -> str:
        """Get ASCII icon by type.

        Args:
            icon_type: Type of icon to retrieve.

        Returns:
            ASCII character for the icon.
        """
        return StatusColorizer.ASCII_ICONS.get(icon_type, "")

    @staticmethod
    def apply_status_colors(text: str) -> str:
        """Apply semantic colors to status line text with ASCII icons.

        Args:
            text: Status text to colorize.

        Returns:
            Colorized text with ANSI codes and ASCII icons.
        """
        # Get pre-compiled patterns (lazy initialization)
        p = _get_status_patterns()

        # Replace emoji-style indicators with ASCII equivalents (simple string replace is fast)
        text = text.replace(
            "🟢",
            f"{ColorPalette.BRIGHT_GREEN}"
            f"{StatusColorizer.ASCII_ICONS['circle_filled']}"
            f"{ColorPalette.RESET}",
        )
        text = text.replace(
            "🟡",
            f"{ColorPalette.DIM_YELLOW}"
            f"{StatusColorizer.ASCII_ICONS['circle_filled']}"
            f"{ColorPalette.RESET}",
        )
        text = text.replace(
            "🔴",
            f"{ColorPalette.DIM_RED}"
            f"{StatusColorizer.ASCII_ICONS['circle_filled']}"
            f"{ColorPalette.RESET}",
        )
        text = text.replace(
            "✅",
            f"{ColorPalette.BRIGHT_GREEN}"
            f"{StatusColorizer.ASCII_ICONS['checkmark']}"
            f"{ColorPalette.RESET}",
        )
        text = text.replace(
            "❌",
            f"{ColorPalette.DIM_RED}"
            f"{StatusColorizer.ASCII_ICONS['error']}"
            f"{ColorPalette.RESET}",
        )

        # Use pre-compiled patterns for regex operations
        text = p["numbers"].sub(f"{ColorPalette.DIM_CYAN}\\1{ColorPalette.RESET}", text)
        text = p["checkmark"].sub(
            f"{ColorPalette.BRIGHT_GREEN}\\1{ColorPalette.RESET} ", text
        )
        text = p["error_x"].sub(f"{ColorPalette.DIM_RED}\\1{ColorPalette.RESET} ", text)
        text = p["processing"].sub(
            f"{ColorPalette.DIM_YELLOW}\\1{ColorPalette.RESET} ", text
        )
        text = p["active"].sub(
            f"{ColorPalette.BRIGHT_GREEN}\\1{ColorPalette.RESET} ", text
        )
        text = p["inactive"].sub(
            f"\\1{ColorPalette.DIM_CYAN}\\2{ColorPalette.RESET} ", text
        )

        # Status indicators
        text = p["processing_yes"].sub(
            f"{ColorPalette.DIM_YELLOW}\\1{ColorPalette.RESET}", text
        )
        text = p["processing_no"].sub(
            f"{ColorPalette.BRIGHT_GREEN}\\1{ColorPalette.RESET}", text
        )
        text = p["ready"].sub(
            f"{ColorPalette.BRIGHT_GREEN}\\1{ColorPalette.RESET}", text
        )
        text = p["active_status"].sub(
            f"{ColorPalette.DIM_YELLOW}\\1{ColorPalette.RESET}", text
        )
        text = p["on"].sub(f"{ColorPalette.DIM_YELLOW}\\1{ColorPalette.RESET}", text)
        text = p["off"].sub(f"{ColorPalette.DIM_CYAN}\\1{ColorPalette.RESET}", text)

        # Queue states
        text = p["queue_zero"].sub(
            f"{ColorPalette.BRIGHT_GREEN}\\1{ColorPalette.RESET}", text
        )
        text = p["queue_nonzero"].sub(
            f"{ColorPalette.DIM_YELLOW}\\1{ColorPalette.RESET}", text
        )

        # Time measurements
        text = p["time_seconds"].sub(
            f"{ColorPalette.DIM_MAGENTA}\\1{ColorPalette.RESET}", text
        )

        # Ratio highlighting
        text = p["ratio"].sub(
            f"{ColorPalette.DIM_BLUE}\\1{ColorPalette.DIM_CYAN}::"
            f"{ColorPalette.DIM_BLUE}\\2{ColorPalette.RESET}",
            text,
        )
        text = p["enhanced_ratio"].sub(
            f"{ColorPalette.DIM_BLUE}\\1{ColorPalette.RESET}", text
        )

        # Percentage highlighting
        text = p["percentage"].sub(
            f"{ColorPalette.DIM_MAGENTA}\\1{ColorPalette.RESET}", text
        )

        # Token highlighting
        text = p["tokens"].sub(f"{ColorPalette.DIM_CYAN}\\1{ColorPalette.RESET}", text)
        text = p["tokens_k"].sub(
            f"{ColorPalette.DIM_CYAN}\\1{ColorPalette.RESET}", text
        )

        return text


class BannerRenderer:
    """Handles startup header and KMO face rendering."""

    # Populated after class definition (see module-level code below)
    KMO_VARIATIONS: dict[str, list[str]]

    # KMO mascot variations (BMO's friendly brother)
    # Border styles: (top_left, top, top_right, side_left, side_right, bot_left, bot, bot_right)
    _KMO_BORDERS = {
        "block": ("▛", "▀", "▜", "▌", "▐", "▙", "▄", "▟"),
        "rounded": ("╭", "─", "╮", "│", "│", "╰", "─", "╯"),
        "quine": ("⌜", "─", "⌝", "│", "│", "⌞", "─", "⌟"),
        "arc": ("◜", "─", "◝", "│", "│", "◟", "─", "◞"),
        "paren": ("⎛", " ", "⎞", "⎜", "⎟", "⎝", " ", "⎠"),
        "dashed": ("┌", "┄", "┐", "┆", "┆", "└", "┄", "┘"),
        "heavy_dash": ("┏", "┅", "┓", "┇", "┇", "┗", "┅", "┛"),
        "heavy": ("┏", "━", "┓", "┃", "┃", "┗", "━", "┛"),
        "double": ("╔", "═", "╗", "║", "║", "╚", "═", "╝"),
        "ascii": (".", "-", ".", "|", "|", "'", "-", "'"),
        "slash": ("/", "-", "\\", "|", "|", "\\", "-", "/"),
    }

    # Current border style
    _border_style = "block"

    # KMO face data: (antenna, eyes, mouth)
    _KMO_FACES = {
        "happy": ("\\|/  ", "◕ ◕", "◡"),
        "super_happy": ("^^^  ", "^_^", "▽"),
        "winking": ("///  ", "◕ ‿", "◡"),
        "sleepy": ("___  ", "- -", "◡"),
        "excited": ("!!!  ", "● ●", "○"),
        "curious": ("???  ", "◔ ◔", "◡"),
        "star_eyes": ("*.*  ", "★ ★", "◡"),
        "thinking": ("~~~  ", "° °", "~"),
        "love": ("♥♥♥  ", "♥ ♥", "◡"),
        "cool": ("===  ", "▀ ▀", "◡"),
        "surprised": ("!!!  ", "O O", "○"),
        "angry": (">|||<", "◣ ◢", "⌢"),
        "dizzy": ("@@@  ", "@ @", "~"),
        "shy": ("...  ", ". .", "◡"),
        "laughing": ("\\^O^/", "X X", "▽"),
        "crying": (";;;  ", "T T", "⌢"),
        "nervous": ("^^;;;", "° °", "◡"),
        "determined": ("-->--", "► ◄", "–"),
        "dreaming": ("~~~  ", "u u", "◡"),
        "grinning": ("^^^  ", "◠ ◠", "▽"),
        "shocked": ("\\O_O/", "O O", "О"),
        "playful": ("~w~  ", "^ ◕", "◡"),
        "tired": ("zzZZ ", "= =", "~"),
        "confused": ("?!?  ", "◔ ◕", "○"),
        "proud": ("^_^_^", "◠ ◠", "◡"),
        "worried": ("...;;", "◔ ◔", "⌢"),
        "silly": ("*~*~*", "x o", "p"),
        "zen": ("ॐॐॐ  ", "- -", "◡"),
        "mischief": (">><<", "≖ ≖", "◠"),
        "sparkle": ("✧✦✧  ", "✦ ✦", "◡"),
    }

    @classmethod
    def set_border_style(cls, style: str) -> None:
        """Set border style for KMO faces."""
        if style in cls._KMO_BORDERS:
            cls._border_style = style

    @classmethod
    def _build_kmo(
        cls, antenna: str, eyes: str, mouth: str, style: str | None = None
    ) -> list:
        """Build KMO face with configurable border style."""
        style = style or cls._border_style
        tl, t, tr, sl, sr, bl, b, br = cls._KMO_BORDERS.get(
            style, cls._KMO_BORDERS["block"]
        )
        return [
            f"  {antenna:^6}  ",
            f"  {tl}{t*5}{tr} ",
            f" <{sl} {eyes} {sr}>",
            f"  {sl}  {mouth}  {sr} ",
            f"  {bl}{b*5}{br} ",
        ]

    @classmethod
    def get_kmo_variation(cls, mood: str, style: str | None = None) -> list:
        """Get KMO variation by mood name."""
        if mood not in cls._KMO_FACES:
            mood = "happy"
        antenna, eyes, mouth = cls._KMO_FACES[mood]
        return cls._build_kmo(antenna, eyes, mouth, style)

    @classmethod
    def create_kollabor_banner(
        cls, version: str = "v1.0.0", context: dict | None = None
    ) -> str:
        """Create compact Kollab startup header using the design system.

        Args:
            version: Version string to display.
            context: Optional dict with agent, model, profile, skills, directory.

        Returns:
            Formatted startup header with context info.
        """
        from .design_system import T
        from .terminal_state import get_global_width

        width = max(40, get_global_width())
        border_fg = T().secondary[0]
        panel_bg = T().dark[0]
        panel_fg = T().text
        panel_muted = T().text_dim

        logo_plain = "  kollab"
        version_plain = version
        header_gap = " " * max(1, width - len(logo_plain) - len(version_plain))
        header = f"{logo_plain}{header_gap}{version}"

        lines = [cls._panel_border("top", width, border_fg)]
        lines.append(cls._panel_line(header, width, panel_bg, panel_fg))

        if context:
            agent = context.get("agent", "")
            model = context.get("model", "")
            profile = context.get("profile", "")
            skills = context.get("skills")
            directory = context.get("directory", "")

            home = os.path.expanduser("~")
            if directory.startswith(home):
                directory = "~" + directory[len(home) :]

            runtime_parts = []
            if agent:
                runtime_parts.append(f"agent {agent}")
            if model:
                runtime_parts.append(f"model {model}")
            if profile:
                runtime_parts.append(f"profile {profile}")
            if skills is not None:
                skill_label = "skill" if skills == 1 else "skills"
                runtime_parts.append(f"{skills} {skill_label}")

            if runtime_parts:
                runtime_line = "  " + " · ".join(runtime_parts)
                lines.append(cls._panel_line(runtime_line, width, panel_bg, panel_fg))

            if directory:
                directory_line = f"  {directory}"
                lines.append(
                    cls._panel_line(directory_line, width, panel_bg, panel_muted)
                )

        lines.append(cls._panel_border("bottom", width, border_fg))
        return "\n" + "\n".join(lines) + "\n"

    @staticmethod
    def _panel_border(position: str, width: int, color: tuple[int, int, int]) -> str:
        """Render a status-widget style half-block panel border."""
        from .design_system import solid_fg

        char = "▄" if position == "top" else "▀"
        return solid_fg(char * width, color)

    @classmethod
    def _panel_line(
        cls,
        text: str,
        width: int,
        bg: tuple[int, int, int],
        fg: tuple[int, int, int],
    ) -> str:
        """Render one high-contrast startup header line."""
        from .design_system import solid

        clipped = cls._truncate_visible(text, width)
        padded = clipped + (" " * max(0, width - len(clipped)))
        return solid(padded, bg, fg, width)

    @staticmethod
    def _truncate_visible(text: str, width: int) -> str:
        """Truncate plain text to a visible terminal width."""
        if len(text) <= width:
            return text
        if width <= 3:
            return text[:width]
        return text[: width - 3] + "..."


# Build KMO_VARIATIONS dict from compact face data
def _build_kmo(antenna: str, eyes: str, mouth: str) -> list:
    return [
        f"  {antenna:^6}  ",
        "  ▛▀▀▀▀▀▜ ",
        f" <▌ {eyes} ▐>",
        f"  ▌  {mouth}  ▐ ",
        "  ▙▄▄▄▄▄▟ ",
    ]


BannerRenderer.KMO_VARIATIONS = {
    mood: _build_kmo(*face) for mood, face in BannerRenderer._KMO_FACES.items()
}


class VisualEffects:
    """Main visual effects coordinator."""

    def __init__(self) -> None:
        """Initialize visual effects system."""
        self.gradient_renderer: GradientRenderer = GradientRenderer()
        self.shimmer_effect: ShimmerEffect = ShimmerEffect()
        self.pulse_effect: PulseEffect = PulseEffect()
        self.scramble_effect: ScrambleEffect = ScrambleEffect()
        self.status_colorizer: StatusColorizer = StatusColorizer()
        self.banner_renderer: BannerRenderer = BannerRenderer()

        # Effect configurations
        self._effects_config: Dict[str, EffectConfig] = {
            "thinking": EffectConfig(EffectType.SHIMMER, speed=3, width=4),
            "gradient": EffectConfig(EffectType.GRADIENT),
            "status": EffectConfig(EffectType.COLOR),
            "banner": EffectConfig(EffectType.GRADIENT),
        }

    def configure_effect(self, effect_name: str, **kwargs) -> None:
        """Configure a specific effect.

        Args:
            effect_name: Name of effect to configure.
            **kwargs: Configuration parameters.
        """
        if effect_name in self._effects_config:
            config = self._effects_config[effect_name]
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)

        # Special handling for shimmer effect
        if effect_name == "thinking":
            self.shimmer_effect.configure(
                kwargs.get("speed", 3), kwargs.get("width", 4)
            )

    def apply_thinking_effect(
        self, text: str, effect_type: str = "shimmer", ansi_aware: bool = True
    ) -> str:
        """Apply thinking visualization effect.

        Args:
            text: Text to apply effect to.
            effect_type: Type of effect ("shimmer", "pulse", "scramble", "dim", "none").
            ansi_aware: If True, use ANSI-aware version that preserves existing escape codes.

        Returns:
            Text with thinking effect applied.
        """
        config = self._effects_config.get("thinking")
        if not config or not config.enabled:
            return text

        if effect_type == "shimmer":
            # Use ANSI-aware version if text contains escape codes or ansi_aware is True
            if ansi_aware and "\033[" in text:
                return self.shimmer_effect.apply_shimmer_ansi(text)
            return self.shimmer_effect.apply_shimmer(text)
        elif effect_type == "pulse":
            # TODO: Add ANSI-aware version for pulse if needed
            if ansi_aware and "\033[" in text:
                return self.pulse_effect.apply_pulse_ansi(text)
            return self.pulse_effect.apply_pulse(text)
        elif effect_type == "scramble":
            return self.scramble_effect.apply_scramble(text)
        elif effect_type == "dim":
            return f"{ColorPalette.DIM}{text}{ColorPalette.RESET}"
        else:  # none or normal
            return text

    def apply_message_gradient(
        self, text: str, gradient_type: str = "dim_white"
    ) -> str:
        """Apply gradient effect to message text.

        Args:
            text: Text to apply gradient to.
            gradient_type: Type of gradient to apply.

        Returns:
            Text with gradient applied.
        """
        config = self._effects_config.get("gradient")
        if not config or not config.enabled:
            return text

        if gradient_type == "white_to_grey":
            return self.gradient_renderer.apply_white_to_grey(text)
        elif gradient_type == "dim_white":
            return self.gradient_renderer.apply_dim_white_gradient(text)
        elif gradient_type == "dim_scheme":
            return self.gradient_renderer.apply_dim_scheme_gradient(text)
        else:
            return text

    def apply_status_colors(self, text: str) -> str:
        """Apply status colors to text.

        Args:
            text: Text to colorize.

        Returns:
            Colorized text.
        """
        config = self._effects_config.get("status")
        if not config or not config.enabled:
            return text

        return self.status_colorizer.apply_status_colors(text)

    def create_banner(
        self, version: str = "v1.0.0", context: dict | None = None
    ) -> str:
        """Create application startup header.

        Args:
            version: Version string.
            context: Optional dict with agent, model, profile, skills, directory.

        Returns:
            Formatted startup header.
        """
        config = self._effects_config.get("banner")
        if not config or not config.enabled:
            return f"kollab console {version}\n"

        return self.banner_renderer.create_kollabor_banner(version, context=context)

    def get_effect_stats(self) -> Dict[str, Any]:
        """Get visual effects statistics.

        Returns:
            Dictionary with effect statistics.
        """
        return {
            "shimmer_position": self.shimmer_effect.position,
            "shimmer_frame_counter": self.shimmer_effect.frame_counter,
            "effects_config": {
                name: {
                    "enabled": config.enabled,
                    "type": getattr(config.effect_type, "value", config.effect_type),
                    "intensity": config.intensity,
                }
                for name, config in self._effects_config.items()
            },
        }
