"""
Anthropic Claude API Adapter.

Handles the Anthropic API format:
- Endpoint: /v1/messages
- Tool definitions use "input_schema" key
- Responses have "tool_use" content blocks
- Tool results use role="user" with tool_result content block
- System message is separate from messages array
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .base import (
    AdapterResponse,
    BaseAPIAdapter,
    ToolCallingFormat,
    ToolCallResult,
)

logger = logging.getLogger(__name__)


class AnthropicAdapter(BaseAPIAdapter):
    """
    Adapter for Anthropic Claude API.

    Key differences from OpenAI:
    - System prompt is a separate field, not in messages
    - Tool definitions use "input_schema" instead of "parameters"
    - Tool calls are "tool_use" content blocks
    - Tool results are "tool_result" content blocks in user messages
    """

    # Anthropic API version header
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, base_url: str = "https://api.anthropic.com"):
        """
        Initialize Anthropic adapter.

        Args:
            base_url: Base URL for the API (default: api.anthropic.com)
        """
        super().__init__(base_url)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def api_endpoint(self) -> str:
        return f"{self._base_url}/v1/messages"

    @property
    def tool_format(self) -> ToolCallingFormat:
        return ToolCallingFormat.ANTHROPIC

    def format_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Format request for Anthropic API.

        Key differences:
        - System message is hoisted to separate "system" field
        - max_tokens is required (not optional)

        Args:
            messages: Conversation messages
            tools: Tool definitions (optional)
            **kwargs: model, temperature, max_tokens, stream

        Returns:
            Anthropic-formatted request payload
        """
        self.validate_messages(messages)

        # Separate system message from conversation
        system_content, conversation_messages = self._separate_system_message(messages)

        payload: Dict[str, Any] = {
            "model": kwargs.get("model", "claude-sonnet-4-6"),
            "max_tokens": kwargs.get("max_tokens", 4096),  # Required for Anthropic
            "messages": conversation_messages,
        }

        # Add system prompt if present
        if system_content:
            payload["system"] = system_content

        # Optional parameters
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]

        if kwargs.get("stream", False):
            payload["stream"] = True

        # Tool configuration
        if tools:
            payload["tools"] = self.format_tool_definitions(tools)
            # Anthropic tool_choice format is different
            tool_choice = kwargs.get("tool_choice", "auto")
            if tool_choice == "auto":
                payload["tool_choice"] = {"type": "auto"}
            elif tool_choice == "any":
                payload["tool_choice"] = {"type": "any"}
            elif tool_choice == "none":
                # Don't include tool_choice for "none"
                pass
            elif isinstance(tool_choice, dict):
                payload["tool_choice"] = tool_choice
            else:
                # Specific tool name
                payload["tool_choice"] = {"type": "tool", "name": tool_choice}

        return payload

    def _separate_system_message(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[str, List[Dict[str, Any]]]:
        """
        Separate system messages from conversation.

        Anthropic requires system message as separate field.
        Multiple system messages are concatenated.

        Args:
            messages: All messages including system

        Returns:
            Tuple of (system_content, conversation_messages)
        """
        system_parts: List[str] = []
        conversation: List[Dict[str, Any]] = []

        for msg in messages:
            # Validate message is a dict
            if not isinstance(msg, dict):
                logger.warning(f"Skipping non-dict message: {type(msg)}")
                continue
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append(content)
            elif role == "user":
                conversation.append(self._format_user_message(msg))
            elif role == "assistant":
                conversation.append(self._format_assistant_message(msg))

        system_content = "\n".join(system_parts) if system_parts else ""
        return system_content, conversation

    def _format_user_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format user message for Anthropic API.

        Handles both simple text and tool_result content.

        Args:
            msg: User message

        Returns:
            Anthropic-formatted user message
        """
        content = msg.get("content", "")

        # Check if this is a tool result message
        if "tool_result" in msg:
            return {
                "role": "user",
                "content": msg["tool_result"],  # Already formatted content blocks
            }

        # Check if content is already a list of content blocks
        if isinstance(content, list):
            return {"role": "user", "content": content}

        # Simple text content
        return {
            "role": "user",
            "content": [{"type": "text", "text": content}],
        }

    def _format_assistant_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format assistant message for Anthropic API.

        Handles text and tool_use content blocks.

        Args:
            msg: Assistant message

        Returns:
            Anthropic-formatted assistant message
        """
        content = msg.get("content", "")
        tool_uses = msg.get("tool_uses", [])

        content_blocks: List[Dict[str, Any]] = []

        # Add text content if present
        if content:
            content_blocks.append({"type": "text", "text": content})

        # Add tool_use blocks
        for tool_use in tool_uses:
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_use.get("id", ""),
                    "name": tool_use.get("name", ""),
                    "input": tool_use.get("input", {}),
                }
            )

        # If content was already a list of blocks, use that
        if isinstance(content, list):
            content_blocks = content

        return {
            "role": "assistant",
            "content": (
                content_blocks if content_blocks else [{"type": "text", "text": ""}]
            ),
        }

    def parse_response(self, raw_response: Dict[str, Any]) -> AdapterResponse:
        """
        Parse Anthropic API response.

        Args:
            raw_response: Raw JSON from Anthropic API

        Returns:
            Unified AdapterResponse
        """
        # Handle error responses
        if "error" in raw_response:
            error_msg = raw_response["error"].get("message", "Unknown error")
            logger.error(f"Anthropic API error: {error_msg}")
            return AdapterResponse(
                content=f"API Error: {error_msg}",
                stop_reason="error",
                raw_response=raw_response,
            )

        # Check if this looks like an OpenAI response (wrong adapter)
        if "choices" in raw_response:
            logger.error(
                "FORMAT MISMATCH: Got OpenAI response but using Anthropic adapter"
            )
            return AdapterResponse(
                content="CONFIG ERROR: Your profile has tool_format='anthropic' but the server "
                "returned an OpenAI-compatible response.\n\n"
                "FIX: Run /profile, select this profile, press 'e' to edit, "
                "change Tool Format to 'openai', then Ctrl+S to save.",
                stop_reason="format_error",
                raw_response=raw_response,
            )

        # Extract content blocks
        content_blocks = raw_response.get("content", [])

        # Validate content_blocks is a list
        if not isinstance(content_blocks, list):
            logger.warning(f"Expected content to be a list, got {type(content_blocks)}")
            content_blocks = []

        # Process content blocks
        text_parts: List[str] = []
        tool_calls: List[ToolCallResult] = []

        for block in content_blocks:
            # Ensure block is a dict before calling .get()
            if not isinstance(block, dict):
                logger.warning(f"Skipping non-dict content block: {type(block)}")
                continue
            block_type = block.get("type", "")

            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCallResult(
                        tool_id=block.get("id", ""),
                        tool_name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )

        # Combine text content
        content = "\n".join(text_parts)

        # Extract usage
        usage = raw_response.get("usage", {})

        # Map stop_reason
        stop_reason = raw_response.get("stop_reason", "unknown")
        # Anthropic uses "end_turn", "tool_use", "max_tokens" directly
        # which matches our unified format

        return AdapterResponse(
            content=content,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0)
                + usage.get("output_tokens", 0),
            },
            stop_reason=stop_reason,
            raw_response=raw_response,
            model=raw_response.get("model", ""),
        )

    def format_tool_definitions(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert tool definitions to Anthropic format.

        Anthropic format:
        {
            "name": "...",
            "description": "...",
            "input_schema": {...}  # JSON Schema
        }

        Args:
            tools: Generic tool definitions

        Returns:
            Anthropic-formatted tool definitions
        """
        formatted = []

        for tool in tools:
            # Handle both "parameters" and "input_schema" keys
            input_schema = tool.get("input_schema") or tool.get("parameters", {})

            formatted.append(
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": input_schema,
                }
            )

        return formatted

    def format_tool_result(
        self, tool_id: str, result: Any, is_error: bool = False
    ) -> Dict[str, Any]:
        """
        Format tool result for Anthropic API.

        Anthropic uses role="user" with tool_result content blocks.

        Args:
            tool_id: ID of the tool call (tool_use_id)
            result: Tool execution result
            is_error: Whether result is an error

        Returns:
            Anthropic-formatted tool result message
        """
        content = result if isinstance(result, str) else json.dumps(result)

        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        }

    def format_multiple_tool_results(
        self, results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Format multiple tool results in a single user message.

        For parallel tool calls, all results must be in one message.

        Args:
            results: List of {tool_id, result, is_error}

        Returns:
            Single user message with all tool_result blocks
        """
        content_blocks = []

        for r in results:
            tool_id = r.get("tool_id", "")
            result = r.get("result", "")
            is_error = r.get("is_error", False)

            content = result if isinstance(result, str) else json.dumps(result)

            content_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content,
                    "is_error": is_error,
                }
            )

        return {
            "role": "user",
            "content": content_blocks,
        }

    def get_headers(self, api_token: Optional[str] = None) -> Dict[str, str]:
        """
        Get headers for Anthropic API requests.

        Includes required anthropic-version header.

        Args:
            api_token: Anthropic API key

        Returns:
            HTTP headers dictionary
        """
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
        if api_token:
            headers["x-api-key"] = api_token
        return headers
