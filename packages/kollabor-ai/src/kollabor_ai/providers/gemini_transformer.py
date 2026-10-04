"""
Gemini provider response transformer.

Converts Gemini API responses to unified format:
- Handles candidates with text, thought-summary and functionCall parts
- Supports streaming chunks (one chunk can carry many parts)
- Extracts usage metadata, including cached and thinking tokens
- Captures thought signatures as ``provider_reasoning`` so the next turn can
  send them back (Gemini 3 answers 400 to a functionCall that lost its
  signature)
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import (
    ProviderType,
    StreamingResponse,
    TextContent,
    TextDelta,
    ThinkingContent,
    ThinkingDelta,
    ToolCallDelta,
    ToolUseContent,
    UnifiedResponse,
    UsageInfo,
)

logger = logging.getLogger(__name__)

SIGNATURE_KEY = "thoughtSignature"


@dataclass
class GeminiStreamState:
    """What one stream has seen so far; the provider makes one per request."""

    items: List[Dict[str, Any]] = field(default_factory=list)


def _call_item(part: Dict[str, Any], call_id: str, api_id: bool) -> Dict[str, Any]:
    """provider_reasoning item for one functionCall part.

    Every call gets one, signed or not: ``api_id`` records whether Gemini issued
    the id, because only a Gemini-issued id may be echoed back on the wire.
    """
    item: Dict[str, Any] = {"type": "functionCall", "id": call_id, "api_id": api_id}
    if part.get(SIGNATURE_KEY):
        item[SIGNATURE_KEY] = part[SIGNATURE_KEY]
    return item


def _reasoning(model: str, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    return {"provider": ProviderType.GEMINI.value, "model": model, "items": items}


class GeminiResponseTransformer:
    """
    Transforms Gemini API responses to unified format.

    Gemini response format:
    - Non-streaming: candidates with content.parts (text, functionCall)
    - Streaming: SSE chunks, each with any number of parts
    - Usage: usageMetadata. promptTokenCount INCLUDES cachedContentTokenCount;
      candidatesTokenCount EXCLUDES thoughtsTokenCount (billed as output).

    Example Gemini response:
    {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [
                    {"text": "Hello"},
                    {"functionCall": {"name": "get_weather", "args": {"location": "NYC"}},
                     "thoughtSignature": "..."}
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
        chunk: Dict[str, Any], model: str, state: GeminiStreamState
    ) -> List[StreamingResponse]:
        """
        Transform one Gemini streaming chunk into zero or more responses.

        Every part of the chunk is emitted in order. Usage, finish_reason and
        is_final ride on the LAST response, and the final chunk's last response
        also carries the complete ``provider_reasoning`` (once per stream).

        Args:
            chunk: Raw Gemini chunk dict
            model: Model name
            state: Per-stream state shared by every chunk of one request

        Returns:
            Responses in part order; empty for a keepalive chunk
        """
        if not chunk or not chunk.get("candidates"):
            return []

        candidate = chunk["candidates"][0]
        parts = (candidate.get("content") or {}).get("parts") or []
        finish_reason = GeminiResponseTransformer._finish_reason(candidate)
        is_final = finish_reason is not None

        responses: List[StreamingResponse] = []
        for part in parts:
            if part.get("thought"):
                # Thought summary: never answer text. Its signature is dropped
                # (summaries are not stored, so there is nothing to re-attach it to).
                if part.get("text"):
                    responses.append(
                        StreamingResponse(
                            delta=ThinkingDelta(content=part["text"]), raw_chunk=chunk
                        )
                    )
            elif "functionCall" in part:
                func_call = part["functionCall"]
                # Gemini 3 issues an id per call; older models do not, and each
                # call arrives complete in one part, so synthesize a unique one
                # (a None id is dropped by ToolCallAccumulator and a per-chunk
                # index would glue two calls into one unparseable buffer).
                call_id = func_call.get("id") or f"gemini_{uuid.uuid4().hex[:8]}"
                state.items.append(_call_item(part, call_id, bool(func_call.get("id"))))
                responses.append(
                    StreamingResponse(
                        delta=ToolCallDelta(
                            tool_call_id=call_id,
                            tool_name=func_call.get("name"),
                            # The accumulator json.loads() this buffer; str() of
                            # a dict never parses.
                            tool_arguments_delta=json.dumps(
                                func_call.get("args") or {}
                            ),
                        ),
                        raw_chunk=chunk,
                    )
                )
            elif "text" in part:
                if part.get(SIGNATURE_KEY):
                    state.items.append(
                        {"type": "text", SIGNATURE_KEY: part[SIGNATURE_KEY]}
                    )
                # An empty part (the signature-only closer) has nothing to show.
                if part["text"]:
                    responses.append(
                        StreamingResponse(
                            delta=TextDelta(content=part["text"]), raw_chunk=chunk
                        )
                    )

        # Final chunk with nothing to show: dropping it would lose the finish
        # reason, usage and the reasoning items.
        if is_final and not responses:
            responses.append(
                StreamingResponse(delta=TextDelta(content=""), raw_chunk=chunk)
            )

        if responses:
            last = responses[-1]
            last.usage = GeminiResponseTransformer._chunk_usage(chunk)
            last.is_final = is_final
            last.finish_reason = finish_reason
            if is_final:
                last.provider_reasoning = _reasoning(model, state.items)

        return responses

    @staticmethod
    def _finish_reason(candidate: Dict[str, Any]) -> Optional[str]:
        """Map MAX_TOKENS to "length", the stop reason auto-continue keys off."""
        reason = candidate.get("finishReason")
        return "length" if reason == "MAX_TOKENS" else reason

    @staticmethod
    def _chunk_usage(chunk: Dict[str, Any]) -> Optional[UsageInfo]:
        """Extract usage from a chunk, or None when it carries no counts."""
        usage_metadata = chunk.get("usageMetadata")
        if not usage_metadata:
            return None
        return GeminiResponseTransformer._usage(usage_metadata)

    @staticmethod
    def _usage(metadata: Dict[str, Any]) -> UsageInfo:
        """Map usageMetadata onto UsageInfo (OpenAI-style: prompt includes cache).

        - prompt: promptTokenCount already includes the cached tokens;
          toolUsePromptTokenCount (built-in tools) is more input.
        - completion: candidatesTokenCount excludes thinking, which is billed
          as output, so thoughtsTokenCount is added.
        """

        def count(key: str) -> int:
            return metadata.get(key) or 0

        prompt = count("promptTokenCount") + count("toolUsePromptTokenCount")
        completion = count("candidatesTokenCount") + count("thoughtsTokenCount")
        return UsageInfo(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=count("totalTokenCount") or prompt + completion,
            cache_read_tokens=count("cachedContentTokenCount"),
        )

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
        """
        if not response or "candidates" not in response or not response["candidates"]:
            raise ValueError("Invalid Gemini response: missing candidates")

        candidate = response["candidates"][0]
        parts = (candidate.get("content") or {}).get("parts") or []
        finish_reason = GeminiResponseTransformer._finish_reason(candidate)

        content_blocks: List[Any] = []
        items: List[Dict[str, Any]] = []

        for i, part in enumerate(parts):
            if part.get("thought"):
                if part.get("text"):
                    content_blocks.append(ThinkingContent(thinking=part["text"]))
            elif "functionCall" in part:
                func_call = part["functionCall"]
                # Gemini 3 issues ids; otherwise index the part.
                call_id = func_call.get("id") or f"gemini_{i}"
                items.append(_call_item(part, call_id, bool(func_call.get("id"))))
                content_blocks.append(
                    ToolUseContent(
                        id=call_id,
                        name=func_call.get("name", ""),
                        input=func_call.get("args") or {},
                    )
                )
            elif "text" in part:
                if part.get(SIGNATURE_KEY):
                    items.append({"type": "text", SIGNATURE_KEY: part[SIGNATURE_KEY]})
                content_blocks.append(TextContent(text=part["text"]))

        return UnifiedResponse(
            content=content_blocks,
            usage=GeminiResponseTransformer._usage(response.get("usageMetadata") or {}),
            model=model,
            provider=ProviderType.GEMINI,
            finish_reason=finish_reason,
            raw_response=response,
            provider_reasoning=_reasoning(model, items),
        )
