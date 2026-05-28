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

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import LLMProvider
from .errors import AuthenticationError, ProviderError, map_openai_error
from .message_sanitizer import strip_local_message_metadata_from_message
from .models import (
    OpenAIResponsesConfig,
    ProviderConfig,
    ProviderType,
    StreamingResponse,
    TextDelta,
    UnifiedResponse,
    UsageInfo,
)
from .openai_responses_transformer import OpenAIResponsesTransformer
from .registry import register_provider

logger = logging.getLogger(__name__)

RESPONSES_TOOL_OUTPUT_MAX_CHARS = 10_485_760


def _cap_function_call_output(output: str) -> str:
    if len(output) <= RESPONSES_TOOL_OUTPUT_MAX_CHARS:
        return output

    suffix = f"\n[output truncated for Responses API: {len(output)} chars total]"
    prefix_len = max(RESPONSES_TOOL_OUTPUT_MAX_CHARS - len(suffix), 0)
    return output[:prefix_len] + suffix


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
        model: Model identifier (default: gpt-5.4)
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

        logger.debug(
            f"OpenAI Responses provider created (model={config.model}, "
            f"store_responses={config.store_responses})"
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
                "httpx not installed. " "Install with: pip install httpx"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI Responses client: {e}")
            raise map_openai_error(e, "openai_responses") from e

    @property
    def _requires_streaming(self) -> bool:
        """ChatGPT codex backend only accepts stream=true."""
        base = self.config.base_url or ""
        return "chatgpt.com" in base

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

                # Detect specific error types by status code and error response
                if response.status_code == 401:
                    error_info = error_data.get("error", {})
                    raise AuthenticationError(
                        error_info.get("message", "Authentication failed"),
                        provider="openai_responses",
                        error_code=error_info.get("type", "authentication_error"),
                    )

                raise map_openai_error(
                    Exception(f"API error {response.status_code}: {error_data}"),
                    "openai_responses",
                )

            # Parse response
            response_dict = response.json()

            # Transform to unified format
            unified_response = OpenAIResponsesTransformer.transform_response(
                response_dict, self.model
            )

            logger.debug(
                f"OpenAI Responses response received "
                f"(tokens={unified_response.usage.total_tokens})"
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

                    if response.status_code == 401:
                        error_info = error_data.get("error", {})
                        raise AuthenticationError(
                            error_info.get("message", "Authentication failed"),
                            provider="openai_responses",
                            error_code=error_info.get("type", "authentication_error"),
                        )

                    raise map_openai_error(
                        Exception(f"API error {response.status_code}: {error_data}"),
                        "openai_responses",
                    )

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
                        raw_event = chunk.raw_chunk
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
            if not output_items and accumulated_text:
                logger.info(
                    f"Final payload had empty output, injecting "
                    f"{len(accumulated_text)} chars from stream deltas"
                )
                final_response["output"] = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": accumulated_text}],
                    }
                ]
            elif not output_items:
                logger.warning(
                    f"OpenAI Responses stream: empty output AND no deltas, "
                    f"status={final_response.get('status')}, "
                    f"id={final_response.get('id')}"
                )

            unified = OpenAIResponsesTransformer.transform_response(
                final_response, self.model
            )

            logger.debug(
                f"OpenAI Responses call-via-stream complete "
                f"(tokens={unified.usage.total_tokens})"
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
                    raise map_openai_error(
                        Exception(f"API error {response.status_code}: {error_data}"),
                        "openai_responses",
                    )

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
        params: Dict[str, Any] = {
            "model": self.model,
            "stream": stream,
            "store": self.config.store_responses,
        }

        # Extract system message to instructions
        instructions = None
        input_messages = []

        for msg in messages:
            role = msg.get("role")
            if role == "system":
                instructions = msg.get("content", "")
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
                input_messages.append(
                    {
                        "role": role,
                        "content": msg.get("content", ""),
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

        # For now, use items array format for consistency
        # TODO: Optimize to use string format for simple single-turn prompts
        params["input"] = input_messages

        # ChatGPT codex backend rejects temperature and max_tokens
        if not self._requires_streaming:
            if "temperature" in kwargs:
                params["temperature"] = kwargs["temperature"]
            else:
                params["temperature"] = self.config.temperature

            if "max_tokens" in kwargs:
                params["max_tokens"] = kwargs["max_tokens"]
            else:
                params["max_tokens"] = self.config.max_tokens

        # Add previous_response_id for state chaining
        if "previous_response_id" in kwargs:
            params["previous_response_id"] = kwargs["previous_response_id"]

        # Transform tools to Responses API format
        # Responses API uses flat format: {"type": "function", "name": ..., ...}
        # NOT the Chat Completions nested format: {"type": "function", "function": {...}}
        if tools:
            responses_tools = []
            for tool in tools:
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
        current_event = None
        current_data = b""
        buffer = b""

        try:
            async for chunk_bytes in response.aiter_bytes():
                buffer += chunk_bytes

                # Split by newlines and process complete lines
                while b"\n" in buffer:
                    line_bytes, buffer = buffer.split(b"\n", 1)

                    # Decode line to string
                    try:
                        line = line_bytes.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        # Skip binary data that can't be decoded
                        continue

                    if not line:
                        # Empty line means end of event
                        if current_event and current_data:
                            # Parse event
                            chunk = self._parse_sse_event(current_event, current_data)
                            if chunk:
                                yield chunk
                            current_event = None
                            current_data = b""
                        continue

                    if line.startswith("event:"):
                        current_event = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        # Keep data as bytes for JSON parsing later
                        current_data = line[len("data:") :].strip().encode("utf-8")

            # Flush remaining event if stream ended without trailing newline
            if current_event and current_data:
                chunk = self._parse_sse_event(current_event, current_data)
                if chunk:
                    yield chunk

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

            # Output item events
            if event in ("response.output_item.added", "response.output_item.done"):
                item = parsed_data.get("item", parsed_data)
                event_data = {"event": event, "item": item}
                return OpenAIResponsesTransformer.transform_streaming_chunk(
                    event_data, self.model
                )

            # Final response (both event names)
            if event in ("response.done", "response.completed"):
                resp_data = parsed_data.get("response", parsed_data)
                event_data = {"event": event, "response": resp_data}

                usage_dict = resp_data.get("usage", {})
                return StreamingResponse(
                    delta=TextDelta(content=""),
                    usage=UsageInfo(
                        prompt_tokens=usage_dict.get("input_tokens", 0),
                        completion_tokens=usage_dict.get("output_tokens", 0),
                        total_tokens=usage_dict.get("input_tokens", 0)
                        + usage_dict.get("output_tokens", 0),
                    ),
                    is_final=True,
                    raw_chunk=event_data,
                )

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
