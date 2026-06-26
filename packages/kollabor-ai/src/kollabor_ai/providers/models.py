"""
Pydantic models for LLM provider configuration and responses.

Provides validated data models for:
- Provider configuration (OpenAI, Anthropic, Azure OpenAI)
- Streaming response chunks
- Content blocks (text, tool use, tool results, thinking)
- Usage information
- Unified response format
"""

import re
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class AuthType(str, Enum):
    """Authentication type for providers."""

    API_KEY = "api_key"
    OAUTH = "oauth"


class ProviderType(str, Enum):
    """Supported LLM provider types."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE_OPENAI = "azure_openai"
    CUSTOM = "custom"
    OPENROUTER = "openrouter"
    OPENAI_RESPONSES = "openai_responses"
    GEMINI = "gemini"


class ProviderConfig(BaseModel):
    """
    Base provider configuration.

    All providers inherit from this base configuration.
    Subclasses add provider-specific validation and fields.
    """

    provider: ProviderType
    auth_type: AuthType = AuthType.API_KEY
    api_key: str = Field(..., min_length=1)
    base_url: Optional[str] = None
    model: str = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # Max OUTPUT tokens to request. The old default (128000) reserved most of
    # the context window for output that's never more than a few thousand
    # tokens, leaving almost no room for input — large reads then tripped the
    # provider window and the call came back empty. Interactive/agent responses
    # fit comfortably in 16k; override per-profile for big-generation tasks.
    max_tokens: int = Field(default=16384, ge=1)
    # Total context window (input + output) the model accepts. The budget guard
    # trims history against this before sending so a request can't exceed the
    # window. Normally set from the model registry at config-creation time; this
    # default is only the fallback for an unknown model — kept conservative
    # (200k, the common floor) so an unknown model can't silently over-send.
    context_window: int = Field(default=200000, ge=1)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    timeout: Optional[float] = Field(default=120.0, ge=0)
    extra_headers: Optional[Dict[str, str]] = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate base URL is HTTPS (except localhost).

        Args:
            v: Base URL to validate

        Returns:
            Validated URL or None

        Raises:
            ValueError: If URL is not HTTPS (and not localhost)
        """
        if v is None:
            return v

        # Allow localhost without HTTPS
        if "localhost" in v or "127.0.0.1" in v:
            return v

        # Enforce HTTPS for remote URLs
        if not v.startswith("https://"):
            raise ValueError(
                f"Base URL must use HTTPS for security (got: {v}). "
                "Localhost is the only exception to this requirement."
            )

        return v

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        """
        Validate temperature is in valid range.

        Args:
            v: Temperature value

        Returns:
            Validated temperature

        Raises:
            ValueError: If temperature is out of range
        """
        if not 0.0 <= v <= 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {v}")
        return v


class OpenAIConfig(ProviderConfig):
    """
    OpenAI provider configuration.

    Validates OpenAI-specific API key format and base URL.
    """

    provider: Literal[ProviderType.OPENAI] = ProviderType.OPENAI
    organization: Optional[str] = None
    api_key_format: Literal["sk-", "sk-proj-"] = "sk-"

    @model_validator(mode="after")
    def validate_openai_api_key(self) -> "OpenAIConfig":
        """Validate OpenAI API key is not empty."""
        if not self.api_key or not self.api_key.strip():
            raise ValueError("OpenAI API key cannot be empty")
        return self

    @field_validator("base_url")
    @classmethod
    def validate_openai_base_url(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate OpenAI base URL.

        Args:
            v: Base URL to validate

        Returns:
            Validated URL or None

        Raises:
            ValueError: If URL is invalid
        """
        if v is None:
            return v

        # Allow localhost
        if "localhost" in v or "127.0.0.1" in v:
            return v

        # Enforce HTTPS
        if not v.startswith("https://"):
            raise ValueError(
                f"OpenAI base URL must use HTTPS (got: {v}). "
                "Use https://api.openai.com/v1 or your custom HTTPS endpoint."
            )

        return v


class AnthropicConfig(ProviderConfig):
    """
    Anthropic provider configuration.

    Validates Anthropic-specific API key format and version.
    """

    provider: Literal[ProviderType.ANTHROPIC] = ProviderType.ANTHROPIC
    api_version: str = Field(default="2023-06-01")
    max_retries: int = Field(default=2, ge=0, le=5)

    @field_validator("api_key")
    @classmethod
    def validate_anthropic_api_key(cls, v: str) -> str:
        """Validate Anthropic API key is not empty."""
        if not v or not v.strip():
            raise ValueError("Anthropic API key cannot be empty")
        return v

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, v: str) -> str:
        """
        Validate Anthropic API version.

        Args:
            v: API version string

        Returns:
            Validated version

        Raises:
            ValueError: If version format is invalid
        """
        # Basic format check (YYYY-MM-DD)
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Anthropic API version must be in YYYY-MM-DD format, got: {v}"
            )
        return v


class CustomConfig(ProviderConfig):
    """
    Custom OpenAI-compatible provider configuration.

    For local LLMs and custom endpoints (vLLM, Ollama, LM Studio, etc.).
    """

    provider: Literal[ProviderType.CUSTOM] = ProviderType.CUSTOM
    base_url: Optional[str] = None  # Custom endpoint URL
    api_key: str = Field(default="", min_length=0)  # Override to allow empty key


class OpenRouterConfig(ProviderConfig):
    """
    OpenRouter provider configuration.

    OpenRouter is an OpenAI-compatible API gateway providing access to multiple
    LLM models from various providers through a unified interface.

    Features:
    - Unified API for 100+ models from OpenAI, Anthropic, Google, Meta, etc.
    - Automatic model routing and fallback
    - Cost tracking and analytics
    - Site tracking via HTTP-Referer and X-Title headers

    Configuration:
        api_key: OpenRouter API key (get from openrouter.ai/settings/keys)
        base_url: Optional custom endpoint (default: https://openrouter.ai/api/v1)
        model: Model identifier (e.g., "openai/gpt-4", "anthropic/claude-3-sonnet")
        http_referer: Optional site URL for rankings (recommended)
        x_title: Optional site name for rankings (recommended)
    """

    provider: Literal[ProviderType.OPENROUTER] = ProviderType.OPENROUTER
    http_referer: Optional[str] = None  # Site URL for OpenRouter rankings
    x_title: Optional[str] = None  # Site name for OpenRouter rankings

    @field_validator("base_url")
    @classmethod
    def validate_openrouter_base_url(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate OpenRouter base URL.

        Args:
            v: Base URL to validate

        Returns:
            Validated URL or None (defaults to https://openrouter.ai/api/v1)

        Raises:
            ValueError: If URL is invalid
        """
        if v is None:
            return v

        # Allow localhost for testing
        if "localhost" in v or "127.0.0.1" in v:
            return v

        # Enforce HTTPS
        if not v.startswith("https://"):
            raise ValueError(
                f"OpenRouter base URL must use HTTPS (got: {v}). "
                "Use https://openrouter.ai/api/v1 or your custom HTTPS endpoint."
            )

        return v


class AzureOpenAIConfig(ProviderConfig):
    """
    Azure OpenAI provider configuration.

    Adds Azure-specific fields like deployment ID and API version.
    """

    provider: Literal[ProviderType.AZURE_OPENAI] = ProviderType.AZURE_OPENAI
    azure_endpoint: str = Field(..., min_length=1)
    api_version: str = Field(default="2024-02-15-preview")
    deployment_id: Optional[str] = None

    @field_validator("azure_endpoint")
    @classmethod
    def validate_azure_endpoint(cls, v: str) -> str:
        """
        Validate Azure OpenAI endpoint.

        Args:
            v: Endpoint URL

        Returns:
            Validated endpoint

        Raises:
            ValueError: If endpoint is invalid
        """
        if not v.startswith("https://"):
            raise ValueError(
                f"Azure endpoint must use HTTPS, got: {v}. "
                "Format: https://<resource>.openai.azure.com"
            )
        return v

    @field_validator("api_key")
    @classmethod
    def validate_azure_api_key(cls, v: str) -> str:
        """
        Validate Azure OpenAI API key.

        Azure keys are typically 32-character hex strings.

        Args:
            v: API key to validate

        Returns:
            Validated API key

        Raises:
            ValueError: If API key format is invalid
        """
        # Azure keys are typically 32 hex characters
        if len(v) < 20:
            raise ValueError(
                f"Azure OpenAI API key appears too short "
                f"(expected 32+ chars, got {len(v)})"
            )
        return v


class OpenAIResponsesConfig(ProviderConfig):
    """
    OpenAI Responses API provider configuration.

    The Responses API is OpenAI's new stateful API format with:
    - Different request format (input field, instructions parameter)
    - Server-managed state (previous_response_id)
    - New streaming events (response.started, output_item.added, etc.)

    Configuration:
        api_key: OpenAI API key (sk- or sk-proj- prefix)
        model: Model identifier (default: codex, resolved to latest on login)
        store_responses: Enable server-side response storage for state management
    """

    provider: Literal[ProviderType.OPENAI_RESPONSES] = ProviderType.OPENAI_RESPONSES
    model: str = Field(default="codex")
    store_responses: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_openai_responses_api_key(self) -> "OpenAIResponsesConfig":
        """Validate OpenAI Responses API key is not empty."""
        if not self.api_key or not self.api_key.strip():
            raise ValueError("OpenAI Responses API key cannot be empty")
        return self


class GeminiConfig(ProviderConfig):
    """
    Google Gemini provider configuration.

    Supports both Gemini API and Vertex AI:
    - API Key: Generative Language API (generativelanguage.googleapis.com)
    - Vertex AI: For enterprise use with project_id and location

    Configuration:
        api_key: Gemini API key (no specific prefix requirement)
        model: Model identifier (default: gemini-3.1-pro-preview)
        project_id: Optional Vertex AI project ID
        location: Optional Vertex AI location (e.g., 'us-central1')
    """

    provider: Literal[ProviderType.GEMINI] = ProviderType.GEMINI
    model: str = Field(default="gemini-3.1-pro-preview")
    project_id: Optional[str] = None
    location: Optional[str] = None


class TextDelta(BaseModel):
    """Text content delta for streaming responses."""

    type: Literal["text"] = "text"
    content: str


class ToolCallDelta(BaseModel):
    """Tool call delta for streaming responses."""

    type: Literal["tool_call_delta"] = "tool_call_delta"
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments_delta: Optional[str] = None


class ThinkingDelta(BaseModel):
    """Thinking content delta for extended thinking."""

    type: Literal["thinking"] = "thinking"
    content: str


StreamingDelta = Union[TextDelta, ToolCallDelta, ThinkingDelta]


class TextContent(BaseModel):
    """Text content block in responses."""

    type: Literal["text"] = "text"
    text: str


class ToolUseContent(BaseModel):
    """Tool use content block in responses."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: Dict[str, Any]


class ToolResultContent(BaseModel):
    """Tool result content block in responses."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


class ThinkingContent(BaseModel):
    """Thinking content block for extended thinking."""

    type: Literal["thinking"] = "thinking"
    thinking: str


ContentBlock = Union[TextContent, ToolUseContent, ToolResultContent, ThinkingContent]


class UsageInfo(BaseModel):
    """Token usage information for API responses."""

    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    total_tokens: int = Field(..., ge=0)
    # Prompt caching metrics (optional, provider-specific)
    # anthropic: cache_creation_input_tokens, cache_read_input_tokens
    # openai: prompt_tokens_details.cached_tokens (mapped to cache_read_tokens)
    cache_creation_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)


class StreamingResponse(BaseModel):
    """
    Streaming response chunk from LLM API.

    Contains delta updates and optional usage information.
    """

    delta: StreamingDelta
    usage: Optional[UsageInfo] = None
    is_final: bool = Field(default=False)
    finish_reason: Optional[str] = None
    raw_chunk: Optional[Dict[str, Any]] = None

    # Note: usage is intentionally optional even on final chunks.
    # Many OpenAI-compatible providers (GLM, etc.) omit usage from streaming
    # chunks entirely. The consumer falls back to zero counts when missing.


class UnifiedResponse(BaseModel):
    """
    Unified response format from any provider.

    Normalizes responses from different providers into a consistent format.
    """

    content: List[ContentBlock] = Field(default_factory=list)
    usage: UsageInfo
    model: str
    provider: ProviderType
    finish_reason: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_content_not_empty(self) -> "UnifiedResponse":
        """
        Validate that response has at least one content block.

        Returns:
            Validated response

        Raises:
            ValueError: If content is empty
        """
        if not self.content:
            raise ValueError("Unified response must contain at least one content block")
        return self

    def get_text_content(self) -> str:
        """
        Extract all text content from response.

        Returns:
            Concatenated text from all text blocks
        """
        text_parts = []
        for block in self.content:
            if isinstance(block, TextContent):
                text_parts.append(block.text)
        return "".join(text_parts)

    def get_tool_uses(self) -> List[ToolUseContent]:
        """
        Extract all tool use blocks from response.

        Returns:
            List of tool use content blocks
        """
        return [block for block in self.content if isinstance(block, ToolUseContent)]

    def get_thinking_content(self) -> Optional[str]:
        """
        Extract thinking content if present.

        Returns:
            Thinking text or None if not present
        """
        for block in self.content:
            if isinstance(block, ThinkingContent):
                return block.thinking
        return None
