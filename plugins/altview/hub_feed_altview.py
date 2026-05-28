"""Hub Feed AltView -- live dashboard of agent activity and messages.

Replaces the old LiveModalRenderer-based /hub feed command.
Uses the AltView stack system, which properly coordinates with
the message coordinator (pauses hub messages during viewing,
replays them on exit).

Features:
    - Live agent roster with status and current task
    - Real-time message feed from vault streams
    - Auto-refresh at 1 FPS
    - Scrollable message history
"""

import logging
from typing import Any

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import T, solid
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class HubFeedAltView(AltView):
    """Live hub feed dashboard showing agent roster and message channel.

    Reads presence files for agent status and vault streams for
    message history. Refreshes at 1 FPS.
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="hub-feed",
            description="Live hub agent feed",
            version="1.0.0",
            author="Kollabor",
            category="internal",
            icon="[H]",
            aliases=["feed"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 1.0
        self.render_on_timer = True
        self._feed: Any = None
        self._scroll_offset = 0

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._scroll_offset = 0
        # Lazy import to avoid circular deps
        try:
            from plugins.hub.feed import HubFeed

            self._feed = HubFeed()
        except ImportError:
            self._feed = None
            logger.warning("Hub feed module not available")

    async def render_frame(self, delta_time: float) -> bool:
        if not self.renderer:
            return False

        width, height = self.renderer.get_terminal_size()
        theme = T()

        self.renderer.clear_screen()

        # Header
        header = " hub feed "
        header_line = header.center(width)
        self.renderer.write_at(
            0,
            0,
            solid(header_line, theme.primary[0], theme.text_dark, width),
            "",
        )

        # Content area
        content_height = height - 2

        if self._feed:
            feed_lines = self._feed.generate_feed_lines(width - 4, content_height)
        else:
            feed_lines = ["  hub feed module not available"]

        # Apply scroll offset
        if self._scroll_offset > 0:
            start = max(0, len(feed_lines) - content_height - self._scroll_offset)
            feed_lines = feed_lines[start : start + content_height]
        else:
            # Show newest (bottom)
            if len(feed_lines) > content_height:
                feed_lines = feed_lines[-content_height:]

        for i in range(content_height):
            row = i + 1
            if i < len(feed_lines):
                line = feed_lines[i]
                self.renderer.write_raw(f"\033[{row + 1};1H{line}")
            else:
                self.renderer.write_raw(f"\033[{row + 1};1H{' ' * width}")

        # Footer
        footer = " Esc: exit | Up/Down: scroll "
        footer_line = footer.center(width)
        self.renderer.write_at(
            0,
            height - 1,
            solid(footer_line, theme.dark[1], theme.text_dim, width),
            "",
        )

        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "Escape":
            return True

        if key_press.name == "ArrowUp":
            self._scroll_offset = min(self._scroll_offset + 3, 200)
            return False

        if key_press.name == "ArrowDown":
            self._scroll_offset = max(self._scroll_offset - 3, 0)
            return False

        if key_press.name == "Home":
            self._scroll_offset = 200
            return False

        if key_press.name == "End":
            self._scroll_offset = 0
            return False

        return False

    async def on_complete(self) -> None:
        self._feed = None
