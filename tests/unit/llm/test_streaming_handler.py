"""Tests for StreamingHandler."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor.llm.streaming_handler import StreamingHandler


class TestStreamingHandler(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.api_service = MagicMock()
        self.api_service.call_llm = AsyncMock(return_value="test response")
        # Default: native tool calling (safe to preview stream content)
        self.api_service._profile = MagicMock()
        self.api_service._profile.get_supports_tools = MagicMock(return_value=True)

        self.message_display_service = MagicMock()
        self.message_display_service.is_streaming_active = MagicMock(return_value=False)
        self.message_display_service.start_streaming_response = MagicMock()
        self.message_display_service.end_streaming_response = MagicMock()
        self.message_display_service.message_coordinator = MagicMock()

        self.renderer = MagicMock()
        self.renderer.update_thinking = MagicMock()

        self.handler = StreamingHandler(
            api_service=self.api_service,
            message_display_service=self.message_display_service,
            renderer=self.renderer,
        )

    def tearDown(self):
        """Clean up."""
        self.loop.close()

    def test_init(self):
        """Test StreamingHandler initialization."""
        self.assertIsNotNone(self.handler)
        self.assertEqual(self.handler.api_service, self.api_service)
        self.assertIsNotNone(self.handler._thinking_parser)
        self.assertIsNotNone(self.handler._thinking_formatter)
        self.assertFalse(self.handler._response_started)
        self.assertEqual(self.handler._streaming_buffer, "")

    def test_cleanup(self):
        """Test cleanup resets streaming state."""
        self.handler._response_started = True
        self.handler._streaming_buffer = "some buffered content"

        self.handler.cleanup()

        self.assertFalse(self.handler._response_started)
        self.assertEqual(self.handler._streaming_buffer, "")

    def test_cleanup_ends_streaming(self):
        """Test cleanup ends streaming when active."""
        self.message_display_service.is_streaming_active = MagicMock(return_value=True)

        self.handler.cleanup()

        self.message_display_service.end_streaming_response.assert_called_once()
        self.assertFalse(self.handler._response_started)

    def test_call_llm_success(self):
        """Test successful LLM call."""
        conversation_history = []
        native_tools = None
        mcp_complete = asyncio.Event()
        mcp_complete.set()

        def is_cancelled_fn():
            return False

        result = self.loop.run_until_complete(
            self.handler.call_llm(
                conversation_history=conversation_history,
                max_history=90,
                native_tools=native_tools,
                mcp_discovery_complete=mcp_complete,
                is_cancelled_fn=is_cancelled_fn,
            )
        )

        self.assertEqual(result, "test response")
        self.api_service.call_llm.assert_called_once()

    def test_call_llm_cancelled_before_start(self):
        """Test LLM call cancelled before starting."""
        conversation_history = []
        native_tools = None
        mcp_complete = asyncio.Event()
        mcp_complete.set()

        def is_cancelled_fn():
            return True

        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(
                self.handler.call_llm(
                    conversation_history=conversation_history,
                    max_history=90,
                    native_tools=native_tools,
                    mcp_discovery_complete=mcp_complete,
                    is_cancelled_fn=is_cancelled_fn,
                )
            )

    @patch(
        "kollabor.llm.streaming_handler.asyncio.wait_for",
        side_effect=asyncio.TimeoutError,
    )
    def test_call_llm_waits_for_mcp_discovery(self, mock_wait):
        """Test LLM call waits for MCP discovery."""
        conversation_history = []
        native_tools = None
        mcp_complete = asyncio.Event()

        def is_cancelled_fn():
            return False

        result = self.loop.run_until_complete(
            self.handler.call_llm(
                conversation_history=conversation_history,
                max_history=90,
                native_tools=native_tools,
                mcp_discovery_complete=mcp_complete,
                is_cancelled_fn=is_cancelled_fn,
            )
        )

        self.assertEqual(result, "test response")

    @patch("kollabor_tui.status.core_widgets.get_token_io_state")
    def test_handle_chunk_plain_text(self, mock_token_io):
        """Test handling plain text chunk without thinking tags."""
        mock_token_io.return_value = MagicMock()
        chunk = "Hello world\n"

        self.loop.run_until_complete(self.handler.handle_chunk(chunk))

        # Plain text is buffered; preview updates on complete line
        self.assertIn("Hello world", self.handler._streaming_buffer)
        self.renderer.update_thinking.assert_called()

    @patch("kollabor_tui.status.core_widgets.get_token_io_state")
    def test_handle_chunk_with_thinking_start(self, mock_token_io):
        """Test handling chunk with think start tag."""
        mock_token_io.return_value = MagicMock()
        chunk = "Before<think>thinking content"

        self.loop.run_until_complete(self.handler.handle_chunk(chunk))

        # Parser should have entered thinking state
        self.assertTrue(self.handler._thinking_parser.state.in_thinking)

    @patch("kollabor_tui.status.core_widgets.get_token_io_state")
    def test_handle_chunk_with_thinking_end(self, mock_token_io):
        """Test handling chunk with think end tag."""
        mock_token_io.return_value = MagicMock()

        # First enter thinking mode
        self.loop.run_until_complete(self.handler.handle_chunk("<think>some thought"))
        # Then end it
        self.loop.run_until_complete(self.handler.handle_chunk("</think>response here"))

        # Should have called renderer for thinking display
        self.renderer.update_thinking.assert_called()

    @patch("kollabor_tui.status.core_widgets.get_token_io_state")
    def test_stream_response_chunk_first_chunk(self, mock_token_io):
        """Test streaming first response chunk starts streaming session and buffers."""
        mock_token_io.return_value = MagicMock()

        # Send chunk without newline — buffers but no preview update yet
        self.handler._stream_response_chunk("First response chunk")
        self.assertTrue(self.handler._response_started)
        self.message_display_service.start_streaming_response.assert_called_once()
        self.assertIn("First response chunk", self.handler._streaming_buffer)
        self.renderer.update_thinking.assert_not_called()

        # Send newline to complete the line — now preview updates
        self.handler._stream_response_chunk("\n")
        self.renderer.update_thinking.assert_called_once_with(
            True, "First response chunk"
        )

    @patch("kollabor_tui.status.core_widgets.get_token_io_state")
    def test_stream_response_chunk_multiline_preview(self, mock_token_io):
        """Test preview shows last 5 complete lines."""
        mock_token_io.return_value = MagicMock()

        for i in range(7):
            self.handler._stream_response_chunk(f"Line {i}\n")

        # Should show lines 2-6 (last 5)
        last_call = self.renderer.update_thinking.call_args
        preview = last_call[0][1]
        self.assertNotIn("Line 0", preview)
        self.assertNotIn("Line 1", preview)
        self.assertIn("Line 2", preview)
        self.assertIn("Line 6", preview)

    @patch("kollabor_tui.status.core_widgets.get_token_io_state")
    def test_stream_response_chunk_inline_xml_no_preview(self, mock_token_io):
        """Test inline XML mode shows token count, not content preview."""
        mock_token_io.return_value = MagicMock()

        # Switch to inline XML tool mode
        self.api_service._profile.get_supports_tools.return_value = False

        self.handler._stream_response_chunk("some <tool_call>xml</tool_call>\n")

        # Should show token counter pattern, not raw XML content
        call_args = self.renderer.update_thinking.call_args
        self.assertTrue(call_args[0][0])  # active=True
        self.assertIn("Receiving...", call_args[0][1])
        self.assertIn("tokens)", call_args[0][1])
        self.assertNotIn("<tool_call>", call_args[0][1])

    @patch("kollabor_tui.status.core_widgets.get_token_io_state")
    def test_stream_response_chunk_empty(self, mock_token_io):
        """Test streaming empty chunk is ignored."""
        mock_token_io.return_value = MagicMock()
        self.handler._stream_response_chunk("")
        self.handler._stream_response_chunk("   ")

        self.assertFalse(self.handler._response_started)
        self.assertEqual(self.handler._streaming_buffer, "")


class TestStreamingThinkingParser(unittest.TestCase):
    """Test the delegated thinking parser from kollabor_ai."""

    def test_parse_plain_text(self):
        from kollabor_ai.streaming_thinking_parser import StreamingThinkingParser

        parser = StreamingThinkingParser()
        result = parser.parse_chunk("Hello world")
        self.assertEqual(result.response_content, ["Hello world"])
        self.assertIsNone(result.thinking_content)

    def test_parse_thinking_start(self):
        from kollabor_ai.streaming_thinking_parser import StreamingThinkingParser

        parser = StreamingThinkingParser()
        result = parser.parse_chunk("<think>thinking")
        self.assertEqual(result.thinking_content, "thinking")
        self.assertTrue(result.in_thinking)
        self.assertFalse(result.thinking_complete)

    def test_parse_thinking_end(self):
        from kollabor_ai.streaming_thinking_parser import StreamingThinkingParser

        parser = StreamingThinkingParser()
        parser.parse_chunk("<think>thinking")
        result = parser.parse_chunk("more</think>response")
        self.assertTrue(result.thinking_complete)
        self.assertIn("response", result.response_content)

    def test_reset(self):
        from kollabor_ai.streaming_thinking_parser import StreamingThinkingParser

        parser = StreamingThinkingParser()
        parser.parse_chunk("<think>thinking")
        self.assertTrue(parser.state.in_thinking)
        parser.reset()
        self.assertFalse(parser.state.in_thinking)
        result = parser.parse_chunk("plain text")
        self.assertEqual(result.response_content, ["plain text"])


class TestThinkingDisplayFormatter(unittest.TestCase):
    """Test the delegated thinking display formatter from kollabor_tui."""

    def test_format_long_content(self):
        """Formatter returns text when content exceeds chunk width."""
        from kollabor_tui.thinking_display import ThinkingDisplayFormatter

        formatter = ThinkingDisplayFormatter()
        # Need content longer than chunk_width (70% of terminal_width)
        long_content = "word " * 30  # ~150 chars
        result = formatter.format(long_content, final=False, terminal_width=80)
        self.assertIsNotNone(result)

    def test_format_short_content_returns_none(self):
        """Formatter returns None for short content (waiting for more)."""
        from kollabor_tui.thinking_display import ThinkingDisplayFormatter

        formatter = ThinkingDisplayFormatter()
        result = formatter.format("short", final=False, terminal_width=80)
        self.assertIsNone(result)

    def test_format_final_shows_remaining(self):
        """Formatter with final=True shows remaining content."""
        from kollabor_tui.thinking_display import ThinkingDisplayFormatter

        formatter = ThinkingDisplayFormatter()
        result = formatter.format(
            "some remaining content", final=True, terminal_width=80
        )
        self.assertIsNotNone(result)

    def test_reset(self):
        from kollabor_tui.thinking_display import ThinkingDisplayFormatter

        formatter = ThinkingDisplayFormatter()
        long_content = "word " * 30
        formatter.format(long_content, final=False, terminal_width=80)
        self.assertGreater(formatter._last_chunk_position, 0)
        formatter.reset()
        self.assertEqual(formatter._last_chunk_position, 0)


if __name__ == "__main__":
    unittest.main()
