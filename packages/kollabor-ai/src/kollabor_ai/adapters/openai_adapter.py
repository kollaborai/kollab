"""
OpenAI API Adapter.

Handles the OpenAI-compatible API format:
- Endpoint: /v1/chat/completions
- Tool definitions use "parameters" key
- Responses have "tool_calls" array
- Tool results use role="tool" with tool_call_id
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


class OpenAIAdapter(BaseAPIAdapter):
    """
    Adapter for OpenAI-compatible APIs.

    Compatible with:
    - OpenAI API (api.openai.com)
    - Local LLM servers (LM Studio, Ollama, vLLM, etc.)
    - Any OpenAI-compatible endpoint
    """

    def __init__(self, base_url: str = "http://localhost:1234"):
        """
        Initialize OpenAI adapter.

        Args:
            base_url: Base URL for the API (default: localhost:1234 for local LLMs)
        """
        super().__init__(base_url)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def api_endpoint(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    @property
    def tool_format(self) -> ToolCallingFormat:
        return ToolCallingFormat.OPENAI

    def format_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Format request for OpenAI API.

        Args:
            messages: Conversation messages
            tools: Tool definitions (optional)
            **kwargs: model, temperature, max_tokens, stream, tool_choice

        Returns:
            OpenAI-formatted request payload
        """
        self.validate_messages(messages)

        payload: Dict[str, Any] = {
            "model": kwargs.get("model", "gpt-4"),
            "messages": self._format_messages(messages),
        }

        # Optional parameters
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]

        if "max_tokens" in kwargs and kwargs["max_tokens"]:
            payload["max_tokens"] = kwargs["max_tokens"]

        if kwargs.get("stream", False):
            payload["stream"] = True

        # Tool configuration
        if tools:
            payload["tools"] = self.format_tool_definitions(tools)
            tool_choice = kwargs.get("tool_choice", "auto")
            payload["tool_choice"] = tool_choice

        return payload

    def _format_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format messages for OpenAI API.

        Handles:
        - System, user, assistant messages
        - Tool call messages
        - Tool result messages

        Args:
            messages: Raw conversation messages

        Returns:
            OpenAI-formatted messages
        """
        formatted = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                formatted.append({"role": "system", "content": content})
            elif role == "user":
                formatted.append({"role": "user", "content": content})
            elif role == "assistant":
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                }
                # Include tool_calls if present
                if "tool_calls" in msg:
                    assistant_msg["tool_calls"] = msg["tool_calls"]
                formatted.append(assistant_msg)
            elif role == "tool":
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id", ""),
                        "content": (
                            content if isinstance(content, str) else json.dumps(content)
                        ),
                    }
                )

        return formatted

    def parse_response(self, raw_response: Dict[str, Any]) -> AdapterResponse:
        """
        Parse OpenAI API response.

        Args:
            raw_response: Raw JSON from OpenAI API

        Returns:
            Unified AdapterResponse
        """
        # Handle error responses
        if "error" in raw_response:
            error_msg = raw_response["error"].get("message", "Unknown error")
            logger.error(f"OpenAI API error: {error_msg}")
            return AdapterResponse(
                content=f"API Error: {error_msg}",
                stop_reason="error",
                raw_response=raw_response,
            )

        # Check if this looks like an Anthropic response (wrong adapter)
        if "content" in raw_response and isinstance(raw_response.get("content"), list):
            # Anthropic returns content as array of blocks, OpenAI returns choices
            if "choices" not in raw_response:
                logger.error(
                    "FORMAT MISMATCH: Got Anthropic response but using OpenAI adapter"
                )
                return AdapterResponse(
                    content="CONFIG ERROR: Your profile has tool_format='openai' but the server "
                    "returned an Anthropic-style response.\n\n"
                    "FIX: Run /profile, select this profile, press 'e' to edit, "
                    "change Tool Format to 'anthropic', then Ctrl+S to save.",
                    stop_reason="format_error",
                    raw_response=raw_response,
                )

        # Extract choice
        choices = raw_response.get("choices", [])
        if not choices:
            logger.warning("Empty choices in response")
            return AdapterResponse(
                content="",
                stop_reason="unknown",
                raw_response=raw_response,
            )

        choice = choices[0]
        message = choice.get("message", {})

        # Extract content
        content = message.get("content", "") or ""

        # Extract tool calls
        tool_calls: List[ToolCallResult] = []
        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                try:
                    arguments = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)

                    tool_calls.append(
                        ToolCallResult(
                            tool_id=tc.get("id", ""),
                            tool_name=tc.get("function", {}).get("name", ""),
                            arguments=arguments,
                        )
                    )
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse tool arguments: {e}")
                    continue

        # Extract usage
        usage = raw_response.get("usage", {})

        # Debug: Log token usage to verify API format
        if usage:
            logger.info(f"Token usage from API: {usage}")
        else:
            logger.warning("No usage data in API response")

        # Map finish_reason to stop_reason
        finish_reason = choice.get("finish_reason", "unknown")
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
            "content_filter": "content_filter",
        }
        stop_reason = stop_reason_map.get(finish_reason, finish_reason)

        return AdapterResponse(
            content=content,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            stop_reason=stop_reason,
            raw_response=raw_response,
            model=raw_response.get("model", ""),
        )

    def format_tool_definitions(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert tool definitions to OpenAI format.

        OpenAI format:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {...}  # JSON Schema
            }
        }

        Args:
            tools: Generic tool definitions

        Returns:
            OpenAI-formatted tool definitions
        """
        formatted = []

        for tool in tools:
            # Handle both "input_schema" and "parameters" keys
            parameters = tool.get("parameters") or tool.get("input_schema", {})

            formatted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": parameters,
                    },
                }
            )

        return formatted

    def format_tool_result(
        self, tool_id: str, result: Any, is_error: bool = False
    ) -> Dict[str, Any]:
        """
        Format tool result for OpenAI API.

        OpenAI uses role="tool" with tool_call_id.

        Args:
            tool_id: ID of the tool call
            result: Tool execution result
            is_error: Whether result is an error

        Returns:
            OpenAI-formatted tool result message
        """
        content = result if isinstance(result, str) else json.dumps(result)

        if is_error:
            content = f"Error: {content}"

        return {
            "role": "tool",
            "tool_call_id": tool_id,
            "content": content,
        }

    def get_headers(self, api_token: Optional[str] = None) -> Dict[str, str]:
        """
        Get headers for OpenAI API requests.

        Args:
            api_token: OpenAI API key

        Returns:
            HTTP headers dictionary
        """
        headers = {
            "Content-Type": "application/json",
        }
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        return headers
