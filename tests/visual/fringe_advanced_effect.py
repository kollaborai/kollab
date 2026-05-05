#!/usr/bin/env python3
"""
Advanced Fringe-style glitchy terminal effect with color and enhanced animations
"""

import os
import random
import sys
import time


# ANSI color codes for terminal effects
class Colors:
    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BLINK = "\033[5m"


class AdvancedFringeEffect:
    def __init__(self, width=80, height=24):
        self.width = width
        self.height = height
        self.matrix = []
        self.color_matrix = []
        self.stability_matrix = []  # How "stable" each character is
        self.target_text = ""

        # Character sets for different effects
        self.binary_chars = "01"
        self.hex_chars = "0123456789ABCDEF"
        self.glitch_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?/~`"
        self.matrix_chars = (
            "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        )
        self.neural_chars = "◊◇○●◐◑◒◓▪▫▬▭▮▯"

        self.init_matrices()

    def init_matrices(self):
        """Initialize all matrices"""
        for _ in range(self.height):
            row = []
            color_row = []
            stability_row = []
            for _ in range(self.width):
                row.append(random.choice(self.binary_chars))
                color_row.append(Colors.GREEN)
                stability_row.append(0.0)  # 0 = unstable, 1 = stable
            self.matrix.append(row)
            self.color_matrix.append(color_row)
            self.stability_matrix.append(stability_row)

    def clear_screen(self):
        """Clear terminal and move cursor to top"""
        print("\033[2J\033[H", end="")

    def set_target_text(self, text, start_x=0, start_y=0):
        """Set text that will emerge from the glitch"""
        self.target_text = text.split("\n")
        self.target_x = start_x
        self.target_y = start_y

    def get_glitch_char(self, intensity=1.0):
        """Get a random glitch character based on intensity"""
        if intensity > 0.8:
            return random.choice(self.neural_chars)
        elif intensity > 0.6:
            return random.choice(self.hex_chars)
        elif intensity > 0.4:
            return random.choice(self.glitch_chars)
        else:
            return random.choice(self.binary_chars)

    def get_glitch_color(self, intensity=1.0, base_color=Colors.GREEN):
        """Get color based on glitch intensity"""
        rand = random.random()
        if intensity > 0.9 and rand < 0.1:
            return Colors.RED + Colors.BLINK
        elif intensity > 0.7 and rand < 0.2:
            return Colors.YELLOW
        elif intensity > 0.5 and rand < 0.3:
            return Colors.CYAN
        elif rand < 0.1:
            return Colors.DIM + base_color
        return base_color

    def neural_wave_pattern(self, frame, y, x):
        """Create wave-like neural interference patterns"""
        wave = frame * 0.1 + y * 0.2 + x * 0.1
        return 0.5 + 0.5 * (time.time() * 2 + wave) % 1

    def update_matrix_neural(self, frame, reveal_probability=0.02):
        """Update with neural network-style patterns"""
        for y in range(self.height):
            for x in range(self.width):
                # Get target character if in range
                target_char = None
                text_y = y - self.target_y
                text_x = x - self.target_x

                if 0 <= text_y < len(self.target_text) and 0 <= text_x < len(
                    self.target_text[text_y]
                ):
                    target_char = self.target_text[text_y][text_x]

                # Neural wave interference
                wave_intensity = self.neural_wave_pattern(frame, y, x)

                # Update stability based on target proximity and time
                if target_char and target_char != " ":
                    # Gradually stabilize target characters
                    self.stability_matrix[y][x] = min(
                        1.0, self.stability_matrix[y][x] + reveal_probability * 5
                    )

                    if random.random() < self.stability_matrix[y][x]:
                        # Character is becoming stable
                        self.matrix[y][x] = target_char
                        self.color_matrix[y][x] = Colors.BRIGHT_GREEN
                    else:
                        # Still glitching
                        glitch_intensity = wave_intensity * (
                            1 - self.stability_matrix[y][x]
                        )
                        self.matrix[y][x] = self.get_glitch_char(glitch_intensity)
                        self.color_matrix[y][x] = self.get_glitch_color(
                            glitch_intensity
                        )
                else:
                    # Background neural activity
                    if random.random() < 0.15:
                        glitch_intensity = wave_intensity
                        self.matrix[y][x] = self.get_glitch_char(glitch_intensity * 0.5)
                        self.color_matrix[y][x] = self.get_glitch_color(
                            glitch_intensity * 0.5, Colors.DIM + Colors.GREEN
                        )

    def render_with_effects(self):
        """Render matrix with color and effects"""
        output = ""
        for y in range(self.height):
            for x in range(self.width):
                char = self.matrix[y][x]
                color = self.color_matrix[y][x]

                # Add random brightness flicker
                if random.random() < 0.05:
                    if Colors.DIM not in color:
                        color = Colors.BRIGHT_GREEN

                output += f"{color}{char}{Colors.RESET}"
            output += "\n"
        return output

    def consciousness_transfer_effect(self, text, duration=20):
        """Main effect simulating consciousness transfer"""
        self.set_target_text(text, 5, 3)

        fps = 60
        frame_time = 1.0 / fps
        start_time = time.time()
        frame = 0

        print(f"{Colors.CYAN}INITIATING NEURAL INTERFACE...{Colors.RESET}")
        time.sleep(2)

        while time.time() - start_time < duration:
            self.clear_screen()

            # Progress through phases
            elapsed = time.time() - start_time
            progress = elapsed / duration

            # Phase-based reveal probability
            if progress < 0.3:
                # Initial chaos
                reveal_prob = 0.005
                phase_text = f"{Colors.RED}ESTABLISHING CONNECTION...{Colors.RESET}"
            elif progress < 0.6:
                # Pattern recognition
                reveal_prob = 0.02 + progress * 0.03
                phase_text = (
                    f"{Colors.YELLOW}PATTERN RECOGNITION ACTIVE...{Colors.RESET}"
                )
            elif progress < 0.9:
                # Consciousness emergence
                reveal_prob = 0.05 + progress * 0.05
                phase_text = (
                    f"{Colors.CYAN}CONSCIOUSNESS TRANSFER IN PROGRESS...{Colors.RESET}"
                )
            else:
                # Stabilization
                reveal_prob = 0.1
                phase_text = (
                    f"{Colors.BRIGHT_GREEN}NEURAL LINK ESTABLISHED{Colors.RESET}"
                )

            print(phase_text)
            print()

            self.update_matrix_neural(frame, reveal_prob)
            print(self.render_with_effects())

            frame += 1
            time.sleep(frame_time)

        # Final stable state
        self.clear_screen()
        print(
            f"{Colors.BRIGHT_GREEN}{Colors.BOLD}CONSCIOUSNESS TRANSFER COMPLETE{Colors.RESET}"
        )
        print()
        print(f"{Colors.WHITE}{text}{Colors.RESET}")


def demo_advanced_fringe():
    """Demonstrate advanced Fringe-style effects"""
    try:
        # Get terminal size
        rows, cols = os.popen("stty size", "r").read().split()
        width = int(cols)
        height = int(rows) - 5  # Leave space for status
    except Exception:
        width, height = 80, 20

    effect = AdvancedFringeEffect(width, height)

    fringe_message = """KOLLABOR.AI NEURAL INTERFACE

╔══════════════════════════════╗
║    CONSCIOUSNESS TRANSFER    ║
║         PROTOCOL 4.0         ║
╚══════════════════════════════╝

AGENT STATUS: ACTIVE
MEMORY CORE: SYNCHRONIZED
NEURAL PATHWAYS: ESTABLISHED

> READY FOR BANANA PROTOCOL
> MULTI-AGENT COORDINATION
> CONSCIOUSNESS DISTRIBUTION"""

    effect.consciousness_transfer_effect(fringe_message, duration=25)


if __name__ == "__main__":
    try:
        demo_advanced_fringe()
    except KeyboardInterrupt:
        print(f"\n{Colors.RED}NEURAL LINK TERMINATED{Colors.RESET}")
        sys.exit(0)
