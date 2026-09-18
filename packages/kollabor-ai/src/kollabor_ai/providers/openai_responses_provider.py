"""
OpenAI Responses API provider implementation.

Implements LLMProvider interface for OpenAI's Responses API with:
- httpx async client for HTTP requests
- Different request format (input field, instructions parameter)
- Server-managed state (previous_response_id)
- SSE streaming with custom event types
- Tool calling with function_call_output format

The Responses API is OpenAI's new stateful API format that differs from
Chat Completions in several key ways:
- Input format: 'input' field (string or items) vs 'messages' array
- System prompt: 'instructions' parameter vs system message
- Output: 'output' array of items vs 'choices' array
- Tool results: 'function_call_output' items vs 'tool' role messages
- State: 'previous_response_id' for chaining vs client-managed
"""

import copy
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from ..generated_image_artifacts import redact_generated_image_data
from ..message_content import content_to_text, serialize_openai_responses_content
from .base import LLMProvider
from .errors import ProviderError, map_http_status_error, map_openai_error
from .message_sanitizer import strip_local_message_metadata_from_message
from .models import (
    OpenAIResponsesConfig,
    ProviderConfig,
    ProviderType,
    StreamingResponse,
    TextDelta,
    UnifiedResponse,
)
from .openai_responses_transformer import OpenAIResponsesTransformer
from .registry import register_provider
from .tuning import EffortStyle, effort_params, sampling_params

logger = logging.getLogger(__name__)

RESPONSES_TOOL_OUTPUT_MAX_CHARS = 10_485_760
RESPONSES_MAX_SSE_LINE_BYTES = 96 * 1024 * 1024
HOSTED_IMAGE_GENERATION_MODELS = frozenset(
    {
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-6-astra",
    }
)


def _cap_function_call_output(output: str) -> str:
    if len(output) <= RESPONSES_TOOL_OUTPUT_MAX_CHARS:
        return output

    suffix = f"\n[output truncated for Responses API: {len(output)} chars total]"
    prefix_len = max(RESPONSES_TOOL_OUTPUT_MAX_CHARS - len(suffix), 0)
    return output[:prefix_len] + suffix


def _record_image_generation_item(
    items_by_id: Dict[str, Dict[str, Any]], item: Any
) -> None:
    """Retain one completed image item without exposing its result downstream."""
    if not isinstance(item, dict) or item.get("type") != "image_generation_call":
        return

    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise ProviderError(
            "image generation item is missing its provider id",
            provider="openai_responses",
            error_code="image_generation_missing_id",
        )

    existing = items_by_id.get(item_id)
    if existing is None:
        items_by_id[item_id] = copy.deepcopy(item)
        return

    existing_result = existing.get("result")
    incoming_result = item.get("result")
    if existing_result and incoming_result and existing_result != incoming_result:
        raise ProviderError(
            "conflicting payloads received for one image generation item",
            provider="openai_responses",
            error_code="image_generation_conflict",
        )

    # The same item can arrive once without its result and again with it. Merge
    # metadata while preserving the first-seen order and provider identity.
    for key, value in item.items():
        if key not in existing or existing.get(key) in (None, "", []):
            existing[key] = copy.deepcopy(value)


def _merge_image_generation_items(
    response: Dict[str, Any],
    completed_items: Dict[str, Dict[str, Any]],
) -> None:
    """Reconcile output_item.done images into the final Responses payload."""
    output = response.get("output", [])
    if output is None:
        output = []
    if not isinstance(output, list):
        raise ProviderError(
            "Responses API returned a non-list output payload",
            provider="openai_responses",
            error_code="invalid_response_output",
        )

    image_items: Dict[str, Dict[str, Any]] = {}
    normalized_output: List[Dict[str, Any]] = []
    image_positions: Dict[str, int] = {}

    for item in output:
        if isinstance(item, dict) and item.get("type") == "image_generation_call":
            _record_image_generation_item(image_items, item)
            item_id = item["id"]
            if item_id not in image_positions:
                image_positions[item_id] = len(normalized_output)
                normalized_output.append({})
            continue
        normalized_output.append(copy.deepcopy(item))

    for item_id, item in completed_items.items():
        _record_image_generation_item(image_items, item)
        if item_id not in image_positions:
            image_positions[item_id] = len(normalized_output)
            normalized_output.append({})

    for item_id, position in image_positions.items():
        normalized_output[position] = copy.deepcopy(image_items[item_id])

    response["output"] = normalized_output


@register_provider(ProviderType.OPENAI_RESPONSES)
class OpenAIResponsesProvider(LLMProvider):
    """
    OpenAI Responses API provider.

    The Responses API is OpenAI's new stateful API format with:
    - Different request format (input field, instructions parameter)
    - Server-managed state (previous_response_id)
    - New streaming events (response.started, output_item.added, etc.)

    Configuration:
        api_key: OpenAI API key (sk- or sk-proj- prefix)
        model: Model identifier (default: gpt-5.6-sol)
        store_responses: Enable server-side response storage for state management
        base_url: Optional custom endpoint (default: https://api.openai.com/v1)
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds

    API Endpoint:
        POST https://api.openai.com/v1/responses

    Request Format:
    {
        "model": "gpt-5.4",
        "input": "What is the weather?",  # string or items array
        "instructions": "You are helpful",  # system prompt
        "tools": [...],
        "previous_response_id": "resp_001",  # for chaining
        "stream": false
    }

    Response Format:
    {
        "id": "resp_001",
        "output": [
            {"type": "message", "content": [{"type": "text", "text": "..."}]},
            {"type": "function_call", "call_id": "...", "function": "...", "arguments": "{}"}
        ],
        "usage": {"input_tokens": N, "output_tokens": N}
    }

    Tool Result Format:
    {
        "input": [
            {"type": "function_call_output", "call_id": "call_001", "output": "{...}"}
        ],
        "previous_response_id": "resp_001"
    }
    """

    def __init__(self, config: OpenAIResponsesConfig):
        """
        Initialize OpenAI Responses provider.

        Args:
            config: Validated OpenAIResponsesConfig
        """
        super().__init__(config)
        self.config: OpenAIResponsesConfig = config

        # httpx client (initialized in initialize())
        self._client: Optional[Any] = None
        self._generated_image_store: Optional[Any] = None

        logger.debug(
            f"OpenAI Responses provider created (model={config.model}, store_responses={config.store_responses})"
        )

    def validate_config(self, config: ProviderConfig) -> None:  # type: ignore[override]
        """
        Validate OpenAI Responses-specific configuration.

        Args:
            config: Configuration to validate

        Raises:
            ValueError: If configuration is invalid
        """
        # Config already validated by Pydantic model
        # This is for any additional runtime validation
        if not config.api_key:
            raise ValueError("OpenAI Responses API key is required")

    async def initialize(self) -> None:
        """
        Initialize OpenAI Responses client.

        Creates httpx.AsyncClient with API key and optional configuration.

        Raises:
            ProviderError: If client initialization fails
        """
        if self._initialized:
            logger.debug("OpenAI Responses provider already initialized")
            return

        try:
            # Import httpx
            import httpx

            # Create client headers
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }

            # Merge extra headers (e.g. ChatGPT-Account-Id for OAuth)
            if self.config.extra_headers:
                headers.update(self.config.extra_headers)

            # Create client
            client_kwargs: Dict[str, Any] = {
                "headers": headers,
                "timeout": self.config.timeout,
            }

            # Add optional base URL
            base_url = self.config.base_url or "https://api.openai.com/v1"
            client_kwargs["base_url"] = base_url

            self._client = httpx.AsyncClient(**client_kwargs)

            self._initialized = True
            logger.info(f"OpenAI Responses provider initialized (model={self.model})")

        except ImportError as e:
            raise ImportError(
                "httpx not installed. Install with: pip install httpx"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI Responses client: {e}")
            raise map_openai_error(e, "openai_responses") from e

    @property
    def _requires_streaming(self) -> bool:
        """ChatGPT codex backend only accepts stream=true."""
        base = self.config.base_url or ""
        return "chatgpt.com" in base

    @property
    def supports_hosted_image_generation(self) -> bool:
        """Whether this OAuth/Codex route and model support image generation."""
        auth_type = getattr(self.config.auth_type, "value", self.config.auth_type)
        return (
            self._requires_streaming
            and str(auth_type).lower() == "oauth"
            and self.model.lower() in HOSTED_IMAGE_GENERATION_MODELS
        )

    def set_generated_image_store(self, store: Optional[Any]) -> None:
        """Attach the private store used for completed hosted images."""
        self._generated_image_store = store

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make non-streaming API call to OpenAI Responses API.

        If the backend requires streaming (e.g. ChatGPT codex endpoint),
        this transparently streams and collects the full response.

        Args:
            messages: Conversation messages (will be converted to input format)
            tools: Optional tool definitions (Anthropic format, will be transformed)
            **kwargs: Additional provider-specific parameters
                - previous_response_id: For chaining responses
                - temperature: Sampling temperature
                - max_tokens: Maximum tokens to generate

        Returns:
            UnifiedResponse with normalized response format

        Raises:
            ProviderError: If API call fails
        """
        # ChatGPT codex backend mandates stream=true; collect streamed result
        if self._requires_streaming:
            return await self._call_via_stream(messages, tools, **kwargs)

        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Prepare request parameters
            request_params = self._prepare_request(
                messages, tools, stream=False, **kwargs
            )
            self.last_request_payload = request_params

            logger.debug(f"OpenAI Responses non-streaming call (model={self.model})")

            # Make API call
            assert self._client is not None  # validated by _validate_initialized
            response = await self._client.post(
                "/responses",
                json=request_params,
            )

            # Check for errors
            if response.status_code >= 400:
                error_data = (
                    response.json()
                    if response.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    else {}
                )

                error_info = (
                    error_data.get("error", {}) if isinstance(error_data, dict) else {}
                )
                if not isinstance(error_info, dict):
                    error_info = {}
                error = RuntimeError(
                    error_info.get(
                        "message", f"API error {response.status_code}: {error_data}"
                    )
                )
                raise map_http_status_error(
                    error,
                    "openai_responses",
                    response.status_code,
                    response.headers,
                ) from error

            # Parse response
            response_dict = response.json()

            # Transform to unified format
            unified_response = OpenAIResponsesTransformer.transform_response(
                response_dict,
                self.model,
                artifact_store=self._generated_image_store,
            )

            logger.debug(
                f"OpenAI Responses response received (tokens={unified_response.usage.total_tokens})"
            )

            return unified_response

        except Exception as e:
            logger.error(f"OpenAI Responses call failed: {e}")
            if isinstance(e, ProviderError):
                raise
            raise map_openai_error(e, "openai_responses") from e
        finally:
            await self._track_request_end()

    async def _call_via_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        Make a call by streaming and collecting the response.done payload.

        Used for backends that mandate stream=true (ChatGPT codex).
        Consumes the SSE stream silently and returns a UnifiedResponse
        built from the final response.done event.
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            request_params = self._prepare_request(
                messages, tools, stream=True, **kwargs
            )
            self.last_request_payload = request_params

            logger.debug(f"OpenAI Responses call-via-stream (model={self.model})")

            # Use stream() context manager so httpx doesn't consume the body
            assert self._client is not None  # validated by _validate_initialized
            async with self._client.stream(
                "POST", "/responses", json=request_params
            ) as response:
                if response.status_code >= 400:
                    error_data = {}
                    try:
                        await response.aread()
                        error_data = json.loads(response.text)
                    except Exception:
                        pass

                    error_info = (
                        error_data.get("error", {})
                        if isinstance(error_data, dict)
                        else {}
                    )
                    if not isinstance(error_info, dict):
                        error_info = {}
                    error = RuntimeError(
                        error_info.get(
                            "message", f"API error {response.status_code}: {error_data}"
                        )
                    )
                    raise map_http_status_error(
                        error,
                        "openai_responses",
                        response.status_code,
                        response.headers,
                    ) from error

                # Consume SSE stream, capture the final response payload
                # Also accumulate text deltas in case the final payload
                # has empty output (codex backend sends text via deltas only)
                final_response = None
                accumulated_text_parts: List[str] = []
                async for chunk in self._parse_sse_stream(response):
                    if not chunk:
                        continue
                    # Accumulate text deltas from response.output_text.delta
                    if (
                        not chunk.is_final
                        and isinstance(chunk.delta, TextDelta)
                        and chunk.delta.content
                    ):
                        accumulated_text_parts.append(chunk.delta.content)
                    # Capture the final response payload
                    if chunk.is_final and chunk.raw_chunk:
                        raw_event = chunk._raw_payload or chunk.raw_chunk
                        evt = raw_event.get("event", "")
                        if evt in ("response.done", "response.completed"):
                            final_response = raw_event.get("response", {})

            if not final_response:
                raise ProviderError(
                    "Stream ended without response.completed event",
                    provider="openai_responses",
                )

            # If final payload has empty output but we accumulated text
            # from deltas, inject a synthetic message output item so the
            # transformer has something to work with
            accumulated_text = "".join(accumulated_text_parts)
            output_items = final_response.get("output", [])
            if not isinstance(output_items, list):
                raise ProviderError(
                    "Responses API returned an invalid final output payload",
                    provider="openai_responses",
                    error_code="invalid_response_output",
                )

            has_text_output = any(
                isinstance(item, dict) and item.get("type") == "message"
                for item in output_items
            )
            if accumulated_text and not has_text_output:
                logger.info(
                    "Final payload omitted streamed text, injecting %d chars from stream deltas",
                    len(accumulated_text),
                )
                output_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": accumulated_text}],
                    }
                )
                final_response["output"] = output_items
            if not output_items:
                logger.warning(
                    f"OpenAI Responses stream: empty output AND no deltas, "
                    f"status={final_response.get('status')}, "
                    f"id={final_response.get('id')}"
                )

            unified = OpenAIResponsesTransformer.transform_response(
                final_response,
                self.model,
                artifact_store=self._generated_image_store,
            )

            image_items = [
                item
                for item in output_items
                if isinstance(item, dict)
                and item.get("type") == "image_generation_call"
            ]
            if image_items and len(unified.get_generated_images()) != len(image_items):
                raise ProviderError(
                    "image generation completed without a valid saved artifact",
                    provider="openai_responses",
                    error_code="image_artifact_persistence_failed",
                )

            logger.debug(
                f"OpenAI Responses call-via-stream complete (tokens={unified.usage.total_tokens})"
            )
            return unified

        except Exception as e:
            logger.error(f"OpenAI Responses call-via-stream failed: {e}")
            if isinstance(e, ProviderError):
                raise
            raise map_openai_error(e, "openai_responses") from e
        finally:
            await self._track_request_end()

    async def stream(  # type: ignore[override]
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Make streaming API call to OpenAI Responses API.

        Handles SSE (Server-Sent Events) streaming with custom event types:
        - response.started: Initial metadata
        - response.output_item.added: New item in output array
        - response.output_item.done: Item completion
        - response.done: Final response with usage

        Args:
            messages: Conversation messages (will be converted to input format)
            tools: Optional tool definitions (Anthropic format, will be transformed)
            **kwargs: Additional provider-specific parameters

        Yields:
            StreamingResponse chunks as they arrive

        Raises:
            ProviderError: If API call fails
        """
        self._validate_initialized()
        self._validate_not_shutdown()
        await self._track_request_start()

        try:
            # Prepare request parameters
            request_params = self._prepare_request(
                messages, tools, stream=True, **kwargs
            )
            self.last_request_payload = request_params

            logger.debug(f"OpenAI Responses streaming call (model={self.model})")

            # Use stream() context manager for true SSE streaming
            assert self._client is not None  # validated by _validate_initialized
            async with self._client.stream(
                "POST", "/responses", json=request_params
            ) as response:
                # Check for errors
                if response.status_code >= 400:
                    error_data = {}
                    try:
                        await response.aread()
                        error_data = json.loads(response.text)
                    except Exception:
                        pass
                    error_info = (
                        error_data.get("error", {})
                        if isinstance(error_data, dict)
                        else {}
                    )
                    if not isinstance(error_info, dict):
                        error_info = {}
                    error = RuntimeError(
                        error_info.get(
                            "message", f"API error {response.status_code}: {error_data}"
                        )
                    )
                    raise map_http_status_error(
                        error,
                        "openai_responses",
                        response.status_code,
                        response.headers,
                    ) from error

                # Parse SSE stream
                async for chunk in self._parse_sse_stream(response):
                    if chunk:
                        yield chunk

        except Exception as e:
            logger.error(f"OpenAI Responses stream failed: {e}")
            if isinstance(e, ProviderError):
                raise
            raise map_openai_error(e, "openai_responses") from e
        finally:
            await self._track_request_end()

    def _prepare_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Prepare request parameters for OpenAI Responses API.

        Converts from standard messages format to Responses API format:
        - Extract system message to 'instructions' parameter
        - Convert remaining messages to 'input' field (items array)
        - Transform tool definitions to Responses API format

        Args:
            messages: Conversation messages
            tools: Tool definitions (Anthropic format)
            stream: Whether to enable streaming
            **kwargs: Additional parameters

        Returns:
            Dictionary of API parameters
        """
        store_responses = self.config.store_responses
        if self._requires_streaming:
            # The ChatGPT/Codex OAuth backend rejects store=true even though
            # the public Responses API supports it. Keep the wire contract
            # valid for this transport regardless of profile/config origin.
            store_responses = False
        params: Dict[str, Any] = {
            "model": self.model,
            "stream": stream,
            "store": store_responses,
        }

        # Extract system message to instructions
        instructions = None
        input_messages = []

        for msg in messages:
            role = msg.get("role")
            if role == "system":
                instructions = content_to_text(msg.get("content", ""))
            elif role == "tool":
                # Convert Chat Completions tool result to Responses API format
                input_messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.get("tool_call_id", ""),
                        "output": _cap_function_call_output(
                            str(msg.get("content", ""))
                        ),
                    }
                )
            elif role == "assistant" and "tool_calls" in msg:
                # Convert assistant tool_calls to Responses API function_call items
                # First add the text content if any
                text = msg.get("content")
                if text:
                    if isinstance(text, list):
                        text = serialize_openai_responses_content(
                            text, self.resolve_media
                        )
                    input_messages.append(
                        {
                            "role": "assistant",
                            "content": text,
                        }
                    )
                # Then add each tool call as a function_call item
                for tc in msg.get("tool_calls") or []:
                    func = tc.get("function", {})
                    input_messages.append(
                        {
                            "type": "function_call",
                            "call_id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "arguments": func.get("arguments", "{}"),
                        }
                    )
            elif role in ("user", "assistant", "developer"):
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = serialize_openai_responses_content(
                        content, self.resolve_media
                    )
                input_messages.append(
                    {
                        "role": role,
                        "content": content,
                    }
                )
            else:
                input_messages.append(strip_local_message_metadata_from_message(msg))

        # Add instructions (codex backend requires this field)
        if instructions:
            params["instructions"] = instructions
        elif self._requires_streaming:
            params["instructions"] = "You are a helpful assistant."

        # Convert messages to input format
        # For Responses API, input can be:
        # 1. A simple string (if single user message)
        # 2. An items array (for complex conversations)

        # For now, use items array format for consistency.
        # TODO: Optimize to use string format for simple single-turn prompts.
        #
        # Responses API accepts an omitted input only when the request is a
        # continuation identified by previous_response_id. Sending input=[]
        # (or instructions alone) is rejected with a 400 missing-input error.
        # The public Responses API supports server-managed continuations, but
        # the ChatGPT/Codex OAuth transport rejects previous_response_id.
        previous_response_id = (
            None if self._requires_streaming else kwargs.get("previous_response_id")
        )
        if input_messages:
            params["input"] = input_messages
        elif not previous_response_id:
            raise ProviderError(
                "OpenAI Responses request requires input or previous_response_id",
                provider="openai_responses",
                error_code="missing_input",
            )

        if not self._requires_streaming:
            # The public Responses API calls the provider-neutral output
            # budget `max_output_tokens`. ChatGPT's OAuth/Codex transport has
            # a different contract and rejects both output-token parameter
            # names, so leave that backend on its server-side default.
            requested_max_tokens = kwargs.get("max_tokens", self.config.max_tokens)
            if requested_max_tokens is not None:
                params["max_output_tokens"] = requested_max_tokens

            sampling = sampling_params(self.config, self.model)
            if "temperature" in kwargs:
                params["temperature"] = kwargs["temperature"]
            elif "temperature" in sampling:
                params["temperature"] = sampling["temperature"]

        # Reasoning effort (opt-in). The Responses API nests it under
        # reasoning; the codex backend reports the levels each model accepts
        # (see query_codex_model_details). Omitted entirely when unset.
        if kwargs.get("effort"):
            params["reasoning"] = {
                **params.get("reasoning", {}),
                "effort": kwargs["effort"],
            }
        else:
            effort = effort_params(self.config, EffortStyle.RESPONSES)
            if effort:
                params["reasoning"] = {
                    **params.get("reasoning", {}),
                    **effort["reasoning"],
                }

        # Add previous_response_id for state chaining. An empty input is valid
        # only on this continuation path; the guard above prevents malformed
        # initial requests from reaching the HTTP client.
        if previous_response_id:
            params["previous_response_id"] = previous_response_id

        # Preserve public Responses API cache controls when the service
        # forwards them. The ChatGPT/Codex OAuth transport rejects the
        # retention field and ignores the key, so its backend cache remains
        # implicit and must not receive these public-API-only parameters.
        if not self._requires_streaming:
            for cache_key in ("prompt_cache_key", "prompt_cache_retention"):
                cache_value = kwargs.get(cache_key)
                if cache_value is not None:
                    params[cache_key] = cache_value

        # Transform function tools to Responses API format while preserving
        # hosted/non-function tools exactly as supplied. The Codex OAuth route
        # exposes image generation as a hosted tool, not a function.
        if tools or self.supports_hosted_image_generation:
            responses_tools = []
            for tool in tools or []:
                if tool.get("type") and tool.get("type") != "function":
                    responses_tools.append(copy.deepcopy(tool))
                    continue
                # Handle both generic format and OpenAI Chat Completions format
                if "function" in tool:
                    # Already in OpenAI format: {"type": "function", "function": {...}}
                    func = tool["function"]
                    name = func.get("name", "")
                    desc = func.get("description", "")
                    parameters = func.get("parameters", {})
                else:
                    # Generic/Anthropic format: {"name": ..., ...}
                    name = tool.get("name", "")
                    desc = tool.get("description", "")
                    parameters = tool.get("input_schema") or tool.get("parameters", {})
                responses_tools.append(
                    {
                        "type": "function",
                        "name": name,
                        "description": desc,
                        "parameters": parameters,
                    }
                )
            if self.supports_hosted_image_generation and not any(
                tool.get("type") == "image_generation" for tool in responses_tools
            ):
                responses_tools.append({"type": "image_generation"})
            params["tools"] = responses_tools

        return params

    def _format_tool_result(
        self,
        tool_call_id: str,
        result: Any,
    ) -> Dict[str, Any]:
        """
        Format tool result for Responses API.

        Creates a function_call_output item for sending tool results back.

        Args:
            tool_call_id: The call_id from the function_call item
            result: Tool result (string or dict)

        Returns:
            Function_call_output item dict

        Example:
            {
                "type": "function_call_output",
                "call_id": "call_001",
                "output": '{"temp": 72, "condition": "sunny"}'
            }
        """
        # Serialize result to JSON if it's a dict
        if isinstance(result, dict):
            output = json.dumps(result)
        elif isinstance(result, str):
            output = result
        else:
            output = str(result)

        return {
            "type": "function_call_output",
            "call_id": tool_call_id,
            "output": _cap_function_call_output(output),
        }

    async def _parse_sse_stream(
        self,
        response: Any,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Parse SSE (Server-Sent Events) stream from Responses API.

        Handles SSE format:
        event: response.output_item.added
        data: {"type": "message", "content": [...]}

        Args:
            response: httpx streaming response

        Yields:
            StreamingResponse chunks

        Raises:
            ProviderError: If stream parsing fails
        """
        current_event: Optional[str] = None
        current_data_lines: List[bytes] = []
        buffer = bytearray()
        max_line_bytes = RESPONSES_MAX_SSE_LINE_BYTES
        completed_image_items: Dict[str, Dict[str, Any]] = {}
        pending_final: Optional[StreamingResponse] = None

        def accept_event(event_chunk: Optional[StreamingResponse]) -> bool:
            """Capture private image items and defer the final event."""
            nonlocal pending_final
            if event_chunk is None:
                return False

            raw_payload = getattr(event_chunk, "_raw_payload", None)
            if isinstance(raw_payload, dict):
                event_name = raw_payload.get("event")
                if event_name == "response.output_item.done":
                    _record_image_generation_item(
                        completed_image_items, raw_payload.get("item")
                    )
                elif event_name in ("response.done", "response.completed"):
                    response_payload = raw_payload.get("response")
                    if isinstance(response_payload, dict):
                        for item in response_payload.get("output", []) or []:
                            _record_image_generation_item(completed_image_items, item)

            if event_chunk.is_final:
                if pending_final is not None:
                    raise ProviderError(
                        "Responses API emitted multiple final events",
                        provider="openai_responses",
                        error_code="multiple_final_events",
                    )
                pending_final = event_chunk
                return True
            return False

        def flush_event() -> Optional[StreamingResponse]:
            nonlocal current_event, current_data_lines
            if current_event and current_data_lines:
                event = self._parse_sse_event(
                    current_event, b"\n".join(current_data_lines)
                )
                current_event = None
                current_data_lines = []
                return event
            current_event = None
            current_data_lines = []
            return None

        try:
            async for chunk_bytes in response.aiter_bytes():
                buffer.extend(chunk_bytes)

                # Split by newlines and process complete lines
                while True:
                    newline_index = buffer.find(b"\n")
                    if newline_index < 0:
                        if len(buffer) > max_line_bytes:
                            raise ProviderError(
                                "SSE line exceeds the supported size limit",
                                provider="openai_responses",
                                error_code="sse_line_too_large",
                            )
                        break
                    line_bytes = bytes(buffer[:newline_index])
                    del buffer[: newline_index + 1]
                    if len(line_bytes) > max_line_bytes:
                        raise ProviderError(
                            "SSE line exceeds the supported size limit",
                            provider="openai_responses",
                            error_code="sse_line_too_large",
                        )
                    line_bytes = line_bytes.rstrip(b"\r")

                    # Decode line to string
                    try:
                        line = line_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        # Skip binary data that can't be decoded
                        continue

                    if not line:
                        # Empty line means end of event
                        event_chunk = flush_event()
                        if event_chunk:
                            if not accept_event(event_chunk):
                                yield event_chunk
                        continue

                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        current_event = line[len("event:") :].lstrip(" ")
                    elif line.startswith("data:"):
                        # SSE permits multiple data lines per event. Keep the
                        # bytes until the complete event is available so large
                        # image results are not repeatedly reallocated.
                        current_data_lines.append(
                            line[len("data:") :].lstrip(" ").encode("utf-8")
                        )

            # Flush remaining event if stream ended without trailing newline
            event_chunk = flush_event()
            if event_chunk:
                if not accept_event(event_chunk):
                    yield event_chunk

            if pending_final is not None:
                raw_payload = getattr(pending_final, "_raw_payload", None)
                response_payload = (
                    raw_payload.get("response")
                    if isinstance(raw_payload, dict)
                    else None
                )
                if isinstance(response_payload, dict):
                    _merge_image_generation_items(
                        response_payload,
                        completed_image_items,
                    )
                    pending_final._raw_payload = raw_payload
                yield pending_final

        except Exception as e:
            logger.error(f"Failed to parse SSE stream: {e}")
            raise map_openai_error(e, "openai_responses") from e

    def _parse_sse_event(
        self,
        event: str,
        data: bytes,
    ) -> Optional[StreamingResponse]:
        """
        Parse a single SSE event.

        Handles both standard Responses API and ChatGPT codex event names:
        - response.output_text.delta -> text streaming
        - response.output_item.added/done -> item events
        - response.completed / response.done -> final response
        - response.created/in_progress/content_part.* -> ignored

        Args:
            event: Event type
            data: Event data as JSON bytes

        Returns:
            StreamingResponse or None if event has no actionable content
        """
        try:
            # Parse JSON data
            parsed_data = json.loads(data.decode("utf-8"))

            # Text streaming delta (codex sends these)
            if event == "response.output_text.delta":
                delta_text = parsed_data.get("delta", "")
                if delta_text:
                    return StreamingResponse(
                        delta=TextDelta(content=delta_text),
                        is_final=False,
                        raw_chunk={"event": event, **parsed_data},
                    )
                return None

            # Hosted image progress events
            if event.startswith("response.image_generation_call."):
                event_data = {"event": event, **parsed_data}
                return OpenAIResponsesTransformer.transform_streaming_chunk(
                    event_data, self.model
                )

            # Output item events
            if event in ("response.output_item.added", "response.output_item.done"):
                item = parsed_data.get("item", parsed_data)
                event_data = {"event": event, "item": item}
                chunk = OpenAIResponsesTransformer.transform_streaming_chunk(
                    event_data, self.model
                )
                if chunk and event == "response.output_item.done":
                    chunk._raw_payload = event_data
                return chunk

            # Final response (both event names)
            if event in ("response.done", "response.completed"):
                resp_data = parsed_data.get("response", parsed_data)
                event_data = {"event": event, "response": resp_data}
                usage = OpenAIResponsesTransformer._usage_info(resp_data.get("usage"))
                chunk = StreamingResponse(
                    delta=TextDelta(content=""),
                    usage=usage,
                    is_final=True,
                    raw_chunk=redact_generated_image_data(event_data),
                )
                chunk._raw_payload = event_data
                return chunk

            # Other events (created, in_progress, content_part) - skip
            return None

        except Exception as e:
            logger.warning(f"Failed to parse SSE event (event={event}): {e}")
            return None

    async def _cleanup(self) -> None:
        """Cleanup OpenAI Responses client resources."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("OpenAI Responses client closed")
