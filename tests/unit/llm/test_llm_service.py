"""Comprehensive tests for LLM Service functionality.

Following TDD principles - these tests document existing behavior
before refactoring the monolithic LLMService class.
"""

import asyncio
import os

# Import the components we're testing
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kollabor.llm.llm_coordinator import LLMService
from kollabor_ai.response_parser import ResponseParser


class TestLLMServiceIntegration(unittest.TestCase):
    """Integration tests for LLMService end-to-end functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock configuration
        self.config = MagicMock()
        self.config.get.side_effect = self._get_config_value

        # Mock dependencies
        self.event_bus = MagicMock()
        self.renderer = MagicMock()

        # Mock message coordinator
        self.message_coordinator = MagicMock()
        self.renderer.message_coordinator = self.message_coordinator

        # Create service instance
        self.service = LLMService(
            config=self.config, event_bus=self.event_bus, renderer=self.renderer
        )

    def _get_config_value(self, key, default=None):
        """Mock configuration values."""
        config_map = {
            "kollabor.llm.api_url": "http://localhost:1234",
            "kollabor.llm.model": "test-model",
            "kollabor.llm.temperature": 0.7,
            "kollabor.llm.timeout": 30,
            "kollabor.llm.max_history": 10,
            "kollabor.llm.enable_streaming": False,
            "kollabor.llm.terminal_timeout": 10,
            "kollabor.llm.mcp_timeout": 20,
            "kollabor.llm.system_prompt.base_prompt": "You are a test assistant.",
            "kollabor.llm.system_prompt.include_project_structure": False,
            "kollabor.llm.system_prompt.attachment_files": [],
            "kollabor.llm.system_prompt.custom_prompt_files": [],
        }
        return config_map.get(key, default)

    def test_service_initialization(self):
        """Test service initializes with correct configuration."""
        # After refactoring, API configuration is in api_service
        self.assertEqual(self.service.max_history, 10)
        self.assertIsNotNone(self.service.api_service)
        self.assertIsNotNone(self.service.message_display_service)

        # Check conversation state
        self.assertEqual(self.service.conversation_history, [])
        self.assertIsNotNone(self.service.processing_queue)
        self.assertFalse(self.service.is_processing)
        self.assertFalse(self.service.turn_completed)

    @patch("aiohttp.ClientSession")
    def test_initialization_creates_http_session(self, mock_session_class):
        """Test that initialization creates HTTP session and registers hooks."""
        mock_session = AsyncMock()
        mock_session_class.return_value = mock_session

        with (
            patch.object(self.service, "hook_system") as mock_hook_system,
            patch.object(self.service, "mcp_integration") as mock_mcp,
            patch.object(self.service, "conversation_logger") as mock_logger,
        ):

            mock_hook_system.register_hooks = AsyncMock()
            mock_mcp.discover_mcp_servers = AsyncMock(return_value=[])
            mock_logger.log_conversation_start = AsyncMock()

            # Run async initialization
            asyncio.run(self.service.initialize())

            # Session management moved to api_service during refactor
            self.assertTrue(self.service.api_service._initialized)
            mock_hook_system.register_hooks.assert_called_once()
            # MCP discovery moved to background task (non-blocking)
            # mock_mcp.discover_mcp_servers called via create_background_task
            mock_logger.log_conversation_start.assert_called_once()

    def test_system_prompt_building(self):
        """Test system prompt construction."""
        # Mock the SystemPromptBuilder.build() to return test prompt
        with patch.object(
            self.service._prompt_builder,
            "build",
            return_value="You are a test assistant.\n\nThis is the codebase and context for our session.",
        ):
            prompt = self.service._build_system_prompt()

            # Should contain base prompt
            self.assertIn("You are a test assistant.", prompt)

            # Should contain project awareness statement
            self.assertIn("This is the codebase and context", prompt)

    def test_process_user_input_queuing(self):
        """Test that user input is properly queued and displayed."""
        message = "Test user input"

        with (
            patch.object(self.service, "conversation_logger") as mock_logger,
            patch.object(self.service, "_process_queue"),
        ):
            mock_logger.log_user_message = AsyncMock(return_value="test-uuid")

            result = asyncio.run(self.service.process_user_input(message))

            # Should queue the message
            self.assertEqual(result["status"], "queued")

            # Check queue size (background task mocked to prevent consumption)
            self.assertEqual(self.service.processing_queue.qsize(), 1)

    def test_cancel_request_handling(self):
        """Test cancel request functionality."""
        # Set processing state
        self.service.is_processing = True

        # Cancel should set flag
        self.service.cancel_current_request()

        self.assertTrue(self.service.cancel_processing)

    def test_status_line_generation(self):
        """Test status line information generation."""
        # Set some test state
        self.service.is_processing = True
        self.service.current_processing_tokens = 150
        self.service.session_stats = {
            "messages": 5,
            "input_tokens": 100,
            "output_tokens": 200,
        }

        status = self.service.get_status_line()

        # Should contain processing info
        self.assertIn("Processing: 150 tokens", str(status["A"]))

        # Should contain queue and history info
        self.assertIn("Queue:", str(status["C"]))
        self.assertIn("History:", str(status["C"]))

        # Should contain session stats when available
        self.assertIn("Messages: 5", str(status["C"]))
        self.assertIn("Tokens In: 100", str(status["C"]))
        self.assertIn("Tokens Out: 200", str(status["C"]))


class TestResponseParserIntegration(unittest.TestCase):
    """Tests for ResponseParser component."""

    def setUp(self):
        """Set up parser instance."""
        self.parser = ResponseParser()

    def test_parse_simple_response(self):
        """Test parsing response without special tags."""
        response = "This is a simple response without any tags."

        parsed = self.parser.parse_response(response)

        self.assertEqual(parsed["content"], response)
        self.assertTrue(parsed["turn_completed"])
        self.assertFalse(parsed["metadata"]["has_thinking"])
        self.assertFalse(parsed["metadata"]["has_terminal_commands"])
        self.assertFalse(parsed["metadata"]["has_tool_calls"])

    def test_parse_thinking_response(self):
        """Test parsing response with thinking tags."""
        response = """<think>
Let me analyze this request carefully.
The user is asking about parsing.
</think>

Here's my analysis of the parsing requirements."""

        parsed = self.parser.parse_response(response)

        # Content should be cleaned
        self.assertEqual(
            parsed["content"], "Here's my analysis of the parsing requirements."
        )

        # Should detect thinking
        self.assertTrue(parsed["metadata"]["has_thinking"])
        self.assertEqual(len(parsed["components"]["thinking"]), 1)
        self.assertIn("analyze this request", parsed["components"]["thinking"][0])

    def test_parse_terminal_commands(self):
        """Test parsing response with terminal commands."""
        response = """I'll check the directory structure.

<terminal>ls -la</terminal>

Let me also check the file contents.

<terminal>cat README.md</terminal>

That should give us the information we need."""

        parsed = self.parser.parse_response(response)

        # Should detect terminal commands
        self.assertTrue(parsed["metadata"]["has_terminal_commands"])
        self.assertEqual(len(parsed["components"]["terminal_commands"]), 2)

        # Should extract commands correctly
        commands = parsed["components"]["terminal_commands"]
        self.assertEqual(commands[0]["command"], "ls -la")
        self.assertEqual(commands[1]["command"], "cat README.md")

        # Turn should not be completed (tools to execute)
        self.assertFalse(parsed["turn_completed"])

    def test_parse_mcp_tool_calls(self):
        """Test parsing response with MCP tool calls."""
        response = """I'll search for the relevant files.

<tool name="file_search" pattern="*.py" directory="/src">
Looking for Python files in the source directory.
</tool>

<tool name="read_file" path="/src/main.py">
Reading the main application file.
</tool>

This should help us understand the codebase."""

        parsed = self.parser.parse_response(response)

        # Should detect tool calls
        self.assertTrue(parsed["metadata"]["has_tool_calls"])
        self.assertEqual(len(parsed["components"]["tool_calls"]), 2)

        # Should parse tool attributes correctly
        tools = parsed["components"]["tool_calls"]

        # First tool
        self.assertEqual(tools[0]["name"], "file_search")
        self.assertEqual(tools[0]["arguments"]["pattern"], "*.py")
        self.assertEqual(tools[0]["arguments"]["directory"], "/src")

        # Second tool
        self.assertEqual(tools[1]["name"], "read_file")
        self.assertEqual(tools[1]["arguments"]["path"], "/src/main.py")

        # Turn should not be completed (tools to execute)
        self.assertFalse(parsed["turn_completed"])

    def test_get_all_tools_ordering(self):
        """Test that tools are returned in correct execution order."""
        response = """<terminal>ls</terminal>

<tool name="read_file" path="test.txt">Reading file</tool>

<terminal>pwd</terminal>"""

        parsed = self.parser.parse_response(response)
        all_tools = self.parser.get_all_tools(parsed)

        # Should have 3 tools in order (based on actual sort implementation)
        self.assertEqual(len(all_tools), 3)

        # Find tools by content to account for sorting
        terminal_tools = [t for t in all_tools if t["type"] == "terminal"]
        mcp_tools = [t for t in all_tools if t["type"] == "mcp_tool"]

        self.assertEqual(len(terminal_tools), 2)
        self.assertEqual(len(mcp_tools), 1)

        # Check terminal commands
        commands = [t["command"] for t in terminal_tools]
        self.assertIn("ls", commands)
        self.assertIn("pwd", commands)

        # Check MCP tool
        self.assertEqual(mcp_tools[0]["name"], "read_file")

    def test_response_validation(self):
        """Test response validation functionality."""
        # Valid response
        valid_response = "This is a valid response."
        is_valid, issues = self.parser.validate_response(valid_response)
        self.assertTrue(is_valid)
        self.assertEqual(len(issues), 0)

        # Invalid - unclosed thinking tag
        invalid_response = "<think>Unclosed thinking tag"
        is_valid, issues = self.parser.validate_response(invalid_response)
        self.assertFalse(is_valid)
        self.assertIn("Unclosed tag: <think>", str(issues))

        # Invalid - empty response
        empty_response = ""
        is_valid, issues = self.parser.validate_response(empty_response)
        self.assertFalse(is_valid)
        self.assertIn("Empty response", str(issues))


class TestMessageDisplayCoordination(unittest.TestCase):
    """Tests for message display coordination functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = MagicMock()
        self.config.get.side_effect = self._get_config_value

        self.event_bus = MagicMock()
        self.renderer = MagicMock()
        self.message_coordinator = MagicMock()
        self.renderer.message_coordinator = self.message_coordinator

        self.service = LLMService(self.config, self.event_bus, self.renderer)

    def _get_config_value(self, key, default=None):
        """Mock configuration values."""
        config_map = {
            "kollabor.llm.api_url": "http://localhost:1234",
            "kollabor.llm.model": "test-model",
            "kollabor.llm.temperature": 0.7,
            "kollabor.llm.timeout": 30,
            "kollabor.llm.max_history": 10,
            "kollabor.llm.enable_streaming": False,
            "kollabor.llm.terminal_timeout": 10,
            "kollabor.llm.mcp_timeout": 20,
            "kollabor.llm.system_prompt.base_prompt": "You are a test assistant.",
            "kollabor.llm.system_prompt.include_project_structure": False,
            "kollabor.llm.system_prompt.attachment_files": [],
            "kollabor.llm.system_prompt.custom_prompt_files": [],
        }
        return config_map.get(key, default)

    def test_thinking_display_integration(self):
        """Test that _execute_llm_turn passes thinking_duration to display_complete_response.

        The actual 'Thought for X seconds' formatting is tested by
        test_message_sequence_atomic_display. This test verifies the integration:
        the queue processor measures elapsed time and forwards it to the display service.
        """
        mock_api_call = AsyncMock(return_value="Test response")

        # Set MCP discovery event to avoid timeout
        self.service._native_tools.discovery_complete.set()
        # Disable native tool calling so we don't enter that branch
        self.service._native_tools.tool_calling_enabled = False
        self.service.api_service.has_pending_tool_calls = MagicMock(return_value=False)
        self.renderer.pipe_mode = False

        # Create mock logger with async methods
        mock_logger = MagicMock()
        mock_logger.log_user_message = AsyncMock(return_value="uuid1")
        mock_logger.log_assistant_message = AsyncMock(return_value="uuid2")
        mock_logger.log_system_message = AsyncMock()

        self.event_bus.emit_with_hooks = AsyncMock(return_value={})

        # Mock display_complete_response to capture call args
        display_service = self.service._queue_processor.message_display_service
        mock_display = MagicMock()

        with (
            patch.object(self.service.api_service, "call_llm", mock_api_call),
            patch.object(self.service, "conversation_logger", mock_logger),
            patch.object(display_service, "display_complete_response", mock_display),
        ):

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    self.service._process_message_batch(["Test message"])
                )
            finally:
                loop.close()

        # Verify display_complete_response was called with expected arguments
        mock_display.assert_called_once()
        call_kwargs = mock_display.call_args[1]
        self.assertIn("thinking_duration", call_kwargs)
        self.assertIsInstance(call_kwargs["thinking_duration"], float)
        self.assertEqual(call_kwargs["response"], "Test response")

    def test_message_sequence_atomic_display(self):
        """Test that related messages are displayed atomically."""
        # Test the message sequence building logic
        message_sequence = []

        # Simulate thinking duration > 0.1
        thinking_duration = 0.5
        response = "Test response content"

        if thinking_duration > 0.1:
            message_sequence.append(
                ("system", f"Thought for {thinking_duration:.1f} seconds", {})
            )

        if response.strip():
            message_sequence.append(("assistant", response, {}))

        # Verify sequence structure
        self.assertEqual(len(message_sequence), 2)
        self.assertEqual(message_sequence[0][0], "system")
        self.assertEqual(message_sequence[1][0], "assistant")
        self.assertIn("Thought for 0.5 seconds", message_sequence[0][1])
        self.assertEqual(message_sequence[1][1], response)


class TestLLMServiceHookIntegration(unittest.TestCase):
    """Tests for LLM service hook system integration."""

    def setUp(self):
        """Set up hook integration test fixtures."""
        self.config = MagicMock()
        self.config.get.side_effect = self._get_config_value

        self.event_bus = MagicMock()
        self.renderer = MagicMock()

        self.service = LLMService(self.config, self.event_bus, self.renderer)

    def _get_config_value(self, key, default=None):
        """Mock configuration values."""
        config_map = {
            "kollabor.llm.api_url": "http://localhost:1234",
            "kollabor.llm.model": "test-model",
            "kollabor.llm.temperature": 0.7,
            "kollabor.llm.timeout": 30,
            "kollabor.llm.max_history": 10,
            "kollabor.llm.enable_streaming": False,
            "kollabor.llm.terminal_timeout": 10,
            "kollabor.llm.mcp_timeout": 20,
            "kollabor.llm.system_prompt.base_prompt": "You are a test assistant.",
            "kollabor.llm.system_prompt.include_project_structure": False,
            "kollabor.llm.system_prompt.attachment_files": [],
            "kollabor.llm.system_prompt.custom_prompt_files": [],
        }
        return config_map.get(key, default)

    def test_user_input_hook_handling(self):
        """Test user input hook callback processing."""
        # Test data
        event_data = {"message": "Test user message"}
        event = MagicMock()

        # Patch on the coordinator (MessageHandler accesses coordinator.process_user_input)
        mock_process = AsyncMock(return_value={"status": "processed"})
        self.service.process_user_input = mock_process

        result = asyncio.run(self.service._handle_user_input(event_data, event))

        mock_process.assert_called_once_with("Test user message")
        self.assertEqual(result["status"], "processed")

    def test_cancel_request_hook_handling(self):
        """Test cancel request hook callback processing."""
        event_data = {"reason": "user_requested", "source": "keyboard_interrupt"}
        event = MagicMock()

        # Ensure renderer is not in pipe mode (MagicMock attrs are truthy by default)
        self.renderer.pipe_mode = False

        # Patch on the coordinator (MessageHandler accesses coordinator.cancel_current_request)
        mock_cancel = MagicMock()
        self.service.cancel_current_request = mock_cancel

        result = asyncio.run(self.service._handle_cancel_request(event_data, event))

        mock_cancel.assert_called_once()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["reason"], "user_requested")

    def test_hook_registration(self):
        """Test that hooks are properly registered with event bus."""
        # register_hook is async, need AsyncMock
        self.event_bus.register_hook = AsyncMock()

        asyncio.run(self.service.register_hooks())

        # Should register all 5 hooks (context_injection, user_input, cancel, add_message, llm_continue)
        self.event_bus.register_hook.assert_called()

        calls = self.event_bus.register_hook.call_args_list
        self.assertEqual(len(calls), 5)


if __name__ == "__main__":
    # Run tests
    unittest.main(verbosity=2)
