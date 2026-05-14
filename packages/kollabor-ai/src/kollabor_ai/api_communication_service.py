"""API Communication Service for LLM requests.

Handles API communication with LLM endpoints using the provider system.
Integrates with ProviderRegistry for unified OpenAI/Anthropic/Azure/Custom support.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from kollabor_ai.profile_manager import LLMProfile
from kollabor_ai.providers.models import (
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    UnifiedResponse,
    UsageInfo,
)
from kollabor_ai.providers.registry import ProviderRegistry, create_config_from_profile
from kollabor_ai.providers.transformers import ToolCallAccumulator

logger = logging.getLogger(__name__)


class APICommunicationService:
    """Pure API communication service for LLM requests.

    Handles request formatting, response parsing,
    and error handling for LLM API communication using provider system.
    """

    def __init__(self, config, raw_conversations_dir, profile: LLMProfile):
        """Initialize API communication service.

        Args:
            config: Configuration manager for API settings
            raw_conversations_dir: Directory for raw interaction logs
            profile: LLM profile with configuration (resolves env vars -> config -> defaults)
        """
        self.config = config
        self.raw_conversations_dir = raw_conversations_dir

        # Initialize from profile (resolves env vars through profile's getter methods)
        self.update_from_profile(profile)

        # Streaming (from config, not profile-specific)
        self.enable_streaming = config.get("kollabor.llm.enable_streaming", False)

        # Session tracking for raw log linking
        self.current_session_id: Optional[str] = None

        # Provider-based communication
        self._provider: Any = None  # Initialized in initialize()
        self._provider_error: Optional[str] = (
            None  # Error message if provider init failed
        )
        self._use_provider = True  # Always use provider system (no legacy fallback)
        self._initialized = False

        # Request cancellation support
        self.current_request_task: Optional[asyncio.Task[str]] = None
        self.cancel_requested = False

        # Token usage tracking
        self.last_token_usage: Dict[str, int] = {}

        # Native tool calling support
        self.last_tool_calls: List[Any] = []  # Tool calls from last response
        self.last_stop_reason = ""  # Stop reason from last response

        # Reasoning/thinking content from last response
        self.last_thinking_content: Optional[str] = None

        # Raw upstream payload from last response. For streaming providers
        # this is a list of raw SSE chunk dicts; for non-streaming it is the
        # provider's raw response dict wrapped in a single-element list.
        # Captured here so raw conversation logs reflect what the provider
        # actually sent (not just our post-processed view).
        self.last_raw_chunks: List[Dict[str, Any]] = []

        # Tool accumulator mode (LEGACY vs EXPLICIT)
        self._use_explicit_accumulation = config.get(
            "kollabor.llm.use_explicit_tool_accumulation", False
        )
        self._debug_tool_stream_path = config.get(
            "kollabor.llm.debug_tool_stream_path", False
        )

        # Tool accumulator for streaming (provider system)
        self._tool_accumulator: Optional[ToolCallAccumulator] = None

        # Resource monitoring and statistics
        self._connection_stats: Dict[str, Any] = {
            "total_requests": 0,
            "failed_requests": 0,
            "last_activity": None,
            "session_creation_time": None,
            "connection_errors": 0,
        }

        # Store profile for provider creation
        self._profile = profile

        logger.info(
            f"API service initialized for provider={profile.provider} (profile: {profile.name})"
        )
        if self._debug_tool_stream_path:
            logger.info("Tool stream path debugging enabled")

    def set_session_id(self, session_id: str) -> None:
        """Set current session ID for raw log linking.

        Args:
            session_id: Session identifier from conversation logger
        """
        self.current_session_id = session_id
        logger.debug(f"API service session ID set to: {session_id}")

    def update_from_profile(self, profile: LLMProfile) -> None:
        """Update API settings from a profile.

        Uses profile getter methods that resolve env var -> config -> default.

        Args:
            profile: LLMProfile with configuration
        """
        old_model = getattr(self, "model", None)

        # Get all values from profile (getter methods resolve env vars)
        self.model = profile.get_model()
        self.temperature = profile.get_temperature()
        self.max_tokens = profile.get_max_tokens()
        self.timeout = profile.get_timeout()

        # Update stored profile for provider recreation
        self._profile = profile

        # Always use provider system (all profiles must have provider field)
        logger.info(
            f"Profile '{profile.name}' uses provider system: {profile.provider}"
        )

        if old_model:
            logger.info(f"API service updated: {old_model} -> {self.model}")

    @property
    def provider_type(self) -> str:
        return self._profile.provider if self._profile else ""

    def cancel_current_request(self):
        """Cancel the current API request."""
        self.cancel_requested = True
        if self.current_request_task and not self.current_request_task.done():
            logger.info("Cancelling current API request")
            self.current_request_task.cancel()
        else:
            logger.debug("No active API request to cancel")

    async def initialize(self) -> bool:
        """Initialize provider with proper error handling.

        Returns:
            True if provider initialized successfully, False if there was
            an error (app can still run, user can fix via /profile)
        """
        if self._initialized:
            return self._provider is not None

        # Initialize provider system (handles its own errors gracefully)
        await self._initialize_provider()

        self._initialized = True
        self._connection_stats["last_activity"] = time.time()

        if self._provider:
            logger.info("API service initialized successfully")
            return True
        else:
            logger.warning(
                "API service initialized with errors - provider not available. "
                "Use /profile to fix configuration."
            )
            return False

    async def _initialize_provider(self) -> None:
        """Initialize provider from profile configuration.

        Handles validation errors gracefully - logs warning and allows app
        to continue so user can fix the profile via /profile command.
        For OAuth profiles, auto-refreshes expired tokens before creating
        the provider.
        """
        try:
            # Auto-refresh OAuth tokens before creating provider
            if self._profile.auth_type == "oauth":
                await self._refresh_oauth_token()

            # Convert profile to provider config
            provider_config = create_config_from_profile(
                self._profile.to_dict(),
            )

            # Get or create provider instance
            provider = await ProviderRegistry.get_provider(provider_config)
            self._provider = provider

            logger.info(
                f"Provider initialized: {provider.provider_name} "
                f"(model={provider.model})"
            )

        except ValueError as e:
            # Config validation failed (e.g., wrong API key format for provider)
            self._provider = None
            self._provider_error = str(e)
            logger.warning(
                f"Profile '{self._profile.name}' has configuration error: {e}. "
                f"Use /profile to fix the configuration."
            )

        except Exception as e:
            # Other errors (network, etc.)
            self._provider = None
            self._provider_error = str(e)
            logger.warning(
                f"Failed to initialize provider for profile '{self._profile.name}': {e}. "
                f"Use /profile to check configuration."
            )

    async def _refresh_oauth_token(self) -> None:
        """Refresh expired OAuth token and update profile in-place.

        Called before provider creation for OAuth profiles. If refresh
        succeeds, updates self._profile.api_key with the fresh token
        and resolves the model to the latest available if still generic.
        If refresh fails (no refresh_token, network error), logs warning
        and continues with the stale token (provider will fail with 401).
        """
        try:
            from kollabor_ai.oauth import OAuthTokenStorage

            storage = OAuthTokenStorage()
            provider_name = "openai"  # only OAuth provider for now

            tokens = await storage.load_tokens(provider_name, auto_refresh=True)
            if tokens:
                if tokens.access_token != self._profile.api_key:
                    self._profile.api_key = tokens.access_token
                    logger.info(
                        "OAuth token auto-refreshed for provider initialization"
                    )
                # Update extra headers if account_id changed
                if tokens.account_id:
                    if not self._profile.extra_headers:
                        self._profile.extra_headers = {}
                    self._profile.extra_headers["ChatGPT-Account-Id"] = (
                        tokens.account_id
                    )

                # Resolve generic model name to latest available
                if self._profile.model in ("codex", ""):
                    await self._resolve_oauth_model(tokens)
            else:
                logger.warning(
                    "OAuth token refresh failed - token may be expired. "
                    "Use /login openai to re-authenticate."
                )
        except Exception as e:
            logger.warning(f"OAuth token refresh error: {e}")

    async def _resolve_oauth_model(self, tokens) -> None:
        """Resolve generic 'codex' model name to the latest available model."""
        try:
            from kollabor_ai.oauth.openai_oauth import (
                pick_best_model,
                query_codex_models,
            )

            models = await query_codex_models(tokens.access_token, tokens.account_id)
            resolved = pick_best_model(models)
            if resolved != "codex":
                self._profile.model = resolved
                logger.info(f"Resolved OAuth model to: {resolved}")
        except Exception as e:
            logger.warning(f"Model resolution failed: {e}")

    async def shutdown(self):
        """Shutdown provider resources with comprehensive error handling."""
        if not self._initialized:
            return

        try:
            logger.info("Starting API communication service shutdown")

            # Cancel any active requests
            if self.current_request_task and not self.current_request_task.done():
                logger.info("Cancelling active request during shutdown")
                self.current_request_task.cancel()
                try:
                    await self.current_request_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error cancelling request during shutdown: {e}")

            # Note: We don't shutdown provider here as it's a singleton
            # ProviderRegistry.shutdown_all() should be called at application shutdown

            self._initialized = False
            logger.info("API communication service shutdown complete")

        except Exception as e:
            logger.error(f"Error during API service shutdown: {e}")
            # Don't raise - we want cleanup to complete even if there are errors

    async def call_llm(
        self,
        conversation_history: List[Dict[str, str]],
        max_history: Optional[int] = None,
        streaming_callback=None,
        tools: Optional[List[Dict[str, Any]]] = None,
        on_rate_limit=None,
    ) -> str:
        """Make API call to LLM with conversation history and robust error handling.

        Args:
            conversation_history: List of conversation messages
            max_history: Maximum number of messages to send (optional)
            streaming_callback: Optional callback for streaming content chunks
            tools: Optional list of tool definitions for native function calling

        Returns:
            LLM response content

        Raises:
            RuntimeError: If provider not initialized
            asyncio.CancelledError: If request was cancelled
            Exception: For API communication errors
        """
        # Reset cancellation flag
        self.cancel_requested = False

        # Store streaming callback for use in handlers
        self.streaming_callback = streaming_callback

        # Update activity tracking
        self._connection_stats["total_requests"] += 1
        self._connection_stats["last_activity"] = time.time()

        # Prepare messages for API
        messages = self._prepare_messages(conversation_history, max_history)

        # Use provider system (always enabled)
        if not self._provider:
            # Provide helpful error with the actual configuration issue
            if self._provider_error:
                raise RuntimeError(
                    f"LLM provider not available due to configuration error:\n"
                    f"{self._provider_error}\n\n"
                    f"Use /profile to fix the configuration."
                )
            else:
                raise RuntimeError("Provider not initialized. Call initialize() first.")

        max_retries = 5
        base_delay = 5.0  # seconds

        for attempt in range(max_retries + 1):
            request_start = time.time()
            try:
                # Wrap provider call in a task so cancel_current_request()
                # can actually cancel the in-flight HTTP request
                if self.enable_streaming:
                    self.current_request_task = asyncio.ensure_future(
                        self._call_provider_stream(messages, tools, streaming_callback)
                    )
                else:
                    self.current_request_task = asyncio.ensure_future(
                        self._call_provider_nonstream(messages, tools)
                    )

                content = await self.current_request_task

                # Log raw interaction on success
                self._log_raw_interaction(
                    messages=messages,
                    tools=tools,
                    response_content=content,
                    duration=time.time() - request_start,
                )
                return str(content)

            except asyncio.CancelledError:
                self._log_raw_interaction(
                    messages=messages,
                    tools=tools,
                    cancelled=True,
                    duration=time.time() - request_start,
                )
                raise asyncio.CancelledError("API request cancelled by user")
            except Exception as e:
                # Check if this is a retryable error
                from kollabor_ai.providers.errors import RateLimitError

                error_str = str(e)
                is_rate_limit = isinstance(e, RateLimitError) or "429" in error_str

                # Server errors (500, 502, 503, 504) and network errors are transient
                is_server_error = any(
                    code in error_str
                    for code in ("500", "502", "503", "504", "Network error")
                )

                is_retryable = is_rate_limit or is_server_error

                if is_retryable and attempt < max_retries:
                    # Use retry_after from headers if available, else exponential backoff
                    retry_after = getattr(e, "retry_after", None)
                    delay = retry_after if retry_after else base_delay * (2**attempt)
                    delay = min(delay, 120.0)  # cap at 2 minutes

                    error_type = "Rate limited" if is_rate_limit else "Server error"
                    logger.warning(
                        f"{error_type} (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {delay:.0f}s: {error_str[:120]}"
                    )
                    if on_rate_limit:
                        try:
                            await on_rate_limit(attempt + 1, max_retries, int(delay))
                        except Exception:
                            pass
                    self._connection_stats["failed_requests"] += 1
                    self._log_raw_interaction(
                        messages=messages,
                        tools=tools,
                        error=f"{error_type}, retry {attempt + 1} in {delay:.0f}s",
                        duration=time.time() - request_start,
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error(f"Provider call failed: {type(e).__name__}: {e}")
                self._connection_stats["failed_requests"] += 1
                self._log_raw_interaction(
                    messages=messages,
                    tools=tools,
                    error=str(e),
                    duration=time.time() - request_start,
                )
                raise
            finally:
                self.current_request_task = None
        # Should never reach here — all paths return or raise
        raise RuntimeError("call_llm: exhausted retries without result")

    async def _call_provider_nonstream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Make non-streaming API call using provider system.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions

        Returns:
            Response content as string

        Raises:
            Exception: If provider call fails
        """
        logger.debug(f"Provider non-streaming call (model={self.model})")

        # Call provider
        response: UnifiedResponse = await self._provider.call(
            messages=messages,
            tools=tools,
        )

        # Extract token usage
        self.last_token_usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "cache_creation_tokens": response.usage.cache_creation_tokens,
            "cache_read_tokens": response.usage.cache_read_tokens,
        }

        # Extract tool calls for backward compatibility
        self.last_tool_calls = response.get_tool_uses()
        self.last_stop_reason = response.finish_reason or "stop"

        # Extract thinking/reasoning content
        self.last_thinking_content = response.get_thinking_content()

        # Capture raw upstream response for raw log
        self.last_raw_chunks = (
            [response.raw_response] if response.raw_response else []
        )

        # Extract text content
        content = response.get_text_content()

        logger.debug(
            f"Provider response received (tokens={response.usage.total_tokens}, "
            f"tool_calls={len(self.last_tool_calls)}, "
            f"thinking={'yes' if self.last_thinking_content else 'no'})"
        )

        return content

    async def _call_provider_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        streaming_callback=None,
    ) -> str:
        """Make streaming API call using provider system.

        Args:
            messages: Conversation messages
            tools: Optional tool definitions
            streaming_callback: Optional callback for streaming chunks

        Returns:
            Complete response content as string

        Raises:
            Exception: If provider stream fails
        """
        logger.debug(f"Provider streaming call (model={self.model})")

        # Initialize tool accumulator with mode from config
        use_legacy = not self._use_explicit_accumulation
        self._tool_accumulator = ToolCallAccumulator(legacy_mode=use_legacy)

        content_parts = []
        thinking_parts = []
        final_usage = None
        final_stop_reason = None
        accumulated_tools = []  # For EXPLICIT mode

        # Reset raw chunk buffer for this call. Streaming captures every
        # transformer-emitted raw_chunk so the raw log reflects what the
        # provider actually sent over the wire.
        self.last_raw_chunks = []

        try:
            # Stream provider responses
            async for streaming_response in self._provider.stream(
                messages=messages,
                tools=tools,
            ):
                # Check for cancellation
                if self.cancel_requested:
                    raise asyncio.CancelledError("Streaming request cancelled")

                # Capture raw upstream chunk for raw log (if transformer attached one)
                if streaming_response.raw_chunk is not None:
                    self.last_raw_chunks.append(streaming_response.raw_chunk)

                # Handle delta types
                delta = streaming_response.delta

                # Text content
                if isinstance(delta, TextDelta):
                    content_chunk = delta.content
                    content_parts.append(content_chunk)

                    # Call streaming callback if provided
                    if streaming_callback:
                        await streaming_callback(content_chunk)

                # Thinking/reasoning content
                elif isinstance(delta, ThinkingDelta):
                    if delta.content:
                        thinking_parts.append(delta.content)

                # Tool call delta
                elif isinstance(delta, ToolCallDelta):
                    if self._debug_tool_stream_path:
                        logger.info(
                            "STREAM_TOOL_DELTA id=%r name=%r args_len=%d",
                            delta.tool_call_id,
                            delta.tool_name,
                            len(delta.tool_arguments_delta or ""),
                        )
                    # Always call add_delta — accumulator handles None ids internally
                    # (Anthropic uses index-based routing: id comes in content_block_start,
                    # subsequent input_json_delta chunks have tool_call_id=None)
                    completed_tools = self._tool_accumulator.add_delta(
                        tool_call_id=delta.tool_call_id,
                        name=delta.tool_name,
                        arguments_delta=delta.tool_arguments_delta,
                    )

                    # EXPLICIT mode: add_delta returns completed tools immediately
                    if self._use_explicit_accumulation and completed_tools:
                        accumulated_tools.extend(completed_tools)
                        logger.debug(
                            f"EXPLICIT mode: {len(completed_tools)} tools completed "
                            f"({len(accumulated_tools)} total)"
                        )
                    if self._debug_tool_stream_path:
                        buf = (
                            self._tool_accumulator.get_buffer_status()
                            if self._tool_accumulator
                            else {}
                        )
                        logger.info(
                            "STREAM_TOOL_ACCUMULATOR buffers=%d completed_now=%d",
                            len(buf),
                            len(completed_tools or []),
                        )

                # Capture stop reason when available (e.g. "tool_use", "end_turn")
                if streaming_response.finish_reason:
                    final_stop_reason = streaming_response.finish_reason

                # Accumulate usage from any chunk that carries it
                # Anthropic sends input_tokens in message_start and
                # output_tokens in message_delta (not message_stop)
                if streaming_response.usage:
                    if final_usage is None:
                        final_usage = streaming_response.usage
                    else:
                        # Merge: accumulate across message_start (input) and
                        # message_delta (output) chunks
                        prompt = max(
                            final_usage.prompt_tokens,
                            streaming_response.usage.prompt_tokens,
                        )
                        completion = max(
                            final_usage.completion_tokens,
                            streaming_response.usage.completion_tokens,
                        )
                        cache_creation = max(
                            final_usage.cache_creation_tokens,
                            streaming_response.usage.cache_creation_tokens,
                        )
                        cache_read = max(
                            final_usage.cache_read_tokens,
                            streaming_response.usage.cache_read_tokens,
                        )
                        final_usage = UsageInfo(
                            prompt_tokens=prompt,
                            completion_tokens=completion,
                            total_tokens=prompt + completion,
                            cache_creation_tokens=cache_creation,
                            cache_read_tokens=cache_read,
                        )

            # Combine content
            content = "".join(content_parts)

            # Set token usage
            if final_usage:
                self.last_token_usage = {
                    "prompt_tokens": final_usage.prompt_tokens,
                    "completion_tokens": final_usage.completion_tokens,
                    "total_tokens": final_usage.total_tokens,
                    "cache_creation_tokens": final_usage.cache_creation_tokens,
                    "cache_read_tokens": final_usage.cache_read_tokens,
                }
            else:
                # Fallback to zero usage if not provided
                self.last_token_usage = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                }

            # Extract completed tool calls
            if self._use_explicit_accumulation:
                # EXPLICIT mode: tools already accumulated during streaming
                self.last_tool_calls = accumulated_tools
                logger.debug(
                    f"EXPLICIT mode: {len(accumulated_tools)} tools accumulated"
                )
            else:
                # LEGACY mode: batch retrieval at end
                self.last_tool_calls = self._tool_accumulator.get_completed_tools()
                logger.debug(
                    f"LEGACY mode: {len(self.last_tool_calls)} tools retrieved"
                )

            self.last_stop_reason = final_stop_reason or "stop"

            # Store accumulated thinking/reasoning content
            self.last_thinking_content = (
                "".join(thinking_parts) if thinking_parts else None
            )

            logger.debug(
                f"Provider streaming complete (tokens={self.last_token_usage.get('total_tokens', 0)}, "
                f"tool_calls={len(self.last_tool_calls)}, "
                f"thinking={'yes' if self.last_thinking_content else 'no'})"
            )
            if self._debug_tool_stream_path:
                acc_status = (
                    self._tool_accumulator.get_buffer_status()
                    if self._tool_accumulator
                    else {}
                )
                logger.info(
                    "STREAM_TOOL_SUMMARY stop_reason=%r text_len=%d tool_calls=%d buffers=%d",
                    self.last_stop_reason,
                    len(content),
                    len(self.last_tool_calls),
                    len(acc_status),
                )

            if (
                self.last_stop_reason == "tool_calls"
                and not self.last_tool_calls
                and not content.strip()
            ):
                logger.warning(
                    "INCONSISTENT_TOOL_STOP: stop_reason=tool_calls but no tool calls "
                    "and empty text content. Possible provider payload inconsistency "
                    "or transformer/accumulator loss."
                )

            return content

        finally:
            # Reset tool accumulator
            if self._tool_accumulator:
                self._tool_accumulator.reset()

    def _prepare_messages(
        self, conversation_history: List[Any], max_history: Optional[int]
    ) -> List[Dict[str, str]]:
        """Prepare conversation messages for API request.

        Args:
            conversation_history: Raw conversation history
            max_history: Maximum messages to include

        Returns:
            List of formatted messages for API
        """
        # Apply history limit if specified
        if max_history:
            recent_messages = conversation_history[-max_history:]
        else:
            recent_messages = conversation_history

        # Format messages for API
        messages: list[dict[str, Any]] = []
        for msg in recent_messages:
            # Handle both ConversationMessage objects and dicts
            if hasattr(msg, "role"):
                role, content = msg.role, msg.content
                meta: dict[str, Any] = getattr(msg, "metadata", {}) or {}
            else:
                role, content = msg["role"], msg["content"]
                meta = msg.get("metadata", {}) or {}

            formatted: dict[str, Any] = {"role": role, "content": content}

            # Preserve tool_calls for assistant messages
            # (needed by Responses API to build function_call items)
            if meta.get("tool_calls"):
                formatted["tool_calls"] = meta["tool_calls"]

            # Preserve tool_call_id for tool result messages
            # (needed by Responses API to build function_call_output items)
            if meta.get("tool_call_id"):
                formatted["tool_call_id"] = meta["tool_call_id"]

            messages.append(formatted)

        return messages

    def get_last_token_usage(self) -> Dict[str, int]:
        """Get token usage from last API call.

        Returns:
            Dictionary with token counts
        """
        return self.last_token_usage

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on provider service.

        Returns:
            Dictionary with health status information
        """
        health_status: Dict[str, Any] = {
            "healthy": True,
            "checks": {},
            "timestamp": time.time(),
        }

        # Check if provider is initialized
        provider_healthy = self._provider is not None and self._initialized
        health_status["checks"]["provider"] = {
            "healthy": provider_healthy,
            "provider_name": self._provider.provider_name if self._provider else None,
            "error": self._provider_error if not provider_healthy else None,
        }
        if not provider_healthy:
            health_status["healthy"] = False

        return health_status

    def get_provider_error(self) -> Optional[str]:
        """Get provider error message if initialization failed.

        Returns:
            Error message string or None if no error
        """
        return self._provider_error

    def is_provider_available(self) -> bool:
        """Check if provider is available for API calls.

        Returns:
            True if provider is initialized and ready
        """
        return self._provider is not None

    async def reinitialize_provider(self, profile: LLMProfile) -> bool:
        """Reinitialize provider with a new profile.

        Used when user changes profile via /profile command.

        Args:
            profile: New LLM profile configuration

        Returns:
            True if provider initialized successfully
        """
        # Update stored profile
        self._profile = profile
        self._provider_error = None

        # Update settings from new profile
        self.update_from_profile(profile)

        # Reinitialize provider
        await self._initialize_provider()

        if self._provider:
            logger.info(f"Provider reinitialized for profile '{profile.name}'")
            return True
        else:
            logger.warning(
                f"Provider reinitialization failed for profile '{profile.name}': "
                f"{self._provider_error}"
            )
            return False

    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics.

        Returns:
            Dictionary with connection stats
        """
        return dict(self._connection_stats)

    def get_api_stats(self) -> Dict[str, Any]:
        """Get API communication statistics.

        Returns:
            Dictionary with API statistics
        """
        return {
            "model": self.model,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "streaming_enabled": self.enable_streaming,
            "connection_stats": self.get_connection_stats(),
        }

    def _log_raw_interaction(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        response_content: Optional[str] = None,
        cancelled: bool = False,
        error: Optional[str] = None,
        duration: float = 0.0,
    ) -> None:
        """Log raw API request/response to raw conversations directory.

        Args:
            messages: Messages sent to API
            tools: Tool definitions sent (if any)
            response_content: Response text (None if error/cancelled)
            cancelled: Whether request was cancelled
            error: Error message if request failed
            duration: Request duration in seconds
        """
        try:
            if not self.raw_conversations_dir:
                return

            entry = {
                "timestamp": datetime.now().isoformat() + "Z",
                "session_id": self.current_session_id,
                "model": self.model,
                "provider": self._profile.provider if self._profile else "unknown",
                "streaming": self.enable_streaming,
                "duration_s": round(duration, 3),
                "request": {
                    "message_count": len(messages),
                    "messages": messages,
                    "tools": tools,
                },
                "response": {
                    "content": response_content,
                    "token_usage": self.last_token_usage,
                    "tool_calls": (
                        [
                            {"id": tc.id, "name": tc.name, "input": tc.input}
                            for tc in self.last_tool_calls
                        ]
                        if self.last_tool_calls
                        else []
                    ),
                    "stop_reason": self.last_stop_reason,
                    "raw_chunks": self.last_raw_chunks,
                },
                "cancelled": cancelled,
                "error": error,
            }

            if (
                self.last_stop_reason == "tool_calls"
                and not self.last_tool_calls
                and not (response_content or "").strip()
            ):
                logger.warning(
                    "RAW_LOG_INCONSISTENT_TOOL_STOP: writing empty content and zero "
                    "tool_calls with stop_reason=tool_calls to raw transcript"
                )

            # Write to session-specific raw log file
            raw_file = (
                self.raw_conversations_dir / f"{self.current_session_id}_raw.jsonl"
            )
            with open(raw_file, "a") as f:
                f.write(json.dumps(entry) + "\n")

        except Exception as e:
            logger.warning(f"Failed to log raw interaction: {e}")

    def has_pending_tool_calls(self) -> bool:
        """Check if there are pending tool calls from last response.

        Returns:
            True if there are pending tool calls to execute
        """
        return bool(self.last_tool_calls)

    def get_last_tool_calls(self) -> list:
        """Get tool calls from the last API response.

        Returns:
            List of ToolUseContent objects from last response
        """
        return self.last_tool_calls

    def format_tool_result(
        self, tool_id: str, result: Any, is_error: bool = False
    ) -> Dict[str, Any]:
        """Format tool result for conversation continuation.

        Creates an OpenAI-compatible tool result message format.

        Args:
            tool_id: ID of the tool call this is responding to
            result: Result from tool execution
            is_error: Whether the result is an error

        Returns:
            Dict with role and content for conversation
        """
        import json as json_module

        content = result if isinstance(result, str) else json_module.dumps(result)

        if is_error:
            content = f"Error: {content}"

        return {
            "role": "tool",
            "tool_call_id": tool_id,
            "content": content,
        }
