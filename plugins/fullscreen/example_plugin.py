"""Enhanced example full-screen plugin demonstrating all framework capabilities.

This comprehensive plugin showcases:
- Table of contents navigation
- Framework statistics and info
- Drawing primitives (borders, shapes, progress, spinners)
- Color showcase (256 colors, gradients)
- Advanced animations (easing, particles, physics)
- Interactive components (text input, checkboxes, sliders)
- Calculator mini-app
- Todo list mini-app
- Conway's Game of Life
- Performance comparison
- Code examples with syntax highlighting
- Resources and documentation
"""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import math
import random
import time
from dataclasses import dataclass
from typing import List

from kollabor_tui.fullscreen import FullScreenPlugin
from kollabor_tui.fullscreen.components.animation import (
    AnimationFramework,
)
from kollabor_tui.fullscreen.components.drawing import DrawingPrimitives
from kollabor_tui.fullscreen.plugin import PluginMetadata
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette, GradientRenderer


@dataclass
class TodoItem:
    """Todo list item."""

    text: str
    completed: bool = False


@dataclass
class PageInfo:
    """Page metadata for navigation."""

    title: str
    description: str
    icon: str


class SimpleTextInput:
    """Lightweight text input handler for fullscreen plugins."""

    def __init__(self, initial_value: str = "", max_length: int = 50):
        """Initialize text input.

        Args:
            initial_value: Starting text value
            max_length: Maximum input length
        """
        self.value = initial_value
        self.cursor_pos = len(initial_value)
        self.max_length = max_length

    def handle_key(self, key_press: KeyPress) -> bool:
        """Handle keyboard input for text editing.

        Args:
            key_press: Key press event

        Returns:
            True if key was handled
        """
        # Backspace
        if key_press.name == "Backspace" or key_press.char in ["\x7f", "\x08"]:
            if self.cursor_pos > 0:
                self.value = (
                    self.value[: self.cursor_pos - 1] + self.value[self.cursor_pos :]
                )
                self.cursor_pos -= 1
            return True

        # Delete
        elif key_press.name == "Delete":
            if self.cursor_pos < len(self.value):
                self.value = (
                    self.value[: self.cursor_pos] + self.value[self.cursor_pos + 1 :]
                )
            return True

        # Arrow left
        elif key_press.name == "ArrowLeft":
            self.cursor_pos = max(0, self.cursor_pos - 1)
            return True

        # Arrow right
        elif key_press.name == "ArrowRight":
            self.cursor_pos = min(len(self.value), self.cursor_pos + 1)
            return True

        # Home
        elif key_press.name == "Home":
            self.cursor_pos = 0
            return True

        # End
        elif key_press.name == "End":
            self.cursor_pos = len(self.value)
            return True

        # Printable character
        elif (
            key_press.char
            and key_press.char.isprintable()
            and len(self.value) < self.max_length
        ):
            self.value = (
                self.value[: self.cursor_pos]
                + key_press.char
                + self.value[self.cursor_pos :]
            )
            self.cursor_pos += 1
            return True

        return False

    def render_with_cursor(self) -> str:
        """Render text with cursor indicator.

        Returns:
            Text with cursor shown as underscore
        """
        if self.cursor_pos < len(self.value):
            return self.value[: self.cursor_pos] + "▌" + self.value[self.cursor_pos :]
        else:
            return self.value + "▌"


class EnhancedExamplePlugin(FullScreenPlugin):
    """Comprehensive example plugin showcasing all framework features.

    Pages:
    0. Table of Contents
    1. Framework Stats & Info
    2. Drawing Primitives
    3. Color Showcase
    4. Animations
    5. Interactive Components
    6. Calculator
    7. Todo List
    8. Game of Life
    9. Performance Demo
    10. Code Examples
    11. Resources & Help
    """

    def __init__(self):
        """Initialize the enhanced example plugin."""
        metadata = PluginMetadata(
            name="example",
            description="Comprehensive framework showcase with 12 interactive pages",
            version="2.0.0",
            author="Kollabor Framework",
            category="demo",
            icon="🎯",
            aliases=["demo", "showcase"],
        )
        super().__init__(metadata)

        # Moderate FPS for smooth animations without excessive CPU
        self.target_fps = 30.0

        # Navigation
        self.current_page = 0
        self.total_pages = 12
        self.frame_count = 0

        # Page definitions
        self.pages = [
            PageInfo("Table of Contents", "Navigate to any page", "📋"),
            PageInfo("Framework Stats", "Live performance metrics", "📊"),
            PageInfo("Drawing Primitives", "Borders, shapes, progress bars", "🎨"),
            PageInfo("Color Showcase", "256 colors and gradients", "🌈"),
            PageInfo("Animations", "Easing functions and particle effects", "✨"),
            PageInfo("Interactive Components", "Text input, checkboxes, sliders", "🎮"),
            PageInfo("Calculator", "Functional number pad calculator", "🔢"),
            PageInfo("Todo List", "Task management with add/remove", "📝"),
            PageInfo("Game of Life", "Conway's cellular automaton", "🧬"),
            PageInfo("Performance Demo", "FPS comparison and optimization", "⚡"),
            PageInfo("Code Examples", "Copy-ready plugin templates", "💻"),
            PageInfo("Resources & Help", "Documentation and key bindings", "📚"),
        ]

        # Animation framework
        self.animation_framework = AnimationFramework()
        self.demo_animations = {}

        # Calculator state
        self.calc_display = "0"
        self.calc_operator = None
        self.calc_operand = None
        self.calc_new_number = True

        # Todo list state
        self.todos: List[TodoItem] = [
            TodoItem("Try the calculator (page 6)", False),
            TodoItem("Explore Game of Life (page 8)", False),
            TodoItem("View code examples (page 10)", False),
        ]
        self.todo_input = SimpleTextInput()
        self.todo_editing = False

        # Game of Life state
        self.life_grid: List[List[bool]] = []
        self.life_running = False
        self.life_generation = 0
        self.life_last_update = 0.0
        self.life_speed = 0.2  # seconds per generation

        # Interactive components state
        self.text_input = SimpleTextInput("Hello, World!")
        self.checkbox_states = [True, False, True]
        self.slider_value = 50
        self.active_input = 0  # Which component is focused

        # Performance demo state
        self.perf_mode = 0  # 0=buffered, 1=unbuffered simulation
        self.perf_fps_history: List[float] = []

    async def initialize(self, renderer) -> bool:
        """Initialize the example plugin."""
        if not await super().initialize(renderer):
            return False

        # Setup initial animations
        current_time = _get_loop().time()
        self.demo_animations["title_fade"] = self.animation_framework.fade_in(
            1.5, current_time
        )
        self.demo_animations["bounce"] = self.animation_framework.bounce_in(
            1.0, current_time + 0.3
        )

        # Initialize Game of Life grid
        self._init_life_grid()

        return True

    async def on_start(self):
        """Called when plugin starts."""
        await super().on_start()
        self.perf_fps_history = []

    async def render_frame(self, delta_time: float) -> bool:
        """Render the current page."""
        if not self.renderer:
            return False

        self.frame_count += 1

        # Update FPS history for performance page
        if len(self.perf_fps_history) > 100:
            self.perf_fps_history.pop(0)
        current_fps = 1.0 / delta_time if delta_time > 0 else 0
        self.perf_fps_history.append(current_fps)

        # Clear screen
        self.renderer.clear_screen()
        width, height = self.renderer.get_terminal_size()

        # Render appropriate page
        if self.current_page == 0:
            self._render_toc_page(width, height)
        elif self.current_page == 1:
            self._render_stats_page(width, height)
        elif self.current_page == 2:
            self._render_drawing_page(width, height)
        elif self.current_page == 3:
            self._render_color_page(width, height)
        elif self.current_page == 4:
            self._render_animation_page(width, height)
        elif self.current_page == 5:
            self._render_interactive_page(width, height)
        elif self.current_page == 6:
            self._render_calculator_page(width, height)
        elif self.current_page == 7:
            self._render_todo_page(width, height)
        elif self.current_page == 8:
            await self._render_life_page(width, height, delta_time)
        elif self.current_page == 9:
            self._render_performance_page(width, height)
        elif self.current_page == 10:
            self._render_code_page(width, height)
        elif self.current_page == 11:
            self._render_resources_page(width, height)

        # Render common navigation footer
        self._render_navigation(width, height)

        return True

    def _render_toc_page(self, width: int, height: int):
        """Render table of contents with grid layout."""
        if self.renderer is None:
            return
        # Title
        title = "🎯 FRAMEWORK SHOWCASE - TABLE OF CONTENTS"
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, title, ColorPalette.BRIGHT_CYAN
        )

        # Subtitle
        subtitle = "Press number key (0-9) or arrow keys to navigate"
        DrawingPrimitives.draw_text_centered(
            self.renderer, 3, subtitle, ColorPalette.DIM_WHITE
        )

        # Draw pages in grid (3 columns)
        start_y = 6
        col_width = width // 3

        for idx, page in enumerate(self.pages):
            row = idx // 3
            col = idx % 3

            x = col * col_width + 5
            y = start_y + (row * 4)

            # Page number and icon
            num_str = f"[{idx}]" if idx < 10 else f"[{chr(65 + idx - 10)}]"
            self.renderer.write_at(x, y, num_str, ColorPalette.BRIGHT_YELLOW)
            self.renderer.write_at(x + 5, y, page.icon, ColorPalette.WHITE)

            # Page title
            self.renderer.write_at(x + 7, y, page.title[:25], ColorPalette.BRIGHT_GREEN)

            # Page description
            desc = page.description[:30]
            self.renderer.write_at(x + 3, y + 1, desc, ColorPalette.DIM_WHITE)

    def _render_stats_page(self, width: int, height: int):
        """Render framework statistics and terminal info."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "📊 FRAMEWORK STATISTICS", ColorPalette.BRIGHT_MAGENTA
        )

        # Calculate stats
        runtime = (
            _get_loop().time() - self.start_time if self.running else 0
        )
        avg_fps = (
            sum(self.perf_fps_history[-30:]) / len(self.perf_fps_history[-30:])
            if self.perf_fps_history
            else 0
        )

        # Draw stats in columns
        left_x = 10
        right_x = width // 2 + 5
        y = 5

        # Left column - Plugin stats
        self.renderer.write_at(
            left_x, y, "PLUGIN INFORMATION", ColorPalette.BRIGHT_CYAN
        )
        y += 2
        self.renderer.write_at(
            left_x, y, f"Name:        {self.metadata.name}", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            left_x, y, f"Version:     {self.metadata.version}", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            left_x, y, f"Category:    {self.metadata.category}", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            left_x, y, f"Target FPS:  {self.target_fps:.1f}", ColorPalette.WHITE
        )
        y += 2

        self.renderer.write_at(
            left_x, y, "RUNTIME STATISTICS", ColorPalette.BRIGHT_GREEN
        )
        y += 2
        self.renderer.write_at(
            left_x, y, f"Uptime:      {runtime:.1f}s", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            left_x, y, f"Frames:      {self.frame_count:,}", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            left_x, y, f"Current FPS: {avg_fps:.1f}", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            left_x,
            y,
            f"Page:        {self.current_page + 1}/{self.total_pages}",
            ColorPalette.WHITE,
        )

        # Right column - Terminal info
        y = 5
        self.renderer.write_at(
            right_x, y, "TERMINAL INFORMATION", ColorPalette.BRIGHT_YELLOW
        )
        y += 2
        self.renderer.write_at(
            right_x, y, f"Size:        {width} x {height}", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            right_x, y, "Colors:      256-color capable", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            right_x, y, "Unicode:     ✓ Supported", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(right_x, y, "Buffering:   ✓ Enabled", ColorPalette.WHITE)
        y += 2

        self.renderer.write_at(
            right_x, y, "FRAMEWORK FEATURES", ColorPalette.BRIGHT_CYAN
        )
        y += 2
        features = [
            "✓ Frame buffering (flicker-free)",
            "✓ Adaptive FPS control",
            "✓ Modal system integration",
            "✓ Drawing primitives library",
            "✓ Animation framework",
            "✓ Event-driven architecture",
        ]
        for feature in features:
            self.renderer.write_at(right_x, y, feature, ColorPalette.GREEN)
            y += 1

        # Live FPS graph
        graph_y = height - 10
        self.renderer.write_at(
            10, graph_y, "FPS GRAPH (last 50 frames)", ColorPalette.DIM_WHITE
        )
        if len(self.perf_fps_history) > 1:
            self._draw_fps_graph(15, graph_y + 1, 50, 5, self.perf_fps_history[-50:])

    def _draw_fps_graph(
        self, x: int, y: int, width: int, height: int, data: List[float]
    ):
        """Draw a simple bar graph of FPS data."""
        if self.renderer is None:
            return
        if not data:
            return

        max_fps = max(data) if data else 60
        min_fps = min(data) if data else 0

        # Draw bars
        for i, fps in enumerate(data[-width:]):
            bar_x = x + i
            normalized = (
                (fps - min_fps) / (max_fps - min_fps) if max_fps > min_fps else 0.5
            )
            bar_height = int(normalized * height)

            for j in range(bar_height):
                bar_y = y + height - 1 - j
                if bar_y >= y:
                    char = "█" if j < bar_height - 1 else "▀"
                    color = (
                        ColorPalette.BRIGHT_GREEN if fps >= 25 else ColorPalette.YELLOW
                    )
                    self.renderer.write_at(bar_x, bar_y, char, color)

    def _render_drawing_page(self, width: int, height: int):
        """Render drawing primitives demonstration."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "🎨 DRAWING PRIMITIVES", ColorPalette.BRIGHT_MAGENTA
        )

        center_x, center_y = width // 2, height // 2

        # Border box
        DrawingPrimitives.draw_border(
            self.renderer, center_x - 25, center_y - 8, 50, 16, color=ColorPalette.CYAN
        )

        # Progress bar (animated)
        progress = (self.frame_count % 100) / 100.0
        DrawingPrimitives.draw_progress_bar(
            self.renderer,
            center_x - 20,
            center_y - 5,
            40,
            progress,
            color=ColorPalette.GREEN,
        )
        self.renderer.write_at(
            center_x - 10, center_y - 6, f"Progress: {progress:.0%}", ColorPalette.WHITE
        )

        # Spinner
        DrawingPrimitives.draw_spinner(
            self.renderer,
            center_x - 2,
            center_y - 2,
            self.frame_count // 3,
            color=ColorPalette.BRIGHT_BLUE,
        )
        self.renderer.write_at(
            center_x + 2, center_y - 2, "Loading...", ColorPalette.WHITE
        )

        # Circle
        DrawingPrimitives.draw_circle_points(
            self.renderer, center_x, center_y + 3, 6, char="●", color=ColorPalette.RED
        )

        # Wave animation
        wave_phase = self.frame_count * 0.1
        DrawingPrimitives.draw_wave(
            self.renderer,
            height - 6,
            2,
            0.3,
            wave_phase,
            char="~",
            color=ColorPalette.BLUE,
        )

        # Text samples
        self.renderer.write_at(5, 5, "Box drawing: ╔═╗ ║ ╚═╝", ColorPalette.WHITE)
        self.renderer.write_at(5, 6, "Blocks: █ ▓ ▒ ░ ▀ ▄ ▌ ▐", ColorPalette.WHITE)
        self.renderer.write_at(5, 7, "Arrows: ← ↑ → ↓ ↔ ↕", ColorPalette.WHITE)
        self.renderer.write_at(5, 8, "Symbols: ✓ ✗ ★ ● ○ ◆ ◇", ColorPalette.WHITE)

    def _render_color_page(self, width: int, height: int):
        """Render 256-color palette and gradients."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "🌈 COLOR SHOWCASE", ColorPalette.BRIGHT_MAGENTA
        )

        # 256-color grid (basic 16 + 216 color cube + 24 grayscale)
        y = 5
        self.renderer.write_at(10, y, "256 COLOR PALETTE", ColorPalette.WHITE)
        y += 2

        # Draw color blocks in grid
        for row in range(16):
            x = 10
            for col in range(16):
                color_num = row * 16 + col
                # ANSI 256-color escape code
                color_code = f"\033[48;5;{color_num}m"
                self.renderer.write_at(
                    x + col * 2, y + row, f"{color_code}  \033[0m", ""
                )

        # Gradient examples
        grad_y = 5
        grad_x = width // 2 + 10
        self.renderer.write_at(grad_x, grad_y, "GRADIENT EXAMPLES", ColorPalette.WHITE)
        grad_y += 2

        # Horizontal gradient
        gradient_text = "Horizontal Gradient Demo"
        colored = GradientRenderer.apply_dim_scheme_gradient(gradient_text)
        self.renderer.write_at(grad_x, grad_y, colored, "")
        grad_y += 2

        # Color names
        colors = [
            ("RED", ColorPalette.RED),
            ("GREEN", ColorPalette.GREEN),
            ("BLUE", ColorPalette.BLUE),
            ("YELLOW", ColorPalette.YELLOW),
            ("MAGENTA", ColorPalette.MAGENTA),
            ("CYAN", ColorPalette.CYAN),
            ("BRIGHT_WHITE", ColorPalette.BRIGHT_WHITE),
            ("DIM_GREY", ColorPalette.DIM_GREY),
        ]

        for name, color in colors:
            self.renderer.write_at(grad_x, grad_y, f"{name:15} ████████", color)
            grad_y += 1

    def _render_animation_page(self, width: int, height: int):
        """Render advanced animation demonstrations."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "✨ ANIMATION SHOWCASE", ColorPalette.BRIGHT_YELLOW
        )

        current_time = _get_loop().time()
        center_x, center_y = width // 2, height // 2

        # Pulsing circle (sine wave)
        pulse_size = 5 + int(3 * math.sin(current_time * 2))
        for r in range(1, pulse_size):
            DrawingPrimitives.draw_circle_points(
                self.renderer, center_x, center_y, r, char="○", color=ColorPalette.GREEN
            )

        # Sliding text with easing
        slide_x = int(20 * math.sin(current_time * 1.5))
        self.renderer.write_at(
            center_x + slide_x, center_y - 7, "← Sliding →", ColorPalette.CYAN
        )

        # Particle effect (rising dots)
        for i in range(20):
            particle_age = (current_time * 2 + i * 0.5) % 3
            particle_y = center_y + 10 - int(particle_age * 5)
            particle_x = center_x - 10 + i * 2
            if particle_y < height - 3 and particle_y > center_y - 10:
                alpha = 1.0 - (particle_age / 3.0)
                char = "●" if alpha > 0.5 else "○"
                self.renderer.write_at(
                    particle_x, particle_y, char, ColorPalette.BRIGHT_BLUE
                )

        # Bouncing ball
        bounce_y = center_y + 5 + int(3 * abs(math.sin(current_time * 3)))
        self.renderer.write_at(center_x + 15, bounce_y, "●", ColorPalette.RED)

        # Rotating spinner variations
        spinners = ["⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "◐◓◑◒", "▁▃▄▅▆▇█▇▆▅▄▃", "←↖↑↗→↘↓↙"]
        y = 5
        for i, spinner in enumerate(spinners):
            idx = (self.frame_count // 2) % len(spinner)
            self.renderer.write_at(
                10,
                y + i * 2,
                f"Spinner {i+1}: {spinner[idx]}",
                ColorPalette.BRIGHT_GREEN,
            )

        # Easing function demo
        easing_y = center_y - 10
        easing_x = 10
        self.renderer.write_at(
            easing_x, easing_y, "EASING FUNCTIONS:", ColorPalette.WHITE
        )
        easing_y += 1

        # Linear
        t = (current_time % 2) / 2
        pos = int(t * 30)
        self.renderer.write_at(easing_x, easing_y, "Linear:   ", ColorPalette.DIM_WHITE)
        self.renderer.write_at(easing_x + 10 + pos, easing_y, "●", ColorPalette.YELLOW)
        easing_y += 1

        # Ease in/out
        ease_t = -2 * t * t * t + 3 * t * t  # Smooth step
        ease_pos = int(ease_t * 30)
        self.renderer.write_at(easing_x, easing_y, "Smooth:   ", ColorPalette.DIM_WHITE)
        self.renderer.write_at(
            easing_x + 10 + ease_pos, easing_y, "●", ColorPalette.GREEN
        )

    def _render_interactive_page(self, width: int, height: int):
        """Render interactive components demonstration."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "🎮 INTERACTIVE COMPONENTS", ColorPalette.BRIGHT_GREEN
        )

        y = 6
        x = 10

        # Instructions
        self.renderer.write_at(
            x,
            y,
            "Use Tab to switch between components, arrow keys to interact",
            ColorPalette.DIM_WHITE,
        )
        y += 3

        # Text input
        is_focused = self.active_input == 0
        label_color = ColorPalette.BRIGHT_YELLOW if is_focused else ColorPalette.WHITE
        self.renderer.write_at(x, y, "Text Input:", label_color)
        input_display = (
            self.text_input.render_with_cursor()
            if is_focused
            else self.text_input.value
        )
        input_color = (
            ColorPalette.BRIGHT_WHITE if is_focused else ColorPalette.DIM_WHITE
        )
        self.renderer.write_at(x + 15, y, f"[{input_display}]", input_color)
        y += 3

        # Checkboxes
        is_focused = self.active_input == 1
        label_color = ColorPalette.BRIGHT_YELLOW if is_focused else ColorPalette.WHITE
        self.renderer.write_at(x, y, "Checkboxes:", label_color)
        y += 1
        for i, (label, checked) in enumerate(
            [
                ("Option A", self.checkbox_states[0]),
                ("Option B", self.checkbox_states[1]),
                ("Option C", self.checkbox_states[2]),
            ]
        ):
            box = "[✓]" if checked else "[ ]"
            color = ColorPalette.BRIGHT_GREEN if checked else ColorPalette.DIM_WHITE
            if is_focused and i == 0:
                color = ColorPalette.BRIGHT_YELLOW
            self.renderer.write_at(x + 2, y, f"{box} {label}", color)
            y += 1
        y += 2

        # Slider
        is_focused = self.active_input == 2
        label_color = ColorPalette.BRIGHT_YELLOW if is_focused else ColorPalette.WHITE
        self.renderer.write_at(x, y, "Slider:", label_color)
        slider_width = 40
        filled = int((self.slider_value / 100.0) * slider_width)
        slider_bar = "█" * filled + "░" * (slider_width - filled)
        self.renderer.write_at(
            x + 10, y, f"[{slider_bar}] {self.slider_value}%", ColorPalette.CYAN
        )
        y += 3

        # Current value display
        self.renderer.write_at(
            x, y, f"Active: Component {self.active_input + 1}/3", ColorPalette.DIM_WHITE
        )
        y += 1
        self.renderer.write_at(
            x, y, f"Text: '{self.text_input.value}'", ColorPalette.DIM_WHITE
        )
        y += 1
        self.renderer.write_at(
            x,
            y,
            f"Checks: {sum(self.checkbox_states)}/3 selected",
            ColorPalette.DIM_WHITE,
        )
        y += 1
        self.renderer.write_at(
            x, y, f"Slider: {self.slider_value}%", ColorPalette.DIM_WHITE
        )

    def _render_calculator_page(self, width: int, height: int):
        """Render functional calculator interface."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "🔢 CALCULATOR", ColorPalette.BRIGHT_CYAN
        )

        center_x, center_y = width // 2, height // 2 - 3

        # Display
        display_width = 24
        display_x = center_x - display_width // 2
        display_y = center_y - 8

        DrawingPrimitives.draw_border(
            self.renderer,
            display_x - 2,
            display_y - 1,
            display_width + 4,
            3,
            color=ColorPalette.CYAN,
        )
        display_text = self.calc_display[: display_width - 2].rjust(display_width - 2)
        self.renderer.write_at(
            display_x, display_y, display_text, ColorPalette.BRIGHT_WHITE
        )

        # Number pad
        pad_x = center_x - 10
        pad_y = center_y - 4

        buttons = [
            ["7", "8", "9", "/"],
            ["4", "5", "6", "*"],
            ["1", "2", "3", "-"],
            ["C", "0", "=", "+"],
        ]

        for row_idx, row in enumerate(buttons):
            for col_idx, btn in enumerate(row):
                btn_x = pad_x + col_idx * 5
                btn_y = pad_y + row_idx * 2

                # Button styling
                if btn in "0123456789":
                    color = ColorPalette.BRIGHT_WHITE
                elif btn in "+-*/":
                    color = ColorPalette.BRIGHT_YELLOW
                elif btn == "=":
                    color = ColorPalette.BRIGHT_GREEN
                else:
                    color = ColorPalette.BRIGHT_RED

                self.renderer.write_at(btn_x, btn_y, f"[{btn}]", color)

        # Instructions
        self.renderer.write_at(
            center_x - 15,
            height - 8,
            "Use number keys and operators: + - * /",
            ColorPalette.DIM_WHITE,
        )
        self.renderer.write_at(
            center_x - 15,
            height - 7,
            "Press = to calculate, C to clear",
            ColorPalette.DIM_WHITE,
        )

    def _render_todo_page(self, width: int, height: int):
        """Render todo list mini-app."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "📝 TODO LIST", ColorPalette.BRIGHT_GREEN
        )

        y = 5
        x = 10

        # Instructions
        if not self.todo_editing:
            self.renderer.write_at(
                x,
                y,
                "Press 'a' to add, Space to toggle, 'd' to delete selected",
                ColorPalette.DIM_WHITE,
            )
        else:
            self.renderer.write_at(
                x,
                y,
                "Type task and press Enter to add (Esc to cancel)",
                ColorPalette.BRIGHT_YELLOW,
            )
        y += 3

        # Add input (if editing)
        if self.todo_editing:
            input_text = self.todo_input.render_with_cursor()
            self.renderer.write_at(
                x, y, f"New task: [{input_text}]", ColorPalette.BRIGHT_CYAN
            )
            y += 2

        # Todo items
        self.renderer.write_at(x, y, "TASKS:", ColorPalette.WHITE)
        y += 1

        for idx, todo in enumerate(self.todos):
            checkbox = "[✓]" if todo.completed else "[ ]"
            text_color = (
                ColorPalette.DIM_WHITE if todo.completed else ColorPalette.WHITE
            )
            checkbox_color = (
                ColorPalette.GREEN if todo.completed else ColorPalette.YELLOW
            )

            self.renderer.write_at(x, y, f"{idx + 1}. {checkbox}", checkbox_color)

            # Strikethrough for completed
            if todo.completed:
                display_text = "".join(c + "\u0336" for c in todo.text)  # Strikethrough
            else:
                display_text = todo.text

            self.renderer.write_at(x + 9, y, display_text, text_color)
            y += 1

        # Stats
        y += 2
        total = len(self.todos)
        completed = sum(1 for t in self.todos if t.completed)
        remaining = total - completed

        self.renderer.write_at(
            x,
            y,
            f"Total: {total} | Completed: {completed} | Remaining: {remaining}",
            ColorPalette.DIM_WHITE,
        )

    async def _render_life_page(self, width: int, height: int, delta_time: float):
        """Render Conway's Game of Life."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "🧬 GAME OF LIFE", ColorPalette.BRIGHT_MAGENTA
        )

        # Update game if running
        if self.life_running:
            current_time = time.time()
            if current_time - self.life_last_update >= self.life_speed:
                self._update_life_generation()
                self.life_last_update = current_time

        # Instructions
        instr_y = 4
        self.renderer.write_at(
            10,
            instr_y,
            "Space: Start/Stop | R: Random | C: Clear | +/-: Speed",
            ColorPalette.DIM_WHITE,
        )
        instr_y += 1
        status = "RUNNING" if self.life_running else "PAUSED"
        status_color = (
            ColorPalette.BRIGHT_GREEN if self.life_running else ColorPalette.YELLOW
        )
        self.renderer.write_at(
            10,
            instr_y,
            f"Status: {status} | Gen: {self.life_generation} | Speed: {self.life_speed:.2f}s",
            status_color,
        )

        # Render grid
        grid_start_x = (width - len(self.life_grid[0])) // 2
        grid_start_y = 8

        for row_idx, row in enumerate(self.life_grid):
            for col_idx, cell in enumerate(row):
                if cell:
                    self.renderer.write_at(
                        grid_start_x + col_idx,
                        grid_start_y + row_idx,
                        "█",
                        ColorPalette.BRIGHT_GREEN,
                    )
                else:
                    self.renderer.write_at(
                        grid_start_x + col_idx,
                        grid_start_y + row_idx,
                        "·",
                        ColorPalette.DIM_GREY,
                    )

    def _render_performance_page(self, width: int, height: int):
        """Render performance comparison and metrics."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "⚡ PERFORMANCE METRICS", ColorPalette.BRIGHT_YELLOW
        )

        y = 5
        x = 10

        # Current FPS
        avg_fps = (
            sum(self.perf_fps_history[-30:]) / len(self.perf_fps_history[-30:])
            if self.perf_fps_history
            else 0
        )
        self.renderer.write_at(
            x,
            y,
            f"Current FPS: {avg_fps:.1f} / {self.target_fps:.1f} target",
            ColorPalette.BRIGHT_GREEN,
        )
        y += 2

        # Frame buffer info
        self.renderer.write_at(x, y, "OPTIMIZATIONS ENABLED:", ColorPalette.WHITE)
        y += 1
        self.renderer.write_at(
            x + 2, y, "✓ Frame buffering (single atomic write)", ColorPalette.GREEN
        )
        y += 1
        self.renderer.write_at(
            x + 2, y, "✓ State tracking (skip unchanged frames)", ColorPalette.GREEN
        )
        y += 1
        self.renderer.write_at(
            x + 2,
            y,
            f"✓ Adaptive FPS ({self.target_fps:.0f} fps for this page)",
            ColorPalette.GREEN,
        )
        y += 3

        # Benefits
        self.renderer.write_at(x, y, "BENEFITS:", ColorPalette.YELLOW)
        y += 1
        self.renderer.write_at(
            x + 2, y, "→ Zero flicker (no visible blank frames)", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            x + 2, y, "→ 60x fewer syscalls per frame", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            x + 2, y, "→ Minimal CPU usage when idle", ColorPalette.WHITE
        )
        y += 1
        self.renderer.write_at(
            x + 2, y, "→ Smooth animations with consistent timing", ColorPalette.WHITE
        )
        y += 3

        # FPS graph
        self.renderer.write_at(
            x, y, "FPS HISTORY (last 60 frames):", ColorPalette.DIM_WHITE
        )
        y += 1
        if self.perf_fps_history:
            self._draw_fps_graph(x, y, 60, 6, self.perf_fps_history[-60:])

    def _render_code_page(self, width: int, height: int):
        """Render code examples and templates."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "💻 CODE EXAMPLES", ColorPalette.BRIGHT_CYAN
        )

        y = 5
        x = 5

        # Example 1: Basic plugin structure
        self.renderer.write_at(
            x, y, "MINIMAL PLUGIN TEMPLATE:", ColorPalette.BRIGHT_YELLOW
        )
        y += 2

        code_lines = [
            ("class", "MyPlugin", "(", "FullScreenPlugin", "):"),
            (
                "    ",
                "async def",
                " ",
                "render_frame",
                "(",
                "self",
                ", ",
                "delta_time",
                "):",
            ),
            ("        ", "self", ".", "renderer", ".", "clear_screen", "()"),
            (
                "        ",
                "self",
                ".",
                "renderer",
                ".",
                "write_at",
                "(",
                "10",
                ", ",
                "5",
                ", ",
                '"Hello!"',
                ")",
            ),
            ("        ", "return", " ", "True"),
            ("", "", "", "", ""),
            (
                "    ",
                "async def",
                " ",
                "handle_input",
                "(",
                "self",
                ", ",
                "key_press",
                "):",
            ),
            ("        ", "return", " ", "key_press", ".", "char", " == ", "'q'"),
        ]

        for line_parts in code_lines:
            line_x = x + 2
            for part in line_parts:
                if part in ["class", "async def", "return"]:
                    color = ColorPalette.BRIGHT_MAGENTA  # Keywords
                elif part in [
                    "MyPlugin",
                    "FullScreenPlugin",
                    "render_frame",
                    "handle_input",
                ]:
                    color = ColorPalette.BRIGHT_CYAN  # Classes/functions
                elif part in ["self"]:
                    color = ColorPalette.BRIGHT_BLUE  # Self
                elif part.startswith('"'):
                    color = ColorPalette.BRIGHT_GREEN  # Strings
                elif part.isdigit():
                    color = ColorPalette.BRIGHT_YELLOW  # Numbers
                else:
                    color = ColorPalette.WHITE

                self.renderer.write_at(line_x, y, part, color)
                line_x += len(part)
            y += 1

        y += 2

        # Key concepts
        self.renderer.write_at(x, y, "KEY CONCEPTS:", ColorPalette.BRIGHT_GREEN)
        y += 1
        concepts = [
            "→ render_frame(): Called every frame, return False to exit",
            "→ handle_input(): Process key presses, return True to exit",
            "→ target_fps: Set frame rate (15-60 fps recommended)",
            "→ Use renderer.begin_frame() / end_frame() for buffering",
            "→ Access width, height via renderer.get_terminal_size()",
        ]
        for concept in concepts:
            self.renderer.write_at(x + 2, y, concept, ColorPalette.DIM_WHITE)
            y += 1

    def _render_resources_page(self, width: int, height: int):
        """Render resources, documentation, and help."""
        if self.renderer is None:
            return
        DrawingPrimitives.draw_text_centered(
            self.renderer, 2, "📚 RESOURCES & HELP", ColorPalette.BRIGHT_GREEN
        )

        y = 5
        x = 10

        # Key bindings
        self.renderer.write_at(x, y, "NAVIGATION:", ColorPalette.BRIGHT_YELLOW)
        y += 1
        bindings = [
            ("←/→ or h/l", "Previous/Next page"),
            ("0-9", "Jump to page by number"),
            ("Home", "First page (Table of Contents)"),
            ("End", "Last page (Resources)"),
            ("q or ESC", "Exit plugin"),
        ]
        for key, desc in bindings:
            self.renderer.write_at(x + 2, y, f"{key:12} - {desc}", ColorPalette.WHITE)
            y += 1

        y += 2

        # Page-specific controls
        self.renderer.write_at(
            x, y, "PAGE-SPECIFIC CONTROLS:", ColorPalette.BRIGHT_CYAN
        )
        y += 1
        controls = [
            ("Calculator", "Number keys, +, -, *, /, =, C"),
            ("Todo List", "a=add, Space=toggle, d=delete"),
            ("Game of Life", "Space=start/stop, r=random, c=clear"),
            ("Interactive", "Tab=switch component, arrows=adjust"),
        ]
        for page, keys in controls:
            self.renderer.write_at(
                x + 2, y, f"{page:15} → {keys}", ColorPalette.DIM_WHITE
            )
            y += 1

        y += 2

        # Documentation
        self.renderer.write_at(x, y, "DOCUMENTATION:", ColorPalette.BRIGHT_MAGENTA)
        y += 1
        docs = [
            "→ Plugin development guide in docs/",
            "→ API reference in core/fullscreen/",
            "→ More examples in plugins/fullscreen/",
            "→ Framework source code is extensively commented",
        ]
        for doc in docs:
            self.renderer.write_at(x + 2, y, doc, ColorPalette.WHITE)
            y += 1

        y += 2

        # Credits
        self.renderer.write_at(
            x, y, "Built with Kollabor Framework 🎯", ColorPalette.BRIGHT_WHITE
        )

    def _render_navigation(self, width: int, height: int):
        """Render navigation footer."""
        if self.renderer is None:
            return
        footer_y = height - 1

        # Page info
        page_info = f"Page {self.current_page + 1}/{self.total_pages}: {self.pages[self.current_page].title}"
        DrawingPrimitives.draw_text_centered(
            self.renderer, footer_y, page_info, ColorPalette.DIM_WHITE
        )

        # Navigation hints (left side)
        nav_hint = "←→/h/l: Navigate | 0-9: Jump | q/ESC: Exit"
        self.renderer.write_at(2, footer_y, nav_hint, ColorPalette.DIM_GREY)

    def _init_life_grid(self):
        """Initialize Game of Life grid."""
        grid_width = 60
        grid_height = 20

        self.life_grid = [
            [False for _ in range(grid_width)] for _ in range(grid_height)
        ]

        # Add a glider
        self.life_grid[5][5] = True
        self.life_grid[6][6] = True
        self.life_grid[7][4] = True
        self.life_grid[7][5] = True
        self.life_grid[7][6] = True

    def _randomize_life_grid(self):
        """Randomize Game of Life grid."""
        for row in range(len(self.life_grid)):
            for col in range(len(self.life_grid[0])):
                self.life_grid[row][col] = random.random() < 0.3
        self.life_generation = 0

    def _clear_life_grid(self):
        """Clear Game of Life grid."""
        for row in range(len(self.life_grid)):
            for col in range(len(self.life_grid[0])):
                self.life_grid[row][col] = False
        self.life_generation = 0

    def _update_life_generation(self):
        """Update Game of Life to next generation."""
        rows = len(self.life_grid)
        cols = len(self.life_grid[0])
        new_grid = [[False for _ in range(cols)] for _ in range(rows)]

        for row in range(rows):
            for col in range(cols):
                # Count neighbors
                neighbors = 0
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = row + dr, col + dc
                        if 0 <= nr < rows and 0 <= nc < cols and self.life_grid[nr][nc]:
                            neighbors += 1

                # Apply rules
                if self.life_grid[row][col]:
                    # Live cell
                    new_grid[row][col] = neighbors in [2, 3]
                else:
                    # Dead cell
                    new_grid[row][col] = neighbors == 3

        self.life_grid = new_grid
        self.life_generation += 1

    def _handle_calculator_input(self, key_press: KeyPress) -> bool:
        """Handle calculator key presses."""
        if key_press.char and key_press.char in "0123456789":
            # Number input
            if self.calc_new_number:
                self.calc_display = key_press.char
                self.calc_new_number = False
            else:
                if len(self.calc_display) < 10:
                    self.calc_display += key_press.char
            return False

        elif key_press.char and key_press.char in "+-*/":
            # Operator
            if self.calc_operator and not self.calc_new_number:
                # Calculate previous operation
                self._calculate()
            self.calc_operand = float(self.calc_display)
            self.calc_operator = key_press.char
            self.calc_new_number = True
            return False

        elif key_press.char and key_press.char in "=\r\n":
            # Equals
            self._calculate()
            return False

        elif key_press.char and key_press.char.lower() == "c":
            # Clear
            self.calc_display = "0"
            self.calc_operator = None
            self.calc_operand = None
            self.calc_new_number = True
            return False

        elif key_press.char == ".":
            # Decimal point
            if "." not in self.calc_display:
                self.calc_display += "."
            return False

        return False

    def _calculate(self):
        """Perform calculator calculation."""
        if self.calc_operator and self.calc_operand is not None:
            try:
                current = float(self.calc_display)

                if self.calc_operator == "+":
                    result = self.calc_operand + current
                elif self.calc_operator == "-":
                    result = self.calc_operand - current
                elif self.calc_operator == "*":
                    result = self.calc_operand * current
                elif self.calc_operator == "/":
                    if current != 0:
                        result = self.calc_operand / current
                    else:
                        self.calc_display = "Error"
                        self.calc_operator = None
                        self.calc_operand = None
                        self.calc_new_number = True
                        return
                else:
                    return

                # Format result
                if result == int(result):
                    self.calc_display = str(int(result))
                else:
                    self.calc_display = f"{result:.6f}".rstrip("0").rstrip(".")

                self.calc_operator = None
                self.calc_operand = None
                self.calc_new_number = True
            except Exception:
                self.calc_display = "Error"
                self.calc_operator = None
                self.calc_operand = None
                self.calc_new_number = True

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle user input for navigation and interactions."""
        # Global: Exit
        if key_press.char in ["q", "\x1b"] or key_press.name == "Escape":
            # Cancel editing mode if active
            if self.current_page == 7 and self.todo_editing:
                self.todo_editing = False
                self.todo_input = SimpleTextInput()
                return False
            return True

        # Page-specific input handling
        if self.current_page == 5:  # Interactive components
            # Tab to switch components
            if key_press.name == "Tab" or key_press.char == "\t":
                self.active_input = (self.active_input + 1) % 3
                return False

            # Component-specific input
            if self.active_input == 0:  # Text input
                self.text_input.handle_key(key_press)
                return False
            elif self.active_input == 1:  # Checkboxes
                if key_press.char == " ":
                    self.checkbox_states[0] = not self.checkbox_states[0]
                elif key_press.name == "ArrowDown":
                    # Rotate through checkboxes
                    self.checkbox_states.append(self.checkbox_states.pop(0))
                return False
            elif self.active_input == 2:  # Slider
                if key_press.name == "ArrowLeft":
                    self.slider_value = max(0, self.slider_value - 5)
                elif key_press.name == "ArrowRight":
                    self.slider_value = min(100, self.slider_value + 5)
                return False

        elif self.current_page == 6:  # Calculator
            return self._handle_calculator_input(key_press)

        elif self.current_page == 7:  # Todo list
            if self.todo_editing:
                # Handle todo input
                if key_press.name == "Enter" or key_press.char in ["\r", "\n"]:
                    if self.todo_input.value.strip():
                        self.todos.append(
                            TodoItem(self.todo_input.value.strip(), False)
                        )
                    self.todo_editing = False
                    self.todo_input = SimpleTextInput()
                    return False
                else:
                    self.todo_input.handle_key(key_press)
                    return False
            else:
                # Todo list commands
                if key_press.char and key_press.char.lower() == "a":
                    self.todo_editing = True
                    return False
                elif key_press.char == " " and self.todos:
                    # Toggle first uncompleted todo
                    for todo in self.todos:
                        if not todo.completed:
                            todo.completed = True
                            break
                    return False
                elif key_press.char and key_press.char.lower() == "d" and self.todos:
                    # Delete first completed todo
                    for i, todo in enumerate(self.todos):
                        if todo.completed:
                            self.todos.pop(i)
                            break
                    return False

        elif self.current_page == 8:  # Game of Life
            if key_press.char == " ":
                self.life_running = not self.life_running
                if self.life_running:
                    self.life_last_update = time.time()
                return False
            elif key_press.char and key_press.char.lower() == "r":
                self._randomize_life_grid()
                return False
            elif key_press.char and key_press.char.lower() == "c":
                self._clear_life_grid()
                self.life_running = False
                return False
            elif key_press.char == "+":
                self.life_speed = max(0.05, self.life_speed - 0.05)
                return False
            elif key_press.char == "-":
                self.life_speed = min(2.0, self.life_speed + 0.05)
                return False

        # Global navigation
        if (
            key_press.char == "h"
            or key_press.char == "a"
            or key_press.name == "ArrowLeft"
        ):
            if self.current_page > 0:
                self.current_page -= 1
        elif (
            key_press.char == "l"
            or key_press.char == "d"
            or key_press.name == "ArrowRight"
        ):
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
        elif key_press.name == "Home":
            self.current_page = 0
        elif key_press.name == "End":
            self.current_page = self.total_pages - 1
        elif key_press.char and key_press.char.isdigit():
            # Jump to page by number
            page_num = int(key_press.char)
            if 0 <= page_num < self.total_pages:
                self.current_page = page_num

        return False

    async def on_stop(self):
        """Called when plugin stops."""
        await super().on_stop()

    async def cleanup(self):
        """Clean up plugin resources."""
        self.animation_framework.clear_all()
        await super().cleanup()
