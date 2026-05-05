"""Event-driven render loop for efficient UI updates.

This module provides a reusable event-driven rendering system that:
- Polls input frequently (100Hz) for instant responsiveness
- Renders only when needed (on input OR timer interval)
- Eliminates unnecessary redraws for static content
- Prevents CPU spinning with efficient sleep timing

Usage:
    loop = EventDrivenRenderLoop(
        render_callback=my_render_function,
        input_callback=my_input_function,
        target_fps=20.0,
        input_poll_rate=100.0
    )

    await loop.run()
"""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Tuple

logger = logging.getLogger(__name__)


class RenderTrigger(Enum):
    """Reasons why a render was triggered."""

    INPUT = "input"  # User input received
    TIMER = "timer"  # Target FPS interval reached
    FORCED = "forced"  # Explicitly requested
    INITIAL = "initial"  # First render


@dataclass
class RenderLoopStats:
    """Statistics for render loop performance monitoring."""

    total_frames: int = 0
    input_triggered_frames: int = 0
    timer_triggered_frames: int = 0
    forced_frames: int = 0
    total_input_events: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    total_hibernation_time: float = 0.0

    @property
    def duration(self) -> float:
        """Total runtime duration."""
        if self.end_time > 0:
            return self.end_time - self.start_time
        return 0.0

    @property
    def average_fps(self) -> float:
        """Average frames per second."""
        if self.duration > 0:
            return self.total_frames / self.duration
        return 0.0

    @property
    def input_efficiency(self) -> float:
        """Percentage of renders triggered by input (higher = more efficient)."""
        if self.total_frames > 0:
            return (self.input_triggered_frames / self.total_frames) * 100.0
        return 0.0


class EventDrivenRenderLoop:
    """Reusable event-driven render loop with efficient input polling.

    This class implements the event-driven rendering pattern where:
    1. Input is polled frequently (default 100Hz) for instant response
    2. Rendering happens only when:
       - User input is received (instant feedback)
       - Target FPS interval is reached (periodic updates)
       - Explicitly forced (external trigger)
    3. CPU usage is minimized with efficient sleep timing

    Attributes:
        target_fps: Target frames per second for periodic renders
        input_poll_rate: Input polling frequency in Hz (default 100)
        render_callback: Async function called to render frame
        input_callback: Async function called to check for input
        stats: Performance statistics
    """

    def __init__(
        self,
        render_callback: Callable[[float, RenderTrigger], Any],
        input_callback: Callable[[], Awaitable[Tuple[bool, bool]]],
        target_fps: float = 20.0,
        input_poll_rate: float = 100.0,
        name: str = "RenderLoop",
    ):
        """Initialize event-driven render loop.

        Args:
            render_callback: Async function(delta_time, trigger) -> should_continue
                Returns False to exit loop, True to continue
            input_callback: Async function() -> (input_processed, should_exit)
                Returns (True, False) if input processed but don't exit
                Returns (True, True) if input processed and should exit
                Returns (False, False) if no input
            target_fps: Target frames per second for periodic renders (must be > 0)
            input_poll_rate: Input polling frequency in Hz (must be > 0)
            name: Name for logging purposes

        Raises:
            ValueError: If target_fps or input_poll_rate <= 0
        """
        # Validate parameters
        if target_fps <= 0:
            raise ValueError(f"target_fps must be > 0, got {target_fps}")
        if input_poll_rate <= 0:
            raise ValueError(f"input_poll_rate must be > 0, got {input_poll_rate}")

        self.render_callback = render_callback
        self.input_callback = input_callback
        self.target_fps = target_fps
        self.input_poll_rate = input_poll_rate
        self.name = name

        # Timing (safe division after validation)
        self.frame_delay = 1.0 / target_fps
        self.poll_delay = 1.0 / input_poll_rate

        # State
        self.running = False
        self.force_render = False
        self.stats = RenderLoopStats()

        # Hibernate/thaw: asyncio.Event that the loop awaits.
        # When cleared, the loop coroutine suspends (zero CPU).
        # When set, the loop runs normally.
        self._awake: asyncio.Event = asyncio.Event()
        self._awake.set()  # start awake
        self._hibernate_start: float = 0.0

        logger.info(
            f"{name} initialized: target_fps={target_fps}, "
            f"input_poll_rate={input_poll_rate}"
        )

    def request_render(self):
        """Request an immediate render on next loop iteration.

        This can be called from external code to force a render
        without waiting for input or timer interval.
        """
        self.force_render = True

    async def run(self) -> bool:
        """Run the event-driven render loop.

        Returns:
            True if loop completed successfully, False if error occurred.
        """
        try:
            self.running = True
            self.stats = RenderLoopStats()
            self.stats.start_time = _get_loop().time()

            logger.info(f"{self.name} starting")

            # Run the loop
            success = await self._loop()

            self.stats.end_time = _get_loop().time()
            logger.info(
                f"{self.name} completed: {self.stats.total_frames} frames, "
                f"{self.stats.average_fps:.1f} fps, "
                f"{self.stats.input_efficiency:.1f}% input-triggered"
            )

            return success

        except Exception as e:
            logger.error(f"{self.name} error: {e}")
            return False
        finally:
            self.running = False

    async def _loop(self) -> bool:
        """Main event-driven render loop.

        Strategy:
        1. Check input frequently (100Hz default)
        2. Render when:
           - Input received (instant feedback)
           - Target FPS interval reached (periodic updates)
           - Explicitly forced via request_render()
        3. Sleep efficiently to prevent CPU spinning

        Returns:
            True if loop completed normally, False if error.
        """
        last_frame_time = _get_loop().time()
        last_render_time = _get_loop().time()

        # Initial render
        delta_time = 0.0
        try:
            should_continue = await self.render_callback(
                delta_time, RenderTrigger.INITIAL
            )
            if not should_continue:
                return True
        except Exception as e:
            logger.error(f"{self.name} initial render failed: {e}", exc_info=True)
            return False

        self.stats.total_frames += 1
        # CRITICAL: Update last_render_time after initial render
        # Otherwise time_since_render will be incorrect for first loop iterations
        last_render_time = _get_loop().time()
        logger.info(f"{self.name} initial render complete, starting loop")

        while self.running:
            # Hibernate gate: if hibernate() was called, the coroutine
            # suspends here with zero CPU until thaw() sets the event.
            await self._awake.wait()

            current_time = _get_loop().time()
            delta_time = current_time - last_frame_time

            try:
                # Check for input (non-blocking, frequent polling)
                input_processed, should_exit = await self.input_callback()

                if should_exit:
                    logger.info(f"{self.name} exit requested by input")
                    return True

                if input_processed:
                    self.stats.total_input_events += 1
                    self.force_render = True

                # Determine if we should render
                time_since_render = current_time - last_render_time
                should_render_timer = time_since_render >= self.frame_delay
                should_render_forced = self.force_render

                # Render if forced OR timer elapsed (timer can trigger even if no input)
                # This ensures animations continue even when user stops typing
                trigger = None
                if should_render_forced:
                    trigger = (
                        RenderTrigger.INPUT if input_processed else RenderTrigger.FORCED
                    )
                elif should_render_timer:
                    trigger = RenderTrigger.TIMER

                if trigger:
                    # Execute render
                    try:
                        should_continue = await self.render_callback(
                            delta_time, trigger
                        )

                        if not should_continue:
                            logger.info(
                                f"{self.name} exit requested by render callback"
                            )
                            return True

                        # Update stats
                        self.stats.total_frames += 1
                        if trigger == RenderTrigger.INPUT:
                            self.stats.input_triggered_frames += 1
                        elif trigger == RenderTrigger.TIMER:
                            self.stats.timer_triggered_frames += 1
                        elif trigger == RenderTrigger.FORCED:
                            self.stats.forced_frames += 1

                        # Update last_render_time to NOW (after render completes)
                        # This ensures time_since_render is accurate for next iteration
                        last_render_time = _get_loop().time()
                        self.force_render = False
                    except Exception as e:
                        logger.error(
                            f"{self.name} render callback failed: {e}", exc_info=True
                        )
                        # Don't exit on render errors, just log and continue
                        last_render_time = _get_loop().time()
                        self.force_render = False

                # Update frame time for delta calculation
                last_frame_time = current_time

                # Efficient sleep to prevent CPU spinning
                # Sleep for poll_delay (10ms for 100Hz input polling)
                await asyncio.sleep(self.poll_delay)

            except Exception as e:
                logger.error(f"{self.name} loop error: {e}", exc_info=True)
                return False

        return True

    def hibernate(self) -> None:
        """Suspend the render loop. Zero CPU until thaw() is called.

        The loop coroutine will block at ``await self._awake.wait()``
        and will not poll input or render frames until woken.
        """
        if not self._awake.is_set():
            logger.debug(f"{self.name} already hibernating")
            return
        self._awake.clear()
        self._hibernate_start = _get_loop().time()
        logger.info(f"{self.name} hibernated")

    def thaw(self) -> None:
        """Resume the render loop after hibernation.

        Wakes the coroutine immediately and forces a render on the
        next iteration so the UI updates without waiting for input
        or the timer interval.
        """
        if self._awake.is_set():
            logger.debug(f"{self.name} already awake")
            return
        if self._hibernate_start > 0:
            duration = _get_loop().time() - self._hibernate_start
            self.stats.total_hibernation_time += duration
            logger.info(f"{self.name} thawed after {duration:.1f}s hibernation")
        self._awake.set()
        self.force_render = True  # immediate first frame on wake

    @property
    def is_hibernating(self) -> bool:
        """True when the loop is suspended via hibernate()."""
        return not self._awake.is_set()

    def stop(self):
        """Stop the render loop gracefully."""
        logger.info(f"{self.name} stop requested")
        self.running = False
        # Wake the loop if hibernating so it can exit cleanly
        self._awake.set()

    def get_stats(self) -> RenderLoopStats:
        """Get current performance statistics.

        Returns:
            Current render loop statistics.
        """
        return self.stats
