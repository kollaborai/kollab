"""Message Display Service for LLM responses.

Handles unified message display coordination, eliminating duplicated
display logic throughout the LLM service. Follows KISS principle with
single responsibility for message display orchestration.
"""

import logging
from typing import Any, Dict, List, Optional

from kollabor_tui.tool_display import (
    extract_file_display_info as _td_extract_file_display_info,
)
from kollabor_tui.tool_display import (
    extract_tool_info as _td_extract_tool_info,
)
from kollabor_tui.tool_display import (
    extract_tool_name_args as _td_extract_tool_name_args,
)
from kollabor_tui.tool_display import (
    format_edit_diff as _td_format_edit_diff,
)

# Tool display formatting extracted to kollabor_tui.tool_display
from kollabor_tui.tool_display import (
    format_tool_header as _td_format_tool_header,
)
from kollabor_tui.tool_display import (
    format_tool_output as _td_format_tool_output,
)
from kollabor_tui.tool_display import (
    format_tool_result as _td_format_tool_result,
)
from kollabor_tui.tool_display import (
    get_result_summary_modern as _td_get_result_summary_modern,
)
from kollabor_tui.tool_display import (
    get_tool_result_summary as _td_get_tool_result_summary,
)
from kollabor_tui.tool_display import (
    should_show_output as _td_should_show_output,
)
from kollabor_tui.tool_display import (
    truncate_tool_args as _td_truncate_tool_args,
)

logger = logging.getLogger(__name__)


class MessageDisplayService:
    """Unified service for coordinating LLM message display.

    Eliminates code duplication by providing a single point of control
    for all message display operations including thinking duration,
    assistant responses, and tool execution results.

    Follows KISS principle: Single responsibility for message display coordination.
    Implements DRY principle: Eliminates ~90 lines of duplicated display code.
    """

    def __init__(self, renderer):
        """Initialize message display service.

        Args:
            renderer: Terminal renderer with message_coordinator
        """
        self.renderer = renderer
        self.message_coordinator = renderer.message_coordinator
        self._streaming_active = False

        logger.info("Message display service initialized")

    def display_thinking_and_response(
        self,
        thinking_duration: float,
        response: str,
        show_thinking_threshold: float = 0.1,
        thinking_content: Optional[List[str] | str] = None,
    ) -> None:
        """Display thinking duration and assistant response atomically.

        Args:
            thinking_duration: Time spent thinking in seconds
            response: Assistant response content
            show_thinking_threshold: Minimum duration to show thinking message
            thinking_content: Provider-supplied reasoning/thinking content
        """
        # Use the unified display method for consistency
        self.display_complete_response(
            thinking_duration=thinking_duration,
            response=response,
            tool_results=[],
            original_tools=[],
            show_thinking_threshold=show_thinking_threshold,
            thinking_content=thinking_content,
        )

    def display_tool_results(
        self, tool_results: List[Any], original_tools: Optional[List[Dict]] = None
    ) -> None:
        """Display tool execution results with consistent formatting.

        Args:
            tool_results: List of tool execution result objects
            original_tools: List of original tool data for command extraction
        """
        for i, result in enumerate(tool_results):
            # Get original tool data for display
            tool_data = (
                original_tools[i] if original_tools and i < len(original_tools) else {}
            )

            # Extract tool name and args for modern rendering
            tool_name, tool_args = self._extract_tool_name_args(result, tool_data)

            logger.info(
                f"[TOOL-DISPLAY-DEBUG] display_tool_results: "
                f"tool_name={tool_name}, tool_type={result.tool_type}, "
                f"is_displaying={self.message_coordinator.is_displaying}, "
                f"writing_messages={self.message_coordinator.terminal_renderer.writing_messages}, "
                f"queue_len={len(self.message_coordinator.message_queue)}"
            )

            # Get result summary for inline display
            result_summary = self._get_result_summary_modern(result, tool_data)

            # Determine status
            tool_status = "error" if not result.success else "success"

            # For file operations, we show summary inline and never show raw output
            # For other tools, we might show output if it's short enough
            output_content = ""
            show_output = self._should_show_output(result)

            if show_output:
                output_lines = self._format_tool_output(result)
                output_content = "\n".join(output_lines)

            # Create message sequence for this tool using modern "tool" type
            tool_messages = [
                (
                    "tool",
                    output_content,
                    {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_status": tool_status,
                        "result_summary": result_summary,
                    },
                )
            ]

            # No spacing between tools - boxes provide visual separation

            # Display tool messages using coordinator
            logger.info(
                f"[TOOL-DISPLAY-DEBUG] calling display_message_sequence "
                f"with tool_name={tool_name}"
            )
            self.message_coordinator.display_message_sequence(tool_messages)

        logger.debug(f"Displayed {len(tool_results)} tool results")

    def display_user_message(self, message: str) -> None:
        """Display user message through coordinator.

        Args:
            message: User's input message
        """
        # Don't display user messages in pipe mode
        if getattr(self.renderer, "pipe_mode", False):
            logger.debug(f"Suppressing user message in pipe mode: {len(message)} chars")
            return

        message_sequence: list[tuple[str, str, dict]] = [("user", message, {})]
        self.message_coordinator.display_message_sequence(message_sequence)
        logger.debug(f"Displayed user message: {len(message)} chars")

    def display_system_message(self, message: str) -> None:
        """Display system message through coordinator.

        Args:
            message: System message to display
        """
        message_sequence: list[tuple[str, str, dict]] = [("system", message, {})]
        self.message_coordinator.display_message_sequence(message_sequence)
        logger.debug(f"Displayed system message: {message[:50]}...")

    def display_error_message(self, error: str) -> None:
        """Display error message through coordinator.

        Args:
            error: Error message to display
        """
        message_sequence: list[tuple[str, str, dict]] = [
            ("error", f"Error: {error}", {})
        ]
        self.message_coordinator.display_message_sequence(message_sequence)
        logger.debug(f"Displayed error message: {error[:50]}...")

    def display_cancellation_message(self) -> None:
        """Display request cancellation message."""
        # Don't display cancellation message in pipe mode (it's expected during cleanup)
        pipe_mode = getattr(self.renderer, "pipe_mode", False)

        if hasattr(self.renderer, "pipe_mode") and pipe_mode:
            logger.debug("Suppressing cancellation message in pipe mode")
            return

        message_sequence: list[tuple[str, str, dict]] = [
            ("system", "Request cancelled", {})
        ]
        self.message_coordinator.display_message_sequence(message_sequence)
        logger.debug("Displayed cancellation message")

    def _format_tool_header(self, result, tool_data: Optional[Dict] = None) -> str:
        """Delegates to kollabor_tui.tool_display._format_tool_header"""
        return _td_format_tool_header(result, tool_data)

    def _extract_file_display_info(
        self, tool_data: Dict, tool_type: str, result=None
    ) -> str:
        """Delegates to kollabor_tui.tool_display._extract_file_display_info"""
        return _td_extract_file_display_info(tool_data, tool_type, result)

    def _format_tool_result(self, result, tool_data: Optional[Dict] = None) -> str:
        """Delegates to kollabor_tui.tool_display._format_tool_result"""
        return _td_format_tool_result(result, tool_data)

    def _extract_tool_info(self, result, tool_data: Optional[Dict] = None) -> tuple:
        """Delegates to kollabor_tui.tool_display._extract_tool_info"""
        return _td_extract_tool_info(result, tool_data)

    def _extract_tool_name_args(self, result, tool_data: Optional[Dict] = None):
        """Delegates to kollabor_tui.tool_display._extract_tool_name_args"""
        return _td_extract_tool_name_args(result, tool_data)

    def _truncate_tool_args(self, args: str, max_length: int = 60) -> str:
        """Delegates to kollabor_tui.tool_display._truncate_tool_args"""
        return _td_truncate_tool_args(args, max_length=60)

    def _get_result_summary_modern(
        self, result, tool_data: Optional[Dict] = None
    ) -> str:
        """Delegates to kollabor_tui.tool_display._get_result_summary_modern"""
        return _td_get_result_summary_modern(result, tool_data)

    def _get_tool_result_summary(self, result, tool_data: Optional[Dict] = None) -> str:
        """Delegates to kollabor_tui.tool_display._get_tool_result_summary"""
        return _td_get_tool_result_summary(result, tool_data)

    def _should_show_output(self, result) -> bool:
        """Delegates to kollabor_tui.tool_display._should_show_output"""
        return bool(_td_should_show_output(result))

    def _format_tool_output(self, result) -> List[str]:
        """Delegates to kollabor_tui.tool_display._format_tool_output"""
        return list(_td_format_tool_output(result))

    def _format_edit_diff(self, result) -> List[str]:
        """Delegates to kollabor_tui.tool_display._format_edit_diff"""
        return list(_td_format_edit_diff(result))

    def display_generating_progress(self, estimated_tokens: int) -> None:
        """Display generating progress with token estimate.

        Args:
            estimated_tokens: Estimated number of tokens being generated
        """
        if estimated_tokens > 0:
            self.renderer.update_thinking(
                True, f"Receiving... ({estimated_tokens} tokens)"
            )
        else:
            self.renderer.update_thinking(True, "Receiving...")
        logger.debug(f"Displaying receiving progress: {estimated_tokens} tokens")

    def clear_thinking_display(self) -> None:
        """Clear thinking/generating display."""
        self.renderer.update_thinking(False)
        logger.debug("Cleared thinking display")

    def show_loading(self, message: str = "Loading...") -> None:
        """Show loading indicator with custom message.

        Args:
            message: Loading message to display (default: "Loading...")
        """
        self.renderer.update_thinking(True, message)
        logger.debug(f"Showing loading: {message}")

    def hide_loading(self) -> None:
        """Hide loading indicator."""
        self.renderer.update_thinking(False)
        logger.debug("Hiding loading indicator")

    def start_streaming_response(self) -> None:
        """Start a streaming response session.

        This method initializes streaming mode, disabling atomic batching
        for the duration of the response to allow real-time display.
        """
        self._streaming_active = True
        logger.debug("Started streaming response session")

    def end_streaming_response(self) -> None:
        """End a streaming response session.

        This method disables streaming mode and returns to normal
        atomic batching behavior.
        """
        self._streaming_active = False
        logger.debug("Ended streaming response session")

    def is_streaming_active(self) -> bool:
        """Check if streaming mode is currently active.

        Returns:
            True if streaming is active, False otherwise
        """
        return bool(self._streaming_active)

    def display_complete_response(
        self,
        thinking_duration: float,
        response: str,
        tool_results: Optional[List[Any]] = None,
        original_tools: Optional[List[Dict]] = None,
        show_thinking_threshold: float = 0.1,
        skip_response_content: bool = False,
        thinking_content: Optional[List[str] | str] = None,
    ) -> None:
        """Display complete response with thinking, content, and tools atomically.

        This unified method ensures that thinking duration, assistant response,
        and tool execution results all display together in a single atomic
        operation, preventing commands from appearing after the response.

        Args:
            thinking_duration: Time spent thinking in seconds
            response: Assistant response content
            tool_results: List of tool execution result objects (optional)
            original_tools: List of original tool data for command extraction (optional)
            show_thinking_threshold: Minimum duration to show thinking message
            skip_response_content: Skip displaying response content (for streaming mode)
            thinking_content: Provider-supplied reasoning/thinking content
        """
        message_sequence: list[tuple[str, str, dict]] = []
        pipe_mode = getattr(self.renderer, "pipe_mode", False)

        # Timing/reasoning rows stay hidden while we tune the UI. Spacing now
        # lives in the message renderers so every visible user/assistant marker
        # gets the same compact separation, including attach/proxy output.
        # status_message = self._build_turn_status_message(
        #     thinking_duration, thinking_content
        # )

        # Add assistant response if present and not skipped (for streaming mode)
        if response.strip() and not skip_response_content:
            message_sequence.append(("assistant", response, {}))

        # Add tool results if present (suppress in pipe mode)
        if tool_results and not pipe_mode:
            for i, result in enumerate(tool_results):
                # Get original tool data for display
                tool_data = (
                    original_tools[i]
                    if original_tools and i < len(original_tools)
                    else {}
                )

                # Extract tool name and arguments for modern rendering
                tool_name, tool_args = self._extract_tool_info(result, tool_data)
                tool_status = "success" if result.success else "error"

                # Get result summary for inline display
                result_summary = self._get_tool_result_summary(result, tool_data)

                # Build tool result content (output only, summary goes inline)
                result_lines = []

                # Add actual output if appropriate
                if self._should_show_output(result):
                    output_lines = self._format_tool_output(result)
                    result_lines.extend(output_lines)

                # Add tool message to sequence with structured kwargs
                message_sequence.append(
                    (
                        "tool",
                        "\n".join(result_lines),
                        {
                            "tool_name": tool_name,
                            "tool_args": tool_args,
                            "tool_status": tool_status,
                            "result_summary": result_summary,
                        },
                    )
                )

        # Display everything atomically to prevent race conditions
        if message_sequence:
            self.message_coordinator.display_message_sequence(message_sequence)
            logger.debug(
                f"Displayed complete response with {len(message_sequence)} messages atomically"
            )

    @staticmethod
    def _build_turn_status_message(
        thinking_duration: float,
        thinking_content: Optional[List[str] | str] = None,
        preview_limit: int = 120,
    ) -> str:
        """Build the compact turn-timing row shown before a response."""
        preview = MessageDisplayService._format_reasoning_preview(
            thinking_content, preview_limit
        )
        if preview:
            return f"reasoning {thinking_duration:.1f}s · {preview}"
        return f"turn took {thinking_duration:.1f}s"

    @staticmethod
    def _format_reasoning_preview(
        thinking_content: Optional[List[str] | str],
        preview_limit: int = 120,
    ) -> str:
        if not thinking_content:
            return ""

        if isinstance(thinking_content, str):
            parts = [thinking_content]
        else:
            parts = [part for part in thinking_content if part]

        preview = " ".join(" ".join(parts).split())
        if not preview:
            return ""

        if len(preview) <= preview_limit:
            return preview
        return preview[: max(preview_limit - 3, 0)].rstrip() + "..."

    def get_display_stats(self) -> Dict[str, int]:
        """Get display operation statistics.

        Returns:
            Dictionary with display operation counts
        """
        # This could be enhanced with actual counters if needed
        return {
            "messages_displayed": 0,  # Placeholder - could track actual counts
            "tool_results_displayed": 0,
            "thinking_displays": 0,
            "streaming_sessions": 1 if self._streaming_active else 0,
        }
