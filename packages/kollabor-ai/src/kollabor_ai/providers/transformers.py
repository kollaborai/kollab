"""
Response transformers for LLM provider integration.

Converts provider-specific responses to unified format:
- ToolCallAccumulator: Accumulates incremental JSON from streaming tool calls
- OpenAIResponseTransformer: Converts OpenAI responses to unified format
- AnthropicResponseTransformer: Converts Anthropic responses to unified format
- ToolSchemaTransformer: Bidirectional tool schema conversion
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set

from .models import (
    ContentBlock,
    ProviderType,
    StreamingResponse,
    TextContent,
    TextDelta,
    ThinkingContent,
    ThinkingDelta,
    ToolCallDelta,
    ToolResultContent,
    ToolUseContent,
    UnifiedResponse,
    UsageInfo,
)

logger = logging.getLogger(__name__)


class ToolCallAccumulator:
    """
    Accumulates incremental tool call deltas from streaming responses.

    OpenAI streams tool arguments as JSON fragments that must be accumulated
    and reconstructed into complete JSON objects.

    Example:
        Chunk 1: '{"query": "test'
        Chunk 2: '", "limit":'
        Chunk 3: ' 10}'

    This class handles:
    - Incremental JSON accumulation
    - Multiple simultaneous tool calls
    - Malformed JSON gracefully
    - Empty chunks
    - Unicode characters
    - Split JSON boundaries

    Modes:
    - LEGACY (default): add_delta() buffers, get_completed_tools() returns tools
    - EXPLICIT: add_delta() returns newly completed tools immediately
    """

    def __init__(self, legacy_mode: bool = True):
        """
        Initialize tool accumulator.

        Args:
            legacy_mode: If True, uses LEGACY mode (buffer + get_completed_tools).
                        If False, uses EXPLICIT mode (add_delta returns tools).
        """
        self.legacy_mode = legacy_mode
        # Map of tool_call_id -> {"name": str, "arguments_buffer": str}
        self._tool_calls: Dict[str, Dict[str, str]] = {}
        # Track which tools have been returned (EXPLICIT mode only)
        self._returned_tools: Set[str] = set()
        # Track the most recently opened tool id (for Anthropic index-based deltas)
        self._current_tool_id: Optional[str] = None

    def add_delta(
        self,
        tool_call_id: Optional[str],
        name: Optional[str],
        arguments_delta: Optional[str],
    ) -> Optional[List[ToolUseContent]]:
        """
        Add incremental tool call delta.

        In LEGACY mode: Accumulates JSON fragments. Returns None.
        Use get_completed_tools() to retrieve completed tools.

        In EXPLICIT mode: Returns list of newly completed tools immediately.

        Args:
            tool_call_id: Unique identifier for this tool call
            name: Tool function name (optional, may come in separate chunk)
            arguments_delta: JSON fragment (partial string)

        Returns:
            LEGACY mode: None
            EXPLICIT mode: List of newly completed ToolUseContent objects

        Raises:
            ValueError: If tool_call_id is None
        """
        if tool_call_id is None:
            # Anthropic index-based streaming: input_json_delta has no id,
            # route to the most recently opened tool
            if self._current_tool_id is None:
                logger.warning(
                    "Received tool delta with no id and no current tool open, dropping"
                )
                return None
            tool_call_id = self._current_tool_id

        # Initialize entry for this tool call
        if tool_call_id not in self._tool_calls:
            self._tool_calls[tool_call_id] = {
                "name": "",
                "arguments_buffer": "",
            }
            self._current_tool_id = tool_call_id

        # Accumulate name if provided
        if name is not None:
            if self._tool_calls[tool_call_id]["name"]:
                # Name already set, verify consistency
                if self._tool_calls[tool_call_id]["name"] != name:
                    logger.warning(
                        f"Tool name changed for {tool_call_id}: "
                        f"'{self._tool_calls[tool_call_id]['name']}' -> '{name}'"
                    )
            else:
                self._tool_calls[tool_call_id]["name"] = name

        # Accumulate arguments if provided
        if arguments_delta is not None:
            self._tool_calls[tool_call_id]["arguments_buffer"] += arguments_delta

        # LEGACY mode: return None (caller uses get_completed_tools)
        if self.legacy_mode:
            return None

        # EXPLICIT mode: return newly completed tools
        newly_completed = []
        for tc_id, data in list(self._tool_calls.items()):
            # Skip if already returned
            if tc_id in self._returned_tools:
                continue

            name = data["name"]
            args_buffer = data["arguments_buffer"]

            # Skip if no name yet
            if not name:
                logger.debug(f"Tool {tc_id} missing name, not complete")
                continue

            # Skip if empty buffer
            if not args_buffer or not args_buffer.strip():
                logger.debug(f"Tool {tc_id} has empty arguments, not complete")
                continue

            # Try to parse JSON
            try:
                arguments = json.loads(args_buffer)

                # Success - create tool use content
                tool_use = ToolUseContent(
                    id=tc_id,
                    name=name,
                    input=arguments,
                )
                newly_completed.append(tool_use)
                self._returned_tools.add(tc_id)

                logger.debug(
                    f"Completed tool {tc_id}: {name} with {len(arguments)} args"
                )

            except json.JSONDecodeError as e:
                # Incomplete JSON - keep buffering
                logger.debug(
                    f"Tool {tc_id} has incomplete JSON: {e}. "
                    f"Buffer so far: {args_buffer[:100]}..."
                )
                continue

        return newly_completed if newly_completed else None

    def get_completed_tools(self) -> List[ToolUseContent]:
        """
        Get all completed tool calls.

        LEGACY mode: Returns all tools that have both a name and valid JSON.
        EXPLICIT mode: Returns tools not yet returned via add_delta().

        Returns:
            List of completed ToolUseContent objects

        Note:
            Tools with incomplete or malformed JSON are not returned but
            remain buffered for future deltas.
        """
        completed_tools = []

        for tool_call_id, data in list(self._tool_calls.items()):
            # EXPLICIT mode: skip already returned tools
            if not self.legacy_mode and tool_call_id in self._returned_tools:
                continue

            name = data["name"]
            args_buffer = data["arguments_buffer"]

            # Skip if no name yet
            if not name:
                logger.debug(f"Tool {tool_call_id} missing name, not complete")
                continue

            # Skip if empty buffer
            if not args_buffer or not args_buffer.strip():
                logger.debug(f"Tool {tool_call_id} has empty arguments, not complete")
                continue

            # Try to parse JSON
            try:
                arguments = json.loads(args_buffer)

                # Success - create tool use content
                tool_use = ToolUseContent(
                    id=tool_call_id,
                    name=name,
                    input=arguments,
                )
                completed_tools.append(tool_use)

                # EXPLICIT mode: mark as returned
                if not self.legacy_mode:
                    self._returned_tools.add(tool_call_id)

                logger.debug(
                    f"Completed tool {tool_call_id}: {name} with {len(arguments)} args"
                )

            except json.JSONDecodeError as e:
                # Incomplete JSON - keep buffering
                logger.debug(
                    f"Tool {tool_call_id} has incomplete JSON: {e}. "
                    f"Buffer so far: {args_buffer[:100]}..."
                )
                continue

        return completed_tools

    def reset(self) -> None:
        """Clear all accumulated tool call state."""
        self._tool_calls.clear()
        self._returned_tools.clear()

    def get_buffer_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all buffered tool calls.

        Useful for debugging and testing.

        Returns:
            Dict mapping tool_call_id to status info:
            {
                "name": str or None,
                "buffer_length": int,
                "buffer_preview": str (first 100 chars),
                "parseable": bool,
                "returned": bool (EXPLICIT mode only)
            }
        """
        status = {}

        for tool_call_id, data in self._tool_calls.items():
            buffer = data["arguments_buffer"]

            # Check if buffer is parseable JSON
            try:
                json.loads(buffer)
                parseable = True
            except (json.JSONDecodeError, ValueError):
                parseable = False

            status[tool_call_id] = {
                "name": data["name"] or None,
                "buffer_length": len(buffer),
                "buffer_preview": buffer[:100],
                "parseable": parseable,
                "returned": tool_call_id in self._returned_tools,
            }

        return status


class OpenAIResponseTransformer:
    """
    Transforms OpenAI API responses to unified format.

    Handles both streaming chunks and complete responses. Extracts
    content, usage, tool calls, and finish reason.

    OpenAI response format:
    - Streaming: chunks with delta.content or delta.tool_calls
    - Complete: choices[].message.content or tool_calls
    """

    @staticmethod
    def transform_openai_chunk(
        chunk: Dict[str, Any], model: str
    ) -> Optional[StreamingResponse]:
        """
        Transform OpenAI streaming chunk to unified format.

        Args:
            chunk: Raw OpenAI chunk dict
            model: Model name

        Returns:
            StreamingResponse or None if chunk has no content

        Example OpenAI chunk:
        {
            "id": "chatcmpl-123",
            "choices": [{
                "delta": {"content": "Hello"},
                "finish_reason": null
            }],
            "usage": null
        }
        """
        if not chunk or "choices" not in chunk or not chunk["choices"]:
            return None

        choice = chunk["choices"][0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # Extract content delta
        content = delta.get("content")

        # Extract tool call delta
        tool_calls = delta.get("tool_calls")

        # Check if final chunk
        is_final = finish_reason is not None

        # Text content
        if content is not None:
            return StreamingResponse(
                delta=TextDelta(content=content),
                is_final=is_final,
                finish_reason=finish_reason,
                raw_chunk=chunk,
            )

        # Tool call delta
        if tool_calls and len(tool_calls) > 0:
            # OpenAI can have multiple tool calls in one chunk
            # For simplicity, we return the first one
            # In production, you'd want to handle all of them
            tool_call = tool_calls[0]
            tool_call_id = tool_call.get("index")  # OpenAI uses index, not ID
            function = tool_call.get("function", {})

            return StreamingResponse(
                delta=ToolCallDelta(
                    tool_call_id=str(tool_call_id),
                    tool_name=function.get("name"),
                    tool_arguments_delta=function.get("arguments", ""),
                ),
                is_final=is_final,
                finish_reason=finish_reason,
                raw_chunk=chunk,
            )

        # Final chunk with usage
        if is_final:
            usage = chunk.get("usage")
            if usage:
                # OpenAI reports cached tokens under prompt_tokens_details
                # cached_tokens is a subset of prompt_tokens (already included)
                details = usage.get("prompt_tokens_details", {}) or {}
                cached = details.get("cached_tokens", 0)
                return StreamingResponse(
                    delta=TextDelta(content=""),
                    usage=UsageInfo(
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                        cache_read_tokens=cached,
                    ),
                    is_final=True,
                    finish_reason=finish_reason,
                    raw_chunk=chunk,
                )
            return StreamingResponse(
                delta=TextDelta(content=""),
                is_final=True,
                finish_reason=finish_reason,
                raw_chunk=chunk,
            )

        # Empty chunk (keepalive)
        return None

    @staticmethod
    def transform_openai_response(
        response: Dict[str, Any], model: str
    ) -> UnifiedResponse:
        """
        Transform complete OpenAI response to unified format.

        Args:
            response: Raw OpenAI response dict
            model: Model name

        Returns:
            UnifiedResponse with all content blocks

        Example OpenAI response:
        {
            "id": "chatcmpl-123",
            "choices": [{
                "message": {
                    "content": "Hello",
                    "tool_calls": [...]
                },
                "finish_reason": "stop"
            }],
            "usage": {...}
        }
        """
        if not response or "choices" not in response or not response["choices"]:
            raise ValueError("Invalid OpenAI response: missing choices")

        choice = response["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason")

        # Extract content blocks
        content_blocks: List[ContentBlock] = []

        # Text content
        text = message.get("content")
        if text:
            content_blocks.append(TextContent(text=text))

        # Tool calls
        tool_calls = message.get("tool_calls") or []
        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            args_str = function.get("arguments", "{}")

            try:
                arguments = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse tool arguments: {args_str}")
                arguments = {}

            content_blocks.append(
                ToolUseContent(
                    id=tool_call.get("id", ""),
                    name=function.get("name", ""),
                    input=arguments,
                )
            )

        # Extract usage (including OpenAI prompt caching metrics)
        usage_dict = response.get("usage", {})
        details = usage_dict.get("prompt_tokens_details", {}) or {}
        cached = details.get("cached_tokens", 0)
        usage = UsageInfo(
            prompt_tokens=usage_dict.get("prompt_tokens", 0),
            completion_tokens=usage_dict.get("completion_tokens", 0),
            total_tokens=usage_dict.get("total_tokens", 0),
            cache_read_tokens=cached,
        )

        return UnifiedResponse(
            content=content_blocks,
            usage=usage,
            model=model,
            provider=ProviderType.OPENAI,
            finish_reason=finish_reason,
            raw_response=response,
        )


class AnthropicResponseTransformer:
    """
    Transforms Anthropic API responses to unified format.

    Handles both streaming chunks and complete responses. Preserves
    thinking blocks which are specific to Anthropic.

    Anthropic response format:
    - Streaming: content_block_delta with text or tool_use
    - Complete: content blocks with text, tool_use, thinking
    """

    @staticmethod
    def transform_anthropic_chunk(
        chunk: Dict[str, Any], model: str
    ) -> Optional[StreamingResponse]:
        """
        Transform Anthropic streaming chunk to unified format.

        Args:
            chunk: Raw Anthropic chunk dict
            model: Model name

        Returns:
            StreamingResponse or None if chunk has no content

        Example Anthropic chunk:
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"},
            "index": 0
        }
        """
        if not chunk:
            return None

        chunk_type = chunk.get("type")

        # content_block_start: announces a new content block (text or tool_use)
        # For tool_use blocks this carries the tool id and name — must be captured
        # before input_json_delta chunks arrive so the accumulator has an id to key on
        if chunk_type == "content_block_start":
            block = chunk.get("content_block", {})
            if block.get("type") == "tool_use":
                return StreamingResponse(
                    delta=ToolCallDelta(
                        tool_call_id=block.get("id", ""),
                        tool_name=block.get("name", ""),
                        tool_arguments_delta="",
                    ),
                    is_final=False,
                    raw_chunk=chunk,
                )

        # Content block delta
        if chunk_type == "content_block_delta":
            delta = chunk.get("delta", {})
            delta_type = delta.get("type")

            # Text delta
            if delta_type == "text_delta":
                text = delta.get("text", "")
                return StreamingResponse(
                    delta=TextDelta(content=text),
                    is_final=False,
                    raw_chunk=chunk,
                )

            # Tool use delta — Anthropic sends incremental JSON for tool input.
            # The tool_call_id is None here because it was already registered via
            # content_block_start. The accumulator uses index-based lookup for these.
            if delta_type == "input_json_delta":
                return StreamingResponse(
                    delta=ToolCallDelta(
                        tool_call_id=None,
                        tool_name=None,
                        tool_arguments_delta=delta.get("partial_json", ""),
                    ),
                    is_final=False,
                    raw_chunk=chunk,
                )

        # Thinking delta (extended thinking)
        if chunk_type == "content_block_delta":
            delta = chunk.get("delta", {})
            if delta.get("type") == "thinking_delta":
                return StreamingResponse(
                    delta=ThinkingDelta(content=delta.get("thinking", "")),
                    is_final=False,
                    raw_chunk=chunk,
                )

        # message_delta: carries output_tokens usage + stop_reason
        # Always emit so stop_reason="tool_use" is visible to the caller
        if chunk_type == "message_delta":
            usage = chunk.get("usage", {})
            output_tokens = usage.get("output_tokens", 0)
            stop_reason = chunk.get("delta", {}).get("stop_reason")
            usage_info = (
                UsageInfo(
                    prompt_tokens=0,
                    completion_tokens=output_tokens,
                    total_tokens=output_tokens,
                )
                if output_tokens
                else None
            )
            return StreamingResponse(
                delta=TextDelta(content=""),
                usage=usage_info,
                finish_reason=stop_reason,
                is_final=False,
                raw_chunk=chunk,
            )

        # message_start: carries input_tokens usage + cache metrics
        if chunk_type == "message_start":
            usage = chunk.get("message", {}).get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            # Emit usage if ANY of these counters have data
            if input_tokens or cache_creation or cache_read:
                # cache_read_tokens are NOT included in input_tokens by
                # anthropic - they're reported separately. Sum for total.
                total_input = input_tokens + cache_creation + cache_read
                return StreamingResponse(
                    delta=TextDelta(content=""),
                    usage=UsageInfo(
                        prompt_tokens=total_input,
                        completion_tokens=0,
                        total_tokens=total_input,
                        cache_creation_tokens=cache_creation,
                        cache_read_tokens=cache_read,
                    ),
                    is_final=False,
                    raw_chunk=chunk,
                )

        # message_stop: final signal, no usage data
        if chunk_type == "message_stop":
            return StreamingResponse(
                delta=TextDelta(content=""),
                is_final=True,
                raw_chunk=chunk,
            )

        return None

    @staticmethod
    def transform_anthropic_response(
        response: Dict[str, Any], model: str
    ) -> UnifiedResponse:
        """
        Transform complete Anthropic response to unified format.

        Args:
            response: Raw Anthropic response dict
            model: Model name

        Returns:
            UnifiedResponse with all content blocks including thinking

        Example Anthropic response:
        {
            "id": "msg_123",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "name": "search", "id": "...", "input": {...}}
            ],
            "stop_reason": "end_turn",
            "usage": {...}
        }
        """
        if not response or "content" not in response:
            raise ValueError("Invalid Anthropic response: missing content")

        content_blocks: List[ContentBlock] = []

        # Process content blocks
        for block in response.get("content", []):
            block_type = block.get("type")

            # Text content
            if block_type == "text":
                content_blocks.append(TextContent(text=block.get("text", "")))

            # Tool use content
            elif block_type == "tool_use":
                content_blocks.append(
                    ToolUseContent(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=block.get("input", {}),
                    )
                )

            # Thinking content (extended thinking)
            elif block_type == "thinking":
                content_blocks.append(
                    ThinkingContent(thinking=block.get("thinking", ""))
                )

            # Tool result content (from multi-turn conversations)
            elif block_type == "tool_result":
                content_blocks.append(
                    ToolResultContent(
                        tool_use_id=block.get("tool_use_id", ""),
                        content=block.get("content", ""),
                        is_error=block.get("is_error", False),
                    )
                )

        # Extract usage (including prompt caching metrics)
        usage_dict = response.get("usage", {})
        input_tokens = usage_dict.get("input_tokens", 0)
        output_tokens = usage_dict.get("output_tokens", 0)
        cache_creation = usage_dict.get("cache_creation_input_tokens", 0)
        cache_read = usage_dict.get("cache_read_input_tokens", 0)
        # anthropic reports cache_read separately from input_tokens
        total_input = input_tokens + cache_creation + cache_read
        usage = UsageInfo(
            prompt_tokens=total_input,
            completion_tokens=output_tokens,
            total_tokens=total_input + output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
        )

        # Map stop_reason to finish_reason
        stop_reason = response.get("stop_reason", "end_turn")
        finish_reason = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }.get(stop_reason, stop_reason)

        return UnifiedResponse(
            content=content_blocks,
            usage=usage,
            model=model,
            provider=ProviderType.ANTHROPIC,
            finish_reason=finish_reason,
            raw_response=response,
        )


class ToolSchemaTransformer:
    """
    Transforms tool schemas between OpenAI, Anthropic, and Gemini formats.

    Provides bidirectional conversion:
    - to_openai_format: Anthropic -> OpenAI (adds "type": "function" wrapper)
    - to_anthropic_format: OpenAI -> Anthropic (removes wrapper, renames fields)
    - to_gemini_format: OpenAI -> Gemini (wraps in functionDeclarations)

    Conversion must be reversible (round-trip preserves schema).
    """

    @staticmethod
    def to_openai_format(anthropic_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert Anthropic tool schema to OpenAI format.

        Anthropic format:
        {
            "name": "get_weather",
            "description": "Get weather",
            "input_schema": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }

        OpenAI format:
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        }

        Args:
            anthropic_tools: List of Anthropic tool schemas

        Returns:
            List of OpenAI tool schemas

        Raises:
            ValueError: If tool schema is invalid
        """
        if not anthropic_tools:
            return []

        openai_tools = []

        for tool in anthropic_tools:
            # Validate required fields
            if "name" not in tool:
                raise ValueError(f"Anthropic tool missing 'name': {tool}")

            # Support both Anthropic format ("input_schema") and generic format ("parameters")
            parameters = tool.get("input_schema") or tool.get("parameters", {})
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": parameters,
                    },
                }
            )

        return openai_tools

    @staticmethod
    def to_anthropic_format(openai_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert OpenAI tool schema to Anthropic format.

        Handles both OpenAI function format and direct parameter format.

        OpenAI function format:
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {...}
            }
        }

        Direct format (for compatibility):
        {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {...}
        }

        Anthropic format:
        {
            "name": "get_weather",
            "description": "Get weather",
            "input_schema": {...}
        }

        Args:
            openai_tools: List of OpenAI tool schemas

        Returns:
            List of Anthropic tool schemas

        Raises:
            ValueError: If tool schema is invalid
        """
        if not openai_tools:
            return []

        anthropic_tools = []

        for tool in openai_tools:
            # OpenAI format with "function" wrapper
            if "function" in tool:
                func = tool["function"]

                if "name" not in func:
                    raise ValueError(f"OpenAI tool function missing 'name': {func}")

                anthropic_tools.append(
                    {
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {}),
                    }
                )

            # Direct format (parameters at top level)
            else:
                if "name" not in tool:
                    raise ValueError(f"OpenAI tool missing 'name': {tool}")

                anthropic_tools.append(
                    {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("parameters", {}),
                    }
                )

        return anthropic_tools

    @staticmethod
    def to_gemini_format(openai_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert OpenAI tool schema to Gemini format.

        Takes OpenAI function format and wraps in Gemini's functionDeclarations.

        OpenAI function format:
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        }

        Gemini format:
        [{
            "functionDeclarations": [{
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }]
        }]

        Note: Gemini's parameters format is identical to OpenAI's (JSON Schema).

        Args:
            openai_tools: List of OpenAI tool schemas

        Returns:
            List of Gemini tool schemas (wrapped in functionDeclarations)

        Raises:
            ValueError: If tool schema is invalid
        """
        if not openai_tools:
            return []

        declarations = []

        for tool in openai_tools:
            # Extract function object from OpenAI format
            if "function" in tool:
                func = tool["function"]

                if "name" not in func:
                    raise ValueError(f"OpenAI tool function missing 'name': {func}")

                declaration = {
                    "name": func["name"],
                    "description": func.get("description", ""),
                }

                # Add parameters if present
                if "parameters" in func:
                    declaration["parameters"] = func["parameters"]

                declarations.append(declaration)

            # Direct format (shouldn't happen with OpenAI tools, but handle gracefully)
            else:
                if "name" not in tool:
                    raise ValueError(f"Tool missing 'name': {tool}")

                declaration = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                }

                if "parameters" in tool:
                    declaration["parameters"] = tool["parameters"]

                declarations.append(declaration)

        # Wrap in Gemini's functionDeclarations format
        return [{"functionDeclarations": declarations}]
