"""Space shooter plugin using the full-screen framework.

A retro 80s arcade-style demo with ships flying through a starfield,
dodging and shooting enemies.
"""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import logging

from kollabor_tui.fullscreen import FullScreenPlugin
from kollabor_tui.fullscreen.components.space_shooter_components import (
    SpaceShooterRenderer,
)
from kollabor_tui.fullscreen.plugin import PluginMetadata
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class SpaceShooterPlugin(FullScreenPlugin):
    """Space shooter demo implemented as a full-screen plugin.

    This plugin creates a retro 80s arcade-style space shooter demo with
    ships flying through a starfield, banking and dodging, shooting enemies.
    """

    def __init__(self):
        """Initialize the space shooter plugin."""
        metadata = PluginMetadata(
            name="space",
            description="80s arcade space shooter demo",
            version="1.0.0",
            author="Framework",
            category="effects",
            icon="*",
            aliases=["shooter", "galaga", "arcade"],
        )
        super().__init__(metadata)

        # Space shooter-specific state
        self.space_renderer = None
        self.start_time = 0

    async def initialize(self, renderer) -> bool:
        """Initialize the space shooter plugin.

        Args:
            renderer: FullScreenRenderer instance

        Returns:
            True if initialization successful
        """
        if not await super().initialize(renderer):
            return False

        try:
            # Get terminal dimensions
            width, height = renderer.get_terminal_size()

            # Create space shooter renderer with current terminal size
            self.space_renderer = SpaceShooterRenderer(width, height)

            return True

        except Exception as e:
            logger.error(f"Failed to initialize space shooter renderer: {e}")
            return False

    async def on_start(self):
        """Called when space shooter plugin starts."""
        await super().on_start()
        self.start_time = _get_loop().time()

        logger.info("Space shooter plugin starting via full-screen framework")

        # Reset renderer for fresh start
        if self.space_renderer:
            self.space_renderer.reset()

    async def render_frame(self, delta_time: float) -> bool:
        """Render a space shooter frame.

        Args:
            delta_time: Time elapsed since last frame

        Returns:
            True to continue, False to exit
        """
        if not self.renderer or not self.space_renderer:
            return False

        try:
            # Calculate current time for animation
            current_time = _get_loop().time() - self.start_time

            # Update animation
            self.space_renderer.update(current_time)

            # Render to screen
            self.space_renderer.render(self.renderer)

            # Update frame statistics
            self.update_frame_stats()

            return True

        except Exception as e:
            logger.error(f"Error rendering space shooter frame: {e}")
            return False

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle input for space shooter plugin.

        Args:
            key_press: Key that was pressed

        Returns:
            True to exit, False to continue
        """
        # Exit on 'q', ESC, or Escape key
        if key_press.char in ["q", "\x1b"] or key_press.name == "Escape":
            return True

        # Continue running for all other keys
        return False

    async def on_stop(self):
        """Called when space shooter plugin stops."""
        await super().on_stop()

    async def cleanup(self):
        """Clean up space shooter plugin resources."""
        self.space_renderer = None
        await super().cleanup()
