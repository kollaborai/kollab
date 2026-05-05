"""Streaming response handling for Kollab LLM service.

Handles streaming chunk processing, thinking content display,
and LLM API call orchestration. Extracted from LLMService as part of
the llm_service.py decomposition (Phase B).

Phase 8A: Thinking parsing and display delegated to kollabor_ai and kollabor_tui.
"""

import asyncio
import logging
from typing import Callable, List, Optional

# Delegated thinking parsing (kollabor_ai)
from kollabor_ai.streaming_thinking_parser import (
    StreamingThinkingParser,
)
from kollabor_tui.status.core_widgets import get_token_io_state

# Delegated thinking display formatting (kollabor_tui)
from kollabor_tui.thinking_display import ThinkingDisplayFormatter

logger = logging.getLogger(__name__)


class StreamingHandler:
    """Handles streaming LLM responses and thinking content display.

    Responsibilities:
    - Process streaming chunks from LLM API
    - Parse and display thinking content (delegated to kollabor_ai + kollabor_tui)
    - Stream response content to message renderer
    - Orchestrate LLM API calls via APICommunicationService
    - Clean up streaming state after completion/error
    """

    def __init__(self, api_service, message_display_service, renderer):
        """Initialize the streaming handler.

        Args:
            api_service: APICommunicationService for making LLM API calls
            message_display_service: MessageDisplayService for streaming output
            renderer: TerminalRenderer for thinking display updates
        """
        self.api_service = api_service
        self.message_display_service = message_display_service
        self.renderer = renderer

        # Streaming state (delegates to kollabor_ai + kollabor_tui)
        self._thinking_parser = StreamingThinkingParser()
        self._thinking_formatter = ThinkingDisplayFormatter()
        self._response_started = False

        # Buffer for streaming chunks — displayed in thinking area as preview,
        # then flushed via display_complete_response when streaming ends.
        self._streaming_buffer: str = ""

    async def call_llm(
        self,
        conversation_history,
        max_history: int,
        native_tools: Optional[List[dict]],
        mcp_discovery_complete: asyncio.Event,
        is_cancelled_fn: Callable[[], bool],
    ) -> str:
        """Make API call to LLM using APICommunicationService.

        Args:
            conversation_history: Current conversation history
            max_history: Maximum history messages to send
            native_tools: Native tool definitions for function calling (or None)
            mcp_discovery_complete: Event signaling MCP discovery is done
            is_cancelled_fn: Callable that returns True if request is cancelled

        Returns:
            LLM response string

        Raises:
            asyncio.CancelledError: If request was cancelled by user
        """
        # Reset streaming state for new request (delegates to packages)
        self._thinking_parser.reset()
        self._thinking_formatter.reset()
        self._response_started = False

        # Check for cancellation before starting
        if is_cancelled_fn():
            logger.info("API call cancelled before starting")
            raise asyncio.CancelledError("Request cancelled by user")

        # Wait for MCP discovery to complete (prevent race condition on first call)
        try:
            await asyncio.wait_for(mcp_discovery_complete.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(
                "MCP discovery did not complete within 5 seconds - "
                "proceeding with available tools (MCP tools may not be available)"
            )

        # Delegate to API communication service
        try:
            return await self.api_service.call_llm(  # type: ignore[no-any-return]
                conversation_history=conversation_history,
                max_history=max_history,
                streaming_callback=self.handle_chunk,
                tools=native_tools,
                on_rate_limit=self._on_rate_limit,
            )
        except asyncio.CancelledError:
            logger.info("LLM API call was cancelled")
            raise
        except Exception as e:
            logger.error(f"LLM API call failed: {type(e).__name__}: {e}")
            raise
        finally:
            self.cleanup()

    async def handle_chunk(self, chunk: str) -> None:
        """Handle streaming content chunk from API.

        Args:
            chunk: Content chunk from streaming API response
        """
        # Delegate to kollabor_ai StreamingThinkingParser
        result = self._thinking_parser.parse_chunk(chunk)

        # Stream response content chunks
        for content in result.response_content:
            self._stream_response_chunk(content)

        # Display thinking content (delegated to kollabor_tui)
        if result.thinking_content:
            display_text = self._thinking_formatter.format(
                result.thinking_content, final=result.thinking_complete
            )
            if display_text:
                self.renderer.update_thinking(True, display_text)

        # Handle thinking complete state (switch to receiving mode)
        if result.thinking_complete:
            self.renderer.update_thinking(True, "Receiving...")

    def cleanup(self) -> None:
        """Clean up streaming state after request completion or failure.

        This ensures streaming state is properly reset even if errors occur.
        Called from finally block so it runs on success, error, and cancel.
        """
        self._thinking_parser.reset()
        self._thinking_formatter.reset()
        self._response_started = False
        self._streaming_buffer = ""

        # End streaming session in message display service if active
        if self.message_display_service.is_streaming_active():
            self.message_display_service.end_streaming_response()

        # Clear "Receiving..." from thinking bar to prevent stale display
        self.renderer.update_thinking(False)

        logger.debug("Cleaned up streaming state")

    async def _on_rate_limit(self, attempt: int, max_retries: int, delay: int) -> None:
        msg = f"rate limited (attempt {attempt}/{max_retries}) — retrying in {delay}s"
        self.renderer.message_coordinator.display_message_sequence(
            [("system", msg, {"display_type": "warning"})]
        )

    def _stream_response_chunk(self, chunk: str) -> None:
        """Buffer a streaming chunk and update the thinking area preview.

        Chunks are accumulated in _streaming_buffer instead of writing directly
        to the terminal. The thinking area shows a scrolling preview of the last
        few words as they arrive. When streaming ends, display_complete_response
        renders the full buffered content through the normal render path.

        Args:
            chunk: Response content chunk to buffer
        """
        # Handle empty chunks gracefully. Keep chunks that contain newlines
        # since they complete lines in the preview buffer.
        if not chunk or (not chunk.strip() and "\n" not in chunk and "\r" not in chunk):
            return

        # Initialize streaming response if this is the first chunk
        if not self._response_started:
            self.message_display_service.start_streaming_response()
            self._response_started = True
            # Start receiving mode for token I/O widget
            get_token_io_state().start_receiving()

        # Accumulate into buffer
        self._streaming_buffer += chunk

        # Count tokens as they stream in (real-time, not fake animation)
        token_io = get_token_io_state()
        token_io.add_chunk(len(chunk))

        # Preview mode depends on tool calling style:
        # - Native tools (supports_tools=True): content stream is clean text,
        #   safe to show line-buffered preview in thinking box.
        # - Inline XML tools (supports_tools=False): response contains raw XML
        #   tool calls mixed with content. Don't preview — just show token count.
        profile = getattr(self.api_service, "_profile", None)
        native_tools = profile.get_supports_tools() if profile else True

        if native_tools:
            # Line-buffered preview: update on complete lines, show last 5.
            lines = self._streaming_buffer.replace("\r\n", "\n").split("\n")
            complete_lines = lines[:-1]
            if not complete_lines:
                return
            preview = "\n".join(complete_lines[-5:])
            self.renderer.update_thinking(True, preview)
        else:
            # Inline XML mode: just show token counter, no content preview
            total = token_io.download_tokens
            self.renderer.update_thinking(True, f"Receiving... ({total} tokens)")
