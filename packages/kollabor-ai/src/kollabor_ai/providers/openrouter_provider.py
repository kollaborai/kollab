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
- Explicit prompt-cache breakpoints for Anthropic routes
- Reasoning continuity: ``reasoning_details`` captured into
  ``provider_reasoning`` and re-sent on the matching assistant message
- Error mapping to unified error hierarchy
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Optional

from ..message_content import serialize_openai_chat_content
from .base import LLMProvider
from .errors import map_openai_error
from .message_sanitizer import strip_local_message_metadata_from_message
from .models import (
    OpenRouterConfig,
    ProviderConfig,
    ProviderType,
    StreamingResponse,
    UnifiedResponse,
)
from .openrouter_model_info import OpenRouterModelInfo
from .registry import register_provider
from .transformers import (
    OpenAIResponseTransformer,
    ToolSchemaTransformer,
)
from .tuning import EffortStyle, effort_params, sampling_params

logger = logging.getLogger(__name__)

# Upstreams that only cache when told to. Anthropic takes explicit
# ``cache_control`` breakpoints on text parts (at most four per request).
# OpenAI, DeepSeek, Grok, Moonshot, Groq and Gemini 2.5+ cache automatically,
# so they get none. Qwen (Alibaba endpoints) and explicit Gemini breakpoints
# are opt-in upstream features and are not sent; add a prefix here to enable.
# https://openrouter.ai/docs/guides/best-practices/prompt-caching
_EXPLICIT_CACHE_PREFIXES = ("anthropic/",)
# The system prefix gets one breakpoint, the newest turns two rolling ones.
_ROLLING_BREAKPOINTS = 2


def _tag_text(message: Dict[str, Any]) -> bool:
    """Breakpoint the message's last non-blank text part; False if it has none.

    A string becomes a one-part list (breakpoints only attach to parts). The
    message gets a fresh list and part, so shared parts are never edited.
    """
    content = message.get("content")
    if isinstance(content, str):
        parts: List[Any] = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        parts = list(content)
    else:
        return False
    for i in range(len(parts) - 1, -1, -1):
        part = parts[i]
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and str(part.get("text") or "").strip()
        ):
            parts[i] = {**part, "cache_control": {"type": "ephemeral"}}
            message["content"] = parts
            return True
    return False


def _add_cache_breakpoints(messages: List[Dict[str, Any]]) -> None:
    """Breakpoint the system prefix and the newest turns, in place.

    ``messages`` is the wire copy, never the caller's list. The system
    breakpoint also covers the tool definitions (they precede it in the cached
    prefix). The second rolling breakpoint is the previous request's tail, so
    the cache read still lands when a turn appends several messages.
    """
    lead = 0
    while lead < len(messages) and messages[lead].get("role") == "system":
        lead += 1
    if lead:
        _tag_text(messages[lead - 1])
    tagged = 0
    for message in reversed(messages[lead:]):
        if tagged == _ROLLING_BREAKPOINTS:
            break
        # A later system message is not a turn: never put a rolling tag in it.
        if message.get("role") != "system":
            tagged += _tag_text(message)


def _reasoning_details(container: Any) -> List[Any]:
    """``reasoning_details`` of a message or delta dict ([] when absent)."""
    details = (container or {}).get("reasoning_details")
    return details if isinstance(details, list) else []


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

        # Model metadata for dynamic max_tokens capping
        self._model_info = OpenRouterModelInfo()

        # Background warmup task reference (prevents unhandled-exception warning)
        self._warmup_task: Optional[asyncio.Task] = None

        # Default base URL if not specified
        if not self.config.base_url:
            self.config.base_url = "https://openrouter.ai/api/v1"

        logger.debug(
            f"OpenRouter provider created (model={config.model}, base_url={config.base_url})"
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
                "max_retries": 0,
            }

            # Add custom headers if present
            if default_headers:
                client_kwargs["default_headers"] = default_headers

            self._client = AsyncOpenAI(**client_kwargs)

            self._initialized = True
            logger.info(
                f"OpenRouter provider initialized (model={self.model}, base_url={self.config.base_url})"
            )

            # Warm model metadata cache in background so it's ready
            # for the first API call. Keep task reference to prevent
            # "Task exception was never retrieved" warnings.
            try:
                loop = asyncio.get_running_loop()
                self._warmup_task = loop.create_task(self._model_info.warm_cache())
                self._warmup_task.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() else None
                )
            except RuntimeError:
                # No running loop — will fetch on first call instead
                logger.debug(
                    "No running loop for background metadata fetch, will fetch on first API call"
                )

        except ImportError as e:
            raise ImportError(
                "OpenAI SDK not installed (required for OpenRouter). Install with: pip install openai"
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
            request_params = await self._build_request(messages, tools, kwargs)

            # Make API call
            self.last_request_payload = request_params
            logger.debug(f"Calling OpenRouter API (model={self.model})")
            assert self._client is not None  # guaranteed by _validate_initialized
            response = await self._client.chat.completions.create(**request_params)

            # Transform response to unified format
            dumped = response.model_dump()
            unified_response = OpenAIResponseTransformer.transform_openai_response(
                dumped, self.model
            )

            # Override provider type (transformer sets it to OPENAI) and carry
            # the reasoning_details the transformer ignores. Use model_copy
            # since Pydantic models are immutable.
            update: Dict[str, Any] = {"provider": ProviderType.OPENROUTER}
            details = _reasoning_details(
                (dumped.get("choices") or [{}])[0].get("message")
            )
            if details:
                update["provider_reasoning"] = self._reasoning_payload(details)
            unified_response = unified_response.model_copy(update=update)

            logger.debug(
                f"OpenRouter response received: {unified_response.usage.total_tokens} tokens"
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

        try:
            request_params = await self._build_request(
                messages, tools, kwargs, stream=True
            )
            self.last_request_payload = request_params
            logger.debug(f"Streaming from OpenRouter (model={self.model})")

            # Make streaming API call
            assert self._client is not None  # guaranteed by _validate_initialized
            raw_stream = await self._client.chat.completions.create(**request_params)

            # The OpenAI transformer ignores reasoning_details, so tap them off
            # the raw chunks on their way in (each chunk's items, in order).
            reasoning_items: List[Any] = []

            async def tapped() -> AsyncIterator[Any]:
                async for raw in raw_stream:
                    payload = raw.model_dump()
                    choice = (payload.get("choices") or [{}])[0]
                    reasoning_items.extend(_reasoning_details(choice.get("delta")))
                    yield SimpleNamespace(model_dump=lambda payload=payload: payload)

            reasoning_sent = False
            async for streaming_response in OpenAIResponseTransformer.iter_chunks(
                tapped(), self.model
            ):
                if (
                    reasoning_items
                    and not reasoning_sent
                    and streaming_response.is_final
                ):
                    # Complete sequence, once, on the first final chunk (the
                    # loop below may keep reading for a trailing usage chunk).
                    streaming_response = streaming_response.model_copy(
                        update={
                            "provider_reasoning": self._reasoning_payload(
                                reasoning_items
                            )
                        }
                    )
                    reasoning_sent = True
                if streaming_response:
                    # Pass every delta (text, tool-call, usage) straight through.
                    # The APICommunicationService layer owns the authoritative
                    # ToolCallAccumulator that reassembles streamed tool-call JSON
                    # fragments. Accumulating here as well and re-emitting the
                    # finished tool with tool_arguments_delta=None dropped the
                    # arguments in the handoff, so the service rebuilt a name-only
                    # call with empty args and discarded it -- INCONSISTENT_TOOL_STOP,
                    # every streamed tool call silently lost.
                    yield streaming_response

                # OpenAI-compatible streams may send the finish-reason chunk
                # before a separate trailing usage-only chunk. Only stop after
                # a final response that actually carries usage; otherwise the
                # usage chunk is left unread and session stats stay at zero.
                if streaming_response and streaming_response.is_final:
                    if streaming_response.usage is not None:
                        logger.debug("OpenRouter stream finished with usage")
                        break
                    logger.debug(
                        "OpenRouter finish chunk received; waiting for trailing usage"
                    )

        except Exception as e:
            logger.error(f"OpenRouter stream failed: {e}")
            raise map_openai_error(e, "openrouter") from e

        finally:
            await self._track_request_end()

    async def _build_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        kwargs: Dict[str, Any],
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Request kwargs for ``chat.completions.create`` (call and stream)."""
        # Dynamically cap max_tokens based on model limits
        await self._model_info.get_model_limits(self.model)
        effective_max = self._model_info.compute_effective_max_tokens(
            self.config.max_tokens, self.model, self._estimate_input_tokens(messages)
        )

        params: Dict[str, Any] = {
            "model": self.model,
            "messages": self._wire_messages(messages),
            "max_tokens": effective_max,
        }
        if stream:
            params["stream"] = True
            # Request usage on streams (cached_tokens + full accounting)
            params["stream_options"] = {"include_usage": True}

        # Tools arrive in Anthropic format, transform to OpenAI format
        openai_tools = ToolSchemaTransformer.to_openai_format(tools) if tools else None
        if openai_tools:
            params["tools"] = openai_tools

        # Sampling params (omitted for reasoning models) + opt-in effort.
        params.update(sampling_params(self.config, self.model))
        params.update(effort_params(self.config, EffortStyle.OPENAI))

        # OpenRouter-only body fields are not SDK arguments (the SDK raises
        # TypeError on them), so they travel in extra_body.
        extra = {
            key: kwargs[key]
            for key in ("models", "route", "provider", "transforms")
            if key in kwargs
        }
        if extra:
            params["extra_body"] = extra
        return params

    def _wire_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Provider-ready copies of ``messages``; the caller's are never edited."""
        wire: List[Dict[str, Any]] = []
        for message in messages:
            out = strip_local_message_metadata_from_message(message)
            # Read from the original: stripping drops provider_reasoning.
            details = self._reasoning_details_for(message)
            if details:
                out["reasoning_details"] = details
            content = out.get("content")
            if isinstance(content, (str, list)):
                out["content"] = serialize_openai_chat_content(
                    content, self.resolve_media
                )
            wire.append(out)
        # After serialization, which rebuilds text parts and would drop tags.
        if self.model.startswith(_EXPLICIT_CACHE_PREFIXES):
            _add_cache_breakpoints(wire)
        return wire

    def _reasoning_details_for(self, message: Dict[str, Any]) -> Optional[List[Any]]:
        """Stored reasoning_details to send back, only from this provider+model."""
        stored = message.get("provider_reasoning")
        if message.get("role") != "assistant" or not isinstance(stored, dict):
            return None
        if (
            stored.get("provider") != ProviderType.OPENROUTER.value
            or stored.get("model") != self.model
        ):
            return None
        items = stored.get("items")
        return list(items) if isinstance(items, list) and items else None

    def _reasoning_payload(self, items: List[Any]) -> Dict[str, Any]:
        """The ``provider_reasoning`` dict for this provider and model."""
        return {
            "provider": ProviderType.OPENROUTER.value,
            "model": self.model,
            "items": list(items),
        }

    @staticmethod
    def _estimate_input_tokens(messages: List[Dict[str, Any]]) -> int:
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
