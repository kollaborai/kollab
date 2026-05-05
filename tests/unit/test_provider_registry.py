"""
Unit tests for provider registry and base provider.

Tests ProviderRegistry registration, singleton behavior, provider creation,
and LLMProvider base class lifecycle management.
Target: 75%+ coverage
"""

import asyncio
from unittest.mock import Mock

import pytest

from kollabor_ai.providers.base import LLMProvider
from kollabor_ai.providers.errors import ProviderError
from kollabor_ai.providers.models import (
    AnthropicConfig,
    OpenAIConfig,
    ProviderConfig,
    ProviderType,
)
from kollabor_ai.providers.registry import (
    ProviderRegistry,
    create_config_from_profile,
    detect_provider_from_profile,
    register_provider,
)

# ===== Mock Provider Implementation for Testing =====


class MockOpenAIProvider(LLMProvider):
    """Mock OpenAI provider for testing."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # Don't set _initialized here - let initialize() method do it

    async def initialize(self) -> None:
        """Mock initialization."""
        self._initialized = True

    async def call(self, messages, tools=None, **kwargs):
        """Mock call implementation."""
        self._validate_initialized()
        self._validate_not_shutdown()
        from kollabor_ai.providers.models import (
            TextContent,
            UnifiedResponse,
            UsageInfo,
        )

        return UnifiedResponse(
            content=[TextContent(text="Mock response")],
            usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model=self.model,
            provider=self.provider_type,
        )

    async def stream(self, messages, tools=None, **kwargs):
        """Mock stream implementation."""
        self._validate_initialized()
        self._validate_not_shutdown()
        from kollabor_ai.providers.models import (
            StreamingResponse,
            TextDelta,
            UsageInfo,
        )

        yield StreamingResponse(delta=TextDelta(content="Hello"))
        yield StreamingResponse(
            delta=TextDelta(content=" world"),
            usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            is_final=True,
        )

    def validate_config(self, config: ProviderConfig) -> None:
        """Mock validation."""
        if not config.api_key:
            raise ValueError("API key required")


class MockAnthropicProvider(LLMProvider):
    """Mock Anthropic provider for testing."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # Don't set _initialized here - let initialize() method do it

    async def initialize(self) -> None:
        """Mock initialization."""
        self._initialized = True

    async def call(self, messages, tools=None, **kwargs):
        """Mock call implementation."""
        self._validate_initialized()
        from kollabor_ai.providers.models import (
            TextContent,
            UnifiedResponse,
            UsageInfo,
        )

        return UnifiedResponse(
            content=[TextContent(text="Mock Anthropic response")],
            usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model=self.model,
            provider=self.provider_type,
        )

    async def stream(self, messages, tools=None, **kwargs):
        """Mock stream implementation."""
        from kollabor_ai.providers.models import StreamingResponse, TextDelta

        yield StreamingResponse(delta=TextDelta(content="Test"))

    def validate_config(self, config: ProviderConfig) -> None:
        """Mock validation."""
        if not config.api_key:
            raise ValueError("API key required")


# Register mock providers
ProviderRegistry.register(ProviderType.OPENAI)(MockOpenAIProvider)
ProviderRegistry.register(ProviderType.ANTHROPIC)(MockAnthropicProvider)


# ===== Provider Registration Tests =====


class TestProviderRegistration:
    """Test provider registration functionality."""

    def test_register_provider_decorator(self):
        """Test @register_provider decorator registers provider."""
        # Mock providers should be auto-registered at module level
        assert ProviderRegistry.is_registered(ProviderType.OPENAI)
        assert ProviderRegistry.is_registered(ProviderType.ANTHROPIC)

    def test_register_provider_returns_class(self):
        """Test decorator returns the original class."""

        # Use AZURE_OPENAI to avoid overwriting mock providers
        @register_provider(ProviderType.AZURE_OPENAI)
        class TestProvider(LLMProvider):
            async def initialize(self):
                pass

            async def call(self, messages, tools=None, **kwargs):
                pass

            async def stream(self, messages, tools=None, **kwargs):
                pass

            def validate_config(self, config):
                pass

        assert TestProvider is not None
        assert TestProvider.__name__ == "TestProvider"
        # Clean up the test registration
        ProviderRegistry._providers.pop(ProviderType.AZURE_OPENAI, None)

    def test_list_providers(self):
        """Test listing registered providers."""
        providers = ProviderRegistry.list_providers()
        assert ProviderType.OPENAI in providers
        assert ProviderType.ANTHROPIC in providers
        assert len(providers) >= 2

    def test_is_registered(self):
        """Test checking if provider is registered."""
        assert ProviderRegistry.is_registered(ProviderType.OPENAI) is True
        assert ProviderRegistry.is_registered(ProviderType.ANTHROPIC) is True
        assert ProviderRegistry.is_registered(ProviderType.AZURE_OPENAI) is False

    def test_get_provider_class(self):
        """Test getting provider class."""
        openai_class = ProviderRegistry.get_provider_class(ProviderType.OPENAI)
        assert openai_class is not None
        assert openai_class == MockOpenAIProvider

        anthropic_class = ProviderRegistry.get_provider_class(ProviderType.ANTHROPIC)
        assert anthropic_class is not None
        assert anthropic_class == MockAnthropicProvider

    def test_get_provider_class_not_registered(self):
        """Test getting unregistered provider class returns None."""
        azure_class = ProviderRegistry.get_provider_class(ProviderType.AZURE_OPENAI)
        assert azure_class is None


# ===== Provider Creation Tests =====


class TestProviderCreation:
    """Test provider instance creation."""

    @pytest.mark.asyncio
    async def test_create_provider_success(self):
        """Test creating a provider instance."""
        config = OpenAIConfig(
            api_key="sk-test-key-123",
            model="gpt-4",
        )

        provider = await ProviderRegistry.create_provider(config)

        assert provider is not None
        assert provider.provider_type == ProviderType.OPENAI
        assert provider.model == "gpt-4"
        assert provider.is_initialized is True

    @pytest.mark.asyncio
    async def test_create_provider_not_registered(self):
        """Test creating unregistered provider raises error."""
        from kollabor_ai.providers.models import AzureOpenAIConfig

        config = AzureOpenAIConfig(
            api_key="x" * 32,
            model="gpt-4",
            azure_endpoint="https://test.openai.azure.com",
        )

        with pytest.raises(ValueError) as exc_info:
            await ProviderRegistry.create_provider(config)

        assert "not registered" in str(exc_info.value).lower()
        assert "azure_openai" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_get_provider_reuses_matching_config_only(self):
        """Test get_provider reuses instances only for matching configs."""
        config1 = OpenAIConfig(
            api_key="sk-test-key-123",
            model="gpt-4",
        )
        config2 = OpenAIConfig(
            api_key="sk-different-key",
            model="gpt-3.5-turbo",
        )

        provider1 = await ProviderRegistry.get_provider(config1)
        provider1_again = await ProviderRegistry.get_provider(config1)
        provider2 = await ProviderRegistry.get_provider(config2)

        assert provider1 is provider1_again
        assert provider1 is not provider2
        assert provider1.model == "gpt-4"
        assert provider2.model == "gpt-3.5-turbo"

    @pytest.mark.asyncio
    async def test_create_provider_non_singleton(self):
        """Test create_provider always creates new instance."""
        config1 = OpenAIConfig(
            api_key="sk-test-key-123",
            model="gpt-4",
        )
        config2 = OpenAIConfig(
            api_key="sk-different-key",
            model="gpt-3.5-turbo",
        )

        provider1 = await ProviderRegistry.create_provider(config1)
        provider2 = await ProviderRegistry.create_provider(config2)

        # Should be different instances
        assert provider1 is not provider2
        assert provider1.model == "gpt-4"
        assert provider2.model == "gpt-3.5-turbo"


# ===== Provider Detection Tests =====


class TestProviderDetection:
    """Test provider type detection from profiles."""

    def test_detect_explicit_provider(self):
        """Test detection with explicit provider field."""
        profile = {"provider": "openai", "api_key": "sk-test", "model": "gpt-4"}
        provider = detect_provider_from_profile(profile)
        assert provider == ProviderType.OPENAI

    def test_detect_anthropic_api_key(self):
        """Test detection from Anthropic API key format."""
        profile = {"api_key": "sk-ant-test123", "model": "claude-3"}
        provider = detect_provider_from_profile(profile)
        assert provider == ProviderType.ANTHROPIC

    def test_detect_openai_api_key(self):
        """Test detection from OpenAI API key format."""
        profile = {"api_key": "sk-test123", "model": "gpt-4"}
        provider = detect_provider_from_profile(profile)
        assert provider == ProviderType.OPENAI

    def test_detect_azure_base_url(self):
        """Test detection from Azure base URL."""
        profile = {
            "api_key": "sk-test",
            "api_base": "https://test.azure.com",
            "model": "gpt-4",
        }
        provider = detect_provider_from_profile(profile)
        assert provider == ProviderType.AZURE_OPENAI

    def test_detect_anthropic_base_url(self):
        """Test detection from Anthropic base URL."""
        profile = {
            "api_key": "test-key",
            "api_base": "https://api.anthropic.com",
            "model": "claude-3",
        }
        provider = detect_provider_from_profile(profile)
        assert provider == ProviderType.ANTHROPIC

    def test_detect_default_anthropic(self):
        """Test default to Anthropic when cannot determine."""
        profile = {"api_key": "test-key", "model": "gpt-4"}
        provider = detect_provider_from_profile(profile)
        assert provider == ProviderType.ANTHROPIC

    def test_detect_invalid_provider_string(self):
        """Test invalid provider string raises error."""
        profile = {"provider": "invalid_provider", "api_key": "test"}
        with pytest.raises(ValueError) as exc_info:
            detect_provider_from_profile(profile)
        assert "unknown provider" in str(exc_info.value).lower()


# ===== Config Creation Tests =====


class TestConfigCreation:
    """Test creating provider configs from profiles."""

    def test_create_openai_config(self):
        """Test creating OpenAI config from profile."""
        profile = {
            "provider": "openai",
            "api_key": "sk-test-key-123",
            "model": "gpt-4",
            "temperature": 0.5,
            "max_tokens": 2048,
            "organization": "org-123",
        }

        config = create_config_from_profile(profile)

        assert isinstance(config, OpenAIConfig)
        assert config.provider == ProviderType.OPENAI
        assert config.api_key == "sk-test-key-123"
        assert config.model == "gpt-4"
        assert config.temperature == 0.5
        assert config.max_tokens == 2048
        assert config.organization == "org-123"

    def test_create_anthropic_config(self):
        """Test creating Anthropic config from profile."""
        profile = {
            "provider": "anthropic",
            "api_key": "sk-ant-test123",
            "model": "claude-3-opus-20240229",
            "max_retries": 3,
        }

        config = create_config_from_profile(profile)

        assert isinstance(config, AnthropicConfig)
        assert config.provider == ProviderType.ANTHROPIC
        assert config.api_key == "sk-ant-test123"
        assert config.max_retries == 3

    def test_create_config_missing_api_key(self):
        """Test missing API key raises error."""
        profile = {"provider": "openai", "model": "gpt-4"}

        with pytest.raises(ValueError) as exc_info:
            create_config_from_profile(profile)
        assert "api_key" in str(exc_info.value).lower()

    def test_create_config_auto_detect_provider(self):
        """Test auto-detect provider from API key."""
        profile = {
            "api_key": "sk-ant-test123",
            "model": "claude-3-opus-20240229",
        }

        config = create_config_from_profile(profile)

        assert isinstance(config, AnthropicConfig)
        assert config.provider == ProviderType.ANTHROPIC

    def test_create_config_with_base_url(self):
        """Test creating config with base URL."""
        profile = {
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4",
            "api_base": "https://api.openai.com/v1",
        }

        config = create_config_from_profile(profile)
        assert config.base_url == "https://api.openai.com/v1"

    def test_create_config_with_timeout(self):
        """Test creating config with timeout."""
        profile = {
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4",
            "timeout": 120.0,
        }

        config = create_config_from_profile(profile)
        assert config.timeout == 120.0


# ===== Base Provider Lifecycle Tests =====


class TestBaseProviderLifecycle:
    """Test LLMProvider base class lifecycle."""

    @pytest.mark.asyncio
    async def test_provider_initialization(self):
        """Test provider initialization."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)
        assert provider.is_initialized is False  # Not initialized yet

        await provider.initialize()
        assert provider.is_initialized is True
        assert provider.is_shutdown is False

    @pytest.mark.asyncio
    async def test_provider_shutdown(self):
        """Test provider shutdown."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = await ProviderRegistry.create_provider(config)

        await provider.shutdown()

        assert provider.is_shutdown is True

    @pytest.mark.asyncio
    async def test_provider_shutdown_idempotent(self):
        """Test shutdown is idempotent (safe to call multiple times)."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = await ProviderRegistry.create_provider(config)

        await provider.shutdown()
        await provider.shutdown()  # Should not raise

        assert provider.is_shutdown is True

    @pytest.mark.asyncio
    async def test_provider_get_metadata(self):
        """Test getting provider metadata."""
        config = OpenAIConfig(
            api_key="sk-test", model="gpt-4", base_url="https://api.openai.com/v1"
        )
        provider = MockOpenAIProvider(config)
        await provider.initialize()  # Initialize the provider

        metadata = provider.get_metadata()

        assert metadata["provider"] == "openai"
        assert metadata["model"] == "gpt-4"
        assert metadata["supports_streaming"] is True
        assert metadata["supports_tools"] is True
        assert metadata["initialized"] is True

    @pytest.mark.asyncio
    async def test_provider_repr(self):
        """Test provider string representation."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)

        repr_str = repr(provider)
        assert "MockOpenAIProvider" in repr_str
        assert "openai" in repr_str
        assert "gpt-4" in repr_str


# ===== Provider Call/Stream Tests =====


class TestProviderCalls:
    """Test provider call and stream methods."""

    @pytest.mark.asyncio
    async def test_provider_call(self):
        """Test making a non-streaming call."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = await ProviderRegistry.create_provider(config)

        response = await provider.call([{"role": "user", "content": "Hello"}])

        assert response is not None
        assert response.get_text_content() == "Mock response"
        assert response.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_provider_stream(self):
        """Test making a streaming call."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = await ProviderRegistry.create_provider(config)

        chunks = []
        async for chunk in provider.stream([{"role": "user", "content": "Hello"}]):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].delta.content == "Hello"
        assert chunks[1].delta.content == " world"
        assert chunks[1].is_final is True


# ===== Registry Shutdown Tests =====


class TestRegistryShutdown:
    """Test registry shutdown functionality."""

    @pytest.mark.asyncio
    async def test_shutdown_all_providers(self):
        """Test shutting down all providers."""
        config1 = OpenAIConfig(api_key="sk-test", model="gpt-4")
        config2 = AnthropicConfig(api_key="sk-ant-test", model="claude-3")

        await ProviderRegistry.get_provider(config1)
        await ProviderRegistry.get_provider(config2)

        await ProviderRegistry.shutdown_all()

        # Providers should be shut down
        # Note: instances are cleared after shutdown
        assert len(ProviderRegistry._instances) == 0

    @pytest.mark.asyncio
    async def test_shutdown_single_provider(self):
        """Test shutting down a specific provider."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        await ProviderRegistry.get_provider(config)

        result = await ProviderRegistry.shutdown_provider(ProviderType.OPENAI)

        assert result is True
        assert all(key[0] != ProviderType.OPENAI for key in ProviderRegistry._instances)

    @pytest.mark.asyncio
    async def test_shutdown_nonexistent_provider(self):
        """Test shutting down non-existent provider returns False."""
        result = await ProviderRegistry.shutdown_provider(ProviderType.AZURE_OPENAI)
        assert result is False


# ===== Error Handling Tests =====


class TestErrorHandling:
    """Test error handling in providers."""

    @pytest.mark.asyncio
    async def test_call_on_shutdown_provider(self):
        """Test calling on shut down provider raises error."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = await ProviderRegistry.create_provider(config)

        await provider.shutdown()

        with pytest.raises(ProviderError) as exc_info:
            await provider.call([{"role": "user", "content": "Hello"}])

        assert "shut down" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_stream_on_shutdown_provider(self):
        """Test streaming on shut down provider raises error."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = await ProviderRegistry.create_provider(config)

        await provider.shutdown()

        with pytest.raises(ProviderError) as exc_info:
            async for _ in provider.stream([{"role": "user", "content": "Hello"}]):
                pass

        assert "shut down" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_validate_config_failure(self):
        """Test config validation failure."""
        # Create a mock config object with empty API key
        # (bypassing Pydantic validation to test provider's validate_config)

        config = Mock(spec=ProviderConfig)
        config.api_key = ""  # Empty API key to trigger validation error
        config.provider = ProviderType.OPENAI
        config.model = "gpt-4"

        provider = MockOpenAIProvider(OpenAIConfig(api_key="sk-test", model="gpt-4"))

        with pytest.raises(ValueError) as exc_info:
            provider.validate_config(config)

        assert "api key" in str(exc_info.value).lower()


# ===== Request Tracking Tests =====


class TestRequestTracking:
    """Test request tracking functionality."""

    @pytest.mark.asyncio
    async def test_track_request_start_increments_counter(self):
        """Test _track_request_start increments active request counter."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)
        await provider.initialize()

        assert provider.active_requests == 0

        await provider._track_request_start()
        assert provider.active_requests == 1

        await provider._track_request_start()
        assert provider.active_requests == 2

        # Clean up
        await provider._track_request_end()
        await provider._track_request_end()

    @pytest.mark.asyncio
    async def test_track_request_start_on_shutdown_provider(self):
        """Test _track_request_start raises error on shutdown provider."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)
        await provider.initialize()

        await provider.shutdown()

        with pytest.raises(ProviderError) as exc_info:
            await provider._track_request_start()

        assert "shut down" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_track_request_end_decrements_counter(self):
        """Test _track_request_end decrements active request counter."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)
        await provider.initialize()

        await provider._track_request_start()
        await provider._track_request_start()
        assert provider.active_requests == 2

        await provider._track_request_end()
        assert provider.active_requests == 1

        await provider._track_request_end()
        assert provider.active_requests == 0

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_active_requests(self):
        """Test shutdown waits for active requests to complete."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)
        await provider.initialize()

        # Simulate active requests
        await provider._track_request_start()
        await provider._track_request_start()
        assert provider.active_requests == 2

        # Start shutdown in background
        shutdown_task = asyncio.create_task(provider.shutdown())

        # Verify shutdown is waiting
        await asyncio.sleep(0.1)
        assert not shutdown_task.done()

        # Complete requests
        await provider._track_request_end()
        await provider._track_request_end()

        # Shutdown should complete
        await shutdown_task
        assert provider.is_shutdown is True

    @pytest.mark.asyncio
    async def test_validate_not_initialized(self):
        """Test _validate_initialized raises error when not initialized."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)
        # Don't initialize

        # The mock call() method validates initialization
        with pytest.raises(ProviderError) as exc_info:
            await provider.call([{"role": "user", "content": "test"}])

        assert "not initialized" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_validate_initialized_property(self):
        """Test is_initialized property returns correct value."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)

        assert provider.is_initialized is False

        await provider.initialize()
        assert provider.is_initialized is True

    @pytest.mark.asyncio
    async def test_active_requests_property(self):
        """Test active_requests property returns correct value."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)
        await provider.initialize()

        assert provider.active_requests == 0

        await provider._track_request_start()
        assert provider.active_requests == 1

        await provider._track_request_end()
        assert provider.active_requests == 0

    @pytest.mark.asyncio
    async def test_provider_name_property(self):
        """Test provider_name property returns correct value."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)

        assert provider.provider_name == "openai"

    @pytest.mark.asyncio
    async def test_supports_streaming_property(self):
        """Test supports_streaming property returns True."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)

        assert provider.supports_streaming is True

    @pytest.mark.asyncio
    async def test_supports_tools_property(self):
        """Test supports_tools property returns True."""
        config = OpenAIConfig(api_key="sk-test", model="gpt-4")
        provider = MockOpenAIProvider(config)

        assert provider.supports_tools is True
