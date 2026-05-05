"""
Base API Adapter interface for LLM providers.

Provides abstract base class and data structures for adapting
between different LLM API formats (OpenAI, Anthropic, etc.).
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolCallingFormat(Enum):
    """Supported tool calling API formats."""

    OPENAI = "openai"  # /v1/chat/completions, parameters, tool_calls
    ANTHROPIC = "anthropic"  # /v1/messages, input_schema, tool_use


@dataclass
class ToolCallResult:
    """
    Unified representation of a tool call from the LLM.

    Attributes:
        tool_id: Unique identifier for this tool call
        tool_name: Name of the tool being called
        arguments: Dictionary of arguments passed to the tool
    """

    tool_id: str
    tool_name: str
    arguments: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
        }


@dataclass
class AdapterResponse:
    """
    Unified response format from any LLM API adapter.

    Attributes:
        content: Text content from the response
        tool_calls: List of tool calls requested by the LLM
        usage: Token usage statistics (prompt_tokens, completion_tokens, total_tokens)
        stop_reason: Why the response ended (end_turn, tool_use, max_tokens)
        raw_response: Original unmodified response from the API
        model: Model that generated the response
    """

    content: str
    tool_calls: List[ToolCallResult] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    stop_reason: str = "unknown"
    raw_response: Dict[str, Any] = field(default_factory=dict)
    model: str = ""

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "content": self.content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "usage": self.usage,
            "stop_reason": self.stop_reason,
            "model": self.model,
        }


class BaseAPIAdapter(ABC):
    """
    Abstract base class for LLM API adapters.

    Adapters handle the differences between API formats:
    - OpenAI: /v1/chat/completions with "parameters" and "tool_calls"
    - Anthropic: /v1/messages with "input_schema" and "tool_use"

    Each adapter must implement methods for:
    - Formatting requests (messages, tools)
    - Parsing responses
    - Formatting tool results
    """

    def __init__(self, base_url: str = ""):
        """
        Initialize adapter with base URL.

        Args:
            base_url: Base URL for the API endpoint
        """
        self._base_url = base_url.rstrip("/") if base_url else ""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Get the provider name (e.g., 'openai', 'anthropic').

        Returns:
            Provider identifier string
        """
        pass

    @property
    @abstractmethod
    def api_endpoint(self) -> str:
        """
        Get the full API endpoint URL.

        Returns:
            Complete URL for API requests
        """
        pass

    @property
    @abstractmethod
    def tool_format(self) -> ToolCallingFormat:
        """
        Get the tool calling format used by this adapter.

        Returns:
            ToolCallingFormat enum value
        """
        pass

    @abstractmethod
    def format_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Format a request payload for the API.

        Args:
            messages: Conversation messages (role, content)
            tools: Tool definitions (optional)
            **kwargs: Additional parameters (model, temperature, max_tokens, etc.)

        Returns:
            Formatted request payload dictionary
        """
        pass

    @abstractmethod
    def parse_response(self, raw_response: Dict[str, Any]) -> AdapterResponse:
        """
        Parse API response into unified format.

        Args:
            raw_response: Raw JSON response from the API

        Returns:
            AdapterResponse with normalized fields
        """
        pass

    @abstractmethod
    def format_tool_definitions(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert tool definitions to provider-specific format.

        OpenAI uses 'parameters' key in function schema.
        Anthropic uses 'input_schema' key.

        Args:
            tools: Tool definitions in generic format

        Returns:
            Tool definitions in provider-specific format
        """
        pass

    @abstractmethod
    def format_tool_result(
        self, tool_id: str, result: Any, is_error: bool = False
    ) -> Dict[str, Any]:
        """
        Format a tool result for conversation continuation.

        Args:
            tool_id: ID of the tool call this is responding to
            result: Result from tool execution
            is_error: Whether the result is an error

        Returns:
            Formatted message dictionary for the conversation
        """
        pass

    def get_headers(self, api_token: Optional[str] = None) -> Dict[str, str]:
        """
        Get headers for API requests.

        Args:
            api_token: API authentication token

        Returns:
            Dictionary of HTTP headers
        """
        headers = {
            "Content-Type": "application/json",
        }
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        return headers

    def validate_messages(self, messages: List[Dict[str, Any]]) -> bool:
        """
        Validate message format.

        Args:
            messages: List of message dictionaries

        Returns:
            True if valid, raises ValueError if not
        """
        for i, msg in enumerate(messages):
            if "role" not in msg:
                raise ValueError(f"Message {i} missing 'role' field")
            if "content" not in msg and msg["role"] != "assistant":
                raise ValueError(f"Message {i} missing 'content' field")
        return True
