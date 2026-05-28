"""Matrix rain effect as an AltView plugin."""

import asyncio
import logging
from typing import Any

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.fullscreen.components.matrix_components import MatrixRenderer
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


class MatrixAltView(AltView):
    """Matrix digital rain effect in the terminal alternate buffer.

    Ephemeral animation -- no named sessions, no background support.
    Any key press exits.
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="matrix",
            description="Matrix rain effect",
            version="1.0.0",
            author="Framework",
            category="effect",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps: float = 60.0
        self.render_on_timer = True

        self._matrix_renderer: MatrixRenderer | None = None
        self._start_time: float = 0.0

    async def on_enter(self, renderer: Any) -> None:
        """Set up the matrix renderer and start the animation."""
        self._renderer = renderer

        width, height = renderer.get_terminal_size()
        self._matrix_renderer = MatrixRenderer(width, height)
        self._matrix_renderer.reset()
        self._start_time = _get_loop().time()

        logger.info("MatrixAltView: entered (%dx%d)", width, height)

    async def render_frame(self, delta_time: float) -> bool:
        """Render one frame of the matrix rain animation.

        Returns True to keep running, False to exit.
        """
        if not self._renderer or not self._matrix_renderer:
            return False

        try:
            current_time = _get_loop().time() - self._start_time
            self._matrix_renderer.update(current_time)
            self._matrix_renderer.render(self._renderer)
            return True
        except Exception as e:
            logger.error("MatrixAltView: render error: %s", e)
            return False

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Any key exits the animation."""
        return True

    async def on_complete(self) -> None:
        """Release the matrix renderer."""
        self._matrix_renderer = None
        await super().on_complete()
