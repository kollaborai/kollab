"""Tests for QueueProcessor."""

import asyncio
import time
import unittest
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from kollabor_agent.queue_processor import (
    QueueProcessor,
    _tool_results_requiring_followup,
)
from kollabor_agent.tool_executor import ToolExecutionResult
from kollabor_events.data_models import ConversationMessage


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

    def test_loop2_continuation_survives_past_300s(self):
        """LOOP 2 must not force-complete a working chain on wall-clock.

        Regression: process_queue's LOOP 2 force-set turn_completed=True
        once the continuation chain's cumulative wall-clock passed 300s,
        orphaning the last turn's tool results and leaving the session
        silent (koordinator 2026-07-03 14:24, bismuth 15:15).
        """
        task_manager = MagicMock()
        process_batch_fn = AsyncMock()

        offset = {"v": 0.0}
        calls = {"n": 0}

        async def continue_fn():
            calls["n"] += 1
            if calls["n"] == 1:
                # Simulate the first continuation turn taking >300s.
                offset["v"] += 400.0
            if calls["n"] >= 2:
                self.processor.turn_completed = True  # natural completion

        self.processor.turn_completed = False
        real_mono = time.monotonic

        with patch(
            "kollabor_agent.queue_processor.time.monotonic",
            side_effect=lambda: real_mono() + offset["v"],
        ):
            self.loop.run_until_complete(
                self.processor.process_queue(
                    task_manager, process_batch_fn, continue_fn
                )
            )

        self.assertEqual(
            calls["n"],
            2,
            "LOOP 2 must continue past 300s until the model completes the "
            "turn (old code force-completed after 1 call)",
        )
        self.assertTrue(self.processor.turn_completed)

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

    def test_context_injection_is_ephemeral_for_wire_request(self):
        """Context blocks reach the request but do not persist in history."""

        class ContextService:
            def increment_turn(self):
                pass

            def build_curator_injection(self):
                return "[ephemeral context]"

            def build_context_snapshot(self):
                return None

            def build_confirmation_injection(self):
                return None

            def build_divergence_warnings(self):
                return None

            def drain_ephemeral_injections(self):
                return ["[legacy context]"]

        wire_contents = []

        async def capture_request(**kwargs):
            wire_contents.append(
                [message.content for message in kwargs["conversation_history"]]
            )
            return "test response"

        self.event_bus.get_service.return_value = ContextService()
        self.conversation_history.append(
            ConversationMessage(role="user", content="original prompt")
        )
        self.streaming_handler.call_llm.side_effect = capture_request
        self.api_service.last_stop_reason = ""
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_tool_calls.return_value = []
        self.api_service.last_thinking_content = None
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 0
        self.response_parser.parse_response.return_value = {
            "content": "test response",
            "components": {},
            "turn_completed": True,
            "question_gate_active": False,
        }
        self.response_parser.get_all_tools.return_value = []
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.processor._bridge_relay = AsyncMock()
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("test response", False, False, False)
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        self.assertEqual(
            wire_contents,
            [
                [
                    "[ephemeral context]\n\n---\n\n"
                    "[legacy context]\n\n---\n\noriginal prompt"
                ]
            ],
        )
        self.assertEqual(self.conversation_history[-1].content, "original prompt")

    def test_pipe_mode_suppresses_intermediate_tool_response(self):
        """Pipe mode emits the continuation, not the pre-tool response."""
        self.renderer.pipe_mode = True
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 1
        self.tool_executor.format_result_for_conversation.return_value = "ok"
        self.tool_executor.execute_tool = AsyncMock(
            return_value=ToolExecutionResult(
                tool_id="terminal_1",
                tool_type="terminal",
                success=True,
                output="",
            )
        )
        self.response_parser.parse_response.return_value = {
            "content": "intermediate answer",
            "components": {},
            "turn_completed": False,
            "question_gate_active": False,
        }
        self.response_parser.get_all_tools.return_value = [
            {"id": "terminal_1", "type": "terminal", "command": "printf ''"}
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.conversation_logger.log_system_message = AsyncMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.processor._bridge_relay = AsyncMock()
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("intermediate answer", False, False, False)
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        self.message_display_service.display_complete_response.assert_not_called()
        self.message_display_service.display_tool_results.assert_called_once()

    def test_pipe_mode_displays_question_gate_response(self):
        """Pipe mode keeps a response that is waiting for user input visible."""
        self.renderer.pipe_mode = True
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.response_parser.parse_response.return_value = {
            "content": "Which file should I inspect?",
            "components": {},
            "turn_completed": True,
            "question_gate_active": True,
        }
        self.response_parser.get_all_tools.return_value = [
            {"id": "terminal_1", "type": "terminal", "command": "printf ''"}
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.processor._bridge_relay = AsyncMock()
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("Which file should I inspect?", False, False, False)
        )

        self.processor.question_gate_enabled = True
        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        self.message_display_service.display_complete_response.assert_called_once()
        self.message_display_service.display_complete_response.assert_called_once_with(
            thinking_duration=ANY,
            response="Which file should I inspect?",
            tool_results=None,
            thinking_content=[],
        )

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

    def test_file_ingestion_uses_raw_hash_for_unchanged_read_detection(self):
        """Rendered read headers must not replace the disk-content hash."""
        from kollabor_ai.context_service.hash_utils import compute_hash
        from kollabor_ai.context_service.service import ContextService

        context_service = ContextService(heavy_threshold_kb=1)
        self.event_bus.get_service.return_value = context_service
        path = "/workspace/large.py"
        raw = b"x" * 9000
        result = ToolExecutionResult(
            tool_id="file_read_1",
            tool_type="file_read",
            success=True,
            output="rendered read header\n\n" + raw.decode("utf-8"),
            metadata={
                "file_path": path,
                "file_content_hash": compute_hash(raw),
            },
        )

        self.processor._ingest_tool_results([result], "message-1")

        self.assertEqual(
            context_service.file_read_hook(path, raw)["action"],
            "stale",
        )


class TestQueueProcessorToolContinuation(unittest.TestCase):
    def test_state_update_requires_followup(self):
        """Every executed tool result is fed back before the turn can end."""
        results = [
            ToolExecutionResult(
                tool_id="state_update_1",
                tool_type="state_update",
                success=True,
                output="saved: ['state']",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), results)

    def test_hub_message_requires_followup(self):
        """Hub sends are tool results and should be visible to the model."""
        results = [
            ToolExecutionResult(
                tool_id="hub_msg_1",
                tool_type="hub_msg",
                success=True,
                output="delivered to koordinator",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), results)

    def test_failed_state_update_requires_followup(self):
        """State-save failures should still be shown to the model."""
        failed_state = ToolExecutionResult(
            tool_id="state_update_1",
            tool_type="state_update",
            success=False,
            error="vault not initialized",
        )
        results = [failed_state]

        self.assertEqual(_tool_results_requiring_followup(results), [failed_state])

    def test_real_tool_requires_followup(self):
        """Task-producing tool results require a follow-up turn."""
        read_result = ToolExecutionResult(
            tool_id="file_read_1",
            tool_type="file_read",
            success=True,
            output="file contents",
        )
        results = [read_result]

        self.assertEqual(_tool_results_requiring_followup(results), [read_result])

    def test_no_tool_results_needs_no_followup(self):
        """Natural stop condition: no tool calls means no continuation."""
        self.assertEqual(_tool_results_requiring_followup([]), [])


if __name__ == "__main__":
    unittest.main()
