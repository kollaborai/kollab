"""Display queue for buffering and replaying AltView frames."""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class QueuedFrame:
    """A single captured frame from an AltView render cycle.

    Attributes:
        timestamp: Monotonic time when the frame was captured.
        render_content: Full rendered output string for this frame.
        frame_number: Sequential counter for ordering.
    """

    timestamp: float
    render_content: str
    frame_number: int


class DisplayQueue:
    """Buffers rendered frames and supports accelerated replay.

    When an AltView is suspended (e.g. user switches away), the queue
    captures frames so they can be replayed at an accelerated speed when
    the user returns. This keeps background work visible without
    blocking the foreground view.

    Replay logic:
    - Preserves relative timing between frames, compressed by REPLAY_SPEED.
    - If total buffered duration exceeds MAX_BUFFER_SECONDS, trims to the
      last 30 frames before replaying.
    - Supports skip requests to jump straight to the final frame.
    """

    MAX_BUFFER_SECONDS: float = 30.0
    MAX_FRAMES: int = 900  # ~30s at 30fps
    REPLAY_SPEED: float = 3.0

    def __init__(self) -> None:
        self._frames: deque[QueuedFrame] = deque(maxlen=self.MAX_FRAMES)
        self._capturing: bool = False
        self._replay_active: bool = False
        self._skip_requested: bool = False
        self._frame_counter: int = 0

    def start_capture(self) -> None:
        """Begin capturing frames into the buffer."""
        self._capturing = True
        self._skip_requested = False
        logger.debug("DisplayQueue: capture started")

    def stop_capture(self) -> None:
        """Stop capturing frames."""
        self._capturing = False
        logger.debug(
            "DisplayQueue: capture stopped, %d frames buffered",
            len(self._frames),
        )

    async def replay(self, render_fn: Callable[[str], Awaitable[None]]) -> None:
        """Replay buffered frames at accelerated speed.

        Renders each captured frame through the provided callback,
        preserving relative timing compressed by REPLAY_SPEED. If the
        total buffered duration exceeds MAX_BUFFER_SECONDS, only the
        last 30 frames are replayed.

        A skip request (via request_skip) will immediately render the
        final frame and return.

        Args:
            render_fn: Async callable that writes one frame to the terminal.
        """
        if not self._frames:
            return

        self._replay_active = True
        self._skip_requested = False

        try:
            frames = list(self._frames)

            # if total buffered time exceeds threshold, trim to last 30
            if len(frames) >= 2:
                total_duration = frames[-1].timestamp - frames[0].timestamp
                if total_duration > self.MAX_BUFFER_SECONDS:
                    frames = frames[-30:]
                    logger.debug(
                        "DisplayQueue: trimmed replay to last 30 frames "
                        "(buffered %.1fs > %.1fs limit)",
                        total_duration,
                        self.MAX_BUFFER_SECONDS,
                    )

            for i, frame in enumerate(frames):
                if self._skip_requested:
                    # render the final frame and bail
                    await render_fn(frames[-1].render_content)
                    logger.debug("DisplayQueue: skip requested, jumped to final frame")
                    return

                await render_fn(frame.render_content)

                # sleep for the compressed delta between this frame and the next
                if i < len(frames) - 1:
                    delta = frames[i + 1].timestamp - frame.timestamp
                    sleep_time = delta / self.REPLAY_SPEED
                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)

            logger.debug(
                "DisplayQueue: replay complete, %d frames rendered",
                len(frames),
            )

        finally:
            self._replay_active = False

    def request_skip(self) -> None:
        """Request that the current replay skip to the final frame."""
        if self._replay_active:
            self._skip_requested = True
            logger.debug("DisplayQueue: skip requested")

    @property
    def is_capturing(self) -> bool:
        """Whether the queue is currently capturing frames."""
        return self._capturing

    @property
    def is_replaying(self) -> bool:
        """Whether the queue is currently replaying frames."""
        return self._replay_active

    @property
    def frame_count(self) -> int:
        """Number of frames currently in the buffer."""
        return len(self._frames)

    def clear(self) -> None:
        """Discard all buffered frames and reset the counter."""
        self._frames.clear()
        self._frame_counter = 0
        self._skip_requested = False
        logger.debug("DisplayQueue: cleared")
