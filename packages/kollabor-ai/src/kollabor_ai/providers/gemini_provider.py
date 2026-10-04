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
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from ..message_content import content_to_text, serialize_gemini_parts
from .base import LLMProvider
from .errors import map_httpx_error
from .gemini_transformer import (
    SIGNATURE_KEY,
    GeminiResponseTransformer,
    GeminiStreamState,
)
from .models import (
    GeminiConfig,
    ProviderType,
    StreamingResponse,
    UnifiedResponse,
)
from .registry import register_provider
from .transformers import ToolSchemaTransformer
from .tuning import sampling_params

logger = logging.getLogger(__name__)


# Default Gemini API endpoint
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

# Documented stand-in for a functionCall whose real thought signature is gone
# (https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures).
SKIP_SIGNATURE_VALIDATION = "skip_thought_signature_validator"


def _requires_signature(model: str) -> bool:
    """Gemini 3+ validates thought signatures on functionCall parts (400 if absent)."""
    match = re.search(r"gemini-(\d+)", model)
    return bool(match) and int(match.group(1)) >= 3


def _parse_args(raw: Any) -> Dict[str, Any]:
    """functionCall args must be an object; history stores them as a JSON string."""
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


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
            f"Gemini provider created (model={config.model}, base_url={self._base_url})"
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
                f"Gemini response received (tokens={unified_response.usage.total_tokens})"
            )

            return unified_response

        except (
            httpx.TimeoutException,
            httpx.TransportError,
            httpx.HTTPStatusError,
        ) as e:
            mapped_error = map_httpx_error(e, "gemini")
            logger.error(f"Gemini request failed: {mapped_error}")
            raise mapped_error from e
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
            state = GeminiStreamState()

            # Make streaming API call
            assert self._client is not None
            async with self._client.stream(
                "POST",
                url,
                json=request_payload,
                headers=self._build_headers(),
            ) as response:
                # A streaming response has no body loaded yet, so the error
                # path must pull it in before raising -- otherwise reading
                # .text/.json() raises ResponseNotRead and the provider's own
                # explanation is lost, leaving only httpx's bare status line.
                # anthropic_provider and openai_responses_provider already do
                # this; Gemini was the outlier.
                if response.status_code >= 400:
                    await response.aread()
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

                            # One chunk can carry several parts
                            for (
                                streaming_response
                            ) in GeminiResponseTransformer.transform_streaming_chunk(
                                chunk_data, self.model, state
                            ):
                                yield streaming_response

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse SSE chunk: {e}")
                            continue

        except (
            httpx.TimeoutException,
            httpx.TransportError,
            httpx.HTTPStatusError,
        ) as e:
            mapped_error = map_httpx_error(e, "gemini")
            logger.error(f"Gemini stream failed: {mapped_error}")
            raise mapped_error from e
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
        system_parts, contents = self._convert_messages(messages)

        # Build request payload
        generation_config: Dict[str, Any] = {
            "maxOutputTokens": self.config.max_tokens,
        }
        # Reasoning models reject sampling params; omitted for those. Gemini
        # has no reasoning-effort parameter, so effort is not sent here.
        generation_config.update(sampling_params(self.config, self.model))

        request_payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }

        # Add system instruction if present
        if system_parts:
            request_payload["systemInstruction"] = {"parts": system_parts}

        # Transform tools to Gemini format
        if tools:
            gemini_tools = ToolSchemaTransformer.to_gemini_format(tools)
            request_payload["tools"] = gemini_tools

        # Add any additional kwargs
        request_payload.update(kwargs)

        return request_payload

    def _convert_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Convert OpenAI-style history to (systemInstruction parts, contents).

        - Only LEADING system messages go into systemInstruction. A later one
          (hub roster, nudges) becomes a user-role text part at its position:
          rewriting the early prefix would break implicit caching every time.
        - Assistant ``tool_calls`` become functionCall parts, ``role: "tool"``
          results become functionResponse parts (Gemini roles are user/model).
        - Consecutive same-role contents merge, so all results of one step land
          in ONE user content as Gemini requires.
        """
        system_parts: List[Dict[str, Any]] = []
        contents: List[Dict[str, Any]] = []
        calls: Dict[str, Tuple[str, bool]] = {}  # tool_call_id -> (name, echo id)
        leading = True

        for msg in messages:
            role = msg.get("role")
            if role == "system" and leading:
                system_parts.extend(
                    serialize_gemini_parts(msg.get("content"), self.resolve_media)
                )
                continue
            leading = False

            if role == "assistant":
                contents.append(
                    {"role": "model", "parts": self._model_parts(msg, calls)}
                )
            elif role == "tool":
                contents.append(
                    {"role": "user", "parts": [self._function_response(msg, calls)]}
                )
            else:  # user, or a mid-conversation system note
                parts = serialize_gemini_parts(msg.get("content"), self.resolve_media)
                contents.append({"role": "user", "parts": parts})

        merged: List[Dict[str, Any]] = []
        for content in contents:
            if merged and merged[-1]["role"] == content["role"]:
                merged[-1]["parts"].extend(content["parts"])
            else:
                merged.append(
                    {"role": content["role"], "parts": list(content["parts"])}
                )
        for content in merged:
            # A result must open its turn; a note that landed between a call and
            # its result goes after it. sort() is stable.
            content["parts"].sort(key=lambda part: "functionResponse" not in part)
        return system_parts, merged

    def _model_parts(
        self, msg: Dict[str, Any], calls: Dict[str, Tuple[str, bool]]
    ) -> List[Dict[str, Any]]:
        """Parts of one assistant message, with its thought signatures re-attached.

        Signatures come from ``msg["provider_reasoning"]`` and are sent ONLY
        when it was captured from this provider AND model: a signature from
        another model is invalid and 400s.
        """
        reasoning = msg.get("provider_reasoning") or {}
        items: List[Dict[str, Any]] = []
        if (
            reasoning.get("provider") == ProviderType.GEMINI.value
            and reasoning.get("model") == self.model
        ):
            items = reasoning.get("items") or []

        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content")
        parts: List[Dict[str, Any]] = (
            serialize_gemini_parts(content or "", self.resolve_media)
            if content or not tool_calls
            else []
        )

        # Docs: the signature of a text response sits on its final part.
        text_signature = next(
            (
                item[SIGNATURE_KEY]
                for item in reversed(items)
                if item.get("type") == "text" and item.get(SIGNATURE_KEY)
            ),
            None,
        )
        if text_signature:
            for part in reversed(parts):
                if "text" in part:
                    part[SIGNATURE_KEY] = text_signature
                    break

        by_id = {i["id"]: i for i in items if i.get("type") == "functionCall"}
        signed = any(
            by_id.get(tc.get("id"), {}).get(SIGNATURE_KEY) for tc in tool_calls
        )
        for index, tool_call in enumerate(tool_calls):
            function = tool_call.get("function") or {}
            call_id = tool_call.get("id") or ""
            item = by_id.get(call_id, {})
            call: Dict[str, Any] = {
                "name": function.get("name", ""),
                "args": _parse_args(function.get("arguments")),
            }
            if item.get("api_id"):
                call["id"] = call_id
            part: Dict[str, Any] = {"functionCall": call}
            if item.get(SIGNATURE_KEY):
                part[SIGNATURE_KEY] = item[SIGNATURE_KEY]
            elif index == 0 and not signed and _requires_signature(self.model):
                # History without a real signature (another provider, an older
                # session): the documented stand-in keeps validation quiet.
                part[SIGNATURE_KEY] = SKIP_SIGNATURE_VALIDATION
            parts.append(part)
            calls[call_id] = (call["name"], bool(item.get("api_id")))
        return parts

    @staticmethod
    def _function_response(
        msg: Dict[str, Any], calls: Dict[str, Tuple[str, bool]]
    ) -> Dict[str, Any]:
        """One ``role: "tool"`` message as a functionResponse part.

        The tool message carries no name; it comes from the assistant call it
        answers. A result whose call is gone (compaction) goes in as text, since
        a functionResponse with no matching functionCall is a 400.
        """
        call_id = msg.get("tool_call_id") or ""
        text = content_to_text(msg.get("content") or "")
        if call_id not in calls:
            return {"text": f"[tool result {call_id}] {text}"}
        name, echo_id = calls[call_id]
        response: Dict[str, Any] = {"name": name, "response": {"result": text}}
        if echo_id:
            response["id"] = call_id
        return {"functionResponse": response}

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
