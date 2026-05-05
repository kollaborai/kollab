"""Custom OpenAI-compatible provider for local LLMs and custom endpoints.

Supports any OpenAI-compatible API (vLLM, Ollama, LM Studio, etc.).
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

import aiohttp
from pydantic import field_validator

from .base import LLMProvider
from .models import (
    ProviderConfig,
    ProviderType,
    StreamingResponse,
    TextContent,
    TextDelta,
    ToolCallDelta,
    ToolUseContent,
    UnifiedResponse,
    UsageInfo,
)
from .registry import ProviderRegistry

logger = logging.getLogger(__name__)


class CustomConfig(ProviderConfig):
    """Configuration for custom OpenAI-compatible provider."""

    provider: Literal[ProviderType.CUSTOM] = ProviderType.CUSTOM

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Custom providers may use empty API key for local endpoints."""
        return v  # Allow empty key


@ProviderRegistry.register(ProviderType.CUSTOM)
class CustomProvider(LLMProvider):
    """
    Custom OpenAI-compatible provider.

    Supports any OpenAI-compatible API endpoint:
    - vLLM (http://localhost:8000/v1)
    - Ollama (http://localhost:11434/v1)
    - LM Studio (http://localhost:1234/v1)
    - Any custom OpenAI-compatible endpoint
    """

    def __init__(self, config: CustomConfig):
        super().__init__(config)  # Initialize base class first
        self.base_url = None  # Custom attribute for base URL

        # Set base URL - should include /chat/completions for custom provider
        if config.base_url:
            self.base_url = config.base_url.rstrip("/")
        else:
            self.base_url = "http://localhost:1234/v1/chat/completions"

        logger.info(
            f"Initialized CustomProvider: {self.provider_name} ({self.base_url})"
        )

    async def initialize(self) -> None:
        """Initialize provider (no-op for custom providers)."""
        # No connection pool needed - we create new sessions per request
        pass

    def validate_config(self, config: ProviderConfig) -> None:
        """Validate custom provider configuration."""
        # Custom providers are flexible - just validate required fields
        if not config.model:
            raise ValueError("Custom provider requires 'model' to be specified")

    async def _make_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Make HTTP request to custom API endpoint."""
        # Don't append /chat/completions if base_url already ends with it
        base = self.base_url or ""
        if base.rstrip("/").endswith("/chat/completions"):
            url = base.rstrip("/")
        else:
            url = f"{base.rstrip('/')}/chat/completions"

        headers = {
            "Content-Type": "application/json",
        }

        # Add API key if provided
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": stream,
        }

        # Add optional parameters
        if tools:
            # Transform tools to OpenAI format (wrap in type: function)
            # MCP returns: {"name": "...", "description": "...", "parameters": {...}}
            # OpenAI expects: {"type": "function", "function": {"name": "...",
            #     "description": "...", "parameters": {...}}}
            openai_tools = []
            for tool in tools:
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.get("name"),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("parameters", {}),
                        },
                    }
                )
            payload["tools"] = openai_tools

        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature

        if self.config.max_tokens:
            payload["max_tokens"] = self.config.max_tokens

        if self.config.top_p is not None:
            payload["top_p"] = self.config.top_p

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=self.config.timeout if self.config.timeout else None
                ),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    if response.status == 429:
                        from kollabor_ai.providers.errors import RateLimitError

                        retry_after = response.headers.get("retry-after")
                        retry_seconds = float(retry_after) if retry_after else None
                        raise RateLimitError(
                            message=f"Rate limited: {error_text}",
                            provider=self.provider_name,
                            retry_after=retry_seconds,
                        )
                    raise RuntimeError(
                        f"Custom API error {response.status}: {error_text}"
                    )

                return dict(await response.json())

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """Make non-streaming call to custom API."""
        logger.debug(f"CustomProvider.call (model={self.model})")

        response_data = await self._make_request(messages, tools, stream=False)

        # Parse response (OpenAI-compatible format)
        choice = response_data["choices"][0]
        message = choice["message"]

        # Parse content
        content_blocks = []
        if "content" in message:
            content_blocks.append(TextContent(type="text", text=message["content"]))

        # Parse tool calls
        tool_uses = []
        if "tool_calls" in message:
            for tool_call in message["tool_calls"]:
                # OpenAI returns arguments as a JSON string, need to parse it
                arguments = tool_call["function"]["arguments"]
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                tool_uses.append(
                    ToolUseContent(
                        type="tool_use",
                        id=tool_call["id"],
                        name=tool_call["function"]["name"],
                        input=arguments,
                    )
                )

        # Parse usage (check both OpenAI and Anthropic cache field formats)
        usage_info = response_data.get("usage", {})
        details = usage_info.get("prompt_tokens_details", {}) or {}
        cache_read = (
            details.get("cached_tokens", 0)
            or usage_info.get("cache_read_input_tokens", 0)
            or usage_info.get("cache_read_tokens", 0)
        )
        cache_creation = (
            usage_info.get("cache_creation_input_tokens", 0)
            or usage_info.get("cache_creation_tokens", 0)
        )
        usage = UsageInfo(
            prompt_tokens=usage_info.get("prompt_tokens", 0),
            completion_tokens=usage_info.get("completion_tokens", 0),
            total_tokens=usage_info.get("total_tokens", 0),
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

        return UnifiedResponse(
            content=list(content_blocks + tool_uses),  # type: ignore[operator]
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            model=self.model,
            provider=self.provider_type,
        )

    async def stream(  # type: ignore[override]
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """Stream response from custom API."""
        logger.debug(f"CustomProvider.stream (model={self.model})")

        # Don't append /chat/completions if base_url already ends with it
        base = self.base_url or ""
        if base.rstrip("/").endswith("/chat/completions"):
            url = base.rstrip("/")
        else:
            url = f"{base.rstrip('/')}/chat/completions"

        headers = {
            "Content-Type": "application/json",
        }

        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        logger.debug(f"Custom provider stream: POST {url} model={self.config.model}")

        if tools:
            # Transform tools to OpenAI format (wrap in type: function)
            # MCP returns: {"name": "...", "description": "...", "parameters": {...}}
            # OpenAI expects: {"type": "function", "function": {"name": "...",
            #     "description": "...", "parameters": {...}}}
            openai_tools = []
            for tool in tools:
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.get("name"),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("parameters", {}),
                        },
                    }
                )
            payload["tools"] = openai_tools

        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature

        if self.config.max_tokens:
            payload["max_tokens"] = self.config.max_tokens

        if self.config.top_p is not None:
            payload["top_p"] = self.config.top_p

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=self.config.timeout if self.config.timeout else None
                ),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    if response.status == 429:
                        from kollabor_ai.providers.errors import RateLimitError

                        retry_after = response.headers.get("retry-after")
                        retry_seconds = float(retry_after) if retry_after else None
                        raise RateLimitError(
                            message=f"Rate limited: {error_text}",
                            provider=self.provider_name,
                            retry_after=retry_seconds,
                        )
                    raise RuntimeError(
                        f"Custom API error {response.status}: {error_text}"
                    )

                # Parse SSE stream
                usage_info = None
                finish_reason = None

                async for line in response.content:
                    line_str = line.decode("utf-8").strip()

                    if not line_str or line_str == "data: [DONE]":
                        continue

                    if line_str.startswith("data: "):
                        data_str = line_str[6:]

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # Parse chunk
                        delta = data.get("choices", [{}])[0].get("delta", {})

                        # Text content
                        if "content" in delta and delta["content"]:
                            yield StreamingResponse(
                                delta=TextDelta(content=delta["content"])
                            )

                        # Tool call delta
                        if "tool_calls" in delta:
                            for tool_delta in delta["tool_calls"]:
                                yield StreamingResponse(
                                    delta=ToolCallDelta(
                                        tool_call_id=tool_delta.get("id"),
                                        tool_name=tool_delta.get("function", {}).get(
                                            "name"
                                        ),
                                        tool_arguments_delta=tool_delta.get(
                                            "function", {}
                                        ).get("arguments"),
                                    )
                                )

                        # Usage info (final chunk)
                        if "usage" in data:
                            usage = data["usage"]
                            details = usage.get("prompt_tokens_details", {}) or {}
                            cache_read = (
                                details.get("cached_tokens", 0)
                                or usage.get("cache_read_input_tokens", 0)
                                or usage.get("cache_read_tokens", 0)
                            )
                            cache_creation = (
                                usage.get("cache_creation_input_tokens", 0)
                                or usage.get("cache_creation_tokens", 0)
                            )
                            usage_info = UsageInfo(
                                prompt_tokens=usage.get("prompt_tokens", 0),
                                completion_tokens=usage.get("completion_tokens", 0),
                                total_tokens=usage.get("total_tokens", 0),
                                cache_read_tokens=cache_read,
                                cache_creation_tokens=cache_creation,
                            )

                        # Finish reason
                        finish_reason = data.get("choices", [{}])[0].get(
                            "finish_reason"
                        )

                # Yield final usage info
                if usage_info:
                    yield StreamingResponse(
                        delta=TextDelta(content=""),
                        usage=usage_info,
                        is_final=True,
                        finish_reason=finish_reason,
                        raw_chunk={"finish_reason": finish_reason},
                    )
