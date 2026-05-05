#!/usr/bin/env python3
"""
Fringe-style glitchy terminal effect
Creates a matrix-like shifting character display similar to the subway ticket effect
"""

import os
import random
import sys
import time


class FringeTerminalEffect:
    def __init__(self, width=80, height=24):
        self.width = width
        self.height = height
        self.matrix = []
        self.target_text = ""
        self.target_x = 0
        self.target_y = 0
        self.glitch_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?/~`"
        self.matrix_chars = "01"
        self.ascii_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

        # Initialize matrix with random characters
        for _ in range(height):
            row = []
            for _ in range(width):
                row.append(random.choice(self.matrix_chars))
            self.matrix.append(row)

    def clear_screen(self):
        """Clear terminal screen"""
        os.system("clear" if os.name == "posix" else "cls")

    def set_target_text(self, text, start_x=0, start_y=0):
        """Set text that will gradually emerge from the glitch"""
        self.target_text = text
        self.target_x = start_x
        self.target_y = start_y

    def update_matrix(self, reveal_probability=0.02, glitch_intensity=0.1):
        """Update the matrix with glitch effects and gradual text reveal"""
        for y in range(self.height):
            for x in range(self.width):
                # Check if this position should show target text
                text_x = x - self.target_x
                text_y = y - self.target_y

                if 0 <= text_y < len(
                    self.target_text.split("\n")
                ) and 0 <= text_x < len(self.target_text.split("\n")[text_y]):

                    target_char = self.target_text.split("\n")[text_y][text_x]

                    # Gradually reveal target character
                    if random.random() < reveal_probability:
                        self.matrix[y][x] = target_char
                    elif random.random() < glitch_intensity:
                        # Glitch effect - mix of target char and random
                        if random.random() < 0.3:
                            self.matrix[y][x] = target_char
                        else:
                            self.matrix[y][x] = random.choice(self.glitch_chars)
                else:
                    # Random matrix background
                    if random.random() < 0.1:
                        self.matrix[y][x] = random.choice(self.matrix_chars)

    def render(self):
        """Render the current matrix state"""
        output = ""
        for row in self.matrix:
            output += "".join(row) + "\n"
        return output

    def glitch_effect(self, duration=10, fps=20):
        """Run the glitch effect for specified duration"""
        frame_time = 1.0 / fps
        start_time = time.time()

        while time.time() - start_time < duration:
            self.clear_screen()
            self.update_matrix()
            print(self.render())
            time.sleep(frame_time)

    def emerge_text_effect(self, text, duration=15, fps=15):
        """Text gradually emerges from glitch matrix"""
        self.set_target_text(text, 10, 5)
        frame_time = 1.0 / fps
        start_time = time.time()

        while time.time() - start_time < duration:
            self.clear_screen()

            # Increase reveal probability over time
            progress = (time.time() - start_time) / duration
            reveal_prob = 0.01 + (progress * 0.05)
            glitch_intensity = 0.2 - (progress * 0.15)

            self.update_matrix(reveal_prob, max(0.05, glitch_intensity))
            print(self.render())
            time.sleep(frame_time)


def demo_fringe_effect():
    """Demonstrate various Fringe-style effects"""
    try:
        # Try to use global terminal state if available
        from kollabor_tui.terminal_state import get_terminal_size

        width, height = get_terminal_size()
        height = height - 2  # Leave space for input
    except ImportError:
        # Fallback for standalone execution
        import shutil

        cols, rows = shutil.get_terminal_size()
        width = cols
        height = rows - 2

    effect = FringeTerminalEffect(width, height)

    print("Starting Fringe-style terminal effect...")
    time.sleep(2)

    # Pure glitch effect
    print("Phase 1: Matrix glitch...")
    effect.glitch_effect(duration=5)

    # Text emergence effect
    fringe_text = """KOLLABOR.AI
NEURAL INTERFACE
PATTERN RECOGNITION
CONSCIOUSNESS TRANSFER"""

    print("Phase 2: Message emergence...")
    effect.emerge_text_effect(fringe_text, duration=12)

    # Final stable state
    effect.clear_screen()
    print("TRANSMISSION COMPLETE")


if __name__ == "__main__":
    try:
        demo_fringe_effect()
    except KeyboardInterrupt:
        print("\nEffect terminated.")
        sys.exit(0)
