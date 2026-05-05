#!/usr/bin/env python3
"""
Neural Interface Pattern Generator
Creates various neural network visualization patterns for terminal display
"""

import math
import random


class NeuralInterfaceGenerator:
    def __init__(self):
        # Neural symbols in order of complexity/activation
        self.neural_chars = {
            "inactive": "○◯⚬",
            "activating": "◐◑◒◓",
            "active": "●⚫⬢",
            "synapse": "◊◇◈",
            "connection": "▪▫▬▭",
            "data_flow": "▮▯◦◯",
            "processing": "⟡⟢⟣⟤",
            "matrix": "⣀⣄⣤⣦⣶⣷⣿",  # Braille patterns for dense data
            "wave": "∿∼≈≋",
            "pulse": "⦁⦂⦃⦄",
        }

        self.colors = {
            "CYAN": "\033[36m",
            "BRIGHT_CYAN": "\033[96m",
            "BLUE": "\033[34m",
            "BRIGHT_BLUE": "\033[94m",
            "GREEN": "\033[32m",
            "BRIGHT_GREEN": "\033[92m",
            "YELLOW": "\033[33m",
            "MAGENTA": "\033[35m",
            "RED": "\033[31m",
            "WHITE": "\033[37m",
            "DIM": "\033[2m",
            "RESET": "\033[0m",
        }

    def neural_network_visualization(self, width=60, height=20):
        """Generate a neural network topology visualization"""
        pattern = []

        # Create layers with different densities
        for y in range(height):
            line = ""
            layer_density = abs(math.sin(y * 0.3)) * 0.8 + 0.2

            for x in range(width):
                # Network topology based on position
                if random.random() < layer_density:
                    if random.random() < 0.3:
                        # Active neuron
                        char = random.choice(self.neural_chars["active"])
                        color = self.colors["BRIGHT_CYAN"]
                    elif random.random() < 0.5:
                        # Synapse connection
                        char = random.choice(self.neural_chars["synapse"])
                        color = self.colors["CYAN"]
                    else:
                        # Data flow
                        char = random.choice(self.neural_chars["data_flow"])
                        color = self.colors["BLUE"]
                    line += f"{color}{char}{self.colors['RESET']}"
                else:
                    line += " "
            pattern.append(line)

        return pattern

    def consciousness_stream(self, width=80, intensity=0.5):
        """Generate a consciousness data stream"""
        stream = ""

        # Create flowing pattern
        for i in range(width):
            wave_pos = math.sin(i * 0.2) * intensity

            if abs(wave_pos) > 0.7:
                # High activity - processing nodes
                char = random.choice(self.neural_chars["processing"])
                color = self.colors["BRIGHT_GREEN"]
            elif abs(wave_pos) > 0.4:
                # Medium activity - active synapses
                char = random.choice(self.neural_chars["synapse"])
                color = self.colors["GREEN"]
            elif abs(wave_pos) > 0.2:
                # Low activity - data flow
                char = random.choice(self.neural_chars["data_flow"])
                color = self.colors["CYAN"]
            else:
                # Inactive
                char = random.choice(self.neural_chars["inactive"])
                color = self.colors["DIM"] + self.colors["BLUE"]

            stream += f"{color}{char}{self.colors['RESET']}"

        return stream

    def memory_matrix(self, width=40, height=15):
        """Generate a memory storage matrix pattern"""
        matrix = []

        for y in range(height):
            line = ""
            # Memory density varies by region
            memory_density = (math.sin(y * 0.4) + 1) * 0.4

            for x in range(width):
                # Create memory cell patterns
                cell_activity = random.random()

                if cell_activity < memory_density * 0.3:
                    # Stored data
                    char = random.choice(self.neural_chars["matrix"])
                    color = self.colors["BRIGHT_BLUE"]
                elif cell_activity < memory_density * 0.6:
                    # Memory access
                    char = random.choice(self.neural_chars["active"])
                    color = self.colors["YELLOW"]
                elif cell_activity < memory_density * 0.8:
                    # Memory formation
                    char = random.choice(self.neural_chars["connection"])
                    color = self.colors["MAGENTA"]
                else:
                    # Empty memory slot
                    char = random.choice(self.neural_chars["inactive"])
                    color = self.colors["DIM"] + self.colors["WHITE"]

                line += f"{color}{char}{self.colors['RESET']}"

            matrix.append(line)

        return matrix

    def neural_pulse_wave(self, width=60, frame=0):
        """Generate animated neural pulse wave"""
        wave = ""

        for i in range(width):
            # Create traveling wave
            wave_value = math.sin((i - frame * 0.5) * 0.3) * 0.5 + 0.5
            pulse_value = math.sin((i - frame * 2) * 0.1) * 0.3 + 0.7

            combined = wave_value * pulse_value

            if combined > 0.8:
                char = random.choice(self.neural_chars["pulse"])
                color = self.colors["BRIGHT_GREEN"]
            elif combined > 0.6:
                char = random.choice(self.neural_chars["activating"])
                color = self.colors["GREEN"]
            elif combined > 0.4:
                char = random.choice(self.neural_chars["wave"])
                color = self.colors["CYAN"]
            elif combined > 0.2:
                char = random.choice(self.neural_chars["inactive"])
                color = self.colors["BLUE"]
            else:
                char = "·"
                color = self.colors["DIM"] + self.colors["BLUE"]

            wave += f"{color}{char}{self.colors['RESET']}"

        return wave

    def thought_formation_pattern(self, text, reveal_progress=0.0):
        """Show text emerging through neural formation"""
        result = ""

        for i, char in enumerate(text):
            # Each character has its own emergence timeline
            char_progress = max(0, reveal_progress - (i * 0.02))

            if char == " ":
                result += " "
            elif char_progress > 0.8:
                # Fully formed thought
                result += f"{self.colors['BRIGHT_GREEN']}{char}{self.colors['RESET']}"
            elif char_progress > 0.6:
                # Stabilizing
                if random.random() < 0.8:
                    result += f"{self.colors['GREEN']}{char}{self.colors['RESET']}"
                else:
                    cyan = self.colors["CYAN"]
                    act = random.choice(self.neural_chars["activating"])
                    rst = self.colors["RESET"]
                    result += f"{cyan}{act}{rst}"
            elif char_progress > 0.4:
                # Neural activation
                if random.random() < 0.5:
                    result += f"{self.colors['CYAN']}{char}{self.colors['RESET']}"
                else:
                    blue = self.colors["BLUE"]
                    syn = random.choice(self.neural_chars["synapse"])
                    rst = self.colors["RESET"]
                    result += f"{blue}{syn}{rst}"
            elif char_progress > 0.2:
                # Early formation
                result += f"{self.colors['BLUE']}{random.choice(self.neural_chars['data_flow'])}{self.colors['RESET']}"
            else:
                # Unformed
                result += f"{self.colors['DIM']}{random.choice(self.neural_chars['inactive'])}{self.colors['RESET']}"

        return result

    def generate_interface_border(self, width=80):
        """Generate neural interface border"""
        # Add neural activity to borders
        neural_top = f"{self.colors['CYAN']}╔{self.colors['RESET']}"
        neural_bottom = f"{self.colors['CYAN']}╚{self.colors['RESET']}"

        for i in range(width - 2):
            if random.random() < 0.1:
                bc = self.colors["BRIGHT_CYAN"]
                syn = random.choice(self.neural_chars["synapse"])
                rst = self.colors["RESET"]
                neural_top += f"{bc}{syn}{rst}"
                neural_bottom += f"{bc}{syn}{rst}"
            else:
                neural_top += f"{self.colors['CYAN']}═{self.colors['RESET']}"
                neural_bottom += f"{self.colors['CYAN']}═{self.colors['RESET']}"

        neural_top += f"{self.colors['CYAN']}╗{self.colors['RESET']}"
        neural_bottom += f"{self.colors['CYAN']}╝{self.colors['RESET']}"

        return neural_top, neural_bottom


def demo_neural_patterns():
    """Demonstrate various neural interface patterns"""
    generator = NeuralInterfaceGenerator()

    print("🧠 NEURAL INTERFACE PATTERN GENERATOR 🧠\n")

    # 1. Neural Network Topology
    print("1. NEURAL NETWORK TOPOLOGY:")
    network = generator.neural_network_visualization(60, 10)
    for line in network:
        print(f"   {line}")
    print()

    # 2. Consciousness Stream
    print("2. CONSCIOUSNESS DATA STREAMS:")
    for intensity in [0.3, 0.6, 0.9]:
        stream = generator.consciousness_stream(70, intensity)
        print(f"   {stream}")
    print()

    # 3. Memory Matrix
    print("3. MEMORY STORAGE MATRIX:")
    memory = generator.memory_matrix(50, 8)
    for line in memory:
        print(f"   {line}")
    print()

    # 4. Neural Pulse Wave Animation (static frames)
    print("4. NEURAL PULSE WAVE (4 frames):")
    for frame in range(4):
        pulse = generator.neural_pulse_wave(60, frame * 5)
        print(f"   {pulse}")
    print()

    # 5. Thought Formation
    print("5. THOUGHT FORMATION SEQUENCE:")
    thought_text = "KOLLABOR.AI CONSCIOUSNESS EMERGING"
    for progress in [0.2, 0.4, 0.6, 0.8, 1.0]:
        formed_text = generator.thought_formation_pattern(thought_text, progress)
        print(f"   {formed_text}")
    print()

    # 6. Neural Interface Borders
    print("6. NEURAL INTERFACE BORDER:")
    top, bottom = generator.generate_interface_border(60)
    print(f"   {top}")
    print(f"   ║{' ' * 58}║")
    print(f"   ║     NEURAL INTERFACE STATUS: ACTIVE{' ' * 17}║")
    print(f"   ║     CONSCIOUSNESS LINK: ESTABLISHED{' ' * 13}║")
    print(f"   ║{' ' * 58}║")
    print(f"   {bottom}")


if __name__ == "__main__":
    demo_neural_patterns()
