"""
Provider models for LLM integration.

Exports all Pydantic models for provider configuration and responses,
unified error hierarchy for exception handling,
and provider registry/base classes for implementation.

Provider implementations are imported here to trigger auto-registration
with the ProviderRegistry decorator.
"""

# Import provider implementations to trigger registration
from .anthropic_provider import AnthropicProvider
from .azure_provider import AzureOpenAIProvider
from .base import LLMProvider
from .custom_provider import CustomProvider
from .errors import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    ContextLengthExceededError,
    InvalidRequestError,
    ProviderError,
    RateLimitError,
    ServerError,
    map_anthropic_error,
    map_openai_error,
)
from .gemini_provider import GeminiProvider
from .models import (
    AnthropicConfig,
    AuthType,
    AzureOpenAIConfig,
    ContentBlock,
    GeminiConfig,
    OpenAIConfig,
    OpenAIResponsesConfig,
    OpenRouterConfig,
    ProviderConfig,
    ProviderType,
    StreamingDelta,
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
from .openai_provider import OpenAIProvider
from .openai_responses_provider import OpenAIResponsesProvider
from .openrouter_provider import OpenRouterProvider
from .registry import (
    ProviderRegistry,
    create_config_from_profile,
    detect_provider_from_profile,
    register_provider,
)

__all__ = [
    # Models
    "AuthType",
    "ProviderType",
    "ProviderConfig",
    "OpenAIConfig",
    "AnthropicConfig",
    "AzureOpenAIConfig",
    "OpenRouterConfig",
    "OpenAIResponsesConfig",
    "GeminiConfig",
    "StreamingDelta",
    "TextDelta",
    "ToolCallDelta",
    "ThinkingDelta",
    "ContentBlock",
    "TextContent",
    "ToolUseContent",
    "ToolResultContent",
    "ThinkingContent",
    "UsageInfo",
    "StreamingResponse",
    "UnifiedResponse",
    # Errors
    "ProviderError",
    "AuthenticationError",
    "RateLimitError",
    "InvalidRequestError",
    "ContextLengthExceededError",
    "APITimeoutError",
    "APIConnectionError",
    "ServerError",
    "map_openai_error",
    "map_anthropic_error",
    # Base and Registry
    "LLMProvider",
    "ProviderRegistry",
    "register_provider",
    "detect_provider_from_profile",
    "create_config_from_profile",
    # Providers
    "OpenAIProvider",
    "AnthropicProvider",
    "AzureOpenAIProvider",
    "CustomProvider",
    "OpenRouterProvider",
    "OpenAIResponsesProvider",
    "GeminiProvider",
]
