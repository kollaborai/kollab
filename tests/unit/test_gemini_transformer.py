"""
Unit tests for Gemini provider transformers.

Tests for:
- ToolSchemaTransformer.to_gemini_format: OpenAI -> Gemini schema conversion
- GeminiResponseTransformer: Gemini response to UnifiedResponse conversion

Target: 75%+ coverage
"""

import pytest

from kollabor_ai.providers.gemini_transformer import GeminiResponseTransformer
from kollabor_ai.providers.models import (
    ProviderType,
    TextContent,
    TextDelta,
    ToolCallDelta,
    ToolUseContent,
)
from kollabor_ai.providers.transformers import ToolSchemaTransformer


class TestGeminiToolSchemaTransformer:
    """Test Gemini tool schema transformation."""

    def test_to_gemini_format_from_openai(self):
        """Test converting OpenAI tool schema to Gemini format."""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                            "unit": {
                                "type": "string",
                                "enum": ["celsius", "fahrenheit"],
                                "description": "Temperature unit",
                            },
                        },
                        "required": ["location"],
                    },
                },
            }
        ]

        gemini_tools = ToolSchemaTransformer.to_gemini_format(openai_tools)

        assert len(gemini_tools) == 1
        assert "functionDeclarations" in gemini_tools[0]
        declarations = gemini_tools[0]["functionDeclarations"]
        assert len(declarations) == 1
        assert declarations[0]["name"] == "get_weather"
        assert declarations[0]["description"] == "Get current weather for a location"
        assert declarations[0]["parameters"]["type"] == "object"
        assert "location" in declarations[0]["parameters"]["properties"]

    def test_to_gemini_format_multiple_tools(self):
        """Test converting multiple tools to Gemini format."""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calculate",
                    "description": "Perform calculations",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

        gemini_tools = ToolSchemaTransformer.to_gemini_format(openai_tools)

        assert len(gemini_tools) == 1
        declarations = gemini_tools[0]["functionDeclarations"]
        assert len(declarations) == 2
        assert declarations[0]["name"] == "search"
        assert declarations[1]["name"] == "calculate"

    def test_to_gemini_format_empty_tools(self):
        """Test handling empty tool list."""
        gemini_tools = ToolSchemaTransformer.to_gemini_format([])
        assert gemini_tools == []

    def test_to_gemini_format_preserves_parameters(self):
        """Test that all JSON Schema parameters are preserved."""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "complex_tool",
                    "description": "A complex tool",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "required_field": {"type": "string"},
                            "optional_field": {"type": "number"},
                            "enum_field": {"type": "string", "enum": ["a", "b"]},
                        },
                        "required": ["required_field"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

        gemini_tools = ToolSchemaTransformer.to_gemini_format(openai_tools)
        params = gemini_tools[0]["functionDeclarations"][0]["parameters"]

        assert params["type"] == "object"
        assert params["required"] == ["required_field"]
        assert params["additionalProperties"] is False
        assert "enum_field" in params["properties"]

    def test_to_gemini_format_no_parameters(self):
        """Test tool with no parameters field."""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "simple_tool",
                    "description": "A simple tool with no parameters",
                },
            }
        ]

        gemini_tools = ToolSchemaTransformer.to_gemini_format(openai_tools)
        declaration = gemini_tools[0]["functionDeclarations"][0]

        assert declaration["name"] == "simple_tool"
        assert (
            declaration.get("parameters") is None or declaration.get("parameters") == {}
        )


class TestGeminiResponseTransformer:
    """Test Gemini response transformation."""

    def test_text_response(self):
        """Test transforming text-only response."""
        gemini_response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Hello, world!"}],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        }

        response = GeminiResponseTransformer.transform_response(
            gemini_response, "gemini-2.0-flash"
        )

        assert response.provider == ProviderType.GEMINI
        assert response.model == "gemini-2.0-flash"
        assert len(response.content) == 1
        assert isinstance(response.content[0], TextContent)
        assert response.content[0].text == "Hello, world!"
        assert response.finish_reason == "STOP"
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
        assert response.usage.total_tokens == 15

    def test_function_call_response(self):
        """Test transforming function call response."""
        gemini_response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "get_weather",
                                    "args": {"location": "NYC", "unit": "celsius"},
                                }
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 10,
                "totalTokenCount": 30,
            },
        }

        response = GeminiResponseTransformer.transform_response(
            gemini_response, "gemini-2.0-flash"
        )

        assert len(response.content) == 1
        assert isinstance(response.content[0], ToolUseContent)
        assert response.content[0].name == "get_weather"
        assert response.content[0].input == {"location": "NYC", "unit": "celsius"}
        assert response.finish_reason == "STOP"

    def test_mixed_content_response(self):
        """Test response with both text and function calls."""
        gemini_response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "Let me check the weather for you."},
                            {
                                "functionCall": {
                                    "name": "get_weather",
                                    "args": {"location": "NYC"},
                                }
                            },
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 15,
                "candidatesTokenCount": 8,
                "totalTokenCount": 23,
            },
        }

        response = GeminiResponseTransformer.transform_response(
            gemini_response, "gemini-2.0-flash"
        )

        assert len(response.content) == 2
        assert isinstance(response.content[0], TextContent)
        assert response.content[0].text == "Let me check the weather for you."
        assert isinstance(response.content[1], ToolUseContent)
        assert response.content[1].name == "get_weather"

    def test_multiple_function_calls(self):
        """Test response with multiple function calls."""
        gemini_response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "search",
                                    "args": {"query": "weather"},
                                }
                            },
                            {
                                "functionCall": {
                                    "name": "search",
                                    "args": {"query": "news"},
                                }
                            },
                        ],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 15,
                "totalTokenCount": 25,
            },
        }

        response = GeminiResponseTransformer.transform_response(
            gemini_response, "gemini-2.0-flash"
        )

        assert len(response.content) == 2
        assert all(isinstance(c, ToolUseContent) for c in response.content)
        assert response.content[0].name == "search"
        assert response.content[1].name == "search"

    def test_finish_reason_mapping(self):
        """Test various finish reasons."""
        finish_reasons = ["STOP", "TOOL_CALLS", "MAX_TOKENS", "SAFETY"]

        for reason in finish_reasons:
            gemini_response = {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Done"}]},
                        "finishReason": reason,
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 1,
                    "totalTokenCount": 2,
                },
            }

            response = GeminiResponseTransformer.transform_response(
                gemini_response, "gemini-2.0-flash"
            )

            assert response.finish_reason == reason

    def test_empty_response_raises(self):
        """Test that empty response raises ValueError."""
        with pytest.raises(ValueError, match="Invalid Gemini response"):
            GeminiResponseTransformer.transform_response({}, "gemini-2.0-flash")

    def test_missing_candidates_raises(self):
        """Test that missing candidates raises ValueError."""
        with pytest.raises(ValueError, match="Invalid Gemini response"):
            GeminiResponseTransformer.transform_response(
                {"candidates": []}, "gemini-2.0-flash"
            )

    def test_raw_response_preserved(self):
        """Test that raw response is preserved."""
        gemini_response = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "Hello"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        }

        response = GeminiResponseTransformer.transform_response(
            gemini_response, "gemini-2.0-flash"
        )

        assert response.raw_response == gemini_response

    def test_streaming_text_chunk(self):
        """Test transforming streaming text chunk."""
        chunk = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Hello"}],
                    },
                    "finishReason": None,
                }
            ],
        }

        response = GeminiResponseTransformer.transform_streaming_chunk(
            chunk, "gemini-2.0-flash"
        )

        assert response is not None
        assert isinstance(response.delta, TextDelta)
        assert response.delta.content == "Hello"
        assert response.is_final is False

    def test_streaming_function_call_chunk(self):
        """Test transforming streaming function call chunk."""
        chunk = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "get_weather",
                                    "args": {"location": "NYC"},
                                }
                            }
                        ],
                    },
                    "finishReason": None,
                }
            ],
        }

        response = GeminiResponseTransformer.transform_streaming_chunk(
            chunk, "gemini-2.0-flash"
        )

        assert response is not None
        assert isinstance(response.delta, ToolCallDelta)
        assert response.delta.tool_name == "get_weather"
        # Note: In streaming, args may come incrementally

    def test_streaming_final_chunk(self):
        """Test transforming final streaming chunk."""
        chunk = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": []},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        }

        response = GeminiResponseTransformer.transform_streaming_chunk(
            chunk, "gemini-2.0-flash"
        )

        assert response is not None
        assert response.is_final is True
        assert response.usage is not None
        assert response.usage.total_tokens == 15

    def test_streaming_empty_chunk(self):
        """Test handling empty streaming chunk."""
        response = GeminiResponseTransformer.transform_streaming_chunk(
            {}, "gemini-2.0-flash"
        )
        assert response is None

    def test_usage_metadata_parsing(self):
        """Test usage metadata is correctly parsed."""
        gemini_response = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "Hi"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50,
                "totalTokenCount": 150,
            },
        }

        response = GeminiResponseTransformer.transform_response(
            gemini_response, "gemini-2.0-flash"
        )

        assert response.usage.prompt_tokens == 100
        assert response.usage.completion_tokens == 50
        assert response.usage.total_tokens == 150

    def test_missing_optional_fields(self):
        """Test handling of missing optional fields."""
        gemini_response = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "Hello"}]},
                    # No finishReason
                }
            ],
            # No usageMetadata
        }

        response = GeminiResponseTransformer.transform_response(
            gemini_response, "gemini-2.0-flash"
        )

        assert response.content[0].text == "Hello"
        assert response.finish_reason is None
        # Default usage if missing
        assert response.usage is not None
