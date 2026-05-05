"""
Unit tests for OpenAI Responses API provider.

Tests for:
- OpenAIResponsesProvider: LLM provider for OpenAI Responses API
- initialize() - Client creation
- validate_config() - Configuration validation
- call() - Non-streaming requests
- stream() - Streaming requests with SSE parsing
- _prepare_request() - Request payload building
- _format_tool_result() - Tool result formatting

Target: 75%+ coverage
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_ai.providers.errors import AuthenticationError, ProviderError
from kollabor_ai.providers.models import (
    OpenAIResponsesConfig,
    ProviderType,
    TextContent,
    ToolUseContent,
    UnifiedResponse,
)
from kollabor_ai.providers.openai_responses_provider import (
    OpenAIResponsesProvider,
)


@pytest.fixture
def provider_config():
    """Create a valid OpenAI Responses provider config."""
    return OpenAIResponsesConfig(
        provider=ProviderType.OPENAI_RESPONSES,
        api_key="sk-test-key-12345",
        model="gpt-5.4",
    )


@pytest.fixture
def sample_messages():
    """Create sample messages for testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello, how are you?"},
    ]


@pytest.fixture
def sample_tools():
    """Create sample tool definitions."""
    return [
        {
            "name": "get_weather",
            "description": "Get the current weather",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"}
                },
                "required": ["location"],
            },
        }
    ]


class TestOpenAIResponsesProviderInit:
    """Test provider initialization and configuration."""

    def test_init(self, provider_config):
        """Test provider initialization."""
        provider = OpenAIResponsesProvider(provider_config)

        assert provider.provider_type == ProviderType.OPENAI_RESPONSES
        assert provider.model == "gpt-5.4"
        assert not provider.is_initialized
        assert provider.active_requests == 0

    def test_validate_config_valid(self, provider_config):
        """Test config validation with valid config."""
        provider = OpenAIResponsesProvider(provider_config)
        # Should not raise
        provider.validate_config(provider_config)

    def test_validate_config_missing_api_key(self):
        """Test config validation with missing API key."""
        with pytest.raises(ValueError, match="api_key"):
            config = OpenAIResponsesConfig(
                provider=ProviderType.OPENAI_RESPONSES,
                api_key="",  # Empty key
                model="gpt-5.4",
            )
            provider = OpenAIResponsesProvider(config)
            provider.validate_config(config)


class TestOpenAIResponsesProviderInitialize:
    """Test provider initialization."""

    @pytest.mark.asyncio
    async def test_initialize_success(self, provider_config):
        """Test successful provider initialization."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            assert provider.is_initialized
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, provider_config):
        """Test that initialize can be called multiple times."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()
            await provider.initialize()  # Second call

            # Should only create client once
            assert mock_client_class.call_count == 1

    @pytest.mark.asyncio
    async def test_shutdown(self, provider_config):
        """Test provider shutdown."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.aclose = AsyncMock()
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()
            await provider.shutdown()

            assert provider.is_shutdown
            mock_client.aclose.assert_called_once()


class TestOpenAIResponsesProviderCall:
    """Test non-streaming API calls."""

    @pytest.mark.asyncio
    async def test_call_simple_message(self, provider_config, sample_messages):
        """Test simple message call."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "resp_001",
            "object": "response",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello! I'm doing well."}],
                }
            ],
            "usage": {"input_tokens": 15, "output_tokens": 10},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            response = await provider.call(sample_messages)

            assert isinstance(response, UnifiedResponse)
            assert len(response.content) == 1
            assert isinstance(response.content[0], TextContent)
            assert "Hello!" in response.content[0].text
            assert response.usage.prompt_tokens == 15
            assert response.usage.completion_tokens == 10

    @pytest.mark.asyncio
    async def test_call_with_system_message(self, provider_config):
        """Test that system message is extracted to instructions parameter."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "resp_001",
            "output": [
                {"type": "message", "content": [{"type": "text", "text": "Hi!"}]}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            await provider.call(messages)

            # Verify the request payload
            call_args = mock_client.post.call_args
            request_payload = call_args[1]["json"]

            # System message should be in 'instructions' field
            assert "instructions" in request_payload
            assert request_payload["instructions"] == "You are a helpful assistant"

            # System message should NOT be in input array
            assert all(msg.get("role") != "system" for msg in request_payload["input"])

    @pytest.mark.asyncio
    async def test_call_with_tools(
        self, provider_config, sample_messages, sample_tools
    ):
        """Test call with tool definitions."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "resp_001",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_001",
                    "function": "get_weather",
                    "arguments": '{"location": "NYC"}',
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 15},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            response = await provider.call(sample_messages, tools=sample_tools)

            # Verify tools were sent in request
            call_args = mock_client.post.call_args
            request_payload = call_args[1]["json"]
            assert "tools" in request_payload

            # Verify tool call in response
            assert len(response.content) == 1
            assert isinstance(response.content[0], ToolUseContent)
            assert response.content[0].name == "get_weather"

    @pytest.mark.asyncio
    async def test_call_with_previous_response_id(
        self, provider_config, sample_messages
    ):
        """Test call with previous_response_id for state chaining."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "resp_002",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "text", "text": "Continued conversation"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            await provider.call(sample_messages, previous_response_id="resp_001")

            # Verify previous_response_id was sent
            call_args = mock_client.post.call_args
            request_payload = call_args[1]["json"]
            assert request_payload.get("previous_response_id") == "resp_001"

    @pytest.mark.asyncio
    async def test_call_api_error(self, provider_config, sample_messages):
        """Test handling of API errors."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "error": {"message": "Invalid API key", "type": "authentication_error"}
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            with pytest.raises(AuthenticationError):
                await provider.call(sample_messages)

    @pytest.mark.asyncio
    async def test_call_without_initialize_raises(
        self, provider_config, sample_messages
    ):
        """Test that calling without initialization raises error."""
        provider = OpenAIResponsesProvider(provider_config)

        with pytest.raises(ProviderError, match="not initialized"):
            await provider.call(sample_messages)


class TestOpenAIResponsesProviderStream:
    """Test streaming API calls."""

    @pytest.mark.asyncio
    async def test_stream_simple(self, provider_config, sample_messages):
        """Test simple streaming response."""

        async def mock_stream():
            """Mock SSE stream."""
            chunks = [
                b"event: response.output_item.added\n",
                b'data: {"type": "message", "content": [{"type": "text", "text": "Hello"}]}\n\n',
                b"event: response.output_item.done\n",
                b'data: {"type": "message", "content": [{"type": "text", "text": " world!"}]}\n\n',
                b"event: response.done\n",
                b'data: {"id": "resp_001", "usage": {"input_tokens": 10, "output_tokens": 5}}\n\n',
            ]
            for chunk in chunks:
                yield chunk

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            stream_context = AsyncMock()
            stream_context.__aenter__.return_value = mock_response
            stream_context.__aexit__.return_value = None
            mock_client.stream = MagicMock(return_value=stream_context)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            chunks = []
            async for chunk in provider.stream(sample_messages):
                chunks.append(chunk)

            assert len(chunks) > 0
            assert any(c.is_final for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_with_tool_calls(
        self, provider_config, sample_messages, sample_tools
    ):
        """Test streaming with tool calls."""

        async def mock_stream():
            """Mock SSE stream with tool calls."""
            chunks = [
                b"event: response.output_item.added\n",
                b'data: {"type": "function_call", "call_id": "call_001", '
                b'"function": "get_weather", "arguments": '
                b'"{\\"location\\": \\"\\"}"}\n\n',
                b"event: response.done\n",
                b'data: {"id": "resp_001", "usage": '
                b'{"input_tokens": 10, "output_tokens": 5}}\n\n',
            ]
            for chunk in chunks:
                yield chunk

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            stream_context = AsyncMock()
            stream_context.__aenter__.return_value = mock_response
            stream_context.__aexit__.return_value = None
            mock_client.stream = MagicMock(return_value=stream_context)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            chunks = []
            async for chunk in provider.stream(sample_messages, tools=sample_tools):
                chunks.append(chunk)

            # Should have tool call delta
            tool_chunks = [c for c in chunks if c.delta.type == "tool_call_delta"]
            assert len(tool_chunks) > 0
            assert tool_chunks[0].delta.tool_name == "get_weather"

    @pytest.mark.asyncio
    async def test_stream_with_reasoning(self, provider_config, sample_messages):
        """Test streaming with reasoning/thinking items."""

        async def mock_stream():
            """Mock SSE stream with reasoning."""
            chunks = [
                b"event: response.output_item.added\n",
                b'data: {"type": "reasoning", "content": [{"type": "text", "text": "Let me think..."}]}\n\n',
                b"event: response.done\n",
                b'data: {"id": "resp_001", "usage": {"input_tokens": 10, "output_tokens": 5}}\n\n',
            ]
            for chunk in chunks:
                yield chunk

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            stream_context = AsyncMock()
            stream_context.__aenter__.return_value = mock_response
            stream_context.__aexit__.return_value = None
            mock_client.stream = MagicMock(return_value=stream_context)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            chunks = []
            async for chunk in provider.stream(sample_messages):
                chunks.append(chunk)

            # Should have thinking delta
            thinking_chunks = [c for c in chunks if c.delta.type == "thinking"]
            assert len(thinking_chunks) > 0

    @pytest.mark.asyncio
    async def test_stream_error_handling(self, provider_config, sample_messages):
        """Test error handling during streaming."""

        async def mock_stream():
            """Mock SSE stream that raises an error."""
            yield b"event: response.output_item.added\n"
            raise Exception("Connection lost")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_bytes = mock_stream
        mock_response.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIResponsesProvider(provider_config)
            await provider.initialize()

            with pytest.raises(ProviderError):
                async for _ in provider.stream(sample_messages):
                    pass


class TestOpenAIResponsesProviderFormatToolResult:
    """Test tool result formatting."""

    def test_format_tool_result(self, provider_config):
        """Test formatting tool result for Responses API."""
        provider = OpenAIResponsesProvider(provider_config)

        tool_result = provider._format_tool_result(
            tool_call_id="call_001", result="Weather is sunny, 72°F"
        )

        assert tool_result["type"] == "function_call_output"
        assert tool_result["call_id"] == "call_001"
        assert tool_result["output"] == "Weather is sunny, 72°F"

    def test_format_tool_result_with_dict(self, provider_config):
        """Test formatting tool result with dict output."""
        provider = OpenAIResponsesProvider(provider_config)

        result = {"temp": 72, "condition": "sunny"}
        tool_result = provider._format_tool_result(
            tool_call_id="call_001", result=result
        )

        # Should JSON-serialize the dict
        import json

        assert json.loads(tool_result["output"]) == result


class TestOpenAIResponsesProviderPrepareRequest:
    """Test request preparation."""

    def test_prepare_request_basic(self, provider_config, sample_messages):
        """Test basic request preparation."""
        provider = OpenAIResponsesProvider(provider_config)

        request = provider._prepare_request(
            messages=sample_messages, tools=None, stream=False
        )

        assert request["model"] == "gpt-5.4"
        assert request["stream"] is False
        assert "input" in request
        assert isinstance(request["input"], list)

    def test_prepare_request_with_system_message(self, provider_config):
        """Test that system message is extracted to instructions."""
        messages = [
            {"role": "system", "content": "System instructions"},
            {"role": "user", "content": "Hello"},
        ]

        provider = OpenAIResponsesProvider(provider_config)
        request = provider._prepare_request(messages, tools=None, stream=False)

        assert request["instructions"] == "System instructions"
        # System message should not be in input
        assert all(m.get("role") != "system" for m in request["input"])

    def test_prepare_request_with_temperature(self, provider_config, sample_messages):
        """Test request with custom temperature."""
        provider = OpenAIResponsesProvider(provider_config)

        request = provider._prepare_request(
            messages=sample_messages, tools=None, stream=False, temperature=0.5
        )

        assert request["temperature"] == 0.5

    def test_prepare_request_with_max_tokens(self, provider_config, sample_messages):
        """Test request with max_tokens."""
        provider = OpenAIResponsesProvider(provider_config)

        request = provider._prepare_request(
            messages=sample_messages, tools=None, stream=False, max_tokens=1000
        )

        assert request["max_tokens"] == 1000

    def test_prepare_request_string_input(self, provider_config):
        """Test request with simple string input (not messages array)."""
        provider = OpenAIResponsesProvider(provider_config)

        # If only one user message, can use string input
        messages = [{"role": "user", "content": "Hello"}]
        request = provider._prepare_request(messages, tools=None, stream=False)

        # For Responses API, single user message can be a string
        # This is an optimization for simple prompts
        assert "input" in request
