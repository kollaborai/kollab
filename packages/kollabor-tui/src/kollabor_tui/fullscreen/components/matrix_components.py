"""Matrix rain components for the full-screen framework."""

import random
from typing import List

from kollabor_tui.visual_effects import ColorPalette


class MatrixColumn:
    """A single column of falling Matrix characters."""

    def __init__(self, x: int, height: int):
        """Initialize Matrix column.

        Args:
            x: X position (column number)
            height: Terminal height
        """
        self.x = x
        self.height = height
        self.chars: List[str] = []
        self.positions: List[int] = []
        self.speed = random.uniform(1.5, 4.0)
        self.next_update = 0.0
        self.length = random.randint(5, 25)

        # Matrix character set (katakana, numbers, symbols)
        self.matrix_chars = [
            "ア",
            "イ",
            "ウ",
            "エ",
            "オ",
            "カ",
            "キ",
            "ク",
            "ケ",
            "コ",
            "サ",
            "シ",
            "ス",
            "セ",
            "ソ",
            "タ",
            "チ",
            "ツ",
            "テ",
            "ト",
            "ナ",
            "ニ",
            "ヌ",
            "ネ",
            "ノ",
            "ハ",
            "ヒ",
            "フ",
            "ヘ",
            "ホ",
            "マ",
            "ミ",
            "ム",
            "メ",
            "モ",
            "ヤ",
            "ユ",
            "ヨ",
            "ラ",
            "リ",
            "ル",
            "レ",
            "ロ",
            "ワ",
            "ヲ",
            "ン",
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            ":",
            ".",
            '"',
            "=",
            "*",
            "+",
            "<",
            ">",
            "|",
            "\\",
            "/",
            "[",
            "]",
            "{",
            "}",
            "(",
            ")",
            "-",
            "_",
        ]

        self._reset()

    def _reset(self):
        """Reset column to start falling from top."""
        self.chars = [random.choice(self.matrix_chars) for _ in range(self.length)]
        self.positions = list(range(-self.length, 0))
        self.speed = random.uniform(1.2, 3.5)
        self.next_update = 0

    def update(self, time: float) -> bool:
        """Update column positions.

        Args:
            time: Current time

        Returns:
            True if column is still active
        """
        if time < self.next_update:
            return True

        self.next_update = time + (1.0 / self.speed)

        # Move all positions down
        for i in range(len(self.positions)):
            self.positions[i] += 1

        # Remove characters that have fallen off screen
        while self.positions and self.positions[0] >= self.height:
            self.positions.pop(0)
            self.chars.pop(0)

        # Check if column is done
        if not self.positions:
            # Random chance to restart
            if random.random() < 0.1:
                self._reset()
            return False

        # Randomly change some characters
        for i in range(len(self.chars)):
            if random.random() < 0.05:
                self.chars[i] = random.choice(self.matrix_chars)

        return True

    def render(self, renderer):
        """Render column using the full-screen renderer.

        Args:
            renderer: FullScreenRenderer instance
        """
        for i, (char, pos) in enumerate(zip(self.chars, self.positions)):
            if 0 <= pos < self.height:
                # Brightest character at the head
                if i == len(self.chars) - 1:
                    color = ColorPalette.BRIGHT_WHITE
                # Bright green for recent characters
                elif i >= len(self.chars) - 3:
                    color = ColorPalette.BRIGHT_GREEN
                # Normal green for middle
                elif i >= len(self.chars) - 8:
                    color = ColorPalette.GREEN
                # Dim green for tail
                else:
                    color = ColorPalette.DIM_GREEN

                # Write character at position
                renderer.write_at(self.x, pos, char, color)


class MatrixRenderer:
    """Renders the complete Matrix rain effect using the full-screen framework."""

    def __init__(self, terminal_width: int, terminal_height: int):
        """Initialize Matrix renderer.

        Args:
            terminal_width: Terminal width in columns
            terminal_height: Terminal height in rows
        """
        self.terminal_width = terminal_width
        self.terminal_height = terminal_height
        self.columns: List[MatrixColumn] = []
        self.start_time = 0

        # Create initial columns
        self._create_columns()

    def _create_columns(self):
        """Create initial set of Matrix columns."""
        self.columns = []
        for x in range(self.terminal_width):
            if random.random() < 0.5:  # 50% chance for each column to be active
                column = MatrixColumn(x, self.terminal_height)
                # Stagger start times
                column.next_update = random.uniform(0, 3.0)
                self.columns.append(column)

    def update(self, current_time: float):
        """Update all Matrix columns.

        Args:
            current_time: Current time for animation
        """
        # Update all columns
        active_columns = []
        for column in self.columns:
            if column.update(current_time):
                active_columns.append(column)

        self.columns = active_columns

        # Add new columns occasionally
        if len(self.columns) < self.terminal_width * 0.4 and random.random() < 0.02:
            x = random.randint(0, self.terminal_width - 1)
            # Make sure we don't have a column too close
            if not any(abs(col.x - x) < 2 for col in self.columns):
                column = MatrixColumn(x, self.terminal_height)
                self.columns.append(column)

    def render(self, renderer):
        """Render all Matrix columns.

        Args:
            renderer: FullScreenRenderer instance
        """
        # Clear screen
        renderer.clear_screen()

        # Render all columns
        for column in self.columns:
            column.render(renderer)

        # Note: Flushing is handled by session's end_frame() for flicker-free rendering

    def reset(self):
        """Reset the Matrix renderer."""
        self._create_columns()
        self.start_time = 0
