"""
API Adapters for different LLM providers.

This module provides adapters that handle the differences between
OpenAI and Anthropic API formats for:
- Request formatting (tool definitions, messages)
- Response parsing (tool calls, content)
- Tool result formatting
"""

from .anthropic_adapter import AnthropicAdapter
from .base import (
    AdapterResponse,
    BaseAPIAdapter,
    ToolCallingFormat,
    ToolCallResult,
)
from .openai_adapter import OpenAIAdapter

__all__ = [
    "BaseAPIAdapter",
    "AdapterResponse",
    "ToolCallResult",
    "ToolCallingFormat",
    "OpenAIAdapter",
    "AnthropicAdapter",
]


def get_adapter(tool_format: str, base_url: str = "") -> BaseAPIAdapter:
    """
    Factory function to get the appropriate adapter.

    Args:
        tool_format: "openai" or "anthropic"
        base_url: Base URL for the API endpoint

    Returns:
        Configured API adapter instance
    """
    if tool_format == "anthropic":
        return AnthropicAdapter(base_url=base_url or "https://api.anthropic.com")
    else:
        return OpenAIAdapter(base_url=base_url or "http://localhost:1234")
