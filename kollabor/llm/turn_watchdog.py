"""Progress watchdog — recovers a session that goes silent while alive.

The recurring "process up, agent mute" failure: the queue processor's state
flags (``is_processing`` / ``turn_completed``) or the message handler's
``_retry_pending`` get wedged, so no turn advances even though there is work
to do. The process stays alive; the agent just stops. Every session-dying bug
in the 2026-07-03 investigation was an instance of this class.

The watchdog is the safety net that ends it. It keeps a heartbeat
(``QueueProcessor.last_progress_at``, bumped on every real turn and enqueue)
and, on a slow cadence, detects two wedge modes and self-heals:

- **stuck-busy**: ``is_processing`` has been True with no forward progress for
  longer than the stuck threshold, and nothing legitimately explains it — no
  API call is in flight, no question gate is open, no cancel is pending. The
  turn is wedged. Heal: reset the flags, close the phantom turn, re-kick the
  queue if work remains.
- **orphaned-queue**: ``is_processing`` is False but the processing queue is
  non-empty across two consecutive checks — a message that never got drained
  (the normal path launches the drain on enqueue; if that failed, the message
  sits forever). Heal: relaunch the queue drain.

Design note — the false-positive guard is everything. A genuinely slow model
call must NOT be treated as a wedge, or the watchdog would kill healthy work
(exactly the bug this whole effort is undoing). So stuck-busy requires that
``api_service.current_request_task`` is done/absent: if the agent is actually
waiting on the model, it is not wedged, full stop.

The detector is pure and clock-injected so it is unit-testable without
sleeping or real time. ``run()`` is the only part that awaits the clock.
"""

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# A wedged session must sit still for this long before the watchdog acts. It
# has to exceed any plausible single healthy turn; the in-flight API check
# already covers slow model calls, so this only bounds a turn that is stuck
# with nothing running. Generous on purpose — never race healthy work.
DEFAULT_STUCK_THRESHOLD_S = 600.0  # 10 minutes
DEFAULT_CHECK_INTERVAL_S = 60.0  # how often the watchdog wakes


class TurnWatchdog:
    """Detects and recovers a wedged (silent-but-alive) session."""

    def __init__(
        self,
        queue_processor,
        api_service,
        message_handler,
        restart_queue: Callable[[], Awaitable[None] | None],
        *,
        stuck_threshold_s: float = DEFAULT_STUCK_THRESHOLD_S,
        check_interval_s: float = DEFAULT_CHECK_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        """
        Args:
            queue_processor: the QueueProcessor whose state is watched
            api_service: exposes ``current_request_task`` (in-flight API call)
            message_handler: exposes ``_retry_pending`` to clear on heal
            restart_queue: callback that relaunches the queue drain (the
                coordinator's ``_process_queue`` launcher). May be sync or
                async; the return value is awaited if awaitable.
            stuck_threshold_s: seconds of no-progress before stuck-busy fires
            check_interval_s: seconds between checks
            clock: monotonic clock, injectable for tests
        """
        self._qp = queue_processor
        self._api = api_service
        self._mh = message_handler
        self._restart_queue = restart_queue
        self._stuck_threshold_s = stuck_threshold_s
        self._check_interval_s = check_interval_s
        self._clock = clock
        # orphaned-queue requires two consecutive positive checks so we never
        # race a message that a normal drain is about to pick up.
        self._orphan_strikes = 0

    def _api_in_flight(self) -> bool:
        """True when a real API request is genuinely awaiting the provider.

        This is the guard that keeps a slow-but-healthy model call from being
        mistaken for a wedge.
        """
        task = getattr(self._api, "current_request_task", None)
        return bool(task is not None and not task.done())

    def _tool_in_flight(self) -> bool:
        """True when a tool is genuinely executing (e.g. a long build).

        Sibling guard to _api_in_flight: a foreground tool can hold
        is_processing for minutes with no API call in flight. Without this,
        the watchdog would flag that healthy work as a wedge and interrupt it.
        """
        executor = getattr(self._qp, "tool_executor", None)
        return bool(getattr(executor, "tool_executing", False))

    def detect(self) -> Optional[str]:
        """Return the wedge mode if the session is stuck, else None.

        Pure: no side effects, no awaiting. Safe to call in tests with a
        crafted clock and state.
        """
        qp = self._qp

        # A cancel in progress is an intentional stop, not a wedge.
        if getattr(qp, "cancel_processing", False):
            self._orphan_strikes = 0
            return None
        # A question gate is intentionally waiting on the user.
        if getattr(qp, "question_gate_active", False):
            self._orphan_strikes = 0
            return None

        now = self._clock()
        since_progress = now - getattr(qp, "last_progress_at", now)

        # Mode A — stuck-busy: claims to be processing, but nothing has moved
        # for too long and neither a model call nor a tool execution explains
        # the silence. Both in-flight guards are what keep healthy-but-slow
        # work (a slow model, a long build) from being killed.
        if (
            getattr(qp, "is_processing", False)
            and since_progress > self._stuck_threshold_s
            and not self._api_in_flight()
            and not self._tool_in_flight()
        ):
            self._orphan_strikes = 0
            return "stuck_busy"

        # Mode B — orphaned-queue: idle but work is waiting. Require two
        # consecutive checks so a just-enqueued message that the normal drain
        # is about to handle is not misread as orphaned.
        queue = getattr(qp, "processing_queue", None)
        queue_has_work = queue is not None and not queue.empty()
        if not getattr(qp, "is_processing", False) and queue_has_work:
            self._orphan_strikes += 1
            if self._orphan_strikes >= 2:
                return "orphaned_queue"
            return None

        self._orphan_strikes = 0
        return None

    async def heal(self, mode: str) -> None:
        """Recover from a detected wedge. Conservative and idempotent."""
        qp = self._qp
        snapshot = (
            f"is_processing={getattr(qp, 'is_processing', None)} "
            f"turn_completed={getattr(qp, 'turn_completed', None)} "
            f"queue={getattr(getattr(qp, 'processing_queue', None), 'qsize', lambda: '?')()} "
            f"retry_pending={getattr(self._mh, '_retry_pending', None)} "
            f"since_progress={self._clock() - getattr(qp, 'last_progress_at', self._clock()):.0f}s"
        )

        if mode == "stuck_busy":
            logger.error(
                "TurnWatchdog: session wedged (stuck-busy) — recovering. %s",
                snapshot,
            )
            # Close the phantom turn and clear the stuck flags so a fresh turn
            # can start. Reset progress so we don't immediately re-fire.
            qp.is_processing = False
            qp.turn_completed = True
            if hasattr(self._mh, "_retry_pending"):
                self._mh._retry_pending = False
            qp.mark_progress()
            self._orphan_strikes = 0
            # If work is waiting, re-kick the drain so it gets processed.
            queue = getattr(qp, "processing_queue", None)
            if queue is not None and not queue.empty():
                await self._kick()

        elif mode == "orphaned_queue":
            logger.error(
                "TurnWatchdog: work stranded in queue with no processor — "
                "relaunching drain. %s",
                snapshot,
            )
            self._orphan_strikes = 0
            qp.mark_progress()
            await self._kick()

    async def _kick(self) -> None:
        """Invoke the restart-queue callback, awaiting it if it is async."""
        try:
            result = self._restart_queue()
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                await result
        except Exception as e:
            logger.error(f"TurnWatchdog: re-kick failed: {e}")

    async def check_once(self) -> Optional[str]:
        """Run one detect+heal cycle. Returns the mode healed, or None."""
        try:
            mode = self.detect()
        except Exception as e:
            logger.error(f"TurnWatchdog detect error: {e}")
            return None
        if mode:
            await self.heal(mode)
        return mode

    async def run(self) -> None:
        """Background loop: check on a slow cadence until cancelled."""
        logger.info(
            "TurnWatchdog started (stuck>%.0fs, every %.0fs)",
            self._stuck_threshold_s,
            self._check_interval_s,
        )
        while True:
            try:
                await asyncio.sleep(self._check_interval_s)
                await self.check_once()
            except asyncio.CancelledError:
                logger.info("TurnWatchdog stopped")
                break
            except Exception as e:  # never let the watchdog itself die
                logger.error(f"TurnWatchdog loop error: {e}")
