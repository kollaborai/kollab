"""
Gemini provider response transformer.

Converts Gemini API responses to unified format:
- Handles candidates with text and functionCall parts
- Supports streaming chunks
- Extracts usage metadata
"""

import logging
from typing import Any, Dict, List, Optional

from .models import (
    ProviderType,
    StreamingResponse,
    TextContent,
    TextDelta,
    ToolCallDelta,
    ToolUseContent,
    UnifiedResponse,
    UsageInfo,
)

logger = logging.getLogger(__name__)


class GeminiResponseTransformer:
    """
    Transforms Gemini API responses to unified format.

    Gemini response format:
    - Non-streaming: candidates with content.parts (text, functionCall)
    - Streaming: SSE chunks with incremental updates
    - Usage: usageMetadata (promptTokenCount, candidatesTokenCount, totalTokenCount)

    Example Gemini response:
    {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [
                    {"text": "Hello"},
                    {"functionCall": {"name": "get_weather", "args": {"location": "NYC"}}}
                ]
            },
            "finishReason": "STOP"
        }],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15
        }
    }
    """

    @staticmethod
    def transform_streaming_chunk(
        chunk: Dict[str, Any], model: str
    ) -> Optional[StreamingResponse]:
        """
        Transform Gemini streaming chunk to unified format.

        Args:
            chunk: Raw Gemini chunk dict
            model: Model name

        Returns:
            StreamingResponse or None if chunk has no content

        Example Gemini streaming chunk:
        {
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": "Hello"}]},
                "finishReason": null
            }]
        }
        """
        if not chunk or "candidates" not in chunk or not chunk["candidates"]:
            return None

        candidate = chunk["candidates"][0]
        parts = candidate.get("content", {}).get("parts", [])
        finish_reason = candidate.get("finishReason")

        # Check if final chunk
        is_final = finish_reason is not None

        # Process parts
        for part in parts:
            # Text content
            if "text" in part:
                return StreamingResponse(
                    delta=TextDelta(content=part["text"]),
                    is_final=is_final,
                    finish_reason=finish_reason,
                    raw_chunk=chunk,
                )

            # Function call content
            if "functionCall" in part:
                func_call = part["functionCall"]
                return StreamingResponse(
                    delta=ToolCallDelta(
                        tool_call_id=None,  # Gemini doesn't provide IDs
                        tool_name=func_call.get("name"),
                        tool_arguments_delta=str(func_call.get("args", {})),
                    ),
                    is_final=is_final,
                    finish_reason=finish_reason,
                    raw_chunk=chunk,
                )

        # Final chunk with usage
        if is_final:
            usage_metadata = chunk.get("usageMetadata")
            if usage_metadata:
                return StreamingResponse(
                    delta=TextDelta(content=""),
                    usage=UsageInfo(
                        prompt_tokens=usage_metadata.get("promptTokenCount", 0),
                        completion_tokens=usage_metadata.get("candidatesTokenCount", 0),
                        total_tokens=usage_metadata.get("totalTokenCount", 0),
                    ),
                    is_final=True,
                    finish_reason=finish_reason,
                    raw_chunk=chunk,
                )

        # Empty chunk (keepalive)
        return None

    @staticmethod
    def transform_response(response: Dict[str, Any], model: str) -> UnifiedResponse:
        """
        Transform complete Gemini response to unified format.

        Args:
            response: Raw Gemini response dict
            model: Model name

        Returns:
            UnifiedResponse with all content blocks

        Raises:
            ValueError: If response is invalid

        Example Gemini response:
        {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "Let me check the weather."},
                        {"functionCall": {"name": "get_weather", "args": {"location": "NYC"}}}
                    ]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15
            }
        }
        """
        if not response or "candidates" not in response or not response["candidates"]:
            raise ValueError("Invalid Gemini response: missing candidates")

        candidate = response["candidates"][0]
        content_data = candidate.get("content", {})
        parts = content_data.get("parts", [])
        finish_reason = candidate.get("finishReason")

        # Extract content blocks
        content_blocks: List[Any] = []

        for i, part in enumerate(parts):
            # Text content
            if "text" in part:
                content_blocks.append(TextContent(text=part["text"]))

            # Function call content
            elif "functionCall" in part:
                func_call = part["functionCall"]
                content_blocks.append(
                    ToolUseContent(
                        id=f"gemini_{i}",  # Generate ID since Gemini doesn't provide one
                        name=func_call.get("name", ""),
                        input=func_call.get("args", {}),
                    )
                )

        # Extract usage metadata
        usage_metadata = response.get("usageMetadata", {})
        usage = UsageInfo(
            prompt_tokens=usage_metadata.get("promptTokenCount", 0),
            completion_tokens=usage_metadata.get("candidatesTokenCount", 0),
            total_tokens=usage_metadata.get("totalTokenCount", 0),
        )

        return UnifiedResponse(
            content=content_blocks,
            usage=usage,
            model=model,
            provider=ProviderType.GEMINI,
            finish_reason=finish_reason,
            raw_response=response,
        )
