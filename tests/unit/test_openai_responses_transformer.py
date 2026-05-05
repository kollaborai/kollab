"""
Unit tests for OpenAI Responses API transformer.

Tests for:
- OpenAIResponsesTransformer: Converts Responses API format to unified format
- Message item transformation
- Function call item transformation
- Reasoning item transformation
- Streaming chunk transformation (SSE events)
- Usage extraction

Target: 75%+ coverage
"""

from kollabor_ai.providers.models import (
    ProviderType,
    StreamingResponse,
    TextContent,
    ThinkingContent,
    ToolUseContent,
    UnifiedResponse,
)
from kollabor_ai.providers.openai_responses_transformer import (
    OpenAIResponsesTransformer,
)


class TestOpenAIResponsesTransformer:
    """Test OpenAI Responses API response transformation."""

    def test_transform_message_item(self):
        """Test transforming a message item with text content."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello, world!"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert isinstance(response, UnifiedResponse)
        assert len(response.content) == 1
        assert isinstance(response.content[0], TextContent)
        assert response.content[0].text == "Hello, world!"
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
        assert response.usage.total_tokens == 15
        assert response.model == "gpt-5.4"
        assert response.provider == ProviderType.OPENAI_RESPONSES

    def test_transform_function_call_item(self):
        """Test transforming a function_call item."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_001",
                    "function": "get_weather",
                    "arguments": '{"location": "NYC", "unit": "celsius"}',
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert isinstance(response, UnifiedResponse)
        assert len(response.content) == 1
        assert isinstance(response.content[0], ToolUseContent)
        assert response.content[0].id == "call_001"
        assert response.content[0].name == "get_weather"
        assert response.content[0].input == {"location": "NYC", "unit": "celsius"}

    def test_transform_reasoning_item(self):
        """Test transforming a reasoning item (extended thinking)."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "reasoning",
                    "content": [
                        {"type": "text", "text": "Let me analyze this step by step..."}
                    ],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert isinstance(response, UnifiedResponse)
        assert len(response.content) == 1
        assert isinstance(response.content[0], ThinkingContent)
        assert response.content[0].thinking == "Let me analyze this step by step..."

    def test_transform_multiple_items(self):
        """Test transforming response with multiple output items."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "reasoning",
                    "content": [{"type": "text", "text": "Thinking..."}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Here's the answer."}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_001",
                    "function": "search",
                    "arguments": '{"query": "test"}',
                },
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert len(response.content) == 3
        assert isinstance(response.content[0], ThinkingContent)
        assert isinstance(response.content[1], TextContent)
        assert isinstance(response.content[2], ToolUseContent)

    def test_transform_empty_output_returns_empty_text_fallback(self):
        """Test that empty output returns safe empty-text fallback."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert isinstance(response, UnifiedResponse)
        assert len(response.content) == 1
        assert isinstance(response.content[0], TextContent)
        assert response.content[0].text == ""
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
        assert response.usage.total_tokens == 15

    def test_transform_invalid_json_arguments(self):
        """Test handling of invalid JSON in function_call arguments."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_001",
                    "function": "test",
                    "arguments": "{invalid json}",
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        # Should handle gracefully with empty dict
        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert response.content[0].input == {}

    def test_streaming_response_started_event(self):
        """Test transforming response.started SSE event."""
        chunk = {
            "event": "response.started",
            "response": {"id": "resp_001", "model": "gpt-5.4", "status": "in_progress"},
        }

        response = OpenAIResponsesTransformer.transform_streaming_chunk(
            chunk, "gpt-5.4"
        )

        # response.started is metadata, should return None or minimal response
        assert response is None or isinstance(response, StreamingResponse)

    def test_streaming_output_item_added_event(self):
        """Test transforming output_item.added SSE event."""
        chunk = {
            "event": "response.output_item.added",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
            },
        }

        response = OpenAIResponsesTransformer.transform_streaming_chunk(
            chunk, "gpt-5.4"
        )

        assert response is not None
        assert isinstance(response, StreamingResponse)
        assert response.delta.type == "text"
        assert response.delta.content == "Hello"

    def test_streaming_content_block_delta_event(self):
        """Test transforming content_block.delta SSE event."""
        chunk = {
            "event": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": " world!"}],
            },
        }

        response = OpenAIResponsesTransformer.transform_streaming_chunk(
            chunk, "gpt-5.4"
        )

        assert response is not None
        assert response.delta.type == "text"
        assert "world!" in response.delta.content

    def test_streaming_function_call_delta(self):
        """Test transforming streaming function call delta."""
        chunk = {
            "event": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "call_001",
                "function": "search",
                "arguments": '{"query": "test"',
            },
        }

        response = OpenAIResponsesTransformer.transform_streaming_chunk(
            chunk, "gpt-5.4"
        )

        assert response is not None
        assert response.delta.type == "tool_call_delta"
        assert response.delta.tool_call_id == "call_001"
        assert response.delta.tool_name == "search"
        assert '{"query": "test"' in response.delta.tool_arguments_delta

    def test_streaming_response_done_event(self):
        """Test transforming response.done SSE event (final chunk)."""
        chunk = {
            "event": "response.done",
            "response": {
                "id": "resp_001",
                "model": "gpt-5.4",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }

        response = OpenAIResponsesTransformer.transform_streaming_chunk(
            chunk, "gpt-5.4"
        )

        assert response is not None
        assert response.is_final is True
        assert response.usage is not None
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5

    def test_streaming_unknown_event_returns_none(self):
        """Test that unknown event types return None."""
        chunk = {"event": "unknown.event", "data": {}}

        response = OpenAIResponsesTransformer.transform_streaming_chunk(
            chunk, "gpt-5.4"
        )

        assert response is None

    def test_streaming_empty_chunk_returns_none(self):
        """Test that empty chunks return None."""
        response = OpenAIResponsesTransformer.transform_streaming_chunk({}, "gpt-5.4")

        assert response is None

    def test_raw_response_preserved(self):
        """Test that raw response is preserved."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Test"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "custom_field": "custom_value",
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert response.raw_response is not None
        assert response.raw_response["custom_field"] == "custom_value"

    def test_usage_calculation_with_zero_tokens(self):
        """Test usage calculation with zero tokens."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": ""}],
                }
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert response.usage.prompt_tokens == 0
        assert response.usage.completion_tokens == 0
        assert response.usage.total_tokens == 0

    def test_multiple_text_content_blocks(self):
        """Test message item with multiple text content blocks."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "First part. "},
                        {"type": "text", "text": "Second part."},
                    ],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        # Multiple text blocks should be concatenated
        assert len(response.content) == 1
        assert isinstance(response.content[0], TextContent)
        assert response.content[0].text == "First part. Second part."

    def test_get_text_content_utility(self):
        """Test get_text_content utility method."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello world"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        text = response.get_text_content()
        assert text == "Hello world"

    def test_get_tool_uses_utility(self):
        """Test get_tool_uses utility method."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_001",
                    "function": "search",
                    "arguments": '{"query": "test"}',
                },
                {
                    "type": "function_call",
                    "call_id": "call_002",
                    "function": "calculate",
                    "arguments": '{"x": 1}',
                },
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        tools = response.get_tool_uses()
        assert len(tools) == 2
        assert tools[0].name == "search"
        assert tools[1].name == "calculate"

    def test_get_thinking_content_utility(self):
        """Test get_thinking_content utility method."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "reasoning",
                    "content": [{"type": "text", "text": "Thinking process..."}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Answer"}],
                },
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        thinking = response.get_thinking_content()
        assert thinking == "Thinking process..."

    def test_missing_usage_defaults_to_zero(self):
        """Test that missing usage defaults to zero tokens."""
        response_dict = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Test"}],
                }
            ],
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert response.usage.prompt_tokens == 0
        assert response.usage.completion_tokens == 0
        assert response.usage.total_tokens == 0

    def test_response_id_preserved(self):
        """Test that response ID is accessible in raw_response."""
        response_dict = {
            "id": "resp_abc123",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Test"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = OpenAIResponsesTransformer.transform_response(
            response_dict, "gpt-5.4"
        )

        assert response.raw_response["id"] == "resp_abc123"
