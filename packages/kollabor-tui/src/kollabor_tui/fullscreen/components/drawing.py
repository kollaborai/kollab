"""Drawing primitives for full-screen plugins."""

import math
from typing import List, Optional, Tuple

from kollabor_tui.visual_effects import ColorPalette


class DrawingPrimitives:
    """Basic drawing operations for full-screen plugins."""

    @staticmethod
    def draw_text_centered(
        renderer, y: int, text: str, color: str = ColorPalette.WHITE
    ):
        """Draw text centered horizontally.

        Args:
            renderer: FullScreenRenderer instance
            y: Row position
            text: Text to draw
            color: Text color
        """
        width, _ = renderer.get_terminal_size()
        x = max(0, (width - len(text)) // 2)
        renderer.write_at(x, y, text, color)

    @staticmethod
    def draw_border(
        renderer,
        x: int,
        y: int,
        width: int,
        height: int,
        border_char: str = "─",
        corner_chars: Tuple[str, str, str, str] = ("╭", "╮", "╯", "╰"),
        color: str = ColorPalette.WHITE,
    ):
        """Draw a decorative border.

        Args:
            renderer: FullScreenRenderer instance
            x, y: Top-left position
            width, height: Border dimensions
            border_char: Character for sides
            corner_chars: Tuple of (top-left, top-right, bottom-right, bottom-left)
            color: Border color
        """
        if width < 2 or height < 2:
            return

        # Top border
        renderer.write_at(x, y, corner_chars[0], color)
        for i in range(1, width - 1):
            renderer.write_at(x + i, y, border_char, color)
        renderer.write_at(x + width - 1, y, corner_chars[1], color)

        # Side borders
        for i in range(1, height - 1):
            renderer.write_at(x, y + i, "│", color)
            renderer.write_at(x + width - 1, y + i, "│", color)

        # Bottom border
        if height > 1:
            renderer.write_at(x, y + height - 1, corner_chars[3], color)
            for i in range(1, width - 1):
                renderer.write_at(x + i, y + height - 1, border_char, color)
            renderer.write_at(x + width - 1, y + height - 1, corner_chars[2], color)

    @staticmethod
    def draw_progress_bar(
        renderer,
        x: int,
        y: int,
        width: int,
        progress: float,
        fill_char: str = "█",
        empty_char: str = "░",
        color: str = ColorPalette.GREEN,
    ):
        """Draw a progress bar.

        Args:
            renderer: FullScreenRenderer instance
            x, y: Position
            width: Bar width
            progress: Progress value (0.0 to 1.0)
            fill_char: Character for filled portion
            empty_char: Character for empty portion
            color: Bar color
        """
        progress = max(0.0, min(1.0, progress))
        filled_width = int(width * progress)

        bar_text = fill_char * filled_width + empty_char * (width - filled_width)
        renderer.write_at(x, y, bar_text, color)

    @staticmethod
    def draw_spinner(
        renderer,
        x: int,
        y: int,
        frame: int,
        frames: Optional[List[str]] = None,
        color: str = ColorPalette.CYAN,
    ):
        """Draw an animated spinner.

        Args:
            renderer: FullScreenRenderer instance
            x, y: Position
            frame: Current frame number
            frames: List of spinner frames
            color: Spinner color
        """
        if frames is None:
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]

        char = frames[frame % len(frames)]
        renderer.write_at(x, y, char, color)

    @staticmethod
    def draw_circle_points(
        renderer,
        center_x: int,
        center_y: int,
        radius: int,
        char: str = "●",
        color: str = ColorPalette.WHITE,
    ):
        """Draw points in a circle pattern.

        Args:
            renderer: FullScreenRenderer instance
            center_x, center_y: Circle center
            radius: Circle radius
            char: Character to draw
            color: Character color
        """
        width, height = renderer.get_terminal_size()

        for angle in range(0, 360, 15):  # Every 15 degrees
            rad = math.radians(angle)
            x = int(center_x + radius * math.cos(rad))
            y = int(
                center_y + radius * math.sin(rad) * 0.5
            )  # Adjust for terminal aspect ratio

            if 0 <= x < width and 0 <= y < height:
                renderer.write_at(x, y, char, color)

    @staticmethod
    def draw_wave(
        renderer,
        y: int,
        amplitude: int,
        frequency: float,
        phase: float,
        char: str = "~",
        color: str = ColorPalette.BLUE,
    ):
        """Draw a wave pattern.

        Args:
            renderer: FullScreenRenderer instance
            y: Base row position
            amplitude: Wave height
            frequency: Wave frequency
            phase: Wave phase offset
            char: Character to draw
            color: Wave color
        """
        width, height = renderer.get_terminal_size()

        for x in range(width):
            wave_y = int(y + amplitude * math.sin(frequency * x + phase))
            if 0 <= wave_y < height:
                renderer.write_at(x, wave_y, char, color)

    @staticmethod
    def fill_area(
        renderer,
        x: int,
        y: int,
        width: int,
        height: int,
        char: str = " ",
        color: str = ColorPalette.RESET,
    ):
        """Fill an area with a character.

        Args:
            renderer: FullScreenRenderer instance
            x, y: Top-left position
            width, height: Area dimensions
            char: Fill character
            color: Fill color
        """
        term_width, term_height = renderer.get_terminal_size()

        for row in range(height):
            for col in range(width):
                draw_x, draw_y = x + col, y + row
                if 0 <= draw_x < term_width and 0 <= draw_y < term_height:
                    renderer.write_at(draw_x, draw_y, char, color)
