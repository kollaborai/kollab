"""Tests for MessageHandler."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

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


if __name__ == "__main__":
    unittest.main()
