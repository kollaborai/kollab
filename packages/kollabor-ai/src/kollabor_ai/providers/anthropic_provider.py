"""
Anthropic provider implementation using httpx client.

Implements LLMProvider interface for Anthropic API with:
- httpx.AsyncClient for HTTP requests
- Streaming and non-streaming completions
- Thinking block preservation
- Tool calling support
- Error mapping to unified error hierarchy
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import LLMProvider
from .errors import map_anthropic_error
from .models import (
    AnthropicConfig,
    ProviderType,
    StreamingResponse,
    UnifiedResponse,
)
from .registry import register_provider
from .transformers import AnthropicResponseTransformer

logger = logging.getLogger(__name__)


@register_provider(ProviderType.ANTHROPIC)
class AnthropicProvider(LLMProvider):
    """
    Anthropic provider using httpx client.

    Features:
    - Direct HTTP requests via httpx.AsyncClient
    - Server-Sent Events (SSE) for streaming
    - Extended thinking block preservation
    - Tool calling with incremental JSON
    - Custom base_url support

    Configuration:
        api_key: Anthropic API key (sk-ant-*)
        base_url: Optional custom endpoint
        model: Model name (e.g., claude-3-opus-20240229)
        api_version: API version string (default: 2023-06-01)
        temperature: Sampling temperature (0.0-1.0)
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds
        max_retries: Number of retries (0-5)
    """

    def __init__(self, config: AnthropicConfig):
        """
        Initialize Anthropic provider.

        Args:
            config: Validated Anthropic configuration
        """
        super().__init__(config)
        self.config: AnthropicConfig = config

        # httpx client (initialized in initialize())
        self._client: Optional[Any] = None

        # Default base URL
        self._default_base_url = "https://api.anthropic.com"

        logger.debug(
            f"Anthropic provider created (model={config.model}, "
            f"api_version={config.api_version})"
        )

    def validate_config(self, config: AnthropicConfig) -> None:  # type: ignore[override]  # type: ignore[override]
        """
        Validate Anthropic-specific configuration.

        Args:
            config: Configuration to validate

        Raises:
            ValueError: If configuration is invalid
        """
        # Config already validated by Pydantic model
        if not config.api_key:
            raise ValueError("Anthropic API key is required")

    async def initialize(self) -> None:
        """
        Initialize httpx client.

        Creates async HTTP client with appropriate headers.

        Raises:
            ProviderError: If client initialization fails
        """
        if self._initialized:
            logger.debug("Anthropic provider already initialized")
            return

        try:
            import httpx

            # Create client
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )

            self._initialized = True
            logger.info(f"Anthropic provider initialized (model={self.model})")

        except ImportError as e:
            raise ImportError(
                "httpx not installed. Install with: pip install httpx"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize Anthropic client: {e}")
            raise map_anthropic_error(e, "anthropic") from e

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make non-streaming API call to Anthropic.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions
            **kwargs: Additional Anthropic-specific parameters

        Returns:
            UnifiedResponse with normalized response format

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Prepare request
            request_data = self._prepare_request(messages, tools, **kwargs)
            self.last_request_payload = request_data
            headers = self._get_headers()

            base_url = self.config.base_url or self._default_base_url
            url = f"{base_url}/v1/messages"

            logger.debug(f"Anthropic non-streaming call (model={self.model})")

            # Make API call
            assert self._client is not None  # validated by _validate_initialized
            response = await self._client.post(url, headers=headers, json=request_data)

            # Check for HTTP errors with body details
            if response.status_code >= 400:
                try:
                    error_body = response.json()
                    error_msg = error_body.get("error", {}).get(
                        "message", response.text
                    )
                except Exception:
                    error_msg = response.text or f"HTTP {response.status_code}"
                raise Exception(f"{error_msg} (HTTP {response.status_code})")

            # Parse response
            response_dict = response.json()

            # Transform to unified format
            unified_response = (
                AnthropicResponseTransformer.transform_anthropic_response(
                    response_dict, self.model
                )
            )

            logger.debug(
                f"Anthropic response received "
                f"(tokens={unified_response.usage.total_tokens})"
            )

            return unified_response

        except Exception as e:
            logger.error(f"Anthropic call failed: {e}")
            raise map_anthropic_error(e, "anthropic") from e
        finally:
            await self._track_request_end()

    async def stream(  # type: ignore[override]
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Make streaming API call to Anthropic.

        Parses Server-Sent Events (SSE) and yields chunks.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions
            **kwargs: Additional Anthropic-specific parameters

        Yields:
            StreamingResponse chunks as they arrive

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Prepare request — always include stream=True for SSE
            request_data = self._prepare_request(messages, tools, stream=True, **kwargs)
            self.last_request_payload = request_data
            headers = self._get_headers()

            base_url = self.config.base_url or self._default_base_url
            url = f"{base_url}/v1/messages"

            logger.debug(f"Anthropic streaming call (model={self.model})")

            # Make streaming API call
            assert self._client is not None  # validated by _validate_initialized
            async with self._client.stream(
                "POST", url, headers=headers, json=request_data
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    try:
                        error_body = response.json()
                        error_msg = error_body.get("error", {}).get(
                            "message", response.text
                        )
                    except Exception:
                        error_msg = response.text or f"HTTP {response.status_code}"
                    raise Exception(f"{error_msg} (HTTP {response.status_code})")

                # Parse SSE stream
                async for chunk in self._parse_sse_stream(response):
                    # Transform chunk
                    streaming_response = (
                        AnthropicResponseTransformer.transform_anthropic_chunk(
                            chunk, self.model
                        )
                    )

                    if streaming_response:
                        yield streaming_response

        except Exception as e:
            logger.error(f"Anthropic stream failed: {e}")
            raise map_anthropic_error(e, "anthropic") from e
        finally:
            await self._track_request_end()

    async def _parse_sse_stream(self, response: Any) -> AsyncIterator[Dict[str, Any]]:
        """
        Parse Server-Sent Events (SSE) stream from Anthropic.

        Anthropic SSE format:
        data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "..."}}

        Args:
            response: httpx response object

        Yields:
            Parsed event dictionaries
        """
        async for line in response.aiter_lines():
            if not line:
                continue

            # SSE events start with "data: "
            if line.startswith("data: "):
                data = line[6:]  # Remove "data: " prefix

                # Skip "[DONE]" sentinel
                if data == "[DONE]":
                    continue

                try:
                    # Parse JSON
                    event = json.loads(data)
                    yield event
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse SSE event: {e}, data: {data}")
                    continue

    def _get_headers(self) -> Dict[str, str]:
        """
        Get HTTP headers for Anthropic API.

        Returns:
            Dictionary of headers
        """
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": self.config.api_version,
            "content-type": "application/json",
        }

    def _prepare_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Prepare request body for Anthropic API.

        Args:
            messages: Conversation messages
            tools: Tool definitions
            **kwargs: Additional parameters

        Returns:
            Request dictionary
        """
        # Anthropic uses a different message format
        # Ensure system message is separate
        system_message = None
        anthropic_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                # Extract system content
                if system_message is None:
                    system_message = msg.get("content", "")
                else:
                    # Append to existing system message
                    system_message += "\n\n" + msg.get("content", "")
            elif msg.get("role") == "tool":
                # Convert OpenAI-style tool results to Anthropic format:
                # role="tool" -> role="user" with tool_result content block
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": msg.get("content", ""),
                            }
                        ],
                    }
                )
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Convert OpenAI-style assistant tool_calls to Anthropic format:
                # tool_calls array -> content blocks with tool_use entries
                content_blocks = []
                text = msg.get("content")
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "{}")
                    try:
                        args = (
                            json.loads(args_str)
                            if isinstance(args_str, str)
                            else args_str
                        )
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "input": args,
                        }
                    )
                anthropic_messages.append(
                    {
                        "role": "assistant",
                        "content": content_blocks,
                    }
                )
            else:
                # Shallow copy so the merger below cannot mutate dicts the
                # caller still holds a reference to (e.g. api_service's
                # `messages` list, which is also what _log_raw_interaction
                # captures). Without this, merging same-role runs rewrites
                # `content` in place and the raw log looks like duplicate
                # data was sent when the API actually received one merged
                # message.
                anthropic_messages.append(dict(msg))

        # Merge consecutive same-role messages (Anthropic requires alternating roles)
        # This happens when multiple tool results create multiple "user" messages
        merged_messages = []
        for msg in anthropic_messages:
            if merged_messages and merged_messages[-1]["role"] == msg["role"]:
                prev = merged_messages[-1]
                # Convert both to content block arrays for merging
                prev_content = prev["content"]
                cur_content = msg["content"]
                if isinstance(prev_content, str):
                    prev_content = [{"type": "text", "text": prev_content}]
                if isinstance(cur_content, str):
                    cur_content = [{"type": "text", "text": cur_content}]
                prev["content"] = prev_content + cur_content
            else:
                merged_messages.append(msg)

        # Build request
        request: Dict[str, Any] = {
            "model": self.model,
            "messages": merged_messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }

        # Add system message as cacheable content block
        # Anthropic prompt caching requires content block array format
        if system_message:
            request["system"] = [
                {
                    "type": "text",
                    "text": system_message,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # Add optional parameters
        if self.config.top_p is not None:
            request["top_p"] = self.config.top_p

        # Normalize tools to Anthropic format (input_schema required)
        # Mark last tool as cacheable breakpoint for prompt caching
        if tools:
            normalized = self._normalize_tools(tools)
            if normalized:
                normalized[-1]["cache_control"] = {"type": "ephemeral"}
            request["tools"] = normalized

        # Add any additional kwargs
        request.update(kwargs)

        return request

    def _normalize_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert tools to Anthropic format, handling both generic and pre-formatted tools.

        Accepts tools with either 'parameters' (OpenAI/generic) or 'input_schema'
        (already Anthropic format) and normalizes to what the API requires.
        """
        normalized = []
        for tool in tools:
            # Already has input_schema — pass through
            if "input_schema" in tool:
                normalized.append(tool)
                continue

            # Generic format with 'parameters' key
            input_schema = tool.get("parameters") or {
                "type": "object",
                "properties": {},
            }
            normalized.append(
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": input_schema,
                }
            )
        return normalized

    async def _cleanup(self) -> None:
        """Cleanup httpx client resources."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("Anthropic httpx client closed")
