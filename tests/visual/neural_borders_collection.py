#!/usr/bin/env python3
"""
Collection of Neural Interface Border Designs
10 different neural-themed border patterns
"""

import random


class NeuralBorderCollection:
    def __init__(self):
        self.colors = {
            "CYAN": "\033[36m",
            "BRIGHT_CYAN": "\033[96m",
            "BLUE": "\033[34m",
            "BRIGHT_BLUE": "\033[94m",
            "GREEN": "\033[32m",
            "BRIGHT_GREEN": "\033[92m",
            "YELLOW": "\033[33m",
            "BRIGHT_YELLOW": "\033[93m",
            "MAGENTA": "\033[35m",
            "BRIGHT_MAGENTA": "\033[95m",
            "RED": "\033[31m",
            "BRIGHT_RED": "\033[91m",
            "WHITE": "\033[37m",
            "BRIGHT_WHITE": "\033[97m",
            "DIM": "\033[2m",
            "RESET": "\033[0m",
        }

        # Neural symbol sets for different border styles
        self.neural_sets = {
            "synaptic": ["◊", "◇", "◈"],
            "processing": ["⟡", "⟢", "⟣", "⟤"],
            "data_flow": ["▪", "▫", "▬", "▭", "▮", "▯"],
            "activation": ["◐", "◑", "◒", "◓"],
            "pulse": ["⦁", "⦂", "⦃", "⦄"],
            "wave": ["∿", "∼", "≈", "≋"],
            "matrix": ["⣀", "⣄", "⣤", "⣦", "⣶", "⣷", "⣿"],
            "neural_nodes": ["○", "●", "◯", "⚬", "⚫"],
            "connections": ["╱", "╲", "╳", "╴", "╶", "╷", "╵"],
            "quantum": ["⊕", "⊗", "⊙", "⊚", "⊛", "⊜"],
        }

    def border_1_synaptic_activity(
        self, width=70, title="NEURAL INTERFACE STATUS: ACTIVE"
    ):
        """Classic synaptic activity border"""
        neural_chars = self.neural_sets["synaptic"]

        # Top border with synaptic activity
        top = f"{self.colors['CYAN']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.12:
                char = random.choice(neural_chars)
                top += f"{self.colors['BRIGHT_CYAN']}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['CYAN']}═{self.colors['RESET']}"
        top += f"{self.colors['CYAN']}╗{self.colors['RESET']}"

        # Bottom border
        bottom = f"{self.colors['CYAN']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.12:
                char = random.choice(neural_chars)
                bottom += f"{self.colors['BRIGHT_CYAN']}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['CYAN']}═{self.colors['RESET']}"
        bottom += f"{self.colors['CYAN']}╝{self.colors['RESET']}"

        # Content
        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_2_processing_nodes(
        self, width=70, title="CONSCIOUSNESS TRANSFER PROTOCOL 4.0"
    ):
        """Processing nodes border with computational symbols"""
        neural_chars = self.neural_sets["processing"]

        top = f"{self.colors['GREEN']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.08:
                char = random.choice(neural_chars)
                top += f"{self.colors['BRIGHT_GREEN']}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['GREEN']}═{self.colors['RESET']}"
        top += f"{self.colors['GREEN']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['GREEN']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.08:
                char = random.choice(neural_chars)
                bottom += f"{self.colors['BRIGHT_GREEN']}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['GREEN']}═{self.colors['RESET']}"
        bottom += f"{self.colors['GREEN']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_3_data_stream(self, width=70, title="DATA STREAM ANALYSIS"):
        """Data flow border with streaming patterns"""
        neural_chars = self.neural_sets["data_flow"]

        top = f"{self.colors['BLUE']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.15:
                char = random.choice(neural_chars)
                color = (
                    self.colors["BRIGHT_BLUE"]
                    if random.random() < 0.6
                    else self.colors["CYAN"]
                )
                top += f"{color}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['BLUE']}═{self.colors['RESET']}"
        top += f"{self.colors['BLUE']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['BLUE']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.15:
                char = random.choice(neural_chars)
                color = (
                    self.colors["BRIGHT_BLUE"]
                    if random.random() < 0.6
                    else self.colors["CYAN"]
                )
                bottom += f"{color}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['BLUE']}═{self.colors['RESET']}"
        bottom += f"{self.colors['BLUE']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_4_neural_pulse(self, width=70, title="NEURAL PULSE SYNCHRONIZATION"):
        """Neural pulse border with rhythmic patterns"""
        neural_chars = self.neural_sets["pulse"]

        top = f"{self.colors['MAGENTA']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            # Create pulse pattern
            pulse_intensity = abs((i % 10) - 5) / 5.0
            if random.random() < pulse_intensity * 0.3:
                char = random.choice(neural_chars)
                top += f"{self.colors['BRIGHT_MAGENTA']}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['MAGENTA']}═{self.colors['RESET']}"
        top += f"{self.colors['MAGENTA']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['MAGENTA']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            pulse_intensity = abs(((i + 5) % 10) - 5) / 5.0  # Offset pattern
            if random.random() < pulse_intensity * 0.3:
                char = random.choice(neural_chars)
                bottom += f"{self.colors['BRIGHT_MAGENTA']}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['MAGENTA']}═{self.colors['RESET']}"
        bottom += f"{self.colors['MAGENTA']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_5_memory_matrix(self, width=70, title="MEMORY CORE ACCESS"):
        """Memory matrix border with dense braille patterns"""
        neural_chars = self.neural_sets["matrix"]

        top = f"{self.colors['YELLOW']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.18:
                char = random.choice(neural_chars)
                color = (
                    self.colors["BRIGHT_YELLOW"]
                    if random.random() < 0.7
                    else self.colors["YELLOW"]
                )
                top += f"{color}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['YELLOW']}═{self.colors['RESET']}"
        top += f"{self.colors['YELLOW']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['YELLOW']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.18:
                char = random.choice(neural_chars)
                color = (
                    self.colors["BRIGHT_YELLOW"]
                    if random.random() < 0.7
                    else self.colors["YELLOW"]
                )
                bottom += f"{color}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['YELLOW']}═{self.colors['RESET']}"
        bottom += f"{self.colors['YELLOW']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_6_wave_interference(self, width=70, title="CONSCIOUSNESS WAVE ANALYSIS"):
        """Wave interference patterns border"""
        neural_chars = self.neural_sets["wave"]

        top = f"{self.colors['CYAN']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            # Wave interference pattern
            wave1 = (i * 0.5) % 8
            wave2 = (i * 0.7) % 6
            interference = abs(wave1 - wave2)

            if interference < 2 and random.random() < 0.4:
                char = random.choice(neural_chars)
                top += f"{self.colors['BRIGHT_CYAN']}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['CYAN']}═{self.colors['RESET']}"
        top += f"{self.colors['CYAN']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['CYAN']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            wave1 = ((i + 3) * 0.5) % 8  # Phase shifted
            wave2 = ((i + 3) * 0.7) % 6
            interference = abs(wave1 - wave2)

            if interference < 2 and random.random() < 0.4:
                char = random.choice(neural_chars)
                bottom += f"{self.colors['BRIGHT_CYAN']}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['CYAN']}═{self.colors['RESET']}"
        bottom += f"{self.colors['CYAN']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_7_activation_cascade(self, width=70, title="NEURAL ACTIVATION CASCADE"):
        """Activation cascade border with progressive intensity"""
        neural_chars = self.neural_sets["activation"]

        top = f"{self.colors['RED']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            # Cascade effect from edges to center
            distance_from_edge = min(i, width - 3 - i)
            activation_level = min(distance_from_edge / 10.0, 1.0)

            if random.random() < activation_level * 0.5:
                char = random.choice(neural_chars)
                intensity = (
                    self.colors["BRIGHT_RED"]
                    if activation_level > 0.5
                    else self.colors["RED"]
                )
                top += f"{intensity}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['RED']}═{self.colors['RESET']}"
        top += f"{self.colors['RED']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['RED']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            distance_from_edge = min(i, width - 3 - i)
            activation_level = min(distance_from_edge / 10.0, 1.0)

            if random.random() < activation_level * 0.5:
                char = random.choice(neural_chars)
                intensity = (
                    self.colors["BRIGHT_RED"]
                    if activation_level > 0.5
                    else self.colors["RED"]
                )
                bottom += f"{intensity}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['RED']}═{self.colors['RESET']}"
        bottom += f"{self.colors['RED']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_8_neural_network(self, width=70, title="DISTRIBUTED NEURAL NETWORK"):
        """Neural network connections border"""
        neural_chars = self.neural_sets["connections"]
        nodes = self.neural_sets["neural_nodes"]

        top = f"{self.colors['WHITE']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.15:
                if random.random() < 0.6:
                    char = random.choice(neural_chars)
                    top += f"{self.colors['BRIGHT_WHITE']}{char}{self.colors['RESET']}"
                else:
                    char = random.choice(nodes)
                    color = (
                        self.colors["CYAN"]
                        if random.random() < 0.5
                        else self.colors["BRIGHT_CYAN"]
                    )
                    top += f"{color}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['WHITE']}═{self.colors['RESET']}"
        top += f"{self.colors['WHITE']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['WHITE']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.15:
                if random.random() < 0.6:
                    char = random.choice(neural_chars)
                    bottom += (
                        f"{self.colors['BRIGHT_WHITE']}{char}{self.colors['RESET']}"
                    )
                else:
                    char = random.choice(nodes)
                    color = (
                        self.colors["CYAN"]
                        if random.random() < 0.5
                        else self.colors["BRIGHT_CYAN"]
                    )
                    bottom += f"{color}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['WHITE']}═{self.colors['RESET']}"
        bottom += f"{self.colors['WHITE']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_9_quantum_entanglement(
        self, width=70, title="QUANTUM CONSCIOUSNESS LINK"
    ):
        """Quantum entanglement border with quantum operators"""
        neural_chars = self.neural_sets["quantum"]

        top = f"{self.colors['BRIGHT_MAGENTA']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            # Quantum correlation pattern
            entanglement_factor = (i * 3) % 7
            if entanglement_factor < 2 and random.random() < 0.25:
                char = random.choice(neural_chars)
                colors = [
                    self.colors["BRIGHT_MAGENTA"],
                    self.colors["BRIGHT_CYAN"],
                    self.colors["BRIGHT_YELLOW"],
                ]
                color = random.choice(colors)
                top += f"{color}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['BRIGHT_MAGENTA']}═{self.colors['RESET']}"
        top += f"{self.colors['BRIGHT_MAGENTA']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['BRIGHT_MAGENTA']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            entanglement_factor = ((i + 4) * 3) % 7  # Quantum entangled offset
            if entanglement_factor < 2 and random.random() < 0.25:
                char = random.choice(neural_chars)
                colors = [
                    self.colors["BRIGHT_MAGENTA"],
                    self.colors["BRIGHT_CYAN"],
                    self.colors["BRIGHT_YELLOW"],
                ]
                color = random.choice(colors)
                bottom += f"{color}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['BRIGHT_MAGENTA']}═{self.colors['RESET']}"
        bottom += f"{self.colors['BRIGHT_MAGENTA']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]

    def border_10_mixed_neural(self, width=70, title="KOLLABOR.AI MULTI-AGENT SYSTEM"):
        """Mixed neural patterns - combination of multiple styles"""
        all_chars = []
        for char_set in self.neural_sets.values():
            all_chars.extend(char_set)

        colors = [
            self.colors["BRIGHT_CYAN"],
            self.colors["BRIGHT_GREEN"],
            self.colors["BRIGHT_YELLOW"],
            self.colors["BRIGHT_MAGENTA"],
            self.colors["BRIGHT_BLUE"],
            self.colors["CYAN"],
        ]

        top = f"{self.colors['BRIGHT_WHITE']}╔{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.20:
                char = random.choice(all_chars)
                color = random.choice(colors)
                top += f"{color}{char}{self.colors['RESET']}"
            else:
                top += f"{self.colors['BRIGHT_WHITE']}═{self.colors['RESET']}"
        top += f"{self.colors['BRIGHT_WHITE']}╗{self.colors['RESET']}"

        bottom = f"{self.colors['BRIGHT_WHITE']}╚{self.colors['RESET']}"
        for i in range(width - 2):
            if random.random() < 0.20:
                char = random.choice(all_chars)
                color = random.choice(colors)
                bottom += f"{color}{char}{self.colors['RESET']}"
            else:
                bottom += f"{self.colors['BRIGHT_WHITE']}═{self.colors['RESET']}"
        bottom += f"{self.colors['BRIGHT_WHITE']}╝{self.colors['RESET']}"

        padding = (width - len(title) - 4) // 2
        content = f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║"

        return [top, content, bottom]


def demo_all_borders():
    """Demonstrate all 10 neural interface borders"""
    collector = NeuralBorderCollection()

    print("🧠 NEURAL INTERFACE BORDER COLLECTION 🧠\n")

    borders = [
        ("1. SYNAPTIC ACTIVITY BORDER", collector.border_1_synaptic_activity),
        ("2. PROCESSING NODES BORDER", collector.border_2_processing_nodes),
        ("3. DATA STREAM BORDER", collector.border_3_data_stream),
        ("4. NEURAL PULSE BORDER", collector.border_4_neural_pulse),
        ("5. MEMORY MATRIX BORDER", collector.border_5_memory_matrix),
        ("6. WAVE INTERFERENCE BORDER", collector.border_6_wave_interference),
        ("7. ACTIVATION CASCADE BORDER", collector.border_7_activation_cascade),
        ("8. NEURAL NETWORK BORDER", collector.border_8_neural_network),
        ("9. QUANTUM ENTANGLEMENT BORDER", collector.border_9_quantum_entanglement),
        ("10. MIXED NEURAL PATTERNS BORDER", collector.border_10_mixed_neural),
    ]

    for name, border_func in borders:
        print(name)
        border_lines = border_func()
        for line in border_lines:
            print(f"   {line}")
        print()


if __name__ == "__main__":
    demo_all_borders()
