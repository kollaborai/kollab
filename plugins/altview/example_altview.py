"""Example AltView plugin -- reference implementation for plugin developers.

This plugin demonstrates every AltView lifecycle method with a minimal
interactive UI. Use it as a starting point when building your own.

Lifecycle overview:
    1. __init__()       -- construct metadata and initial state
    2. create_session() -- framework assigns a session name (if named)
    3. on_enter()       -- view takes foreground; store renderer, init UI
    4. render_frame()   -- called every frame while RUNNING
    5. handle_input()   -- called on each keypress while in foreground
    6. on_suspend()     -- view moves to background (session persists)
    7. on_resume()      -- view returns to foreground
    8. on_complete()    -- teardown; release resources

Key concepts shown:
    - Named sessions (supports_named_sessions=True)
    - Background tasks (supports_background=True)
    - State persistence across suspend/resume cycles
    - Design system usage (T, solid, solid_fg, C)
    - Spawning tracked background tasks with spawn_background_task()
"""

import asyncio
import logging
import time
from typing import Any

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class ExampleAltView(AltView):
    """Minimal interactive AltView with counter, background tasks, and sessions.

    Registers as /example slash command. Demonstrates all lifecycle hooks
    and design system rendering patterns.

    State that persists across suspend/resume:
        - frame_count: total frames rendered
        - bg_task_progress: progress of the background counter (0-10)
        - bg_task_running: whether a background task is active
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="example",
            description="Example AltView plugin",
            version="1.0.0",
            author="Kollabor",
            category="general",
            icon="[EX]",
            aliases=["ex", "demo"],
            supports_named_sessions=True,
            supports_background=True,
        )
        super().__init__(metadata)

        # Lower FPS -- this is a simple counter, not an animation
        self.target_fps = 10.0

        # Persistent state (survives suspend/resume)
        self.frame_count: int = 0
        self.bg_task_progress: int = 0
        self.bg_task_running: bool = False
        self._enter_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle: on_enter
    # ------------------------------------------------------------------
    # Called when the view takes foreground control of the alternate
    # buffer. The framework has already switched to the alt screen and
    # hidden the cursor. Store the renderer reference and set up any
    # rendering state here.
    # ------------------------------------------------------------------

    async def on_enter(self, renderer: Any) -> None:
        """Store renderer and record entry time.

        Called each time the view takes foreground -- both on first
        launch and after on_resume(). The renderer may be a different
        instance if the terminal was resized, so always re-store it.
        """
        self._renderer = renderer
        self._enter_time = time.monotonic()
        logger.info(
            "ExampleAltView entered (session=%s, frames=%d)",
            self.session_name,
            self.frame_count,
        )

    # ------------------------------------------------------------------
    # Lifecycle: render_frame
    # ------------------------------------------------------------------
    # Called every frame (~target_fps) while the view is in RUNNING
    # state. delta_time is seconds since the previous frame. Return
    # True to keep running, False to signal completion (triggers
    # on_complete).
    # ------------------------------------------------------------------

    async def render_frame(self, delta_time: float) -> bool:
        """Draw the example UI: box with counter, session info, and help.

        Returns True to keep running. The framework calls this at
        approximately self.target_fps times per second.
        """
        if not self.renderer:
            return False

        self.frame_count += 1

        width, height = self.renderer.get_terminal_size()
        theme = T()

        # -- layout constants --
        box_w = min(60, width - 4)
        box_h = 12
        box_x = (width - box_w) // 2
        box_y = max(1, (height - box_h) // 2)

        # -- clear screen --
        self.renderer.clear_screen()

        # -- top edge --
        self.renderer.write_at(
            box_x,
            box_y,
            solid_fg(str(C["half_bottom"]) * box_w, theme.primary[0]),
            "",
        )

        # -- title row --
        title = " Example AltView "
        title_line = title.center(box_w)
        self.renderer.write_at(
            box_x,
            box_y + 1,
            solid(title_line, theme.primary[0], theme.text_dark, box_w),
            "",
        )

        # -- content rows (dark background) --
        inner_w = box_w
        content_lines = self._build_content_lines(inner_w)

        for i, line in enumerate(content_lines):
            row = box_y + 2 + i
            padded = line[:inner_w].ljust(inner_w)
            self.renderer.write_at(
                box_x,
                row,
                solid(padded, theme.dark[0], theme.text, inner_w),
                "",
            )

        # fill remaining box rows
        rows_used = len(content_lines)
        for i in range(rows_used, box_h - 3):
            row = box_y + 2 + i
            self.renderer.write_at(
                box_x,
                row,
                solid(" " * inner_w, theme.dark[0], theme.text, inner_w),
                "",
            )

        # -- footer row (keybinds) --
        footer = " b: background task  r: reset  q/Esc: exit "
        footer_line = footer.center(box_w)
        self.renderer.write_at(
            box_x,
            box_y + box_h - 1,
            solid(footer_line, theme.dark[1], theme.text_dim, box_w),
            "",
        )

        # -- bottom edge --
        self.renderer.write_at(
            box_x,
            box_y + box_h,
            solid_fg(str(C["half_top"]) * box_w, theme.dark[1]),
            "",
        )

        return True

    def _build_content_lines(self, width: int) -> list[str]:
        """Build the content lines for the main box."""
        elapsed = time.monotonic() - self._enter_time if self._enter_time else 0
        session = self.session_name or "(unnamed)"

        # Background task status
        if self.bg_task_running:
            filled = int((self.bg_task_progress / 10) * 10)
            bar = str(C["bar_full"]) * filled + str(C["bar_empty"]) * (10 - filled)
            bg_status = f"running [{bar}] {self.bg_task_progress}/10"
        elif self.bg_task_progress >= 10:
            bg_status = "complete"
        else:
            bg_status = "idle (press b to start)"

        lines = [
            "",
            f"  Session:     {session}",
            f"  Frames:      {self.frame_count}",
            f"  Elapsed:     {elapsed:.1f}s",
            "",
            f"  Background:  {bg_status}",
            "",
        ]
        return lines

    # ------------------------------------------------------------------
    # Lifecycle: handle_input
    # ------------------------------------------------------------------
    # Called when a key is pressed while this view is in the foreground.
    # Return True to exit (triggers on_suspend or on_complete depending
    # on session support). Return False to keep running.
    # ------------------------------------------------------------------

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle user input. Returns True to exit the view.

        Keybindings:
            b  -- spawn a background task (counts to 10)
            r  -- reset the frame counter
            q  -- exit (suspend session)
            Esc -- exit (suspend session)
        """
        # Exit
        if key_press.name == "Escape" or key_press.char in ("q", "\x1b"):
            logger.info("ExampleAltView: user requested exit")
            return True

        # Reset counter
        if key_press.char == "r":
            self.frame_count = 0
            self.bg_task_progress = 0
            self.bg_task_running = False
            self._enter_time = time.monotonic()
            logger.info("ExampleAltView: counters reset")
            return False

        # Spawn background task
        if key_press.char == "b":
            if not self.bg_task_running:
                self.bg_task_progress = 0
                self.bg_task_running = True
                self.spawn_background_task(
                    self._background_counter(),
                    name="counter",
                )
                logger.info("ExampleAltView: background task started")
            return False

        return False

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------
    # Use self.spawn_background_task(coro, name) to launch tracked
    # async tasks. They continue running even when the view is
    # suspended (supports_background=True). The framework auto-cleans
    # them on on_complete().
    # ------------------------------------------------------------------

    async def _background_counter(self) -> None:
        """Simple background task that counts from 1 to 10.

        Updates self.bg_task_progress each second. The render_frame()
        method reads this to display a progress bar. This task continues
        running even if the view is suspended, demonstrating background
        task persistence.
        """
        try:
            for i in range(1, 11):
                await asyncio.sleep(1.0)
                self.bg_task_progress = i
                logger.debug("ExampleAltView: background counter at %d", i)
        except asyncio.CancelledError:
            logger.info("ExampleAltView: background task cancelled")
            raise
        finally:
            self.bg_task_running = False

    # ------------------------------------------------------------------
    # Lifecycle: on_suspend
    # ------------------------------------------------------------------
    # Called when the view is moved to the background (another view
    # takes focus, or user exits to chat). State is preserved -- the
    # instance stays alive. Background tasks keep running if
    # supports_background=True.
    # ------------------------------------------------------------------

    async def on_suspend(self) -> None:
        """Log suspension. State and background tasks persist."""
        await super().on_suspend()
        logger.info(
            "ExampleAltView suspended (session=%s, frames=%d, bg=%s)",
            self.session_name,
            self.frame_count,
            "running" if self.bg_task_running else "idle",
        )

    # ------------------------------------------------------------------
    # Lifecycle: on_resume
    # ------------------------------------------------------------------
    # Called when the view returns to the foreground after being
    # suspended. Refresh any cached render state here. The renderer
    # may have changed (e.g. terminal resize).
    # ------------------------------------------------------------------

    async def on_resume(self) -> None:
        """Log resumption and reset entry time for elapsed display."""
        await super().on_resume()
        self._enter_time = time.monotonic()
        logger.info(
            "ExampleAltView resumed (session=%s, frames=%d)",
            self.session_name,
            self.frame_count,
        )

    # ------------------------------------------------------------------
    # Lifecycle: on_complete
    # ------------------------------------------------------------------
    # Called when the view is being torn down. The framework cancels
    # remaining background tasks after this returns. Release any
    # resources (file handles, network connections, etc.) here.
    # ------------------------------------------------------------------

    async def on_complete(self) -> None:
        """Clean up. Base class cancels background tasks."""
        logger.info(
            "ExampleAltView complete (session=%s, total_frames=%d)",
            self.session_name,
            self.frame_count,
        )
        await super().on_complete()
