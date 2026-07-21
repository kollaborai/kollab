"""Tests for MessageHandler."""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor.llm.message_handler import MessageHandler


def _make_coordinator(**overrides):
    """Create a mock coordinator with standard defaults."""
    coord = MagicMock()

    # Event bus with startup_ready already set
    startup_ready = asyncio.Event()
    startup_ready.set()
    coord.event_bus.get_service = MagicMock(return_value=startup_ready)

    # Renderer
    coord.renderer.pipe_mode = False
    coord.renderer.input_handler = MagicMock()
    coord.renderer.input_handler.buffer_manager = MagicMock()
    coord.renderer.input_handler.buffer_manager.content = ""

    # Queue/continue flow
    coord._queue_processor = MagicMock()
    coord._queue_processor.cancel_processing = False
    coord._queue_processor.is_processing = False
    coord._queue_processor.turn_completed = True
    coord._continue_conversation = AsyncMock()

    # Context service
    coord.context_service.trigger_context_injection = AsyncMock()

    # Conversation logger
    coord.conversation_logger.log_user_message = AsyncMock(return_value="uuid-1")
    coord.conversation_logger.log_assistant_message = AsyncMock(return_value="uuid-2")
    coord.conversation_logger.log_system_message = AsyncMock()

    # Message display service
    coord.message_display_service.show_loading = MagicMock()
    coord.message_display_service.hide_loading = MagicMock()
    coord.message_display_service.message_coordinator = MagicMock()
    coord.message_display_service.message_coordinator.display_message_sequence = (
        AsyncMock()
    )

    # Functions
    coord._add_conversation_message = MagicMock()
    coord._enqueue_with_overflow_strategy = AsyncMock()
    coord.cancel_current_request = MagicMock()
    coord.process_user_input = AsyncMock(return_value={"status": "processed"})
    coord.create_background_task = MagicMock()
    coord._process_queue = AsyncMock()

    # State
    coord.conversation_history = []
    coord.current_parent_uuid = "test-parent-uuid"
    coord.is_processing = False
    coord.turn_completed = True
    coord.cancel_processing = False
    coord.session_stats = {"messages": 0}
    coord.api_service.model = "test-model"

    # Apply overrides
    for key, value in overrides.items():
        setattr(coord, key, value)

    return coord


class TestMessageHandler(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.coordinator = _make_coordinator()
        self.handler = MessageHandler(coordinator=self.coordinator)

    def tearDown(self):
        """Clean up."""
        self.loop.close()

    def test_init(self):
        """Test MessageHandler initialization."""
        self.assertIsNotNone(self.handler)
        self.assertIs(self.handler._coordinator, self.coordinator)

    def test_handle_context_injection(self):
        """Test context injection handler."""
        data = {"message": "test message with keyword"}
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_context_injection(data, event)
        )

        self.coordinator.context_service.trigger_context_injection.assert_called_once_with(
            "test message with keyword"
        )
        self.assertEqual(result, data)

    def test_handle_user_input(self):
        """Test user input handler."""
        data = {"message": "hello"}
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_user_input(data, event)
        )

        self.coordinator.process_user_input.assert_called_once_with("hello")
        self.assertEqual(result, {"status": "processed"})

    def test_handle_user_input_empty(self):
        """Test user input handler with empty message."""
        data = {"message": "   "}
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_user_input(data, event)
        )

        self.assertFalse(self.coordinator.process_user_input.called)
        self.assertEqual(result, {"status": "empty_message"})

    def test_handle_cancel_request(self):
        """Test cancel request handler."""
        data = {"reason": "user Ctrl+C", "source": "stdin"}
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_cancel_request(data, event)
        )

        self.coordinator.cancel_current_request.assert_called_once()
        self.assertEqual(result, {"status": "cancelled", "reason": "user Ctrl+C"})

    def test_handle_cancel_request_pipe_mode(self):
        """Test cancel request is ignored in pipe mode."""
        self.coordinator.renderer.pipe_mode = True
        data = {"reason": "user Ctrl+C", "source": "stdin"}
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_cancel_request(data, event)
        )

        self.assertFalse(self.coordinator.cancel_current_request.called)
        self.assertEqual(result, {"status": "ignored", "reason": "pipe_mode"})

    def test_handle_add_message(self):
        """Test ADD_MESSAGE handler."""
        data = {
            "messages": [{"role": "user", "content": "test"}],
            "options": {"show_loading": True, "trigger_llm": False},
        }
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_add_message(data, event)
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["message_count"], 1)
        self.coordinator.message_display_service.show_loading.assert_called_once()
        self.coordinator.message_display_service.hide_loading.assert_called_once()

    def test_handle_add_message_empty(self):
        """Test ADD_MESSAGE handler with no messages."""
        data = {"messages": []}
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_add_message(data, event)
        )

        self.assertFalse(result["success"])
        self.assertIn("No messages provided", result["error"])

    def test_handle_llm_continue(self):
        """Test TRIGGER_LLM_CONTINUE handler."""
        self.coordinator.conversation_history.append(
            type("obj", (object,), {"role": "user", "content": "last message"})()
        )

        data = {"source": "test-plugin"}
        event = MagicMock()

        result = self.loop.run_until_complete(
            self.handler.handle_llm_continue(data, event)
        )

        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["source"], "test-plugin")
        self.coordinator.create_background_task.assert_called_once()

    def test_handle_llm_continue_already_processing(self):
        """Test TRIGGER_LLM_CONTINUE skips when already processing."""
        coord = _make_coordinator(is_processing=True)
        handler = MessageHandler(coordinator=coord)

        data = {"source": "test-plugin"}
        event = MagicMock()

        result = self.loop.run_until_complete(handler.handle_llm_continue(data, event))

        self.assertEqual(result["status"], "queued_for_retry")
        coord.create_background_task.assert_called_once()

    def test_handle_llm_continue_coalesces_retries(self):
        """Multiple TRIGGER_LLM_CONTINUE while busy spawn only one retry task.

        Regression: peer messages arriving during a busy turn used to each
        spawn a new _retry_continue background task, causing N stacked
        retries that all fired _hub_continue when processing ended,
        producing N redundant LLM calls.
        """
        coord = _make_coordinator(is_processing=True)
        handler = MessageHandler(coordinator=coord)
        # Simulate retry already in flight
        handler._retry_pending = True

        data = {"source": "second-peer"}
        event = MagicMock()

        result = self.loop.run_until_complete(handler.handle_llm_continue(data, event))

        self.assertEqual(result["status"], "coalesced")
        coord.create_background_task.assert_not_called()

    # ------------------------------------------------------------------ #
    # Regression: background terminal stalls user message delivery         #
    # (2026-06-20)                                                         #
    # ------------------------------------------------------------------ #

    def test_hub_continue_drains_queue_after_completion(self):
        """_hub_continue() must schedule _process_queue() when user messages
        arrived in processing_queue while is_processing was held.

        Regression: process_user_input() skips launching _process_queue()
        when is_processing is True. If _hub_continue() finishes without
        re-checking the queue, those messages are silently dropped.
        """

        async def run():
            # Conversation history must be non-empty for handle_llm_continue
            # to proceed past its early-exit guard.
            self.coordinator.conversation_history.append(
                type("obj", (object,), {"role": "user", "content": "hello"})()
            )

            # Use a real asyncio.Queue so .empty() / .qsize() behave correctly.
            real_queue = asyncio.Queue()
            self.coordinator._queue_processor.processing_queue = real_queue

            # Override _process_queue so calling it returns a plain object
            # (not a coroutine) — avoids "coroutine never awaited" warnings
            # while still letting us assert create_background_task was called.
            self.coordinator._process_queue = MagicMock(return_value=object())

            # Step 1 — fire handle_llm_continue; it schedules _hub_continue().
            await self.handler.handle_llm_continue({"source": "hub-test"}, MagicMock())
            self.assertTrue(self.coordinator.create_background_task.called)

            hub_coro = self.coordinator.create_background_task.call_args[0][0]
            self.coordinator.create_background_task.reset_mock()

            # Step 2 — a user message lands in the queue (simulates the user
            # typing while _hub_continue() is running).
            real_queue.put_nowait("reply from user during hub continue")

            # Step 3 — actually run _hub_continue() to completion.
            await hub_coro

            # Step 4 — the fix must have called create_background_task once
            # more, this time to drain the processing_queue.
            self.assertTrue(
                self.coordinator.create_background_task.called,
                "_hub_continue() must restart _process_queue() when "
                "processing_queue has messages after releasing is_processing",
            )

        self.loop.run_until_complete(run())

    def test_hub_continue_no_drain_when_queue_empty(self):
        """_hub_continue() must NOT spawn a spurious _process_queue() task
        when processing_queue is already empty after the turn completes."""

        async def run():
            self.coordinator.conversation_history.append(
                type("obj", (object,), {"role": "user", "content": "hello"})()
            )

            # Empty queue.
            self.coordinator._queue_processor.processing_queue = asyncio.Queue()
            self.coordinator._process_queue = MagicMock(return_value=object())

            # Realistic mock: a real _continue_conversation sets turn_completed
            # when the model finishes the turn. Without this the continuation
            # loop never exits — the default AsyncMock never flips the flag, so
            # the loop relied on the (now-removed) 300s deadline to terminate.
            # That reliance made this test a 5-min hang, then an infinite one
            # (2026-07-03). Simulate a completed turn so termination is driven
            # by real turn completion, not a backstop.
            async def complete_turn():
                self.coordinator._queue_processor.turn_completed = True

            self.coordinator._continue_conversation = complete_turn

            await self.handler.handle_llm_continue({"source": "hub-test"}, MagicMock())
            hub_coro = self.coordinator.create_background_task.call_args[0][0]
            self.coordinator.create_background_task.reset_mock()

            await hub_coro

            self.assertFalse(
                self.coordinator.create_background_task.called,
                "_hub_continue() must not spawn _process_queue() when queue is empty",
            )

        self.loop.run_until_complete(run())

    # ------------------------------------------------------------------ #
    # Regression: 300s wall-clock deadline killed healthy chains          #
    # mid-tool-call (2026-07-03)                                          #
    # ------------------------------------------------------------------ #

    def _patch_monotonic(self, offset):
        """Patch time.monotonic to real time plus a controllable offset."""
        real_mono = time.monotonic
        return patch(
            "kollabor.llm.message_handler.time.monotonic",
            side_effect=lambda: real_mono() + offset["v"],
        )

    def test_hub_continue_survives_past_300s(self):
        """A chain running longer than 300s must NOT be force-completed.

        Regression: _hub_continue force-set turn_completed=True once the
        chain's cumulative wall-clock passed 300s. The last turn's tool
        results were already in history but the LLM was never re-invoked
        to see them, so the session went silent (lapis, quantum-flux,
        2026-07-03 19:15:06).
        """

        async def run():
            self.coordinator.conversation_history.append(
                type("obj", (object,), {"role": "user", "content": "hello"})()
            )
            qp = self.coordinator._queue_processor
            qp.processing_queue = asyncio.Queue()
            self.coordinator._process_queue = MagicMock(return_value=object())

            offset = {"v": 0.0}
            calls = {"n": 0}

            async def fake_continue():
                calls["n"] += 1
                if calls["n"] == 1:
                    # Simulate the first turn taking >300s of wall clock.
                    offset["v"] += 400.0
                if calls["n"] >= 2:
                    qp.turn_completed = True  # model finishes naturally

            self.coordinator._continue_conversation = fake_continue

            with self._patch_monotonic(offset):
                await self.handler.handle_llm_continue(
                    {"source": "hub-test"}, MagicMock()
                )
                hub_coro = self.coordinator.create_background_task.call_args[0][0]
                await hub_coro

            self.assertEqual(
                calls["n"],
                2,
                "chain must continue past 300s until the model completes "
                "the turn (old code force-completed after 1 call)",
            )
            self.assertTrue(qp.turn_completed)

        self.loop.run_until_complete(run())

    def test_hub_continue_yields_to_user_message_mid_chain(self):
        """A user message arriving mid-chain must break the chain so the
        queue drain processes it — without force-completing the turn."""

        async def run():
            self.coordinator.conversation_history.append(
                type("obj", (object,), {"role": "user", "content": "hello"})()
            )
            qp = self.coordinator._queue_processor
            real_queue = asyncio.Queue()
            qp.processing_queue = real_queue
            self.coordinator._process_queue = MagicMock(return_value=object())

            calls = {"n": 0}

            async def fake_continue():
                calls["n"] += 1
                # User types while the chain is running; turn never
                # completes on its own.
                real_queue.put_nowait("user interjection")

            self.coordinator._continue_conversation = fake_continue

            await self.handler.handle_llm_continue(
                {"source": "hub-test"}, MagicMock()
            )
            hub_coro = self.coordinator.create_background_task.call_args[0][0]
            self.coordinator.create_background_task.reset_mock()
            await hub_coro

            self.assertEqual(
                calls["n"], 1, "chain must yield before running another turn"
            )
            self.assertFalse(
                qp.turn_completed,
                "yielding to user input must not force-complete the turn",
            )
            self.assertTrue(
                self.coordinator.create_background_task.called,
                "queue drain must be scheduled for the interjected message",
            )

        self.loop.run_until_complete(run())

    def test_retry_continue_never_cancels_busy_session(self):
        """A hub trigger during a long busy chain must wait, not cancel.

        Regression: _retry_continue set coord.cancel_processing=True after
        waiting 300s, aborting healthy long-running chains 5 minutes after
        any hub message arrived.
        """
        coord = _make_coordinator(is_processing=True)
        handler = MessageHandler(coordinator=coord)

        async def run():
            result = await handler.handle_llm_continue(
                {"source": "peer"}, MagicMock()
            )
            self.assertEqual(result["status"], "queued_for_retry")
            retry_coro = coord.create_background_task.call_args[0][0]
            coord.create_background_task.reset_mock()

            offset = {"v": 0.0}
            sleeps = {"n": 0}

            async def fake_sleep(_secs):
                sleeps["n"] += 1
                offset["v"] += 400.0  # each wait tick jumps 400s
                if sleeps["n"] >= 2:
                    coord.is_processing = False  # chain finishes naturally

            real_mono = time.monotonic
            with patch(
                "kollabor.llm.message_handler.time.monotonic",
                side_effect=lambda: real_mono() + offset["v"],
            ), patch(
                "kollabor.llm.message_handler.asyncio.sleep", fake_sleep
            ):
                await retry_coro

            self.assertFalse(
                coord.cancel_processing,
                "retry waiter must never cancel a busy session "
                "(old code set cancel_processing=True after 300s)",
            )
            self.assertTrue(
                coord.create_background_task.called,
                "retry must fire the continuation once the chain finishes",
            )
            # Close the unawaited _hub_continue coroutine handed to the mock.
            coord.create_background_task.call_args[0][0].close()

        self.loop.run_until_complete(run())

    def test_retry_continue_gives_up_after_an_hour_without_cancelling(self):
        """If a session stays busy >1h, drop the retry — never cancel."""
        coord = _make_coordinator(is_processing=True)
        handler = MessageHandler(coordinator=coord)

        async def run():
            await handler.handle_llm_continue({"source": "peer"}, MagicMock())
            retry_coro = coord.create_background_task.call_args[0][0]
            coord.create_background_task.reset_mock()

            offset = {"v": 0.0}

            async def fake_sleep(_secs):
                offset["v"] += 4000.0  # jump past the 3600s give-up point

            real_mono = time.monotonic
            with patch(
                "kollabor.llm.message_handler.time.monotonic",
                side_effect=lambda: real_mono() + offset["v"],
            ), patch(
                "kollabor.llm.message_handler.asyncio.sleep", fake_sleep
            ):
                await retry_coro

            self.assertFalse(coord.cancel_processing)
            self.assertFalse(
                coord.create_background_task.called,
                "retry must be dropped, not fired, after the give-up point",
            )
            self.assertFalse(handler._retry_pending)

        self.loop.run_until_complete(run())


if __name__ == "__main__":
    unittest.main()
