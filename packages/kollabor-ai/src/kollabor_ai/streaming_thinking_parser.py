# Streaming thinking content parser for LLM responses.
#
# To be added to __init__.py:
#     from .streaming_thinking_parser import (
#         StreamingThinkingParser,
#         StreamingThinkingState,
#         ThinkingParseResult,
#     )
#     __all__.extend([
#         "StreamingThinkingParser",
#         "StreamingThinkingState",
#         "ThinkingParseResult",
#     ])

"""Streaming thinking content parser for LLM responses.

Extracts and parses </think> tags from streaming chunks.
This is stateful parsing for real-time processing, unlike the
complete-response parsing in response_parser.py.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ThinkingParseResult:
    """Result of parsing a thinking chunk.

    Attributes:
        response_content: Non-thinking content to stream as response
        thinking_content: Thinking content extracted (complete when final=True)
        in_thinking: Whether we're currently inside thinking tags
        thinking_complete: Whether a complete thinking block just finished
    """

    response_content: List[str] = field(default_factory=list)
    thinking_content: Optional[str] = None
    in_thinking: bool = False
    thinking_complete: bool = False


@dataclass
class StreamingThinkingState:
    """State for streaming thinking parser.

    Attributes:
        buffer: Current streaming buffer
        in_thinking: Whether we're currently inside thinking tags
        thinking_buffer: Accumulated thinking content
    """

    buffer: str = ""
    in_thinking: bool = False
    thinking_buffer: str = ""

    def reset(self) -> None:
        """Reset state for new request."""
        self.buffer = ""
        self.in_thinking = False
        self.thinking_buffer = ""


class StreamingThinkingParser:
    """Parse thinking content from streaming LLM responses.

    This parser handles the real-time nature of streaming where tags
    may arrive split across multiple chunks.
    """

    THINK_START = "<think>"
    THINK_END = "</think>"

    def __init__(self, state: Optional[StreamingThinkingState] = None):
        """Initialize the parser.

        Args:
            state: Existing state to continue parsing (optional)
        """
        self.state = state or StreamingThinkingState()
        logger.debug("Streaming thinking parser initialized")

    def reset(self) -> None:
        """Reset parser state for new request."""
        self.state.reset()

    def parse_chunk(self, chunk: str) -> ThinkingParseResult:
        """Parse a streaming chunk and extract thinking/response content.

        Args:
            chunk: Content chunk from streaming API response

        Returns:
            ThinkingParseResult with extracted content and state
        """
        # Add chunk to buffer
        self.state.buffer += chunk

        result = ThinkingParseResult(in_thinking=self.state.in_thinking)

        # Process content in a loop until we need more data
        while True:
            if not self.state.in_thinking:
                # Look for start of thinking
                if self.THINK_START in self.state.buffer:
                    parts = self.state.buffer.split(self.THINK_START, 1)
                    if len(parts) == 2:
                        # Stream any content before thinking tag
                        if parts[0].strip():
                            result.response_content.append(parts[0])
                        self.state.buffer = parts[1]
                        self.state.in_thinking = True
                        result.in_thinking = True
                        self.state.thinking_buffer = ""
                    else:
                        break
                else:
                    # No thinking tags found, stream the content as response
                    if self.state.buffer.strip():
                        result.response_content.append(self.state.buffer)
                        self.state.buffer = ""
                    break
            else:
                # We're in thinking mode, look for end or accumulate content
                if self.THINK_END in self.state.buffer:
                    parts = self.state.buffer.split(self.THINK_END, 1)
                    self.state.thinking_buffer += parts[0]
                    self.state.buffer = parts[1]

                    # Complete thinking content ready
                    if self.state.thinking_buffer.strip():
                        result.thinking_content = self.state.thinking_buffer
                        result.thinking_complete = True

                    # Reset thinking state
                    self.state.in_thinking = False
                    result.in_thinking = False
                    self.state.thinking_buffer = ""
                else:
                    # Still in thinking, accumulate content
                    if self.state.buffer:
                        self.state.thinking_buffer += self.state.buffer
                        # Stream thinking content as we get it
                        if self.state.thinking_buffer.strip():
                            result.thinking_content = self.state.thinking_buffer
                        self.state.buffer = ""
                    break

        return result
