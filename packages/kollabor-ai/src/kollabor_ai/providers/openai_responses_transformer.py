"""
Response transformer for OpenAI Responses API.

Converts OpenAI Responses API format to unified response format.
Handles both complete responses and streaming SSE events.

Responses API format:
- Input: 'input' field, 'instructions' parameter
- Output: 'output' array with items (message, function_call, reasoning)
- Streaming: SSE events (response.started, output_item.added, content_block.delta, response.done)
"""

import json
import logging
from typing import Any, Dict, List, Optional, Union

from .models import (
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


class OpenAIResponsesTransformer:
    """
    Transforms OpenAI Responses API responses to unified format.

    Handles both complete responses and streaming SSE events.
    Converts Responses API's item-based format to unified content blocks.

    Response format:
    {
        "id": "resp_001",
        "output": [
            {"type": "message", "content": [{"type": "text", "text": "..."}]},
            {"type": "function_call", "call_id": "...", "function": "...", "arguments": "{}"},
            {"type": "reasoning", "content": [{"type": "text", "text": "..."}]}
        ],
        "usage": {"input_tokens": N, "output_tokens": N}
    }

    Streaming events:
    - response.started: Initial metadata
    - response.output_item.added: New item in output array
    - response.output_item.done: Item completion
    - response.done: Final response with usage
    """

    @staticmethod
    def _usage_info(usage_dict: Optional[Dict[str, Any]]) -> UsageInfo:
        """Normalize Responses usage, including prompt-cache accounting.

        The public Responses API reports cache counters in
        ``input_tokens_details``.  The OAuth/Codex transport has also
        returned the Chat Completions-shaped ``prompt_tokens_details`` in
        some responses, so accept that shape without changing the request
        contract.  The top-level aliases keep telemetry useful for proxies
        that flatten provider usage fields.
        """
        usage = usage_dict or {}
        details = (
            usage.get("input_tokens_details")
            or usage.get("prompt_tokens_details")
            or {}
        )
        if not isinstance(details, dict):
            details = {}

        prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        completion_tokens = usage.get(
            "output_tokens", usage.get("completion_tokens", 0)
        ) or 0
        cached_tokens = details.get("cached_tokens", 0) or usage.get(
            "cache_read_input_tokens", usage.get("cache_read_tokens", 0)
        ) or 0
        cache_write_tokens = details.get("cache_write_tokens", 0) or usage.get(
            "cache_creation_input_tokens", usage.get("cache_creation_tokens", 0)
        ) or 0
        total_tokens = usage.get("total_tokens") or prompt_tokens + completion_tokens

        return UsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_creation_tokens=cache_write_tokens,
            cache_read_tokens=cached_tokens,
        )

    @staticmethod
    def transform_response(response: Dict[str, Any], model: str) -> UnifiedResponse:
        """
        Transform complete OpenAI Responses API response to unified format.

        Args:
            response: Raw Responses API response dict
            model: Model name

        Returns:
            UnifiedResponse with all content blocks

        Raises:
            ValueError: If response is invalid or output is empty

        Example response:
        {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello"}]
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5}
        }
        """
        if not response or "output" not in response:
            logger.warning(
                "Responses API response missing output field, "
                f"keys={list(response.keys()) if response else 'None'}, "
                f"status={response.get('status', 'unknown') if response else 'N/A'}"
            )
            # Return empty text response instead of crashing
            usage = UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0)
            return UnifiedResponse(
                content=[TextContent(text="")],
                usage=usage,
                model=model,
                provider=ProviderType.OPENAI_RESPONSES,
                finish_reason=response.get("status") if response else None,
                raw_response=response,
            )

        output_items = response.get("output", [])
        if not output_items:
            logger.warning(
                "Responses API returned empty output array, "
                f"status={response.get('status', 'unknown')}, "
                f"id={response.get('id', 'unknown')}"
            )
            # Return empty text response instead of crashing
            usage = OpenAIResponsesTransformer._usage_info(response.get("usage"))
            return UnifiedResponse(
                content=[TextContent(text="")],
                usage=usage,
                model=model,
                provider=ProviderType.OPENAI_RESPONSES,
                finish_reason=response.get("status"),
                raw_response=response,
            )

        content_blocks: List[
            Union[TextContent, ToolUseContent, ToolResultContent, ThinkingContent]
        ] = []

        # Process each output item
        for item in output_items:
            item_type = item.get("type")

            # Message item -> TextContent
            if item_type == "message":
                content_blocks.extend(
                    OpenAIResponsesTransformer._transform_message_item(item)
                )

            # Function call item -> ToolUseContent
            elif item_type == "function_call":
                content_blocks.append(
                    OpenAIResponsesTransformer._transform_function_call_item(item)
                )

            # Reasoning item -> ThinkingContent
            elif item_type == "reasoning":
                content_blocks.append(
                    OpenAIResponsesTransformer._transform_reasoning_item(item)
                )

            else:
                logger.warning(f"Unknown output item type: {item_type}")

        # Extract usage, including cache reads and writes.
        usage = OpenAIResponsesTransformer._usage_info(response.get("usage"))

        return UnifiedResponse(
            content=content_blocks,
            usage=usage,
            model=model,
            provider=ProviderType.OPENAI_RESPONSES,
            finish_reason=response.get("status"),
            raw_response=response,
        )

    @staticmethod
    def _transform_message_item(item: Dict[str, Any]) -> List[TextContent]:
        """
        Transform message item to TextContent blocks.

        Args:
            item: Message item dict

        Returns:
            List of TextContent blocks (concatenated if multiple)
        """
        content_array = item.get("content", [])

        # Concatenate multiple text blocks into one
        # Handles both standard "text" and codex "output_text" types
        text_parts = []
        for content_block in content_array:
            if content_block.get("type") in ("text", "output_text"):
                text_parts.append(content_block.get("text", ""))

        combined_text = "".join(text_parts)

        # Always return at least one TextContent block, even if empty
        # This ensures UnifiedResponse validation passes
        return [TextContent(text=combined_text)]

    @staticmethod
    def _transform_function_call_item(item: Dict[str, Any]) -> ToolUseContent:
        """
        Transform function_call item to ToolUseContent.

        Args:
            item: Function call item dict

        Returns:
            ToolUseContent block
        """
        call_id = item.get("call_id", "")
        function_name = item.get("name", "") or item.get("function", "")
        arguments_str = item.get("arguments", "{}")

        # Parse JSON arguments
        try:
            arguments = json.loads(arguments_str) if arguments_str else {}
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse function call arguments: {arguments_str}")
            arguments = {}

        return ToolUseContent(
            id=call_id,
            name=function_name,
            input=arguments,
        )

    @staticmethod
    def _transform_reasoning_item(item: Dict[str, Any]) -> ThinkingContent:
        """
        Transform reasoning item to ThinkingContent.

        Args:
            item: Reasoning item dict

        Returns:
            ThinkingContent block
        """
        content_array = item.get("content", [])
        text_parts = []

        for content_block in content_array:
            if content_block.get("type") == "text":
                text_parts.append(content_block.get("text", ""))

        combined_thinking = "".join(text_parts)

        return ThinkingContent(thinking=combined_thinking)

    @staticmethod
    def transform_streaming_chunk(
        chunk: Dict[str, Any], model: str
    ) -> Optional[StreamingResponse]:
        """
        Transform OpenAI Responses API streaming chunk to unified format.

        Args:
            chunk: Raw SSE event dict
            model: Model name

        Returns:
            StreamingResponse or None if chunk has no actionable content

        Events:
        - response.started: Metadata only, returns None
        - response.output_item.added: New item (may have partial content)
        - response.output_item.done: Item completion
        - response.done: Final response with usage

        Example chunks:
        {
            "event": "response.output_item.added",
            "item": {"type": "message", "content": [{"type": "text", "text": "Hello"}]}
        }
        {
            "event": "response.done",
            "response": {"id": "resp_001", "usage": {...}}
        }
        """
        if not chunk:
            return None

        event_type = chunk.get("event")

        # response.started - metadata only
        if event_type == "response.started":
            return None

        # response.done - final chunk with usage
        if event_type == "response.done":
            response_data = chunk.get("response", {})
            usage = OpenAIResponsesTransformer._usage_info(
                response_data.get("usage")
            )

            return StreamingResponse(
                delta=TextDelta(content=""),
                usage=usage,
                is_final=True,
                raw_chunk=chunk,
            )

        # output_item events
        if event_type in ("response.output_item.added", "response.output_item.done"):
            item = chunk.get("item", {})
            item_type = item.get("type")

            # Message item -> text delta
            if item_type == "message":
                content_array = item.get("content", [])
                text_parts = []

                for content_block in content_array:
                    if content_block.get("type") == "text":
                        text_parts.append(content_block.get("text", ""))

                combined_text = "".join(text_parts)

                if combined_text:
                    return StreamingResponse(
                        delta=TextDelta(content=combined_text),
                        is_final=False,
                        raw_chunk=chunk,
                    )

            # Function call item -> tool call delta
            elif item_type == "function_call":
                call_id = item.get("call_id", "")
                function_name = item.get("name", "") or item.get("function", "")
                arguments_str = item.get("arguments", "")

                return StreamingResponse(
                    delta=ToolCallDelta(
                        tool_call_id=call_id,
                        tool_name=function_name,
                        tool_arguments_delta=arguments_str,
                    ),
                    is_final=False,
                    raw_chunk=chunk,
                )

            # Reasoning item -> thinking delta
            elif item_type == "reasoning":
                content_array = item.get("content", [])
                text_parts = []

                for content_block in content_array:
                    if content_block.get("type") == "text":
                        text_parts.append(content_block.get("text", ""))

                combined_thinking = "".join(text_parts)

                if combined_thinking:
                    return StreamingResponse(
                        delta=ThinkingDelta(content=combined_thinking),
                        is_final=False,
                        raw_chunk=chunk,
                    )

        # Unknown event type
        return None
