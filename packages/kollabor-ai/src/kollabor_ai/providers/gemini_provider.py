"""
Gemini provider implementation using httpx.

Implements LLMProvider interface for Google Gemini API with:
- httpx.AsyncClient for HTTP requests
- Streaming and non-streaming completions
- Tool calling with Gemini's functionCall format
- Error mapping to unified error hierarchy
- Usage tracking
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .base import LLMProvider
from .gemini_transformer import GeminiResponseTransformer
from .models import (
    GeminiConfig,
    ProviderType,
    StreamingResponse,
    UnifiedResponse,
)
from .registry import register_provider
from .transformers import ToolSchemaTransformer

logger = logging.getLogger(__name__)


# Default Gemini API endpoint
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"


@register_provider(ProviderType.GEMINI)
class GeminiProvider(LLMProvider):
    """
    Google Gemini provider using httpx.

    Features:
    - httpx.AsyncClient for async HTTP requests
    - Streaming with SSE parsing
    - Tool calling with functionCall/functionResponse
    - API key authentication (URL param or header)
    - Usage tracking

    Configuration:
        api_key: Gemini API key (from Google AI Studio)
        base_url: Optional custom endpoint (default: generativelanguage.googleapis.com)
        model: Model name (e.g., gemini-2.0-flash, gemini-1.5-pro)
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate
        project_id: Optional Vertex AI project ID
        location: Optional Vertex AI location
    """

    def __init__(self, config: GeminiConfig):
        """
        Initialize Gemini provider.

        Args:
            config: Validated Gemini configuration
        """
        super().__init__(config)
        self.config: GeminiConfig = config

        # httpx client (initialized in initialize())
        self._client: Optional[httpx.AsyncClient] = None

        # Build base URL
        self._base_url = config.base_url or DEFAULT_GEMINI_BASE_URL

        logger.debug(
            f"Gemini provider created (model={config.model}, "
            f"base_url={self._base_url})"
        )

    def validate_config(self, config: GeminiConfig) -> None:  # type: ignore[override]  # type: ignore[override]
        """
        Validate Gemini-specific configuration.

        Args:
            config: Configuration to validate

        Raises:
            ValueError: If configuration is invalid
        """
        if not config.api_key:
            raise ValueError("Gemini API key is required")

    async def initialize(self) -> None:
        """
        Initialize httpx client.

        Creates httpx.AsyncClient with appropriate headers and timeout.

        Raises:
            ProviderError: If client initialization fails
        """
        if self._initialized:
            logger.debug("Gemini provider already initialized")
            return

        try:
            # Create httpx client
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )

            self._initialized = True
            logger.info(f"Gemini provider initialized (model={self.model})")

        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            raise

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make non-streaming API call to Gemini.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (OpenAI format, will be transformed)
            **kwargs: Additional Gemini-specific parameters

        Returns:
            UnifiedResponse with normalized response format

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Prepare request payload
            request_payload = self._prepare_request(messages, tools, **kwargs)
            self.last_request_payload = request_payload

            # Build URL with API key
            url = self._build_url(stream=False)

            logger.debug(f"Gemini non-streaming call (model={self.model})")

            # Make API call
            assert self._client is not None
            response = await self._client.post(
                url,
                json=request_payload,
                headers=self._build_headers(),
            )
            response.raise_for_status()

            # Parse response
            response_data = response.json()

            # Transform to unified format
            unified_response = GeminiResponseTransformer.transform_response(
                response_data, self.model
            )

            logger.debug(
                f"Gemini response received "
                f"(tokens={unified_response.usage.total_tokens})"
            )

            return unified_response

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Gemini HTTP error: {e.response.status_code} {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Gemini call failed: {e}")
            raise
        finally:
            await self._track_request_end()

    async def stream(  # type: ignore[override]
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Make streaming API call to Gemini.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions (OpenAI format, will be transformed)
            **kwargs: Additional Gemini-specific parameters

        Yields:
            StreamingResponse chunks as they arrive

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Prepare request payload
            request_payload = self._prepare_request(messages, tools, **kwargs)
            self.last_request_payload = request_payload

            # Build URL with API key and alt=sse for streaming
            url = self._build_url(stream=True)

            logger.debug(f"Gemini streaming call (model={self.model})")

            # Make streaming API call
            assert self._client is not None
            async with self._client.stream(
                "POST",
                url,
                json=request_payload,
                headers=self._build_headers(),
            ) as response:
                response.raise_for_status()

                # Parse SSE stream
                async for line in response.aiter_lines():
                    if not line or not line.strip():
                        continue

                    # Gemini SSE format: "data: {json}"
                    if line.startswith("data: "):
                        data_str = line[6:].strip()  # Remove "data: " prefix

                        try:
                            chunk_data = json.loads(data_str)

                            # Transform chunk
                            streaming_response = (
                                GeminiResponseTransformer.transform_streaming_chunk(
                                    chunk_data, self.model
                                )
                            )

                            if streaming_response:
                                yield streaming_response

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse SSE chunk: {e}")
                            continue

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Gemini HTTP error: {e.response.status_code} {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Gemini stream failed: {e}")
            raise
        finally:
            await self._track_request_end()

    def _prepare_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Prepare request payload for Gemini API.

        Converts messages to contents format, extracts system instruction,
        and transforms tool definitions.

        Args:
            messages: Conversation messages (OpenAI format)
            tools: Tool definitions (OpenAI format)
            **kwargs: Additional parameters

        Returns:
            Gemini request payload
        """
        # Extract system message
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                # Extract system instruction
                system_instruction = {"parts": [{"text": content}]}
            else:
                # Convert role to Gemini format
                gemini_role = "model" if role == "assistant" else role

                # Build content parts
                parts = [{"text": content}]

                contents.append({"role": gemini_role, "parts": parts})

        # Build request payload
        request_payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.config.temperature,
                "maxOutputTokens": self.config.max_tokens,
            },
        }

        # Add system instruction if present
        if system_instruction:
            request_payload["systemInstruction"] = system_instruction

        # Transform tools to Gemini format
        if tools:
            gemini_tools = ToolSchemaTransformer.to_gemini_format(tools)
            request_payload["tools"] = gemini_tools

        # Add any additional kwargs
        request_payload.update(kwargs)

        return request_payload

    def _format_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> Dict[str, Any]:
        """
        Format tool result for Gemini API.

        Gemini uses functionResponse parts to return tool results.

        Args:
            tool_call_id: Tool call identifier
            tool_name: Name of the tool that was called
            result: Tool result string (typically JSON)

        Returns:
            Content dict with functionResponse part
        """
        # Parse result as JSON if possible
        try:
            result_data = json.loads(result)
        except json.JSONDecodeError:
            result_data = {"result": result}

        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": tool_name,
                        "response": result_data,
                    }
                }
            ],
        }

    def _build_url(self, stream: bool = False) -> str:
        """
        Build API URL with endpoint and API key.

        Args:
            stream: Whether to add alt=sse parameter

        Returns:
            Complete API URL
        """
        # Build endpoint path
        endpoint = f"/v1beta/models/{self.model}:generateContent"

        # Add API key as query param
        url = f"{self._base_url}{endpoint}?key={self.config.api_key}"

        # Add alt=sse for streaming
        if stream:
            url += "&alt=sse"

        return url

    def _build_headers(self) -> Dict[str, str]:
        """
        Build request headers.

        Returns:
            Headers dict
        """
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.config.api_key,
        }

    async def _cleanup(self) -> None:
        """Cleanup httpx client resources."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("Gemini client closed")
