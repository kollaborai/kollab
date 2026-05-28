"""
OpenAI provider implementation using official OpenAI SDK.

Implements LLMProvider interface for OpenAI API with:
- Official openai.AsyncOpenAI client
- Streaming and non-streaming completions
- Tool calling with incremental JSON accumulation
- Error mapping to unified error hierarchy
- Usage tracking
"""

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import LLMProvider
from .errors import map_openai_error
from .message_sanitizer import strip_local_message_metadata
from .models import (
    OpenAIConfig,
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


@register_provider(ProviderType.OPENAI)
class OpenAIProvider(LLMProvider):
    """
    OpenAI provider using official OpenAI SDK.

    Features:
    - AsyncOpenAI client with automatic error handling
    - Streaming with tool call accumulation
    - Custom base_url support
    - Organization ID support
    - Usage tracking

    Configuration:
        api_key: OpenAI API key (sk-*)
        base_url: Optional custom endpoint (default: https://api.openai.com/v1)
        model: Model name (e.g., gpt-4, gpt-3.5-turbo)
        organization: Optional OpenAI organization ID
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds
    """

    def __init__(self, config: OpenAIConfig):
        """
        Initialize OpenAI provider.

        Args:
            config: Validated OpenAI configuration
        """
        super().__init__(config)
        self.config: OpenAIConfig = config

        # OpenAI client (initialized in initialize())
        self._client: Optional[Any] = None

        # Tool accumulator for streaming
        self._tool_accumulator: Optional[ToolCallAccumulator] = None

        logger.debug(
            f"OpenAI provider created (model={config.model}, "
            f"base_url={config.base_url or 'default'})"
        )

    def validate_config(self, config: OpenAIConfig) -> None:  # type: ignore[override]
        """
        Validate OpenAI-specific configuration.

        Args:
            config: Configuration to validate

        Raises:
            ValueError: If configuration is invalid
        """
        # Config already validated by Pydantic model
        # This is for any additional runtime validation
        if not config.api_key:
            raise ValueError("OpenAI API key is required")

    async def initialize(self) -> None:
        """
        Initialize OpenAI client.

        Creates AsyncOpenAI client with API key and optional configuration.

        Raises:
            ProviderError: If client initialization fails
        """
        if self._initialized:
            logger.debug("OpenAI provider already initialized")
            return

        try:
            # Import OpenAI SDK
            from openai import AsyncOpenAI

            # Create client
            client_kwargs: Dict[str, Any] = {
                "api_key": self.config.api_key,
                "timeout": self.config.timeout,
            }

            # Add optional parameters
            if self.config.base_url:
                client_kwargs["base_url"] = self.config.base_url

            if self.config.organization:
                client_kwargs["organization"] = self.config.organization

            self._client = AsyncOpenAI(**client_kwargs)

            self._initialized = True
            logger.info(f"OpenAI provider initialized (model={self.model})")

        except ImportError as e:
            raise ImportError(
                "OpenAI SDK not installed. " "Install with: pip install openai"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            raise map_openai_error(e, "openai") from e

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make non-streaming API call to OpenAI.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (Anthropic format, will be transformed)
            **kwargs: Additional OpenAI-specific parameters

        Returns:
            UnifiedResponse with normalized response format

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Prepare request parameters
            request_params = self._prepare_request_params(
                messages, tools, stream=False, **kwargs
            )
            self.last_request_payload = request_params

            logger.debug(f"OpenAI non-streaming call (model={self.model})")

            # Make API call
            if self._client is None:
                raise RuntimeError("OpenAI client not initialized")
            response = await self._client.chat.completions.create(**request_params)

            # Convert to dict for transformer
            response_dict = response.model_dump()

            # Transform to unified format
            unified_response = OpenAIResponseTransformer.transform_openai_response(
                response_dict, self.model
            )

            logger.debug(
                f"OpenAI response received "
                f"(tokens={unified_response.usage.total_tokens})"
            )

            return unified_response

        except Exception as e:
            logger.error(f"OpenAI call failed: {e}")
            # Debug: log full exception details
            import sys

            print(
                f"[OPENAI-ERROR] Call failed: {type(e).__name__}: {e}", file=sys.stderr
            )
            if hasattr(e, "response"):
                print(
                    f"[OPENAI-ERROR] Response status: {getattr(e.response, 'status_code', 'unknown')}",
                    file=sys.stderr,
                )
                if hasattr(e.response, "text"):
                    print(
                        f"[OPENAI-ERROR] Response body: {e.response.text}",
                        file=sys.stderr,
                    )
            raise map_openai_error(e, "openai") from e
        finally:
            await self._track_request_end()

    async def stream(  # type: ignore[override]
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Make streaming API call to OpenAI.

        Accumulates tool call deltas across streaming chunks.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (Anthropic format, will be transformed)
            **kwargs: Additional OpenAI-specific parameters

        Yields:
            StreamingResponse chunks as they arrive

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        # Initialize tool accumulator for this stream
        self._tool_accumulator = ToolCallAccumulator()

        try:
            # Prepare request parameters
            request_params = self._prepare_request_params(
                messages, tools, stream=True, **kwargs
            )
            self.last_request_payload = request_params

            logger.debug(f"OpenAI streaming call (model={self.model})")

            # Make streaming API call
            if self._client is None:
                raise RuntimeError("OpenAI client not initialized")
            stream = await self._client.chat.completions.create(**request_params)

            async for chunk in stream:
                # Convert chunk to dict
                chunk_dict = chunk.model_dump()

                # Transform chunk
                streaming_response = OpenAIResponseTransformer.transform_openai_chunk(
                    chunk_dict, self.model
                )

                if streaming_response:
                    # Handle tool call accumulation
                    if streaming_response.delta.type == "tool_call_delta":
                        delta = streaming_response.delta
                        self._tool_accumulator.add_delta(
                            tool_call_id=delta.tool_call_id,
                            name=delta.tool_name,
                            arguments_delta=delta.tool_arguments_delta,
                        )

                    yield streaming_response

                    # If final chunk, add completed tools
                    if streaming_response.is_final:
                        completed_tools = self._tool_accumulator.get_completed_tools()
                        if completed_tools:
                            # Yield completed tool calls
                            for tool in completed_tools:
                                yield StreamingResponse(
                                    delta=ToolCallDelta(
                                        tool_call_id=tool.id,
                                        tool_name=tool.name,
                                        tool_arguments_delta=None,
                                    ),
                                    is_final=False,
                                )

        except Exception as e:
            logger.error(f"OpenAI stream failed: {e}")
            raise map_openai_error(e, "openai") from e
        finally:
            # Reset tool accumulator
            if self._tool_accumulator:
                self._tool_accumulator.reset()
                self._tool_accumulator = None

            await self._track_request_end()

    def _prepare_request_params(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Prepare request parameters for OpenAI API.

        Args:
            messages: Conversation messages
            tools: Tool definitions (Anthropic format)
            stream: Whether to enable streaming
            **kwargs: Additional parameters

        Returns:
            Dictionary of API parameters
        """
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": strip_local_message_metadata(messages),
            "stream": stream,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        # Request usage object on streaming calls (needed for cached_tokens
        # and full token accounting - openai omits usage by default on streams)
        if stream:
            params["stream_options"] = {"include_usage": True}

        # Add optional parameters
        if self.config.top_p is not None:
            params["top_p"] = self.config.top_p

        # Transform tools to OpenAI format
        if tools:
            openai_tools = ToolSchemaTransformer.to_openai_format(tools)
            params["tools"] = openai_tools

        # Add any additional kwargs
        params.update(kwargs)

        return params

    async def _cleanup(self) -> None:
        """Cleanup OpenAI client resources."""
        if self._client:
            # Close the client (close() is synchronous in AsyncOpenAI)
            self._client.close()
            self._client = None
            logger.debug("OpenAI client closed")
