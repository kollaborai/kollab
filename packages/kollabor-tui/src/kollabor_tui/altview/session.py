"""AltView session management.

Unlike FullScreenSession (which creates and destroys each time),
AltViewSession can be entered and exited multiple times. Terminal state
is set up / torn down on each enter / exit, but the plugin state persists.
"""

import asyncio
import logging
import select
import sys
from typing import Optional

from kollabor_events.models import EventType, Hook, HookPriority
from kollabor_tui.fullscreen.renderer import FullScreenRenderer
from kollabor_tui.key_parser import KeyParser
from kollabor_tui.render_loop import EventDrivenRenderLoop, RenderTrigger

from .base import AltView, AltViewState
from .display_queue import DisplayQueue

logger = logging.getLogger(__name__)


class AltViewSession:
    """Manages runtime execution of a single AltView session.

    The session wraps one AltView plugin and handles:
    - Terminal alternate buffer enter/exit on each cycle
    - Input hook registration / teardown
    - Display queue capture for background frame replay
    - Delegation to the EventDrivenRenderLoop for the render/input loop
    """

    def __init__(
        self,
        altview: AltView,
        event_bus,
        session_name: str,
    ) -> None:
        self.altview = altview
        self.event_bus = event_bus
        self.session_name = session_name

        self.display_queue = DisplayQueue()
        self.renderer = FullScreenRenderer()
        self.pending_input: asyncio.Queue = asyncio.Queue()

        self._input_hook_registered: bool = False
        self._running: bool = False
        self._is_first_entry: bool = True
        self._render_loop: Optional[EventDrivenRenderLoop] = None
        self._entry_count: int = 0

    # -- public lifecycle ---------------------------------------------------

    async def enter(self) -> None:
        """Enter this session. Can be called multiple times for re-entry."""
        self.display_queue.start_capture()

        await self._register_input_hook()

        if not self.renderer.setup_terminal():
            logger.error("AltViewSession: failed to set up terminal")
            raise RuntimeError("Terminal setup failed")

        if self._is_first_entry:
            await self.altview.create_session(self.session_name)
            self.altview._set_renderer(self.renderer)
            await self.altview.on_enter(self.renderer)
            self._is_first_entry = False
        else:
            self.altview._set_renderer(self.renderer)
            await self.altview.on_resume()

        self.altview._set_state(AltViewState.RUNNING)
        self._entry_count += 1
        self._running = True

        logger.info(
            "AltViewSession[%s]: entered (entry #%d)",
            self.session_name,
            self._entry_count,
        )

    async def run_loop(self) -> None:
        """Run the render/input loop until exit is requested."""
        self._render_loop = EventDrivenRenderLoop(
            render_callback=self._render_callback,
            input_callback=self._input_callback,
            target_fps=self.altview.target_fps,
            input_poll_rate=100.0,
            name=f"AltView[{self.session_name}]",
        )
        await self._render_loop.run()
        self._render_loop = None

    async def exit(self) -> None:
        """Exit session. Does NOT destroy it -- can be re-entered later."""
        self._running = False

        if self._render_loop is not None:
            self._render_loop.stop()

        await self.altview.on_suspend()
        self.altview._set_state(AltViewState.SUSPENDED)

        self.renderer.restore_terminal()
        await self._unregister_input_hook()
        self.display_queue.stop_capture()

        logger.info("AltViewSession[%s]: exited (suspended)", self.session_name)

    async def destroy(self) -> None:
        """Permanently destroy this session and its plugin."""
        if self._running:
            await self.exit()

        await self.altview.on_complete()
        self.altview._set_state(AltViewState.COMPLETE)
        self.display_queue.clear()

        logger.info("AltViewSession[%s]: destroyed", self.session_name)

    # -- render / input callbacks -------------------------------------------

    async def _render_callback(self, delta_time: float, trigger: RenderTrigger) -> bool:
        """Render callback for EventDrivenRenderLoop.

        Returns:
            True to continue, False to exit.
        """
        try:
            self.renderer.begin_frame()
            should_continue = await self.altview.render_frame(delta_time)
            self.renderer.end_frame()
            return should_continue

        except Exception as e:
            logger.error(
                "AltViewSession[%s]: render error: %s",
                self.session_name,
                e,
            )
            return False

    async def _input_callback(self) -> tuple:
        """Input callback for EventDrivenRenderLoop.

        Reads stdin directly using select (non-blocking). The main app's
        input loop is blocked awaiting push() to return, so we are the
        only reader on stdin. This matches the FullScreenSession pattern.

        Returns:
            (input_processed: bool, should_exit: bool)
        """
        try:
            # Priority 1: Check pending_input queue (from hooks, if any)
            try:
                key_press = self.pending_input.get_nowait()
                if key_press:
                    exit_requested = await self.altview.handle_input(key_press)
                    return (True, exit_requested)
            except asyncio.QueueEmpty:
                pass

            # Priority 2: Direct stdin reading (primary path - main input
            # loop is blocked waiting for push() to return so we own stdin)
            if not sys.stdin.isatty():
                return (False, False)

            ready, _, _ = select.select([sys.stdin], [], [], 0.01)
            if not ready:
                return (False, False)

            # Non-blocking read
            import fcntl
            import os as os_module

            fd = sys.stdin.fileno()
            old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os_module.O_NONBLOCK)
            try:
                chars = sys.stdin.read(32)
            except (IOError, BlockingIOError):
                chars = ""
            finally:
                fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)

            if not chars:
                return (False, False)

            # Parse through key parser
            if not hasattr(self, "_key_parser"):
                self._key_parser = KeyParser()

            key_press = None
            for char in chars:
                key_press = self._key_parser.parse_char(char)
                if key_press:
                    break

            if not key_press:
                key_press = self._key_parser.check_for_standalone_escape()
                if not key_press:
                    return (False, False)

            exit_requested = await self.altview.handle_input(key_press)
            return (True, exit_requested)

        except Exception as e:
            logger.error(
                "AltViewSession[%s]: input handler error: %s",
                self.session_name,
                e,
            )
            return (False, False)

    # -- input hook ---------------------------------------------------------

    async def _register_input_hook(self) -> None:
        """Register a FULLSCREEN_INPUT hook to receive keystrokes."""
        if not self.event_bus or self._input_hook_registered:
            return

        try:
            hook = Hook(
                name="fullscreen_input",
                plugin_name=f"altview_session_{self.session_name}",
                event_type=EventType.FULLSCREEN_INPUT,
                priority=HookPriority.DISPLAY.value,
                callback=self._handle_fullscreen_input,
            )
            success = await self.event_bus.register_hook(hook)
            if success:
                self._input_hook_registered = True
                logger.info(
                    "AltViewSession[%s]: registered FULLSCREEN_INPUT hook",
                    self.session_name,
                )
            else:
                logger.error(
                    "AltViewSession[%s]: failed to register input hook",
                    self.session_name,
                )
        except Exception as e:
            logger.error(
                "AltViewSession[%s]: error registering input hook: %s",
                self.session_name,
                e,
            )

    async def _unregister_input_hook(self) -> None:
        """Unregister the FULLSCREEN_INPUT hook."""
        if not self.event_bus or not self._input_hook_registered:
            return

        try:
            hook_id = f"altview_session_{self.session_name}.fullscreen_input"
            await self.event_bus.unregister_hook(hook_id)
            self._input_hook_registered = False
            logger.info(
                "AltViewSession[%s]: unregistered FULLSCREEN_INPUT hook",
                self.session_name,
            )
        except Exception as e:
            logger.error(
                "AltViewSession[%s]: error unregistering input hook: %s",
                self.session_name,
                e,
            )

    async def _handle_fullscreen_input(self, event_data, context=None):
        """Handle FULLSCREEN_INPUT events from the InputHandler."""
        try:
            key_press = event_data.get("key_press")
            if key_press:
                await self.pending_input.put(key_press)
                return {"success": True, "handled": True}
            return {"success": False, "error": "No key_press in event data"}
        except Exception as e:
            logger.error(
                "AltViewSession[%s]: error handling input event: %s",
                self.session_name,
                e,
            )
            return {"success": False, "error": str(e)}

    # -- properties ---------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether this session is currently in its run loop."""
        return self._running

    @property
    def is_first_entry(self) -> bool:
        """Whether the session has never been entered."""
        return self._is_first_entry

    @property
    def entry_count(self) -> int:
        """Number of times this session has been entered."""
        return self._entry_count
