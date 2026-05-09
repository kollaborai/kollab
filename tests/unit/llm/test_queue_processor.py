"""Tests for QueueProcessor."""

import asyncio
import unittest
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from kollabor_agent.queue_processor import (
    QueueProcessor,
    _tool_results_requiring_followup,
)
from kollabor_agent.tool_executor import ToolExecutionResult


@dataclass
class MockQueueConfig:
    """Mock queue config for testing."""

    overflow_strategy: str = "drop_oldest"
    log_queue_events: bool = False
    enable_queue_metrics: bool = False
    block_timeout: Optional[float] = None


@dataclass
class MockTaskConfig:
    """Mock task config for QueueProcessor tests."""

    queue: MockQueueConfig = field(default_factory=MockQueueConfig)


class TestQueueProcessor(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Shared mutable containers
        self.conversation_history = []
        self.session_stats = {
            "messages": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        self.stats = {"total_thinking_time": 0}
        self.pending_tools = []
        self.queue_metrics = {
            "total_enqueue_attempts": 0,
            "total_enqueue_successes": 0,
            "drop_oldest_count": 0,
            "drop_newest_count": 0,
            "block_count": 0,
            "block_timeout_count": 0,
        }

        # Mock dependencies
        self.task_config = MockTaskConfig()

        self.api_service = AsyncMock()
        self.tool_executor = MagicMock()
        self.response_parser = MagicMock()
        self.message_display_service = MagicMock()
        self.renderer = MagicMock()
        self.config = MagicMock()
        self.config.get = MagicMock(return_value=0.1)
        self.event_bus = MagicMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.conversation_logger = AsyncMock()
        self.streaming_handler = MagicMock()
        self.streaming_handler.call_llm = AsyncMock(return_value="test response")
        self.native_tools_handler = MagicMock()
        self.native_tools_handler.tools = None
        self.native_tools_handler.tool_calling_enabled = False
        self.native_tools_handler.discovery_complete = asyncio.Event()
        self.native_tools_handler.discovery_complete.set()

        self.add_message_fn = MagicMock()
        self.max_history = 90
        self.question_gate_enabled = False

        self.processor = QueueProcessor(
            conversation_history=self.conversation_history,
            session_stats=self.session_stats,
            stats=self.stats,
            pending_tools=self.pending_tools,
            queue_metrics=self.queue_metrics,
            task_config=self.task_config,
            api_service=self.api_service,
            tool_executor=self.tool_executor,
            response_parser=self.response_parser,
            message_display_service=self.message_display_service,
            renderer=self.renderer,
            config=self.config,
            event_bus=self.event_bus,
            conversation_logger=self.conversation_logger,
            streaming_handler=self.streaming_handler,
            native_tools_handler=self.native_tools_handler,
            add_message_fn=self.add_message_fn,
            max_history=self.max_history,
            question_gate_enabled=self.question_gate_enabled,
            max_queue_size=10,
        )
        # Default to turn_completed=True so process_queue doesn't enter
        # the infinite continue_conversation loop
        self.processor.turn_completed = True

    def tearDown(self):
        """Clean up."""
        self.loop.close()

    def test_init(self):
        """Test QueueProcessor initialization."""
        self.assertIsNotNone(self.processor)
        self.assertEqual(self.processor.max_queue_size, 10)
        self.assertEqual(self.processor.dropped_messages, 0)
        self.assertFalse(self.processor.is_processing)
        # turn_completed set to True in setUp to prevent infinite loops
        self.assertFalse(self.processor.cancel_processing)

    def test_enqueue_success(self):
        """Test successful message enqueue."""
        self.loop.run_until_complete(self.processor.enqueue("test message"))

        self.assertEqual(self.queue_metrics["total_enqueue_attempts"], 1)
        self.assertEqual(self.queue_metrics["total_enqueue_successes"], 1)
        self.assertEqual(self.processor.processing_queue.qsize(), 1)

    def test_enqueue_drop_newest_strategy(self):
        """Test drop_newest overflow strategy raises RuntimeError."""
        self.task_config.queue.overflow_strategy = "drop_newest"
        self.processor.max_queue_size = 1
        self.processor.processing_queue = asyncio.Queue(maxsize=1)

        # Fill queue
        self.loop.run_until_complete(self.processor.enqueue("first"))

        # Should raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            self.loop.run_until_complete(self.processor.enqueue("second"))

        self.assertIn("Queue is full", str(ctx.exception))
        self.assertEqual(self.queue_metrics["drop_newest_count"], 1)

    def test_enqueue_block_strategy_timeout(self):
        """Test block strategy with timeout."""
        self.task_config.queue.overflow_strategy = "block"
        self.task_config.queue.block_timeout = 0.1
        self.processor.max_queue_size = 1
        self.processor.processing_queue = asyncio.Queue(maxsize=1)

        # Fill queue
        self.loop.run_until_complete(self.processor.enqueue("first"))

        # Should timeout and drop
        self.loop.run_until_complete(self.processor.enqueue("second"))

        self.assertEqual(self.queue_metrics["block_count"], 1)
        self.assertEqual(self.queue_metrics["block_timeout_count"], 1)
        self.assertEqual(self.processor.dropped_messages, 1)

    def test_process_queue_empty(self):
        """Test processing empty queue returns immediately."""
        task_manager = MagicMock()
        process_batch_fn = AsyncMock()
        continue_fn = AsyncMock()

        self.loop.run_until_complete(
            self.processor.process_queue(task_manager, process_batch_fn, continue_fn)
        )

        self.assertFalse(process_batch_fn.called)
        self.assertFalse(continue_fn.called)
        self.assertFalse(self.processor.is_processing)

    def test_process_queue_with_messages(self):
        """Test processing messages from queue."""
        task_manager = MagicMock()
        process_batch_fn = AsyncMock()
        continue_fn = AsyncMock()

        # Add messages
        self.loop.run_until_complete(self.processor.enqueue("msg1"))
        self.loop.run_until_complete(self.processor.enqueue("msg2"))

        self.loop.run_until_complete(
            self.processor.process_queue(task_manager, process_batch_fn, continue_fn)
        )

        process_batch_fn.assert_called_once_with(["msg1", "msg2"])
        self.assertFalse(self.processor.is_processing)

    # ------------------------------------------------------------------
    # Tests for _emit_llm_response_and_handle
    # ------------------------------------------------------------------

    def test_emit_llm_response_no_event_bus(self):
        """Returns (clean_response, False, False) when event_bus is None."""
        self.processor.event_bus = None
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="hello",
                clean_response="hello",
                thinking_duration=0.5,
            )
        )
        self.assertEqual(result, ("hello", False, False, False))

    def test_emit_llm_response_basic(self):
        """Emits LLM_RESPONSE event and returns clean response when no modifications."""
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="some text",
                clean_response="some text",
                thinking_duration=1.2,
            )
        )
        self.assertEqual(result, ("some text", False, False, False))
        self.event_bus.emit_with_hooks.assert_called_once()

    def test_emit_llm_response_force_continue_from_hook(self):
        """Sets force_continue=True when a hook sets it in final_data."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "pre": {"final_data": {"force_continue": True}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="x", clean_response="x", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("x", True, False, False))

    def test_emit_llm_response_suppress_display_from_hook(self):
        """Sets suppress_display=True when a hook sets it in final_data."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "main": {"final_data": {"suppress_display": True}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="y", clean_response="y", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("y", False, True, False))

    def test_emit_llm_response_clean_response_modified_by_hook(self):
        """Hook can replace clean_response via final_data."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "post": {"final_data": {"clean_response": "HOOKED"}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="original", clean_response="original", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("HOOKED", False, False, False))

    def test_emit_llm_response_multiple_phases_last_wins(self):
        """Multiple phases can modify clean_response; last one wins."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "pre": {"final_data": {"clean_response": "FIRST"}},
                "main": {"final_data": {"clean_response": "SECOND"}},
                "post": {"final_data": {"clean_response": "THIRD"}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="orig", clean_response="orig", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("THIRD", False, False, False))

    def test_emit_llm_response_force_continue_and_suppress_combined(self):
        """Both flags can be set simultaneously."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "pre": {"final_data": {"force_continue": True}},
                "main": {"final_data": {"suppress_display": True}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="x", clean_response="x", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("x", True, True, False))

    def test_emit_llm_response_hub_tags_in_original_strips_logging(self):
        """When response_text contains hub tags, hook modifications are logged."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "post": {"final_data": {"clean_response": "cleaned"}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text='<hub_msg to="x">hi</hub_msg>',
                clean_response='<hub_msg to="x">hi</hub_msg>',
                thinking_duration=0.0,
            )
        )
        # Should still return modified clean_response
        self.assertEqual(result, ("cleaned", False, False, False))

    def test_emit_llm_response_empty_response(self):
        """Handles empty/None response_text gracefully."""
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="", clean_response="", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("", False, False, False))

    def test_emit_llm_response_with_log_prefix(self):
        """Log prefix parameter doesn't affect return value."""
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="x", clean_response="x", thinking_duration=0.0,
                log_prefix="native",
            )
        )
        self.assertEqual(result, ("x", False, False, False))

    # ------------------------------------------------------------------
    # Tests for _bridge_relay
    # ------------------------------------------------------------------

    def test_bridge_relay_no_event_bus(self):
        """No-op when event_bus is None."""
        self.processor.event_bus = None
        # Should not raise
        self.loop.run_until_complete(
            self.processor._bridge_relay("some response")
        )

    def test_bridge_relay_empty_response(self):
        """No-op when clean_response is empty."""
        self.loop.run_until_complete(self.processor._bridge_relay(""))

    def test_bridge_relay_no_hub_plugin(self):
        """No-op when hub_plugin service is not available."""
        self.event_bus.get_service = MagicMock(return_value=None)
        self.loop.run_until_complete(self.processor._bridge_relay("hello"))
        self.event_bus.get_service.assert_called_with("hub_plugin")

    def test_bridge_relay_hub_no_bridge(self):
        """No-op when hub_plugin exists but has no _bridge."""
        hub = MagicMock()
        hub._bridge = None
        self.event_bus.get_service = MagicMock(return_value=hub)
        self.loop.run_until_complete(self.processor._bridge_relay("hello"))

    def test_bridge_relay_sends_to_bridge(self):
        """Sends clean_response to bridge when bridge platform found in history."""
        hub = MagicMock()
        hub._bridge = MagicMock()
        hub.bridge_send = AsyncMock()

        @dataclass
        class MockMsg:
            role: str
            metadata: Optional[dict] = None

        llm_service = MagicMock()
        llm_service.conversation_history = [
            MockMsg(role="user", metadata={"bridge_platform": "discord"}),
        ]

        def get_service(name):
            if name == "hub_plugin":
                return hub
            if name == "llm_service":
                return llm_service
            return None

        self.event_bus.get_service = MagicMock(side_effect=get_service)
        self.loop.run_until_complete(self.processor._bridge_relay("relay this"))
        hub.bridge_send.assert_called_once_with("relay this")

    def test_bridge_relay_skips_non_bridge_users(self):
        """Does not relay when no user message has bridge_platform metadata."""
        hub = MagicMock()
        hub._bridge = MagicMock()

        @dataclass
        class MockMsg:
            role: str
            metadata: Optional[dict] = None

        llm_service = MagicMock()
        llm_service.conversation_history = [
            MockMsg(role="user", metadata=None),
        ]

        def get_service(name):
            if name == "hub_plugin":
                return hub
            if name == "llm_service":
                return llm_service
            return None

        self.event_bus.get_service = MagicMock(side_effect=get_service)
        self.loop.run_until_complete(self.processor._bridge_relay("should not relay"))
        hub.bridge_send.assert_not_called()

    def test_bridge_relay_stops_at_first_bridge_user(self):
        """Finds the most recent bridge user (reversed history search)."""
        hub = MagicMock()
        hub._bridge = MagicMock()
        hub.bridge_send = AsyncMock()

        @dataclass
        class MockMsg:
            role: str
            metadata: Optional[dict] = None

        llm_service = MagicMock()
        llm_service.conversation_history = [
            MockMsg(role="user", metadata={"bridge_platform": "slack"}),
            MockMsg(role="assistant"),
            MockMsg(role="user", metadata={"bridge_platform": "discord"}),
        ]

        def get_service(name):
            if name == "hub_plugin":
                return hub
            if name == "llm_service":
                return llm_service
            return None

        self.event_bus.get_service = MagicMock(side_effect=get_service)
        self.loop.run_until_complete(self.processor._bridge_relay("msg"))
        # Should find discord (most recent bridge user) and send
        hub.bridge_send.assert_called_once_with("msg")

    def test_bridge_relay_stops_at_first_non_bridge_user(self):
        """Stops searching at the first user message without bridge metadata."""
        hub = MagicMock()
        hub._bridge = MagicMock()

        @dataclass
        class MockMsg:
            role: str
            metadata: Optional[dict] = None

        llm_service = MagicMock()
        llm_service.conversation_history = [
            MockMsg(role="user", metadata={"bridge_platform": "discord"}),
            MockMsg(role="assistant"),
            MockMsg(role="user", metadata=None),  # non-bridge user
            MockMsg(role="assistant"),
            MockMsg(role="user", metadata=None),  # most recent, no bridge -> breaks loop
        ]

        def get_service(name):
            if name == "hub_plugin":
                return hub
            if name == "llm_service":
                return llm_service
            return None

        self.event_bus.get_service = MagicMock(side_effect=get_service)
        self.loop.run_until_complete(self.processor._bridge_relay("msg"))
        # Reversed: last user has metadata=None -> hits elif branch, breaks immediately
        hub.bridge_send.assert_not_called()

    def test_bridge_relay_exception_handled_gracefully(self):
        """Exceptions in bridge relay are caught and logged."""
        self.event_bus.get_service = MagicMock(side_effect=RuntimeError("boom"))
        # Should not raise
        self.loop.run_until_complete(self.processor._bridge_relay("hello"))


class TestQueueProcessorToolContinuation(unittest.TestCase):
    def test_state_update_with_wait_for_user_does_not_force_followup(self):
        """A successful state save can park in the same turn as wait_for_user."""
        results = [
            ToolExecutionResult(
                tool_id="state_update_1",
                tool_type="state_update",
                success=True,
                output="saved: ['state']",
            ),
            ToolExecutionResult(
                tool_id="wait_for_user_1",
                tool_type="wait_for_user",
                success=True,
                output="[wait_for_user] parked",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), [])

    def test_hub_message_with_wait_for_user_does_not_force_followup(self):
        """Hub sends are bookkeeping when the model explicitly parks."""
        results = [
            ToolExecutionResult(
                tool_id="hub_msg_1",
                tool_type="hub_msg",
                success=True,
                output="delivered to koordinator",
            ),
            ToolExecutionResult(
                tool_id="wait_for_user_1",
                tool_type="wait_for_user",
                success=True,
                output="[wait_for_user] parked",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), [])

    def test_failed_state_update_with_wait_for_user_still_forces_followup(self):
        """State-save failures should still be shown to the model."""
        failed_state = ToolExecutionResult(
            tool_id="state_update_1",
            tool_type="state_update",
            success=False,
            error="vault not initialized",
        )
        results = [
            failed_state,
            ToolExecutionResult(
                tool_id="wait_for_user_1",
                tool_type="wait_for_user",
                success=True,
                output="[wait_for_user] parked",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), [failed_state])

    def test_real_tool_with_wait_for_user_still_forces_followup(self):
        """Task-producing tool results still require a follow-up turn."""
        read_result = ToolExecutionResult(
            tool_id="file_read_1",
            tool_type="file_read",
            success=True,
            output="file contents",
        )
        results = [
            read_result,
            ToolExecutionResult(
                tool_id="wait_for_user_1",
                tool_type="wait_for_user",
                success=True,
                output="[wait_for_user] parked",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), [read_result])

    def test_wait_for_user_disabled_state_update_requires_followup(self):
        """When parking is off, state_update is not swallowed by wait pairing."""
        results = [
            ToolExecutionResult(
                tool_id="state_update_1",
                tool_type="state_update",
                success=True,
                output="saved",
            ),
            ToolExecutionResult(
                tool_id="wait_for_user_1",
                tool_type="wait_for_user",
                success=True,
                output="ignored",
            ),
        ]
        self.assertEqual(
            _tool_results_requiring_followup(results, wait_for_user_enabled=False),
            [results[0]],
        )

    def test_wait_for_user_disabled_hub_msg_requires_followup(self):
        results = [
            ToolExecutionResult(
                tool_id="hub_msg_1",
                tool_type="hub_msg",
                success=True,
                output="sent",
            ),
            ToolExecutionResult(
                tool_id="wait_for_user_1",
                tool_type="wait_for_user",
                success=True,
                output="ignored",
            ),
        ]
        self.assertEqual(
            _tool_results_requiring_followup(results, wait_for_user_enabled=False),
            [results[0]],
        )


if __name__ == "__main__":
    unittest.main()
