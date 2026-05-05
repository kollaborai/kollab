"""Base class for full-screen plugins."""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from kollabor_tui.fullscreen.renderer import FullScreenRenderer
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


@dataclass
class PluginMetadata:
    """Metadata for a full-screen plugin."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    category: str = "general"
    icon: str = ""
    aliases: list = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)


class FullScreenPlugin(ABC):
    """Base class for all full-screen plugins.

    This class provides the standard interface that all full-screen plugins
    must implement. It handles the lifecycle, rendering, and input management
    for plugins that take complete terminal control.
    """

    def __init__(self, metadata: PluginMetadata):
        """Initialize the full-screen plugin.

        Args:
            metadata: Plugin metadata including name, description, etc.
        """
        self.metadata = metadata
        self.renderer: Optional["FullScreenRenderer"] = None
        self.running = False
        self.initialized = False

        # Plugin state
        self.start_time = 0.0
        self.frame_count = 0
        self.last_frame_time = 0.0

        # Frame rate control (can be overridden by subclasses)
        # Static plugins (forms, menus) should use 15-20 fps
        # Animated plugins (matrix, effects) should use 60 fps
        self.target_fps = 60.0

        logger.info(f"Initialized full-screen plugin: {metadata.name}")

    @property
    def name(self) -> str:
        """Get the plugin name."""
        return self.metadata.name

    @property
    def description(self) -> str:
        """Get the plugin description."""
        return self.metadata.description

    async def initialize(self, renderer: "FullScreenRenderer") -> bool:
        """Initialize the plugin with a renderer.

        This method is called once when the plugin is loaded. Override
        this method to set up any resources your plugin needs.

        Args:
            renderer: The full-screen renderer instance.

        Returns:
            True if initialization was successful, False otherwise.
        """
        try:
            self.renderer = renderer
            self.initialized = True
            logger.info(f"Plugin {self.name} initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize plugin {self.name}: {e}")
            return False

    @abstractmethod
    async def render_frame(self, delta_time: float) -> bool:
        """Render a single frame.

        This method is called every frame while the plugin is active.
        Override this method to implement your plugin's visual output.

        Args:
            delta_time: Time elapsed since last frame in seconds.

        Returns:
            True to continue running, False to exit the plugin.
        """
        pass

    @abstractmethod
    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle user input.

        This method is called whenever the user presses a key while
        your plugin is active. Override this method to handle input.

        Args:
            key_press: The key that was pressed.

        Returns:
            True to exit the plugin, False to continue running.
        """
        pass

    async def on_start(self):
        """Called when the plugin starts running.

        Override this method to perform any setup that should happen
        when the plugin begins execution (after initialization).
        """
        self.running = True
        self.start_time = _get_loop().time()
        self.frame_count = 0
        logger.info(f"Plugin {self.name} started")

    async def on_stop(self):
        """Called when the plugin stops running.

        Override this method to perform any cleanup that should happen
        when the plugin finishes execution.
        """
        self.running = False
        logger.info(f"Plugin {self.name} stopped")

    async def cleanup(self):
        """Clean up plugin resources.

        This method is called when the plugin is being unloaded.
        Override this method to clean up any resources your plugin allocated.
        """
        self.initialized = False
        self.renderer = None
        logger.info(f"Plugin {self.name} cleaned up")

    def get_runtime_stats(self) -> Dict[str, Any]:
        """Get runtime statistics for the plugin.

        Returns:
            Dictionary with runtime statistics.
        """
        current_time = _get_loop().time()
        runtime = current_time - self.start_time if self.running else 0

        return {
            "name": self.name,
            "running": self.running,
            "initialized": self.initialized,
            "runtime_seconds": runtime,
            "frame_count": self.frame_count,
            "fps": self.frame_count / runtime if runtime > 0 else 0,
        }

    def update_frame_stats(self):
        """Update frame statistics. Called by the framework."""
        self.frame_count += 1
        self.last_frame_time = _get_loop().time()


class ExamplePlugin(FullScreenPlugin):
    """Example plugin implementation for reference."""

    def __init__(self):
        """Initialize the example plugin."""
        metadata = PluginMetadata(
            name="example",
            description="Example full-screen plugin for demonstration",
            author="Framework",
            category="demo",
        )
        super().__init__(metadata)

    async def render_frame(self, delta_time: float) -> bool:
        """Render example content."""
        if not self.renderer:
            return False

        # Clear screen and show example content
        self.renderer.clear_screen()

        # Center message
        width, height = self.renderer.get_terminal_size()
        message = f"Example Plugin - Frame {self.frame_count}"
        x = (width - len(message)) // 2
        y = height // 2

        self.renderer.write_at(x, y, message)
        self.renderer.write_at(x - 5, y + 2, "Press 'q' or ESC to exit")

        self.update_frame_stats()
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle input for example plugin."""
        # Exit on 'q' or ESC
        return key_press.char in ["q", "\x1b"] or key_press.name == "Escape"
