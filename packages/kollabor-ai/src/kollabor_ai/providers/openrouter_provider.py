"""
OpenRouter provider implementation using OpenAI SDK.

OpenRouter is an OpenAI-compatible API gateway providing unified access
to 100+ LLM models from various providers (OpenAI, Anthropic, Google, Meta, etc.).

Implements LLMProvider interface for OpenRouter with:
- OpenAI SDK with custom base URL (https://openrouter.ai/api/v1)
- Site tracking headers (HTTP-Referer, X-Title) for rankings
- Streaming and non-streaming completions
- Tool calling with incremental JSON accumulation
- Model routing and fallback capabilities
- Error mapping to unified error hierarchy
"""

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import LLMProvider
from .errors import map_openai_error
from .openrouter_model_info import OpenRouterModelInfo
from .models import (
    OpenRouterConfig,
    ProviderConfig,
    ProviderType,
    StreamingResponse,
    ToolCallDelta,
    UnifiedResponse,
)
from .registry import register_provider
from .transformers import (
    OpenAIResponseTransformer,
    ToolCallAccumulator,
    ToolSchemaTransformer,
)

logger = logging.getLogger(__name__)


@register_provider(ProviderType.OPENROUTER)
class OpenRouterProvider(LLMProvider):
    """
    OpenRouter provider using OpenAI SDK with custom base URL.

    OpenRouter provides a unified API gateway for accessing multiple LLM models:
    - 100+ models from OpenAI, Anthropic, Google, Meta, Mistral, and more
    - Automatic model routing and fallback
    - Cost tracking and analytics
    - Site rankings via HTTP-Referer and X-Title headers

    Features:
    - AsyncOpenAI client with OpenRouter base URL
    - Site tracking for OpenRouter rankings (optional)
    - Streaming with tool call accumulation
    - OpenAI-compatible API format
    - Usage tracking and cost analytics

    Configuration:
        api_key: OpenRouter API key (from openrouter.ai/settings/keys)
        base_url: Optional custom endpoint (default: https://openrouter.ai/api/v1)
        model: Model identifier (e.g., "openai/gpt-4", "anthropic/claude-3-sonnet")
        http_referer: Optional site URL for rankings
        x_title: Optional site name for rankings
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds

    Example:
        config = OpenRouterConfig(
            api_key="sk-or-...",
            model="openai/gpt-4",
            http_referer="https://myapp.com",
            x_title="MyApp"
        )
        provider = OpenRouterProvider(config)
        await provider.initialize()
        response = await provider.call([{"role": "user", "content": "Hello"}])
    """

    def __init__(self, config: OpenRouterConfig):
        """
        Initialize OpenRouter provider.

        Args:
            config: Validated OpenRouter configuration
        """
        super().__init__(config)
        self.config: OpenRouterConfig = config

        # OpenAI client (initialized in initialize())
        self._client: Optional[Any] = None

        # Tool accumulator for streaming
        self._tool_accumulator: Optional[ToolCallAccumulator] = None

        # Model metadata for dynamic max_tokens capping
        self._model_info = OpenRouterModelInfo()

        # Background warmup task reference (prevents unhandled-exception warning)
        self._warmup_task: Optional[asyncio.Task] = None

        # Default base URL if not specified
        if not self.config.base_url:
            self.config.base_url = "https://openrouter.ai/api/v1"

        logger.debug(
            f"OpenRouter provider created (model={config.model}, "
            f"base_url={config.base_url})"
        )

    def validate_config(self, config: ProviderConfig) -> None:
        """
        Validate OpenRouter-specific configuration.

        Args:
            config: Configuration to validate

        Raises:
            ValueError: If configuration is invalid
        """
        if not config.api_key:
            raise ValueError("OpenRouter API key is required")

        if not config.model:
            raise ValueError("OpenRouter model is required")

    async def initialize(self) -> None:
        """
        Initialize OpenRouter client using OpenAI SDK.

        Creates AsyncOpenAI client with OpenRouter base URL and custom headers.

        Raises:
            ProviderError: If client initialization fails
        """
        if self._initialized:
            logger.debug("OpenRouter provider already initialized")
            return

        try:
            # Import OpenAI SDK
            from openai import AsyncOpenAI

            # Build custom headers for site tracking
            default_headers: Dict[str, str] = {}
            if self.config.http_referer:
                default_headers["HTTP-Referer"] = self.config.http_referer
            if self.config.x_title:
                default_headers["X-Title"] = self.config.x_title

            # Create client with OpenRouter base URL
            client_kwargs: Dict[str, Any] = {
                "api_key": self.config.api_key,
                "base_url": self.config.base_url,
                "timeout": self.config.timeout,
            }

            # Add custom headers if present
            if default_headers:
                client_kwargs["default_headers"] = default_headers

            self._client = AsyncOpenAI(**client_kwargs)

            self._initialized = True
            logger.info(
                f"OpenRouter provider initialized (model={self.model}, "
                f"base_url={self.config.base_url})"
            )

            # Warm model metadata cache in background so it's ready
            # for the first API call. Keep task reference to prevent
            # "Task exception was never retrieved" warnings.
            try:
                loop = asyncio.get_running_loop()
                self._warmup_task = loop.create_task(
                    self._model_info.warm_cache()
                )
                self._warmup_task.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() else None
                )
            except RuntimeError:
                # No running loop — will fetch on first call instead
                logger.debug(
                    "No running loop for background metadata fetch, "
                    "will fetch on first API call"
                )

        except ImportError as e:
            raise ImportError(
                "OpenAI SDK not installed (required for OpenRouter). "
                "Install with: pip install openai"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize OpenRouter client: {e}")
            raise map_openai_error(e, "openrouter") from e

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make non-streaming API call to OpenRouter.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (Anthropic format, will be transformed)
            **kwargs: Additional OpenRouter-specific parameters:
                - models: List of model IDs for fallback routing
                - route: Routing strategy ("fallback" for automatic failover)
                - provider: Provider preferences
                - transforms: Prompt transformation options

        Returns:
            UnifiedResponse with normalized response format

        Raises:
            ProviderError: If the API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Dynamically cap max_tokens based on model limits
            await self._model_info.get_model_limits(self.model)
            input_tokens = self._estimate_input_tokens(messages)
            effective_max = self._model_info.compute_effective_max_tokens(
                self.config.max_tokens, self.model, input_tokens
            )

            # Transform tools to OpenAI format if provided
            openai_tools = None
            if tools:
                # Tools are in Anthropic format, transform to OpenAI format
                openai_tools = ToolSchemaTransformer.to_openai_format(tools)

            # Build request parameters
            request_params = {
                "model": self.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": effective_max,
            }

            if openai_tools:
                request_params["tools"] = openai_tools

            # Add optional parameters
            if self.config.top_p is not None:
                request_params["top_p"] = self.config.top_p

            # Add OpenRouter-specific parameters from kwargs
            for key in ["models", "route", "provider", "transforms"]:
                if key in kwargs:
                    request_params[key] = kwargs[key]

            # Make API call
            logger.debug(f"Calling OpenRouter API (model={self.model})")
            assert self._client is not None  # guaranteed by _validate_initialized
            response = await self._client.chat.completions.create(**request_params)

            # Transform response to unified format
            unified_response = OpenAIResponseTransformer.transform_openai_response(
                response.model_dump(), self.model
            )

            # Override provider type (transformer sets it to OPENAI)
            # Use model_copy since Pydantic models are immutable
            unified_response = unified_response.model_copy(
                update={"provider": ProviderType.OPENROUTER}
            )

            logger.debug(
                f"OpenRouter response received: "
                f"{unified_response.usage.total_tokens} tokens"
            )

            return unified_response

        except Exception as e:
            logger.error(f"OpenRouter API call failed: {e}")
            raise map_openai_error(e, "openrouter") from e

        finally:
            await self._track_request_end()

    async def stream(  # type: ignore[override, misc]
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Make streaming API call to OpenRouter.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (Anthropic format)
            **kwargs: Additional OpenRouter-specific parameters

        Yields:
            StreamingResponse chunks as they arrive

        Raises:
            ProviderError: If the API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        # Initialize tool accumulator
        self._tool_accumulator = ToolCallAccumulator(legacy_mode=False)

        try:
            # Dynamically cap max_tokens based on model limits
            await self._model_info.get_model_limits(self.model)
            input_tokens = self._estimate_input_tokens(messages)
            effective_max = self._model_info.compute_effective_max_tokens(
                self.config.max_tokens, self.model, input_tokens
            )

            # Transform tools to OpenAI format if provided
            openai_tools = None
            if tools:
                openai_tools = ToolSchemaTransformer.to_openai_format(tools)

            # Build request parameters
            request_params = {
                "model": self.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": effective_max,
                "stream": True,
                # Request usage on streams (cached_tokens + full accounting)
                "stream_options": {"include_usage": True},
            }

            if openai_tools:
                request_params["tools"] = openai_tools

            if self.config.top_p is not None:
                request_params["top_p"] = self.config.top_p

            # Add OpenRouter-specific parameters
            for key in ["models", "route", "provider", "transforms"]:
                if key in kwargs:
                    request_params[key] = kwargs[key]

            logger.debug(f"Streaming from OpenRouter (model={self.model})")

            # Make streaming API call
            assert self._client is not None  # guaranteed by _validate_initialized
            stream = await self._client.chat.completions.create(**request_params)

            async for chunk in stream:
                # Transform chunk to unified format
                streaming_response = OpenAIResponseTransformer.transform_openai_chunk(
                    chunk.model_dump(), self.model
                )

                if streaming_response:
                    # Handle tool call accumulation
                    if isinstance(streaming_response.delta, ToolCallDelta):
                        delta = streaming_response.delta
                        completed_tools = self._tool_accumulator.add_delta(
                            tool_call_id=delta.tool_call_id,
                            name=delta.tool_name,
                            arguments_delta=delta.tool_arguments_delta,
                        )

                        # If tools completed, yield them
                        if completed_tools:
                            for tool in completed_tools:
                                yield StreamingResponse(
                                    delta=ToolCallDelta(
                                        tool_call_id=tool.id,
                                        tool_name=tool.name,
                                        tool_arguments_delta=None,
                                    ),
                                    is_final=False,
                                )
                    else:
                        yield streaming_response

                # Check if stream is finished
                if streaming_response and streaming_response.is_final:
                    logger.debug("OpenRouter stream finished")
                    break

        except Exception as e:
            logger.error(f"OpenRouter stream failed: {e}")
            raise map_openai_error(e, "openrouter") from e

        finally:
            self._tool_accumulator = None
            await self._track_request_end()

    @staticmethod
    def _estimate_input_tokens(
        messages: List[Dict[str, Any]]
    ) -> int:
        """
        Rough estimate of input token count from messages.

        Uses ~4 chars per token heuristic. Not exact but good enough
        for budgeting max_tokens against context_length.

        Args:
            messages: Conversation messages

        Returns:
            Estimated token count
        """
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # Multimodal content blocks
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "")
                        total_chars += len(text)
            # Account for role and formatting overhead (~10 tokens per message)
            total_chars += 40
        return max(total_chars // 4, 1)

    async def _cleanup(self) -> None:
        """Cleanup OpenRouter-specific resources."""
        if self._warmup_task and not self._warmup_task.done():
            self._warmup_task.cancel()
            self._warmup_task = None
        if self._client:
            # Close client connections if needed
            await self._client.close()
            self._client = None
        logger.debug("OpenRouter provider cleanup complete")
