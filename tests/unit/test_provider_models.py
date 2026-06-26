"""
Unit tests for provider models.

Tests Pydantic model validation, validators, and edge cases.
Target: 80%+ coverage
"""

import pytest
from pydantic import ValidationError

from kollabor_ai.providers.models import (
    AnthropicConfig,
    AzureOpenAIConfig,
    OpenAIConfig,
    ProviderConfig,
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


class TestProviderType:
    """Test ProviderType enum."""

    def test_provider_type_values(self):
        """Test ProviderType enum has correct values."""
        assert ProviderType.OPENAI == "openai"
        assert ProviderType.ANTHROPIC == "anthropic"
        assert ProviderType.AZURE_OPENAI == "azure_openai"


class TestProviderConfig:
    """Test base ProviderConfig model."""

    def test_minimal_config(self):
        """Test minimal valid configuration."""
        config = ProviderConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key-123",
            model="gpt-4",
        )
        assert config.provider == ProviderType.OPENAI
        assert config.api_key == "test-key-123"
        assert config.model == "gpt-4"
        assert config.temperature == 0.7
        # Output reserve: kept modest so it can't eat the context window. The
        # budget guard scales the effective request against context_window.
        assert config.max_tokens == 16384
        # Conservative fallback window; the real per-model value comes from the
        # registry at config-creation time.
        assert config.context_window == 200000

    def test_custom_temperature(self):
        """Test custom temperature in valid range."""
        config = ProviderConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key-123",
            model="gpt-4",
            temperature=1.5,
        )
        assert config.temperature == 1.5

    def test_temperature_out_of_range_high(self):
        """Test temperature above maximum raises error."""
        with pytest.raises(ValidationError) as exc_info:
            ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key="test-key-123",
                model="gpt-4",
                temperature=2.5,
            )
        assert "temperature" in str(exc_info.value).lower()

    def test_temperature_out_of_range_low(self):
        """Test temperature below minimum raises error."""
        with pytest.raises(ValidationError) as exc_info:
            ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key="test-key-123",
                model="gpt-4",
                temperature=-0.1,
            )
        assert "temperature" in str(exc_info.value).lower()

    def test_max_tokens_validation(self):
        """Test max_tokens must be at least 1."""
        with pytest.raises(ValidationError):
            ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key="test-key-123",
                model="gpt-4",
                max_tokens=0,
            )

    def test_top_p_in_range(self):
        """Test top_p in valid range."""
        config = ProviderConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key-123",
            model="gpt-4",
            top_p=0.9,
        )
        assert config.top_p == 0.9

    def test_top_p_out_of_range(self):
        """Test top_p out of range raises error."""
        with pytest.raises(ValidationError):
            ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key="test-key-123",
                model="gpt-4",
                top_p=1.5,
            )

    def test_base_url_https_required(self):
        """Test base_url must be HTTPS (except localhost)."""
        with pytest.raises(ValidationError) as exc_info:
            ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key="test-key-123",
                model="gpt-4",
                base_url="http://api.example.com",
            )
        assert "https" in str(exc_info.value).lower()

    def test_base_url_localhost_allowed(self):
        """Test localhost URLs can use HTTP."""
        config = ProviderConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key-123",
            model="gpt-4",
            base_url="http://localhost:8000",
        )
        assert config.base_url == "http://localhost:8000"

    def test_base_url_127_0_0_1_allowed(self):
        """Test 127.0.0.1 URLs can use HTTP."""
        config = ProviderConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key-123",
            model="gpt-4",
            base_url="http://127.0.0.1:8080",
        )
        assert config.base_url == "http://127.0.0.1:8080"

    def test_timeout_zero_is_allowed(self):
        """Test timeout may be zero to disable timeout handling."""
        config = ProviderConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key-123",
            model="gpt-4",
            timeout=0,
        )
        assert config.timeout == 0

    def test_empty_api_key_raises_error(self):
        """Test empty API key raises validation error."""
        with pytest.raises(ValidationError):
            ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key="",
                model="gpt-4",
            )

    def test_empty_model_raises_error(self):
        """Test empty model raises validation error."""
        with pytest.raises(ValidationError):
            ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key="test-key-123",
                model="",
            )


class TestOpenAIConfig:
    """Test OpenAI-specific configuration."""

    def test_valid_openai_config(self):
        """Test valid OpenAI configuration."""
        config = OpenAIConfig(
            api_key="sk-test-key-123",
            model="gpt-4",
        )
        assert config.provider == ProviderType.OPENAI
        assert config.api_key == "sk-test-key-123"

    def test_openai_api_key_format_sk(self):
        """Test OpenAI API key with sk- prefix."""
        config = OpenAIConfig(
            api_key="sk-example-openai-key",
            model="gpt-4",
        )
        assert config.api_key == "sk-example-openai-key"

    def test_openai_api_key_format_sk_proj(self):
        """Test OpenAI API key with sk-proj- prefix."""
        config = OpenAIConfig(
            api_key="sk-proj-example-project-key",
            model="gpt-4",
        )
        assert config.api_key == "sk-proj-example-project-key"

    def test_openai_api_key_non_prefixed_values_are_allowed(self):
        """Test OpenAI config allows non-prefixed API keys for oauth/custom auth."""
        config = OpenAIConfig(
            api_key="invalid-key-format",
            model="gpt-4",
        )
        assert config.api_key == "invalid-key-format"

    def test_openai_base_url_https(self):
        """Test OpenAI base URL must be HTTPS."""
        with pytest.raises(ValidationError) as exc_info:
            OpenAIConfig(
                api_key="sk-test-key-123",
                model="gpt-4",
                base_url="http://api.openai.com/v1",
            )
        assert "https" in str(exc_info.value).lower()

    def test_openai_organization_optional(self):
        """Test organization ID is optional."""
        config = OpenAIConfig(
            api_key="sk-test-key-123",
            model="gpt-4",
            organization="org-123",
        )
        assert config.organization == "org-123"


class TestAnthropicConfig:
    """Test Anthropic-specific configuration."""

    def test_valid_anthropic_config(self):
        """Test valid Anthropic configuration."""
        config = AnthropicConfig(
            api_key="sk-ant-test-key-123",
            model="claude-3-opus-20240229",
        )
        assert config.provider == ProviderType.ANTHROPIC
        assert config.api_key == "sk-ant-test-key-123"

    def test_anthropic_api_key_non_prefixed_values_are_allowed(self):
        """Test Anthropic config allows non-prefixed or alternate auth tokens."""
        config = AnthropicConfig(
            api_key="sk-wrong-format",
            model="claude-3-opus-20240229",
        )
        assert config.api_key == "sk-wrong-format"

    def test_anthropic_api_version_default(self):
        """Test default API version."""
        config = AnthropicConfig(
            api_key="sk-ant-test-key-123",
            model="claude-3-opus-20240229",
        )
        assert config.api_version == "2023-06-01"

    def test_anthropic_api_version_custom(self):
        """Test custom API version."""
        config = AnthropicConfig(
            api_key="sk-ant-test-key-123",
            model="claude-3-opus-20240229",
            api_version="2024-01-01",
        )
        assert config.api_version == "2024-01-01"

    def test_anthropic_api_version_invalid_format(self):
        """Test invalid API version format raises error."""
        with pytest.raises(ValidationError) as exc_info:
            AnthropicConfig(
                api_key="sk-ant-test-key-123",
                model="claude-3-opus-20240229",
                api_version="invalid",
            )
        assert (
            "YYYY-MM-DD" in str(exc_info.value)
            or "format" in str(exc_info.value).lower()
        )

    def test_max_retries_in_range(self):
        """Test max_retries in valid range."""
        config = AnthropicConfig(
            api_key="sk-ant-test-key-123",
            model="claude-3-opus-20240229",
            max_retries=3,
        )
        assert config.max_retries == 3

    def test_max_retries_out_of_range_high(self):
        """Test max_retries above maximum raises error."""
        with pytest.raises(ValidationError):
            AnthropicConfig(
                api_key="sk-ant-test-key-123",
                model="claude-3-opus-20240229",
                max_retries=6,
            )


class TestAzureOpenAIConfig:
    """Test Azure OpenAI-specific configuration."""

    def test_valid_azure_config(self):
        """Test valid Azure OpenAI configuration."""
        config = AzureOpenAIConfig(
            api_key="azure-example-api-key-value",
            model="gpt-4",
            azure_endpoint="https://test.openai.azure.com",
        )
        assert config.provider == ProviderType.AZURE_OPENAI
        assert config.azure_endpoint == "https://test.openai.azure.com"

    def test_azure_endpoint_http_rejected(self):
        """Test Azure endpoint must be HTTPS."""
        with pytest.raises(ValidationError) as exc_info:
            AzureOpenAIConfig(
                api_key="azure-example-api-key-value",
                model="gpt-4",
                azure_endpoint="http://test.openai.azure.com",
            )
        assert "https" in str(exc_info.value).lower()

    def test_azure_api_key_too_short(self):
        """Test Azure API key too short raises error."""
        with pytest.raises(ValidationError) as exc_info:
            AzureOpenAIConfig(
                api_key="short",
                model="gpt-4",
                azure_endpoint="https://test.openai.azure.com",
            )
        assert "too short" in str(exc_info.value).lower()

    def test_azure_deployment_id_optional(self):
        """Test deployment ID is optional."""
        config = AzureOpenAIConfig(
            api_key="azure-example-api-key-value",
            model="gpt-4",
            azure_endpoint="https://test.openai.azure.com",
            deployment_id="gpt-4-deployment",
        )
        assert config.deployment_id == "gpt-4-deployment"

    def test_azure_api_version_default(self):
        """Test default API version."""
        config = AzureOpenAIConfig(
            api_key="azure-example-api-key-value",
            model="gpt-4",
            azure_endpoint="https://test.openai.azure.com",
        )
        assert config.api_version == "2024-02-15-preview"


class TestStreamingDelta:
    """Test streaming response delta models."""

    def test_text_delta(self):
        """Test text delta."""
        delta = TextDelta(content="Hello, world!")
        assert delta.type == "text"
        assert delta.content == "Hello, world!"

    def test_tool_call_delta(self):
        """Test tool call delta."""
        delta = ToolCallDelta(
            tool_call_id="call_123",
            tool_name="search",
            tool_arguments_delta='{"query": "test"}',
        )
        assert delta.type == "tool_call_delta"
        assert delta.tool_call_id == "call_123"
        assert delta.tool_name == "search"

    def test_thinking_delta(self):
        """Test thinking delta."""
        delta = ThinkingDelta(content="Let me think about this...")
        assert delta.type == "thinking"
        assert delta.content == "Let me think about this..."


class TestContentBlock:
    """Test content block models."""

    def test_text_content(self):
        """Test text content block."""
        content = TextContent(text="Hello, world!")
        assert content.type == "text"
        assert content.text == "Hello, world!"

    def test_tool_use_content(self):
        """Test tool use content block."""
        content = ToolUseContent(
            id="tool_123",
            name="search",
            input={"query": "test"},
        )
        assert content.type == "tool_use"
        assert content.id == "tool_123"
        assert content.name == "search"
        assert content.input == {"query": "test"}

    def test_tool_result_content(self):
        """Test tool result content block."""
        content = ToolResultContent(
            tool_use_id="tool_123",
            content="Result: success",
            is_error=False,
        )
        assert content.type == "tool_result"
        assert content.tool_use_id == "tool_123"
        assert content.content == "Result: success"
        assert content.is_error is False

    def test_thinking_content(self):
        """Test thinking content block."""
        content = ThinkingContent(thinking="Analyzing the request...")
        assert content.type == "thinking"
        assert content.thinking == "Analyzing the request..."


class TestUsageInfo:
    """Test usage information model."""

    def test_usage_info(self):
        """Test usage info."""
        usage = UsageInfo(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150

    def test_usage_info_negative_tokens(self):
        """Test negative tokens raise error."""
        with pytest.raises(ValidationError):
            UsageInfo(
                prompt_tokens=-1,
                completion_tokens=50,
                total_tokens=49,
            )


class TestStreamingResponse:
    """Test streaming response model."""

    def test_streaming_response_with_text_delta(self):
        """Test streaming response with text delta."""
        response = StreamingResponse(
            delta=TextDelta(content="Hello"),
        )
        assert response.delta.type == "text"
        assert response.is_final is False

    def test_streaming_response_final_usage_is_optional(self):
        """Test final streaming responses may omit usage data."""
        response = StreamingResponse(
            delta=TextDelta(content="Hello"),
            is_final=True,
        )
        assert response.is_final is True
        assert response.usage is None

    def test_streaming_response_final_with_usage(self):
        """Test final response with usage is valid."""
        response = StreamingResponse(
            delta=TextDelta(content="Hello"),
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            is_final=True,
        )
        assert response.is_final is True
        assert response.usage is not None


class TestUnifiedResponse:
    """Test unified response model."""

    def test_unified_response_with_text(self):
        """Test unified response with text content."""
        response = UnifiedResponse(
            content=[TextContent(text="Hello, world!")],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            model="gpt-4",
            provider=ProviderType.OPENAI,
        )
        assert len(response.content) == 1
        assert response.get_text_content() == "Hello, world!"

    def test_unified_response_empty_content_raises_error(self):
        """Test empty content raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            UnifiedResponse(
                content=[],
                usage=UsageInfo(
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                ),
                model="gpt-4",
                provider=ProviderType.OPENAI,
            )
        assert (
            "content" in str(exc_info.value).lower()
            or "at least one" in str(exc_info.value).lower()
        )

    def test_get_text_content_multiple_blocks(self):
        """Test extracting text from multiple text blocks."""
        response = UnifiedResponse(
            content=[
                TextContent(text="Hello, "),
                TextContent(text="world!"),
            ],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            model="gpt-4",
            provider=ProviderType.OPENAI,
        )
        assert response.get_text_content() == "Hello, world!"

    def test_get_tool_uses(self):
        """Test extracting tool uses from response."""
        response = UnifiedResponse(
            content=[
                TextContent(text="I'll search for you."),
                ToolUseContent(
                    id="tool_123",
                    name="search",
                    input={"query": "test"},
                ),
            ],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            model="claude-3-opus-20240229",
            provider=ProviderType.ANTHROPIC,
        )
        tool_uses = response.get_tool_uses()
        assert len(tool_uses) == 1
        assert tool_uses[0].name == "search"

    def test_get_thinking_content(self):
        """Test extracting thinking content."""
        response = UnifiedResponse(
            content=[
                ThinkingContent(thinking="Analyzing..."),
                TextContent(text="Result: 42"),
            ],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            model="claude-3-opus-20240229",
            provider=ProviderType.ANTHROPIC,
        )
        thinking = response.get_thinking_content()
        assert thinking == "Analyzing..."

    def test_get_thinking_content_none(self):
        """Test get_thinking_content returns None when not present."""
        response = UnifiedResponse(
            content=[TextContent(text="Hello")],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            model="gpt-4",
            provider=ProviderType.OPENAI,
        )
        thinking = response.get_thinking_content()
        assert thinking is None

    def test_unified_response_with_finish_reason(self):
        """Test unified response with finish reason."""
        response = UnifiedResponse(
            content=[TextContent(text="Hello")],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            model="gpt-4",
            provider=ProviderType.OPENAI,
            finish_reason="stop",
        )
        assert response.finish_reason == "stop"

    def test_unified_response_with_raw_response(self):
        """Test unified response with raw API response."""
        raw = {"id": "chatcmpl-123", "object": "chat.completion"}
        response = UnifiedResponse(
            content=[TextContent(text="Hello")],
            usage=UsageInfo(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
            model="gpt-4",
            provider=ProviderType.OPENAI,
            raw_response=raw,
        )
        assert response.raw_response == raw
