"""Matrix rain plugin using the full-screen framework."""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import logging

from kollabor_tui.fullscreen import FullScreenPlugin
from kollabor_tui.fullscreen.components.matrix_components import MatrixRenderer
from kollabor_tui.fullscreen.plugin import PluginMetadata
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class MatrixRainPlugin(FullScreenPlugin):
    """Matrix rain effect implemented as a full-screen plugin.

    This plugin creates the iconic Matrix digital rain effect with falling
    green characters, providing an immersive full-screen experience.
    """

    def __init__(self):
        """Initialize the Matrix rain plugin."""
        metadata = PluginMetadata(
            name="matrix",
            description="Enter the Matrix with falling code rain",
            version="1.0.0",
            author="Framework",
            category="effects",
            icon="🔋",
            aliases=[],
        )
        super().__init__(metadata)

        # Matrix-specific state
        self.matrix_renderer = None
        self.start_time = 0

    async def initialize(self, renderer) -> bool:
        """Initialize the Matrix plugin.

        Args:
            renderer: FullScreenRenderer instance

        Returns:
            True if initialization successful
        """
        if not await super().initialize(renderer):
            return False

        try:
            # Get terminal dimensions for Matrix renderer
            width, height = renderer.get_terminal_size()

            # Create Matrix renderer with current terminal size
            self.matrix_renderer = MatrixRenderer(width, height)

            return True

        except Exception as e:
            logger.error(f"Failed to initialize Matrix renderer: {e}")
            return False

    async def on_start(self):
        """Called when Matrix plugin starts."""
        await super().on_start()
        self.start_time = _get_loop().time()

        logger.info("MatrixRainPlugin.on_start() called")

        # Reset Matrix renderer for fresh start
        if self.matrix_renderer:
            self.matrix_renderer.reset()

    async def render_frame(self, delta_time: float) -> bool:
        """Render a Matrix rain frame.

        Args:
            delta_time: Time elapsed since last frame

        Returns:
            True to continue, False to exit
        """
        if not self.renderer or not self.matrix_renderer:
            return False

        try:
            # Calculate current time for Matrix animation
            current_time = _get_loop().time() - self.start_time

            # Update Matrix animation
            self.matrix_renderer.update(current_time)

            # Render Matrix to screen
            self.matrix_renderer.render(self.renderer)

            # Update frame statistics
            self.update_frame_stats()

            return True

        except Exception as e:
            logger.error(f"Error rendering Matrix frame: {e}")
            return False

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle input for Matrix plugin.

        Args:
            key_press: Key that was pressed

        Returns:
            True to exit Matrix, False to continue
        """
        # Exit on 'q', ESC, or Escape key
        if key_press.char in ["q", "\x1b"] or key_press.name == "Escape":
            return True

        # Continue running for all other keys
        return False

    async def on_stop(self):
        """Called when Matrix plugin stops."""
        await super().on_stop()

        # Optional: Could add fade-out effect here
        # or save statistics about the session

    async def cleanup(self):
        """Clean up Matrix plugin resources."""
        self.matrix_renderer = None
        await super().cleanup()
