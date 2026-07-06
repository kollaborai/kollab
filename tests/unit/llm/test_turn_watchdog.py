"""Tests for TurnWatchdog — the silent-but-alive session recovery net.

The detector is pure and clock-injected, so every case runs without sleeping
or real time. The critical case is test_slow_api_call_never_flagged: a healthy
but slow model call must never be treated as a wedge.
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor.llm.turn_watchdog import TurnWatchdog
from kollabor_agent.queue_processor import QueueProcessor


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeQueue:
    def __init__(self, n=0):
        self._n = n

    def empty(self):
        return self._n == 0

    def qsize(self):
        return self._n

    def set(self, n):
        self._n = n


class FakeToolExec:
    def __init__(self, executing=False):
        self.tool_executing = executing


class FakeQP:
    def __init__(self, clock, **kw):
        self._clock = clock
        self.is_processing = kw.get("is_processing", False)
        self.turn_completed = kw.get("turn_completed", False)
        self.cancel_processing = kw.get("cancel_processing", False)
        self.question_gate_active = kw.get("question_gate_active", False)
        self.last_progress_at = kw.get("last_progress_at", clock())
        self.processing_queue = FakeQueue(kw.get("queue", 0))
        self.tool_executor = FakeToolExec(kw.get("tool_executing", False))
        self.progress_marks = 0

    def mark_progress(self):
        self.progress_marks += 1
        self.last_progress_at = self._clock()


class FakeTask:
    def __init__(self, done):
        self._done = done

    def done(self):
        return self._done


class FakeAPI:
    def __init__(self, in_flight=False):
        # None = no call; a not-done task = a call genuinely in flight
        self.current_request_task = FakeTask(False) if in_flight else None


class FakeMH:
    def __init__(self):
        self._retry_pending = False


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make(clock, api=None, kicks=None, **qp_kw):
    api = api or FakeAPI()
    mh = FakeMH()
    calls = kicks if kicks is not None else []

    async def restart():
        calls.append(1)

    qp = FakeQP(clock, **qp_kw)
    wd = TurnWatchdog(
        qp,
        api,
        mh,
        restart,
        stuck_threshold_s=600,
        check_interval_s=60,
        clock=clock,
    )
    return wd, qp, api, mh, calls


class TestTurnWatchdogDetect(unittest.TestCase):
    def test_healthy_idle_not_flagged(self):
        c = Clock()
        wd, *_ = _make(c, is_processing=False, queue=0, last_progress_at=c())
        c.t += 10_000  # ages far past threshold
        self.assertIsNone(wd.detect())

    def test_stuck_busy_detected(self):
        c = Clock()
        wd, *_ = _make(c, is_processing=True, last_progress_at=c())
        c.t += 601  # just past 600s threshold
        self.assertEqual(wd.detect(), "stuck_busy")

    def test_busy_within_threshold_not_flagged(self):
        c = Clock()
        wd, *_ = _make(c, is_processing=True, last_progress_at=c())
        c.t += 300  # under threshold
        self.assertIsNone(wd.detect())

    def test_slow_api_call_never_flagged(self):
        """The false-positive guard: a genuinely slow model call, no matter
        how long, is NOT a wedge. This is the whole point — never kill
        healthy work."""
        c = Clock()
        wd, *_ = _make(
            c, api=FakeAPI(in_flight=True), is_processing=True, last_progress_at=c()
        )
        c.t += 100_000  # absurdly long, but the API call is genuinely running
        self.assertIsNone(wd.detect())

    def test_long_running_tool_never_flagged(self):
        """A long foreground tool (e.g. a 10-min build) holds is_processing
        with no API call in flight — it must NOT be flagged as a wedge."""
        c = Clock()
        wd, *_ = _make(
            c, is_processing=True, tool_executing=True, last_progress_at=c()
        )
        c.t += 100_000  # long build; tool genuinely executing
        self.assertIsNone(wd.detect())

    def test_cancel_in_progress_not_flagged(self):
        c = Clock()
        wd, *_ = _make(
            c, is_processing=True, cancel_processing=True, last_progress_at=c()
        )
        c.t += 10_000
        self.assertIsNone(wd.detect())

    def test_question_gate_not_flagged(self):
        c = Clock()
        wd, *_ = _make(
            c, is_processing=True, question_gate_active=True, last_progress_at=c()
        )
        c.t += 10_000
        self.assertIsNone(wd.detect())

    def test_orphaned_queue_needs_two_strikes(self):
        c = Clock()
        wd, qp, *_ = _make(c, is_processing=False, queue=2, last_progress_at=c())
        # First check: one strike, not yet healed (avoid racing normal drain).
        self.assertIsNone(wd.detect())
        # Second consecutive check: now it's orphaned.
        self.assertEqual(wd.detect(), "orphaned_queue")

    def test_orphan_strikes_reset_when_processing_resumes(self):
        c = Clock()
        wd, qp, *_ = _make(c, is_processing=False, queue=1, last_progress_at=c())
        self.assertIsNone(wd.detect())  # strike 1
        qp.is_processing = True  # a drain started — no longer orphaned
        self.assertIsNone(wd.detect())  # resets strikes
        qp.is_processing = False
        self.assertIsNone(wd.detect())  # strike 1 again, not 2
        self.assertEqual(wd.detect(), "orphaned_queue")  # strike 2


class TestTurnWatchdogHeal(unittest.TestCase):
    def test_heal_stuck_busy_resets_flags_and_kicks(self):
        c = Clock()
        wd, qp, api, mh, calls = _make(
            c, is_processing=True, turn_completed=False, queue=1, last_progress_at=c()
        )
        mh._retry_pending = True
        c.t += 601
        mode = _run(wd.check_once())
        self.assertEqual(mode, "stuck_busy")
        self.assertFalse(qp.is_processing)
        self.assertTrue(qp.turn_completed)
        self.assertFalse(mh._retry_pending)
        self.assertEqual(len(calls), 1)  # queue had work → re-kicked
        self.assertGreater(qp.progress_marks, 0)  # heartbeat reset

    def test_heal_stuck_busy_no_kick_when_queue_empty(self):
        c = Clock()
        wd, qp, api, mh, calls = _make(
            c, is_processing=True, queue=0, last_progress_at=c()
        )
        c.t += 601
        mode = _run(wd.check_once())
        self.assertEqual(mode, "stuck_busy")
        self.assertFalse(qp.is_processing)
        self.assertEqual(len(calls), 0)  # nothing to drain → no kick

    def test_heal_orphaned_queue_kicks(self):
        c = Clock()
        wd, qp, api, mh, calls = _make(
            c, is_processing=False, queue=3, last_progress_at=c()
        )
        _run(wd.check_once())  # strike 1, no heal
        self.assertEqual(len(calls), 0)
        mode = _run(wd.check_once())  # strike 2 → heal
        self.assertEqual(mode, "orphaned_queue")
        self.assertEqual(len(calls), 1)

    def test_check_once_healthy_returns_none_no_kick(self):
        c = Clock()
        wd, qp, api, mh, calls = _make(c, is_processing=False, queue=0)
        c.t += 10_000
        self.assertIsNone(_run(wd.check_once()))
        self.assertEqual(len(calls), 0)

    def test_sync_restart_callback_supported(self):
        """restart_queue may be a plain (non-async) callable."""
        c = Clock()
        hits = []
        qp = FakeQP(c, is_processing=True, queue=1, last_progress_at=c())
        wd = TurnWatchdog(
            qp,
            FakeAPI(),
            FakeMH(),
            lambda: hits.append(1),  # sync callback returning None
            stuck_threshold_s=600,
            check_interval_s=60,
            clock=c,
        )
        c.t += 601
        _run(wd.check_once())
        self.assertEqual(len(hits), 1)


class TestTurnWatchdogAgainstRealQueueProcessor(unittest.TestCase):
    """Contract test: the watchdog reads a REAL QueueProcessor, not a fake.

    Catches drift between the watchdog's assumptions (last_progress_at,
    mark_progress, is_processing, processing_queue, question_gate_active,
    cancel_processing) and the actual class it watches in production.
    """

    def _make_real_qp(self):
        native = MagicMock()
        native.tools = None
        native.tool_calling_enabled = False
        native.discovery_complete = asyncio.Event()
        native.discovery_complete.set()
        return QueueProcessor(
            conversation_history=[],
            session_stats={},
            stats={},
            pending_tools=[],
            queue_metrics={
                "total_enqueue_attempts": 0,
                "total_enqueue_successes": 0,
                "drop_oldest_count": 0,
                "drop_newest_count": 0,
                "block_count": 0,
                "block_timeout_count": 0,
            },
            task_config=MagicMock(),
            api_service=AsyncMock(),
            tool_executor=MagicMock(),
            response_parser=MagicMock(),
            message_display_service=MagicMock(),
            renderer=MagicMock(),
            config=MagicMock(get=MagicMock(return_value=0.1)),
            event_bus=MagicMock(),
            conversation_logger=AsyncMock(),
            streaming_handler=MagicMock(),
            native_tools_handler=native,
            add_message_fn=MagicMock(),
            max_history=90,
            question_gate_enabled=False,
            max_queue_size=10,
        )

    def test_real_qp_has_heartbeat_contract(self):
        qp = self._make_real_qp()
        # The fields/method the watchdog depends on must exist for real.
        self.assertTrue(hasattr(qp, "last_progress_at"))
        self.assertTrue(callable(getattr(qp, "mark_progress", None)))
        before = qp.last_progress_at
        time.sleep(0.001)
        qp.mark_progress()
        self.assertGreaterEqual(qp.last_progress_at, before)

    def test_real_qp_stuck_busy_detected_and_healed(self):
        clock = Clock()
        qp = self._make_real_qp()
        qp.tool_executor.tool_executing = False  # MagicMock would be truthy
        qp.is_processing = True
        qp.turn_completed = False
        qp.last_progress_at = clock()  # align heartbeat to injected clock
        qp.processing_queue.put_nowait("stranded message")

        api = FakeAPI(in_flight=False)
        mh = FakeMH()
        mh._retry_pending = True
        kicks = []

        async def restart():
            kicks.append(1)

        wd = TurnWatchdog(
            qp, api, mh, restart,
            stuck_threshold_s=600, check_interval_s=60, clock=clock,
        )
        clock.t += 601  # wedge past threshold

        mode = _run(wd.check_once())
        self.assertEqual(mode, "stuck_busy")
        self.assertFalse(qp.is_processing)  # real flag reset
        self.assertTrue(qp.turn_completed)
        self.assertFalse(mh._retry_pending)
        self.assertEqual(len(kicks), 1)  # stranded message → re-kicked

    def test_real_qp_slow_api_not_flagged(self):
        clock = Clock()
        qp = self._make_real_qp()
        qp.tool_executor.tool_executing = False
        qp.is_processing = True
        qp.last_progress_at = clock()

        class RunningTask:
            def done(self):
                return False

        api = MagicMock()
        api.current_request_task = RunningTask()  # genuinely in flight
        wd = TurnWatchdog(
            qp, api, FakeMH(), lambda: None,
            stuck_threshold_s=600, check_interval_s=60, clock=clock,
        )
        clock.t += 100_000
        self.assertIsNone(wd.detect())  # slow call, not a wedge


if __name__ == "__main__":
    unittest.main()
