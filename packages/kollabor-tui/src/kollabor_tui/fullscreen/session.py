"""Full-screen session management."""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import logging
import sys
from dataclasses import dataclass
from typing import Any, Optional

from kollabor_tui.key_parser import KeyParser
from kollabor_tui.render_loop import EventDrivenRenderLoop, RenderTrigger

from .plugin import FullScreenPlugin
from .renderer import FullScreenRenderer

# Platform-specific imports for input handling
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import msvcrt
else:
    import select

logger = logging.getLogger(__name__)


@dataclass
class SessionStats:
    """Statistics for a full-screen session."""

    start_time: float
    end_time: Optional[float] = None
    frame_count: int = 0
    input_events: int = 0
    average_fps: float = 0.0

    @property
    def duration(self) -> float:
        """Get session duration in seconds."""
        end = self.end_time or _get_loop().time()
        return end - self.start_time


class FullScreenSession:
    """Manages a full-screen plugin execution session.

    This class handles the complete lifecycle of running a full-screen plugin,
    including terminal setup, input handling, rendering loop, and cleanup.
    """

    def __init__(
        self,
        plugin: FullScreenPlugin,
        event_bus=None,
        target_fps: Optional[float] = None,
    ):
        """Initialize a full-screen session.

        Args:
            plugin: The plugin to run.
            event_bus: Event bus for input routing (optional, falls back to direct stdin).
            target_fps: Target frame rate for rendering (None = use plugin's target_fps).
        """
        self.plugin = plugin
        self.event_bus = event_bus
        # Use plugin's target_fps if not explicitly overridden
        self.target_fps = (
            target_fps
            if target_fps is not None
            else getattr(plugin, "target_fps", 20.0)
        )
        self.frame_delay = 1.0 / self.target_fps

        # Session state
        self.running = False
        self.renderer = FullScreenRenderer()
        self.key_parser = KeyParser()
        self.stats = SessionStats(start_time=0)

        # CRITICAL FIX: Input routing through events
        self.pending_input: "asyncio.Queue[Any]" = asyncio.Queue()
        self.input_hook_registered = False

        logger.info(f"Created session for plugin: {plugin.name}")

    async def _register_input_hook(self):
        """Register hook to receive input from InputHandler."""
        if self.event_bus and not self.input_hook_registered:
            try:
                from kollabor_events.models import EventType, Hook, HookPriority

                hook = Hook(
                    name="fullscreen_input",
                    plugin_name=f"fullscreen_session_{self.plugin.name}",
                    event_type=EventType.FULLSCREEN_INPUT,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._handle_fullscreen_input,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self.input_hook_registered = True
                    logger.info(
                        f"✅ Registered FULLSCREEN_INPUT hook for {self.plugin.name}"
                    )
                else:
                    logger.error(
                        f"❌ Failed to register FULLSCREEN_INPUT hook for {self.plugin.name}"
                    )
            except Exception as e:
                logger.error(f"Error registering fullscreen input hook: {e}")

    async def _handle_fullscreen_input(self, event_data, context=None):
        """Handle FULLSCREEN_INPUT events from InputHandler."""
        try:
            key_press = event_data.get("key_press")
            if key_press:
                logger.debug(f"Session received input: {key_press.name}")
                await self.pending_input.put(key_press)
                return {"success": True, "handled": True}
            logger.warning("No key_press in FULLSCREEN_INPUT event data")
            return {"success": False, "error": "No key_press in event data"}
        except Exception as e:
            logger.error(f"Error handling fullscreen input: {e}")
            return {"success": False, "error": str(e)}

    async def run(self) -> bool:
        """Run the full-screen session.

        Returns:
            True if session completed successfully, False if error occurred.
        """
        try:
            # Initialize session
            if not await self._initialize():
                return False

            logger.info(f"Starting full-screen session for {self.plugin.name}")
            self.running = True
            self.stats.start_time = _get_loop().time()

            # Main session loop
            await self._session_loop()

            logger.info(f"Session completed for {self.plugin.name}")
            return True

        except Exception as e:
            logger.error(f"Session error for {self.plugin.name}: {e}")
            return False

        finally:
            await self._cleanup()

    async def _initialize(self) -> bool:
        """Initialize the session.

        Returns:
            True if initialization successful, False otherwise.
        """
        try:
            # CRITICAL FIX: Register input hook before terminal setup
            try:
                await self._register_input_hook()
            except Exception as e:
                logger.warning(f"Input hook registration failed: {e}")
                # Continue anyway, input might still work via fallback

            # Setup terminal
            if not self.renderer.setup_terminal():
                logger.error("Failed to setup terminal")
                return False

            # Initialize plugin
            if not await self.plugin.initialize(self.renderer):
                logger.error(f"Failed to initialize plugin {self.plugin.name}")
                return False

            # Start plugin
            await self.plugin.on_start()

            return True

        except Exception as e:
            logger.error(f"Session initialization failed: {e}")
            return False

    async def _session_loop(self):
        """Main session loop using EventDrivenRenderLoop.

        Delegates to the reusable EventDrivenRenderLoop component which handles:
        - High-frequency input polling (100Hz)
        - Event-driven rendering (on input or timer)
        - Efficient CPU usage
        """

        # Create render callback that wraps plugin render
        async def render_callback(delta_time: float, trigger: RenderTrigger) -> bool:
            """Render frame wrapper for EventDrivenRenderLoop.

            Args:
                delta_time: Time since last frame
                trigger: Why this render was triggered

            Returns:
                True to continue, False to exit
            """
            try:
                # Begin buffered frame (eliminates flicker)
                self.renderer.begin_frame()

                # Render frame
                should_continue = await self.plugin.render_frame(delta_time)

                # End frame and flush all buffered writes atomically
                self.renderer.end_frame()

                # Update session stats
                self.stats.frame_count += 1

                return should_continue

            except Exception as e:
                logger.error(f"Error rendering frame: {e}")
                return False

        # Create input callback that wraps input handling
        async def input_callback():
            """Input check wrapper for EventDrivenRenderLoop.

            Returns:
                Tuple of (input_processed, should_exit)
            """
            input_processed, exit_requested = await self._handle_input_internal()
            return (input_processed, exit_requested)

        # Create and run event-driven render loop
        render_loop = EventDrivenRenderLoop(
            render_callback=render_callback,
            input_callback=input_callback,
            target_fps=self.target_fps,
            input_poll_rate=100.0,  # 100Hz input polling for instant response
            name=f"FullScreen[{self.plugin.name}]",
        )

        # Run the loop
        (
            await render_loop.run()
        )  # noqa: F841 - captures result for potential future use

        # Copy stats from render loop to session stats
        loop_stats = render_loop.get_stats()
        logger.info(
            f"Session stats: {loop_stats.total_frames} frames, "
            f"{loop_stats.average_fps:.1f} fps, "
            f"{loop_stats.input_efficiency:.1f}% input-triggered, "
            f"{loop_stats.total_input_events} input events"
        )

    async def _handle_input_with_flag(self):
        """Handle input events with flag for render triggering.

        Returns:
            - True: exit was requested
            - "input_received": input was processed (trigger render)
            - False: no input available
        """
        input_processed, exit_requested = await self._handle_input_internal()
        if exit_requested:
            return True
        elif input_processed:
            return "input_received"
        else:
            return False

    async def _handle_input(self) -> bool:
        """Handle input events.

        Returns:
            True if exit was requested, False otherwise.
        """
        input_processed, exit_requested = await self._handle_input_internal()
        return exit_requested

    async def _handle_input_internal(self) -> tuple[bool, bool]:
        """Internal input handler that returns both processed and exit flags.

        Returns:
            Tuple of (input_processed: bool, exit_requested: bool)
        """
        try:
            # Priority 1: Check pending_input queue (from event-routed input)
            try:
                key_press = self.pending_input.get_nowait()
                if key_press:
                    logger.info(
                        f"Processing queued input: {key_press.name} ({key_press.char})"
                    )
                    self.stats.input_events += 1
                    exit_requested = await self.plugin.handle_input(key_press)
                    return (
                        True,
                        exit_requested,
                    )  # input_processed=True, exit_requested from plugin
            except asyncio.QueueEmpty:
                pass  # No queued input, try fallback

            # Priority 2: Direct stdin reading (fallback when main input loop not running)
            # Check for available input (non-blocking)
            if not sys.stdin.isatty():
                return (False, False)  # No input, no exit

            # Platform-specific input checking
            has_input = False
            char = None

            if IS_WINDOWS:
                # Windows: Use msvcrt to check for input
                if msvcrt.kbhit():  # type: ignore[attr-defined]
                    has_input = True
                    char_bytes = msvcrt.getch()  # type: ignore[attr-defined]
                    char = (
                        char_bytes.decode("utf-8", errors="ignore")
                        if char_bytes
                        else None
                    )

                    # Handle extended keys on Windows (arrow keys, etc.)
                    if char_bytes and char_bytes[0] in (0, 224):
                        ext_char = msvcrt.getch()  # type: ignore[attr-defined]
                        ext_code = ext_char[0] if ext_char else 0
                        # Map Windows extended key codes to escape sequences
                        win_key_map = {
                            72: "\x1b[A",  # ArrowUp
                            80: "\x1b[B",  # ArrowDown
                            75: "\x1b[D",  # ArrowLeft
                            77: "\x1b[C",  # ArrowRight
                            71: "\x1b[H",  # Home
                            79: "\x1b[F",  # End
                            83: "\x1b[3~",  # Delete
                        }
                        if ext_code in win_key_map:
                            # Process escape sequence character by character
                            for c in win_key_map[ext_code]:
                                if not hasattr(self, "_key_parser"):
                                    self._key_parser = KeyParser()
                                key_press = self._key_parser.parse_char(c)
                            if key_press:
                                self.stats.input_events += 1
                                exit_requested = await self.plugin.handle_input(
                                    key_press
                                )
                                return (True, exit_requested)
                        return (False, False)
            else:
                # Unix: Use select to check for input without blocking
                ready, _, _ = select.select([sys.stdin], [], [], 0)
                if ready:
                    has_input = True
                    # Read all available characters at once
                    import fcntl
                    import os as os_module

                    # Set non-blocking mode temporarily
                    fd = sys.stdin.fileno()
                    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                    fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os_module.O_NONBLOCK)
                    try:
                        chars = sys.stdin.read(32)  # Read up to 32 chars
                    except (IOError, BlockingIOError):
                        chars = ""
                    finally:
                        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)

                    if not chars:
                        return (False, False)

            if not has_input:
                return (False, False)

            # Use the actual key parser for proper parsing
            if not hasattr(self, "_key_parser"):
                self._key_parser = KeyParser()

            # Parse all characters through the key parser
            key_press = None
            for char in chars:
                key_press = self._key_parser.parse_char(char)
                if key_press:
                    break

            # If still no key press, check for standalone ESC
            if not key_press:
                key_press = self._key_parser.check_for_standalone_escape()
                if not key_press:
                    return (False, False)

            self.stats.input_events += 1

            # Let plugin handle input (plugin decides if it wants to exit)
            exit_requested = await self.plugin.handle_input(key_press)
            return (True, exit_requested)

        except Exception as e:
            logger.error(f"Error handling input: {e}")
            return (False, False)

    async def _cleanup(self):
        """Clean up session resources."""
        try:
            self.running = False
            self.stats.end_time = _get_loop().time()

            # Calculate final stats
            if self.stats.duration > 0:
                self.stats.average_fps = self.stats.frame_count / self.stats.duration

            # Stop plugin
            await self.plugin.on_stop()

            # Restore terminal
            self.renderer.restore_terminal()

            # Unregister input hook if it was registered
            if self.input_hook_registered and self.event_bus:
                try:
                    hook_id = f"fullscreen_session_{self.plugin.name}.fullscreen_input"
                    await self.event_bus.unregister_hook(hook_id)
                    self.input_hook_registered = False
                    logger.info(
                        f"✅ Unregistered FULLSCREEN_INPUT hook for {self.plugin.name}"
                    )
                except Exception as e:
                    logger.error(f"Error unregistering hook: {e}")

            # Cleanup plugin
            await self.plugin.cleanup()

            logger.info(f"Session cleanup complete for {self.plugin.name}")
            logger.info(
                f"Session stats: {self.stats.frame_count} frames, "
                f"{self.stats.average_fps:.1f} fps, "
                f"{self.stats.input_events} inputs, "
                f"{self.stats.duration:.1f}s duration"
            )

        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")

    def stop(self):
        """Stop the session gracefully."""
        self.running = False
        logger.info(f"Stop requested for session: {self.plugin.name}")

    def get_stats(self) -> SessionStats:
        """Get current session statistics.

        Returns:
            Current session statistics.
        """
        return self.stats
