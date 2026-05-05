"""
Unit tests for Gemini provider implementation.

Tests for:
- Provider initialization and configuration
- Request preparation (messages -> contents, tools -> functionDeclarations)
- Non-streaming and streaming API calls
- Tool result formatting (functionResponse)
- Error handling

Target: 75%+ coverage
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from kollabor_ai.providers.gemini_provider import GeminiProvider
from kollabor_ai.providers.models import (
    GeminiConfig,
    ProviderType,
    TextContent,
    ToolUseContent,
)


@pytest.fixture
def provider_config():
    """Create a Gemini provider config for testing."""
    return GeminiConfig(
        provider=ProviderType.GEMINI,
        api_key="example-api-key",
        model="gemini-2.0-flash",
        temperature=0.7,
        max_tokens=4096,
    )


@pytest.fixture
def sample_messages():
    """Sample messages for testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "What is the weather in NYC?"},
    ]


@pytest.fixture
def sample_tools():
    """Sample tools in OpenAI format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"},
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["location"],
                },
            },
        }
    ]


class TestGeminiProviderInit:
    """Test provider initialization."""

    def test_init(self, provider_config):
        """Test provider initialization."""
        provider = GeminiProvider(provider_config)

        assert provider.provider_type == ProviderType.GEMINI
        assert provider.model == "gemini-2.0-flash"
        assert not provider.is_initialized
        assert not provider.is_shutdown
        assert provider.active_requests == 0

    def test_config_validation(self, provider_config):
        """Test config validation."""
        provider = GeminiProvider(provider_config)
        provider.validate_config(provider_config)
        # Should not raise

    def test_config_validation_missing_api_key(self):
        """Test validation fails with missing API key."""
        with pytest.raises(ValueError, match="api_key"):
            config = GeminiConfig(
                provider=ProviderType.GEMINI,
                api_key="",  # Empty
                model="gemini-2.0-flash",
            )
            provider = GeminiProvider(config)
            provider.validate_config(config)


class TestGeminiProviderInitialize:
    """Test provider initialization."""

    @pytest.mark.asyncio
    async def test_initialize_success(self, provider_config):
        """Test successful initialization."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()

            assert provider.is_initialized
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, provider_config):
        """Test initialize can be called multiple times."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()
            await provider.initialize()  # Second call should be safe

            assert provider.is_initialized
            # Should only create client once
            assert mock_client_class.call_count == 1


class TestGeminiProviderPrepareRequest:
    """Test request preparation."""

    def test_prepare_request_basic(self, provider_config, sample_messages):
        """Test basic request preparation."""
        provider = GeminiProvider(provider_config)
        request = provider._prepare_request(sample_messages, tools=None)

        # Check structure
        assert "contents" in request
        assert "systemInstruction" in request
        assert "generationConfig" in request

        # Check system instruction extracted
        assert (
            request["systemInstruction"]["parts"][0]["text"]
            == "You are a helpful assistant"
        )

        # Check user message converted to contents
        assert len(request["contents"]) == 1
        assert request["contents"][0]["role"] == "user"
        assert (
            request["contents"][0]["parts"][0]["text"] == "What is the weather in NYC?"
        )

        # Check generation config
        assert request["generationConfig"]["temperature"] == 0.7
        assert request["generationConfig"]["maxOutputTokens"] == 4096

    def test_prepare_request_with_tools(
        self, provider_config, sample_messages, sample_tools
    ):
        """Test request preparation with tools."""
        provider = GeminiProvider(provider_config)
        request = provider._prepare_request(sample_messages, tools=sample_tools)

        # Check tools are transformed
        assert "tools" in request
        assert len(request["tools"]) == 1
        assert "functionDeclarations" in request["tools"][0]
        assert request["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"

    def test_prepare_request_no_system_message(self, provider_config):
        """Test request preparation without system message."""
        messages = [{"role": "user", "content": "Hello"}]
        provider = GeminiProvider(provider_config)
        request = provider._prepare_request(messages, tools=None)

        # Should not have systemInstruction
        assert request.get("systemInstruction") is None

    def test_prepare_request_multi_turn(self, provider_config):
        """Test multi-turn conversation preparation."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        provider = GeminiProvider(provider_config)
        request = provider._prepare_request(messages, tools=None)

        # Check all messages converted
        assert len(request["contents"]) == 3
        assert request["contents"][0]["role"] == "user"
        assert request["contents"][1]["role"] == "model"
        assert request["contents"][2]["role"] == "user"


class TestGeminiProviderCall:
    """Test non-streaming API calls."""

    @pytest.mark.asyncio
    async def test_call_success(self, provider_config, sample_messages):
        """Test successful non-streaming call."""
        mock_response_data = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Hello! How can I help you today?"}],
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

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()

            response = await provider.call(sample_messages)

            assert response.provider == ProviderType.GEMINI
            assert response.model == "gemini-2.0-flash"
            assert len(response.content) == 1
            assert isinstance(response.content[0], TextContent)
            assert response.content[0].text == "Hello! How can I help you today?"
            assert response.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_call_with_tools(
        self, provider_config, sample_messages, sample_tools
    ):
        """Test call with tool definitions."""
        mock_response_data = {
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
                "promptTokenCount": 20,
                "candidatesTokenCount": 10,
                "totalTokenCount": 30,
            },
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()

            response = await provider.call(sample_messages, tools=sample_tools)

            # Should have both text and tool use
            assert len(response.content) == 2
            assert isinstance(response.content[0], TextContent)
            assert isinstance(response.content[1], ToolUseContent)
            assert response.content[1].name == "get_weather"

    @pytest.mark.asyncio
    async def test_call_error(self, provider_config, sample_messages):
        """Test call with API error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("API Error"))
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()

            with pytest.raises(Exception, match="API Error"):
                await provider.call(sample_messages)


class TestGeminiProviderStream:
    """Test streaming API calls."""

    @pytest.mark.asyncio
    async def test_stream_text(self, provider_config, sample_messages):
        """Test streaming text response."""
        # Mock SSE chunks (aiter_lines returns str, not bytes)
        chunks = [
            'data: {"candidates": [{"content": {"role": "model", '
            '"parts": [{"text": "Hello"}]}, "finishReason": null}]}\n',
            'data: {"candidates": [{"content": {"role": "model", '
            '"parts": [{"text": " world"}]}, "finishReason": null}]}\n',
            'data: {"candidates": [{"content": {"role": "model", "parts": []}, '
            '"finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, '
            '"candidatesTokenCount": 5, "totalTokenCount": 15}}\n',
        ]

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()

            # Mock async iteration over lines
            async def mock_aiter_lines():
                for chunk in chunks:
                    yield chunk

            mock_response.aiter_lines = mock_aiter_lines
            mock_response.raise_for_status = Mock()

            # Create async context manager mock
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def mock_stream(*args, **kwargs):
                yield mock_response

            mock_client.stream = mock_stream
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()

            responses = []
            async for response in provider.stream(sample_messages):
                responses.append(response)

            # Should have multiple text deltas plus final
            assert len(responses) >= 2
            assert any(
                r.delta.content == "Hello"
                for r in responses
                if hasattr(r.delta, "content")
            )
            assert any(r.is_final for r in responses)

    @pytest.mark.asyncio
    async def test_stream_tool_call(
        self, provider_config, sample_messages, sample_tools
    ):
        """Test streaming with tool call."""
        chunks = [
            'data: {"candidates": [{"content": {"role": "model", "parts": '
            '[{"functionCall": {"name": "get_weather", '
            '"args": {"location": "NYC"}}}]}, "finishReason": null}]}\n',
        ]

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()

            async def mock_aiter_lines():
                for chunk in chunks:
                    yield chunk

            mock_response.aiter_lines = mock_aiter_lines
            mock_response.raise_for_status = Mock()

            # Create async context manager mock
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def mock_stream(*args, **kwargs):
                yield mock_response

            mock_client.stream = mock_stream
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()

            responses = []
            async for response in provider.stream(sample_messages, tools=sample_tools):
                responses.append(response)

            assert len(responses) >= 1


class TestGeminiProviderToolResult:
    """Test tool result formatting."""

    def test_format_tool_result(self, provider_config):
        """Test formatting tool result for Gemini."""
        provider = GeminiProvider(provider_config)

        tool_result = provider._format_tool_result(
            tool_call_id="call_123",
            tool_name="get_weather",
            result='{"temp": 72, "condition": "sunny"}',
        )

        assert tool_result["role"] == "user"
        assert len(tool_result["parts"]) == 1
        assert "functionResponse" in tool_result["parts"][0]
        assert tool_result["parts"][0]["functionResponse"]["name"] == "get_weather"
        assert tool_result["parts"][0]["functionResponse"]["response"]["temp"] == 72


class TestGeminiProviderShutdown:
    """Test provider shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown(self, provider_config):
        """Test clean shutdown."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.aclose = AsyncMock()
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()
            await provider.shutdown()

            assert provider.is_shutdown
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, provider_config):
        """Test shutdown can be called multiple times."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.aclose = AsyncMock()
            mock_client_class.return_value = mock_client

            provider = GeminiProvider(provider_config)
            await provider.initialize()
            await provider.shutdown()
            await provider.shutdown()  # Second call safe

            assert provider.is_shutdown
            # Should only close once
            assert mock_client.aclose.call_count == 1
