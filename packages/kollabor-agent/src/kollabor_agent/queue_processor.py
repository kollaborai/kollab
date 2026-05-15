"""Queue processing for Kollab LLM service.

Handles message queue management, overflow strategies, and LLM turn execution.
Extracted from LLMService as part of the llm_service.py decomposition.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from kollabor_agent.tool_executor import ToolExecutionResult
from kollabor_ai.cost_calculator import calculate_cost
from kollabor_events.data_models import ConversationMessage
from kollabor_events.models import EventType
from kollabor_tui.status.core_widgets import get_token_io_state

logger = logging.getLogger(__name__)


def _should_ingest(result: ToolExecutionResult) -> bool:
    """Check if a tool result is worth ingesting into the context ledger."""
    output = result.output if result.success else result.error
    return bool(output) and len(output.encode("utf-8", errors="replace")) >= 8192


def _tool_results_requiring_followup(
    results: List[ToolExecutionResult],
) -> List[ToolExecutionResult]:
    """Return tool results that need another LLM turn."""
    return list(results)


class QueueProcessor:
    """Handles queue processing and LLM turn execution.

    Responsibilities:
    - Message queue management with overflow strategies
    - Batch message processing
    - Conversation continuation (agentic turns)
    - Deduped LLM turn execution (_execute_llm_turn)
    - Queue state management (is_processing, turn_completed, etc.)

    Key Design:
    - _execute_llm_turn() deduplicates _process_message_batch and _continue_conversation
    - Only difference: _process_message_batch adds user message first
    - All queue state owned by this class (accessed via properties on LLMService)
    """

    def __init__(
        self,
        conversation_history: List[ConversationMessage],
        session_stats: Dict[str, Any],
        stats: Dict[str, Any],
        pending_tools: List[Dict[str, Any]],
        queue_metrics: Dict[str, Any],
        task_config,
        api_service,
        tool_executor,
        response_parser,
        message_display_service,
        renderer,
        config,
        event_bus,
        conversation_logger,
        streaming_handler,
        native_tools_handler,
        add_message_fn: Callable,
        max_history: int,
        question_gate_enabled: bool,
        max_queue_size: int,
    ):
        """Initialize queue processor.

        Args:
            conversation_history: Shared conversation history list (mutable reference)
            session_stats: Shared session stats dict (mutable reference)
            stats: Shared stats dict (mutable reference)
            pending_tools: Shared pending tools list (mutable reference)
            queue_metrics: Shared queue metrics dict (mutable reference)
            task_config: LLMTaskConfig for queue settings
            api_service: APICommunicationService instance
            tool_executor: ToolExecutor instance
            response_parser: ResponseParser instance
            message_display_service: MessageDisplayService instance
            renderer: TerminalRenderer instance
            config: Config service instance
            event_bus: EventBus instance
            conversation_logger: KollaborConversationLogger instance
            streaming_handler: StreamingHandler instance
            native_tools_handler: NativeToolsHandler instance
            add_message_fn: Callback to add message to conversation (LLMService._add_conversation_message)
            max_history: Maximum history messages for API calls
            question_gate_enabled: Whether question gate is enabled
            max_queue_size: Maximum queue size
        """
        # Shared mutable containers (passed by reference)
        self.conversation_history = conversation_history
        self.session_stats = session_stats
        self.stats = stats
        self.pending_tools = pending_tools
        self._queue_metrics = queue_metrics

        # Configuration and dependencies
        self.task_config = task_config
        self.api_service = api_service
        self.tool_executor = tool_executor
        self.response_parser = response_parser
        self.message_display_service = message_display_service
        self.renderer = renderer
        self.config = config
        self.event_bus = event_bus
        self.conversation_logger = conversation_logger
        self._streaming_handler = streaming_handler
        self._native_tools_handler = native_tools_handler
        self._add_message_fn = add_message_fn
        self._max_history = max_history
        self.question_gate_enabled = question_gate_enabled

        # Queue state (owned by QueueProcessor, accessed via properties)
        self.processing_queue: asyncio.Queue[Any] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self.max_queue_size = max_queue_size
        self.dropped_messages = 0
        self.is_processing = False
        self.turn_completed = False
        self.cancel_processing = False
        self.cancellation_message_shown = False

        # Processing state (owned by QueueProcessor)
        self.current_processing_tokens = 0
        self.processing_start_time: Optional[float] = None
        self.question_gate_active = False
        self._last_tool_error_sig: Optional[str] = None

    def _drain_env_block(self) -> str:
        """Drain pending env events and render as an [env: N events] block.

        Returns an empty string when the queue plugin isn't loaded or
        no events are pending. Never raises.
        """
        try:
            event_bus = getattr(self, "_event_bus", None) or getattr(
                self, "event_bus", None
            )
            if event_bus is None:
                return ""
            queue = event_bus.get_service("env_queue")
            if queue is None:
                return ""
            from kollabor_ai.notifications import render_env_block

            return render_env_block(queue.drain())
        except Exception:
            return ""

    async def enqueue(self, message: str) -> None:
        """Enqueue message with overflow strategy."""
        self._queue_metrics["total_enqueue_attempts"] += 1

        if self.task_config.queue.log_queue_events:
            logger.debug(
                f"Attempting to enqueue message (queue size: {self.processing_queue.qsize()}/{self.max_queue_size})"
            )

        # Try immediate enqueue
        try:
            self.processing_queue.put_nowait(message)
            self._queue_metrics["total_enqueue_successes"] += 1
            if self.task_config.queue.log_queue_events:
                logger.debug("Message enqueued successfully")
            return
        except asyncio.QueueFull:
            pass

        strategy = self.task_config.queue.overflow_strategy

        if strategy == "drop_oldest":
            await self._drop_oldest_strategy(message)
        elif strategy == "drop_newest":
            self._drop_newest_strategy()
        elif strategy == "block":
            await self._block_strategy(message)
        else:
            self._unknown_strategy(message)

    async def _drop_oldest_strategy(self, message: str) -> None:
        """Drop oldest task to make room."""
        if self.task_config.queue.log_queue_events:
            logger.debug("Applying drop_oldest strategy")

        # Find oldest task by start_time from task manager
        # Note: We need access to task_manager metadata here
        # For now, we'll drop from queue directly
        try:
            self.processing_queue.get_nowait()
            self._queue_metrics["drop_oldest_count"] += 1
            if self.task_config.queue.log_queue_events:
                logger.info("Dropped oldest message from queue")
        except asyncio.QueueEmpty:
            pass

        await asyncio.sleep(0.01)

        try:
            self.processing_queue.put_nowait(message)
            self._queue_metrics["total_enqueue_successes"] += 1
        except asyncio.QueueFull:
            self.dropped_messages += 1

    def _drop_newest_strategy(self) -> None:
        """Raise error when queue is full."""
        self._queue_metrics["drop_newest_count"] += 1
        if self.task_config.queue.log_queue_events:
            logger.debug("Applying drop_newest strategy - raising RuntimeError")
        raise RuntimeError(
            f"Queue is full (max size: {self.max_queue_size}) and overflow strategy is 'drop_newest'"
        )

    async def _block_strategy(self, message: str) -> None:
        """Block until queue has space or timeout."""
        self._queue_metrics["block_count"] += 1
        if self.task_config.queue.log_queue_events:
            logger.debug(
                f"Applying block strategy (timeout: {self.task_config.queue.block_timeout}s)"
            )

        start_time = time.time()
        poll_interval = 0.01

        while True:
            if self.processing_queue.qsize() < self.max_queue_size:
                try:
                    self.processing_queue.put_nowait(message)
                    self._queue_metrics["total_enqueue_successes"] += 1
                    return
                except asyncio.QueueFull:
                    pass

            elapsed = time.time() - start_time
            if (
                self.task_config.queue.block_timeout is not None
                and elapsed >= self.task_config.queue.block_timeout
            ):
                self._queue_metrics["block_timeout_count"] += 1
                if self.task_config.queue.log_queue_events:
                    logger.warning(
                        f"Block timeout after {elapsed:.2f}s, dropping message"
                    )
                self.dropped_messages += 1
                return

            await asyncio.sleep(poll_interval)

    def _unknown_strategy(self, message: str) -> None:
        """Handle unknown overflow strategy."""
        logger.warning(
            f"Unknown overflow strategy '{self.task_config.queue.overflow_strategy}', defaulting to drop_oldest"
        )
        try:
            self.processing_queue.get_nowait()
            self.processing_queue.put_nowait(message)
            self._queue_metrics["total_enqueue_successes"] += 1
        except asyncio.QueueEmpty:
            self.dropped_messages += 1

    async def process_queue(
        self,
        task_manager,
        process_message_batch_fn: Callable,
        continue_conversation_fn: Callable,
    ):
        """Process queued messages.

        Args:
            task_manager: BackgroundTaskManager for accessing task metadata
            process_message_batch_fn: Callable to process message batch
            continue_conversation_fn: Callable to continue conversation
        """
        self.is_processing = True
        self.current_processing_tokens = 0
        self.processing_start_time = time.time()
        logger.info("Started processing queue")

        try:
            # Outer loop: re-check queue after each conversation completes.
            # Messages can arrive (user typing, hub peers) while LOOP 2
            # continues the conversation.  Without this outer loop those
            # messages sit in the queue forever.
            while not self.cancel_processing:

                # LOOP 1 — drain queued messages
                while not self.processing_queue.empty() and not self.cancel_processing:
                    try:
                        messages = []
                        while not self.processing_queue.empty():
                            message = await self.processing_queue.get()
                            messages.append(message)

                        if messages and not self.cancel_processing:
                            await process_message_batch_fn(messages)

                    except Exception as e:
                        logger.error(f"Queue processing error: {e}")
                        error_msg = str(e)
                        if "'str' object has no attribute 'get'" in error_msg:
                            error_msg = (
                                "API format mismatch. Your profile's tool_format setting may be wrong.\n"
                                "Run /profile, press 'e' to edit, and check Tool Format matches your API."
                            )
                        self.message_display_service.display_error_message(error_msg)
                        break

                # LOOP 2 — continue conversation until turn completes
                turn_count = 0
                consecutive_errors = 0
                last_error_sig = None
                MAX_CONSECUTIVE_ERRORS = 3
                loop_deadline = time.monotonic() + 300  # 5min max
                while not self.turn_completed and not self.cancel_processing:
                    # User input takes priority: if a new message arrived
                    # while we're continuing, break out so LOOP 1 processes
                    # it as a fresh turn.
                    if not self.processing_queue.empty():
                        logger.info(
                            "New message arrived during continuation, "
                            "breaking to process"
                        )
                        break

                    if time.monotonic() > loop_deadline:
                        logger.warning(
                            "LOOP 2: exceeded 300s deadline, forcing turn completion"
                        )
                        self.turn_completed = True
                        break

                    try:
                        turn_count += 1
                        logger.info(
                            f"Turn not completed - continuing conversation "
                            f"(turn {turn_count})"
                        )
                        await continue_conversation_fn()

                        # Detect stuck loops (model repeating identical broken calls)
                        last_resp = getattr(self, "_last_tool_error_sig", None)
                        if last_resp and last_resp == last_error_sig:
                            consecutive_errors += 1
                            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                logger.warning(
                                    f"Circuit breaker: {consecutive_errors} identical "
                                    f"tool errors in a row, breaking loop"
                                )
                                self.message_display_service.display_error_message(
                                    f"Stuck loop detected: same tool error repeated "
                                    f"{consecutive_errors} times. Breaking."
                                )
                                self.turn_completed = True
                                break
                        else:
                            consecutive_errors = 0
                        last_error_sig = last_resp

                    except Exception as e:
                        logger.error(
                            f"Continued conversation error (turn {turn_count}): {e}"
                        )
                        self.message_display_service.display_error_message(
                            f"conversation failed on turn {turn_count}: {e}"
                        )
                        self.turn_completed = True
                        break

                # Nothing left -- exit outer loop
                if self.processing_queue.empty() or self.cancel_processing:
                    break

                # Messages arrived during LOOP 2 -- reset for next batch
                self.turn_completed = False
                logger.info("Re-entering queue drain after mid-turn message arrival")

        finally:
            self.is_processing = False
            self.current_processing_tokens = 0
            self.processing_start_time = None

            if self.cancel_processing:
                logger.info("Processing cancelled by user")
                if not self.cancellation_message_shown:
                    self.cancellation_message_shown = True
                    self.message_display_service.display_cancellation_message()
            else:
                logger.info("Finished processing queue")

            # Ensure render state is clean so the input box reappears.
            # Multi-tool sequences can leave writing_messages or the
            # render cache in a stale state if async render tasks
            # didn't fire before the next turn started.
            coordinator = getattr(self.renderer, "message_coordinator", None)
            if coordinator and hasattr(coordinator, "force_ready"):
                coordinator.force_ready()

    async def process_message_batch(
        self,
        messages: List[str],
        current_parent_uuid: str,
    ) -> str:
        """Process a batch of messages.

        Args:
            messages: List of user messages to process
            current_parent_uuid: Current parent UUID for message threading

        Returns:
            Updated parent UUID
        """
        combined_message = "\n".join(messages)

        # Add user message to conversation history
        self._add_message_fn(
            ConversationMessage(role="user", content=combined_message),
            parent_uuid=current_parent_uuid,
        )

        # Execute LLM turn with user message
        return await self._execute_llm_turn(
            user_message_provided=True,
            current_parent_uuid=current_parent_uuid,
        )

    async def continue_conversation(self, current_parent_uuid: str) -> str:
        """Continue an ongoing conversation (no new user message).

        Args:
            current_parent_uuid: Current parent UUID for message threading

        Returns:
            Updated parent UUID
        """
        return await self._execute_llm_turn(
            user_message_provided=False,
            current_parent_uuid=current_parent_uuid,
        )

    async def _execute_llm_turn(
        self,
        user_message_provided: bool,
        current_parent_uuid: str,
    ) -> str:
        """Execute a single LLM turn (deduped from process_message_batch and continue_conversation).

        The ONLY difference between the two call sites:
        - process_message_batch: adds user message to history first (user_message_provided=True)
        - continue_conversation: skips user message step (user_message_provided=False)

        Args:
            user_message_provided: True if user message was just added to history
            current_parent_uuid: Current parent UUID for message threading

        Returns:
            Updated parent UUID

        Raises:
            asyncio.CancelledError: If request was cancelled by user
        """
        # Context service: signal new turn for curator throttling
        context_svc = None
        if self.event_bus:
            _cs = self.event_bus.get_service("context_service")
            if (
                _cs is not None
                and type(_cs).__module__ != "unittest.mock"
                and hasattr(_cs, "increment_turn")
            ):
                context_svc = _cs
        if context_svc is not None:
            context_svc.increment_turn()

        # Start thinking animation - Sending phase
        self.renderer.update_thinking(True, "Sending...")
        thinking_start = time.time()

        # Estimate input tokens for status display
        total_input_chars = sum(
            len(msg.content) for msg in self.conversation_history[-3:]
        )
        estimated_input_tokens = total_input_chars // 4
        self.current_processing_tokens = estimated_input_tokens

        # Trigger token I/O upload animation
        get_token_io_state().start_upload(estimated_input_tokens)

        # Switch to Waiting phase before API call
        self.renderer.update_thinking(True, "Waiting...")
        get_token_io_state().start_waiting()

        # Emit LLM_REQUEST_PRE directly (POST emitted separately after API call)
        await self.event_bus.emit_with_hooks(
            EventType.LLM_REQUEST_PRE,
            {
                "model": getattr(self.api_service, "model", "unknown"),
                "message_count": len(self.conversation_history),
                "max_history": self._max_history,
            },
            "llm_service",
        )

        response = None
        parent_uuid = current_parent_uuid

        try:
            # Context service: inject ephemeral prompts (curator,
            # snapshot, confirmation) before the API call.
            context_svc = None
            if self.event_bus:
                _svc = self.event_bus.get_service("context_service")
                # Guard against MagicMock from test fixtures
                if (
                    _svc is not None
                    and type(_svc).__module__ != "unittest.mock"
                    and hasattr(_svc, "build_curator_injection")
                ):
                    context_svc = _svc
            if context_svc is not None:
                injections: list[str] = []

                # Curator prompt has highest priority
                curator = context_svc.build_curator_injection()
                if curator:
                    injections.append(curator)
                else:
                    # Snapshot and confirmation only when curator isn't active
                    snapshot = context_svc.build_context_snapshot()
                    if snapshot:
                        injections.append(snapshot)
                    confirmation = context_svc.build_confirmation_injection()
                    if confirmation:
                        injections.append(confirmation)
                    if hasattr(context_svc, "build_divergence_warnings"):
                        divergence = context_svc.build_divergence_warnings()
                        if divergence:
                            injections.append(divergence)

                # Env notification queue drains regardless of curator state —
                # capability / peer events shouldn't wait for the curator.
                env_block = self._drain_env_block()
                if env_block:
                    injections.append(env_block)

                if injections:
                    combined = "\n\n---\n\n".join(injections)
                    # Prepend to last user message in conversation history
                    for i in range(len(self.conversation_history) - 1, -1, -1):
                        msg = self.conversation_history[i]
                        if getattr(msg, "role", "") == "user":
                            msg.content = combined + "\n\n---\n\n" + msg.content
                            break

            # Call LLM API via streaming handler (with auto-continuation on truncation)
            MAX_CONTINUATIONS = 3
            continuation_count = 0
            accumulated_response = ""
            accumulated_tokens = {
                "prompt": 0, "completion": 0,
                "cache_creation": 0, "cache_read": 0,
            }

            # turn_id binds the initial call and any auto-continuations
            # together in the raw log so a truncated-then-continued
            # response is stitchable. The initial call gets a fresh id;
            # each continuation references it as parent.
            root_turn_id = str(uuid.uuid4())

            response = await self._streaming_handler.call_llm(
                conversation_history=self.conversation_history,
                max_history=self._max_history,
                native_tools=self._native_tools_handler.tools,
                mcp_discovery_complete=self._native_tools_handler.discovery_complete,
                is_cancelled_fn=lambda: self.cancel_processing,
                turn_id=root_turn_id,
            )

            # Auto-continue if response was truncated (stop_reason=length)
            continuation_msg_count = 0
            while (
                getattr(self.api_service, "last_stop_reason", "") == "length"
                and continuation_count < MAX_CONTINUATIONS
                and not self.cancel_processing
            ):
                continuation_count += 1

                # Accumulate token usage from the truncated call
                trunc_usage = self.api_service.get_last_token_usage()
                if trunc_usage:
                    accumulated_tokens["prompt"] += trunc_usage.get(
                        "prompt_tokens", 0
                    )
                    accumulated_tokens["completion"] += trunc_usage.get(
                        "completion_tokens", 0
                    )
                    accumulated_tokens["cache_creation"] += trunc_usage.get(
                        "cache_creation_tokens", 0
                    )
                    accumulated_tokens["cache_read"] += trunc_usage.get(
                        "cache_read_tokens", 0
                    )

                logger.warning(
                    f"Response truncated (stop_reason=length), "
                    f"auto-continuing ({continuation_count}/{MAX_CONTINUATIONS})"
                )
                accumulated_response += response or ""

                # Add partial response as assistant, then ask to continue
                self.conversation_history.append(
                    ConversationMessage(role="assistant", content=response or "")
                )
                self.conversation_history.append(
                    ConversationMessage(
                        role="user",
                        content=(
                            "[system] Your response was truncated due to output "
                            "token limits. Continue exactly where you left off."
                        ),
                    )
                )
                continuation_msg_count += 2

                self.renderer.update_thinking(
                    True, f"Continuing... ({continuation_count})"
                )
                response = await self._streaming_handler.call_llm(
                    conversation_history=self.conversation_history,
                    max_history=self._max_history,
                    native_tools=self._native_tools_handler.tools,
                    mcp_discovery_complete=self._native_tools_handler.discovery_complete,
                    is_cancelled_fn=lambda: self.cancel_processing,
                    parent_turn_id=root_turn_id,
                )

            # If we continued, merge response and clean up history fragments
            if accumulated_response:
                response = accumulated_response + (response or "")
                logger.info(
                    f"Auto-continuation complete after {continuation_count} retries, "
                    f"merged response length: {len(response)}"
                )
                # Remove the intermediate fragment messages from history
                # (partial assistant + "[system] continue" pairs)
                if continuation_msg_count > 0:
                    del self.conversation_history[-continuation_msg_count:]

            # Update session stats with actual token usage (accumulated across continuations)
            token_usage = self.api_service.get_last_token_usage()
            prompt_tokens = accumulated_tokens["prompt"]
            completion_tokens = accumulated_tokens["completion"]
            cache_creation_tokens = accumulated_tokens["cache_creation"]
            cache_read_tokens = accumulated_tokens["cache_read"]
            if token_usage:
                prompt_tokens += token_usage.get("prompt_tokens", 0)
                completion_tokens += token_usage.get("completion_tokens", 0)
                cache_creation_tokens += token_usage.get(
                    "cache_creation_tokens", 0
                )
                cache_read_tokens += token_usage.get("cache_read_tokens", 0)

                # Finalize token I/O with actual counts
                token_io = get_token_io_state()
                token_io.upload_target = prompt_tokens
                token_io.upload_tokens = prompt_tokens
                token_io.upload_display = prompt_tokens
                token_io.finish(completion_tokens)

                # Store and accumulate stats
                self.session_stats["input_tokens"] = prompt_tokens
                self.session_stats["output_tokens"] = completion_tokens
                self.session_stats["total_input_tokens"] += prompt_tokens
                self.session_stats["total_output_tokens"] += completion_tokens
                # Cache metrics (anthropic + openai)
                self.session_stats["cache_creation_tokens"] = cache_creation_tokens
                self.session_stats["cache_read_tokens"] = cache_read_tokens
                self.session_stats["total_cache_creation_tokens"] = (
                    self.session_stats.get("total_cache_creation_tokens", 0)
                    + cache_creation_tokens
                )
                self.session_stats["total_cache_read_tokens"] = (
                    self.session_stats.get("total_cache_read_tokens", 0)
                    + cache_read_tokens
                )

                # Cost calculation
                provider_type = getattr(
                    self.api_service, "provider_type", ""
                )
                model = getattr(self.api_service, "model", "unknown")
                turn_cost = calculate_cost(
                    provider_type,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    cache_read_tokens,
                )
                self.session_stats["cost_usd"] = turn_cost
                self.session_stats["total_cost_usd"] = (
                    self.session_stats.get("total_cost_usd", 0.0)
                    + turn_cost
                )

                logger.debug(
                    f"Token usage: {prompt_tokens} input, {completion_tokens} output, "
                    f"cache_write={cache_creation_tokens}, cache_read={cache_read_tokens}"
                )

            # Emit LLM_REQUEST_POST with actual token data
            # Also read from session_stats which may have been set by streaming
            final_in = prompt_tokens or self.session_stats.get("input_tokens", 0)
            final_out = completion_tokens or self.session_stats.get("output_tokens", 0)
            await self.event_bus.emit_with_hooks(
                EventType.LLM_REQUEST_POST,
                {
                    "model": getattr(self.api_service, "model", "unknown"),
                    "message_count": len(self.conversation_history),
                    "input_tokens": final_in,
                    "output_tokens": final_out,
                    "cache_creation_tokens": cache_creation_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cost_usd": self.session_stats.get("cost_usd", 0.0),
                    "total_cost_usd": self.session_stats.get("total_cost_usd", 0.0),
                },
                "llm_service",
            )

            # ----------------------------------------------------------
            # UNIFIED PIPELINE: single path for native + XML + plugin tools
            # ----------------------------------------------------------

            # Step 1: Extract native tool calls (if any)
            has_native_tools = (
                self._native_tools_handler.tool_calling_enabled
                and self.api_service.has_pending_tool_calls()
            )
            raw_tool_calls = []
            if has_native_tools:
                logger.info("Processing native tool calls from API response")
                raw_tool_calls = self.api_service.get_last_tool_calls()

            # Step 2: Parse ALL tags from response text (always runs)
            thinking_duration = time.time() - thinking_start
            self.renderer.update_thinking(False)

            # Brief pause for clean transition
            await asyncio.sleep(self.config.get("kollabor.llm.processing_delay", 0.1))

            parsed_response = self.response_parser.parse_response(response)
            clean_response = parsed_response["content"]
            all_tools = self.response_parser.get_all_tools(parsed_response)

            # Step 3: Emit LLM_THINKING (merge native reasoning + XML thinking)
            thinking_blocks = parsed_response.get("components", {}).get("thinking", [])
            native_thinking = self.api_service.last_thinking_content
            if native_thinking and native_thinking not in thinking_blocks:
                thinking_blocks.append(native_thinking)
            if thinking_blocks and self.event_bus:
                await self.event_bus.emit_with_hooks(
                    EventType.LLM_THINKING,
                    {
                        "content": thinking_blocks[0][:200] if thinking_blocks else "",
                        "thinking": thinking_blocks,
                        "block_count": len(thinking_blocks),
                    },
                    "llm_service",
                )

            # Update turn completion and stats. response_parser counts XML
            # tools only (terminal_commands, tool_calls, file_operations,
            # plugin_tools), not native API tool_use blocks. If the model
            # returned native tools, the turn is NOT done -- the model
            # still needs to see the tool results. Exception: question_gate
            # active means the user must answer first regardless.
            self.turn_completed = parsed_response["turn_completed"]
            question_gate_active = parsed_response.get("question_gate_active", False)
            has_xml_question = (
                parsed_response.get("components", {}).get("question") is not None
            )
            if has_native_tools and not (question_gate_active or has_xml_question):
                self.turn_completed = False
            self.stats["total_thinking_time"] += thinking_duration
            self.session_stats["messages"] += 1

            # Show "Receiving..." briefly before displaying
            if clean_response.strip() or all_tools or has_native_tools:
                estimated_tokens = len(clean_response) // 4 if clean_response else 0
                self.current_processing_tokens = estimated_tokens
                self.renderer.update_thinking(
                    True, f"Receiving... ({estimated_tokens} tokens)"
                )
                await asyncio.sleep(self.config.get("kollabor.llm.thinking_delay", 0.3))
                self.renderer.update_thinking(False)

            # Step 4: Emit LLM_RESPONSE (hub/plugins can set force_continue, etc.)
            clean_response, force_continue, suppress_display, turn_complete = (
                await self._emit_llm_response_and_handle(
                    response, clean_response, thinking_duration,
                    all_tools=all_tools, has_native_tools=has_native_tools,
                )
            )
            if force_continue:
                self.turn_completed = False
                logger.info("Plugin requested turn continuation")
            if turn_complete:
                self.turn_completed = True
                logger.debug("Plugin requested turn completion")

            # Step 5: Display clean text (before tool results)
            if suppress_display:
                logger.info("Hub consumed response, display suppressed")
            if not suppress_display:
                self.message_display_service.display_complete_response(
                    thinking_duration=thinking_duration,
                    response=clean_response,
                    tool_results=None,
                )

            # Step 6: Execute native tools (batch via native_tools_handler)
            native_results = []
            if has_native_tools:
                tool_count = len(raw_tool_calls)
                tool_desc = (
                    raw_tool_calls[0].name if tool_count == 1 else f"{tool_count} tools"
                )
                self.renderer.update_thinking(True, f"Executing {tool_desc}...")

                native_results = await self._native_tools_handler.execute_tool_calls(
                    self.tool_executor
                )
                self.renderer.update_thinking(False)

                # Display native tool results
                # NOTE: Native tool results are ALWAYS displayed, even when
                # suppress_display is True.  suppress_display controls text
                # content (the hub plugin sets it when clean_response is empty
                # after stripping hub XML tags).  But native tool calls arrive
                # via a separate channel (API tool_use blocks, not text), so
                # an empty clean_response is expected when the LLM only emits
                # tool calls.  The user must see tool boxes regardless.
                if native_results:
                    original_tools_for_display = [
                        {"name": tc.name, "arguments": tc.input}
                        for tc in raw_tool_calls
                    ]
                    self.message_display_service.display_tool_results(
                        native_results, original_tools_for_display
                    )

            # Step 7: Execute XML tools (incremental, with question gate)
            xml_tool_results = []
            if all_tools:
                if self.question_gate_enabled and parsed_response.get(
                    "question_gate_active"
                ):
                    self.pending_tools.clear()
                    self.pending_tools.extend(all_tools)
                    self.question_gate_active = True
                    logger.info(
                        f"Question gate: suspended {len(all_tools)} tool(s) pending user response"
                    )
                elif not suppress_display:
                    # Execute tools one at a time and display each result
                    # incrementally so the user sees progress in real time.
                    for i, tool_data in enumerate(all_tools):
                        if self.tool_executor.is_cancelled():
                            # Add cancelled results for remaining tools
                            for remaining in all_tools[i:]:
                                xml_tool_results.append(
                                    ToolExecutionResult(
                                        tool_id=remaining.get("id", "unknown"),
                                        tool_type=remaining.get("type", "unknown"),
                                        success=False,
                                        error="Cancelled by user",
                                        metadata={"cancelled": True},
                                    )
                                )
                            break

                        tool_count = len(all_tools)
                        tool_desc = tool_data.get("type", "tool")
                        self.renderer.update_thinking(
                            True,
                            f"Executing {tool_desc}... ({i + 1}/{tool_count})",
                        )

                        result = await self.tool_executor.execute_tool(tool_data)
                        xml_tool_results.append(result)

                        self.renderer.update_thinking(False)

                        self.message_display_service.display_tool_results(
                            [result], [tool_data]
                        )

                        # Yield to event loop so the render task
                        # (scheduled by message_coordinator) can redraw
                        # the input bar and status widgets before the
                        # next tool's thinking animation starts.
                        await asyncio.sleep(0)
                else:
                    # suppress_display: execute XML tools but still display results.
                    # suppress_display only controls text content rendering (hub
                    # plugin sets it when clean_response is empty after stripping
                    # XML tags).  Tool execution results must always be visible so
                    # the user can see what the agent is doing.
                    logger.info(
                        f"[TOOL-DISPLAY-DEBUG] suppress_display else branch: "
                        f"all_tools={len(all_tools)}, "
                        f"cancel={self.cancel_processing}"
                    )
                    for i, tool_data in enumerate(all_tools):
                        if self.cancel_processing:
                            break
                        result = await self.tool_executor.execute_tool(tool_data)
                        xml_tool_results.append(result)
                        logger.info(
                            f"[TOOL-DISPLAY-DEBUG] calling display_tool_results "
                            f"for {result.tool_type}:{result.tool_id} "
                            f"success={result.success}"
                        )
                        self.message_display_service.display_tool_results(
                            [result], [tool_data]
                        )
                        await asyncio.sleep(0)

            # Step 8: Bridge relay
            await self._bridge_relay(clean_response)

            # Step 9: Conversation logging + history
            # Build tool call entries for JSONL logging
            tool_call_entries = None
            if has_native_tools:
                # Native tools: structured entries from API response
                tool_call_entries = [
                    {"id": tc.id, "name": tc.name, "input": tc.input}
                    for tc in raw_tool_calls
                ]
            elif all_tools:
                # XML tools: type/params format
                tool_call_entries = [
                    {
                        "id": t.get("id", f"xml_{i}"),
                        "name": t.get("type", "unknown"),
                        "input": {
                            k: v for k, v in t.items() if k not in ("type", "id")
                        },
                    }
                    for i, t in enumerate(all_tools)
                ]

            # Log assistant response
            thinking_list = thinking_blocks if thinking_blocks else None
            parent_uuid = await self.conversation_logger.log_assistant_message(
                clean_response or response,
                parent_uuid=parent_uuid,
                model=self.api_service.model,
                usage_stats={
                    "input_tokens": self.session_stats.get("input_tokens", 0),
                    "output_tokens": self.session_stats.get("output_tokens", 0),
                    "thinking_duration": thinking_duration,
                },
                thinking_content=thinking_list,
                tool_calls=tool_call_entries,
            )

            # Add assistant message to conversation history
            # BRANCH POINT: history format differs between native and XML paths
            if has_native_tools:
                # Native path: store tool_calls in metadata so Responses API
                # can rebuild function_call items with proper call_ids
                assistant_metadata = {}
                if raw_tool_calls:
                    assistant_metadata["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": (
                                    json.dumps(tc.input)
                                    if isinstance(tc.input, dict)
                                    else str(tc.input or "{}")
                                ),
                            },
                        }
                        for tc in raw_tool_calls
                    ]
                self._add_message_fn(
                    ConversationMessage(
                        role="assistant",
                        content=response,
                        metadata=assistant_metadata,
                    ),
                    parent_uuid=parent_uuid,
                )

                # Native tool results: add to history in native API format
                # so provider can reconstruct tool_result turns correctly
                for result in native_results:
                    output_text = result.output if result.success else result.error
                    await self.conversation_logger.log_system_message(
                        f"Executed {result.tool_type} ({result.tool_id}): {output_text}",
                        parent_uuid=parent_uuid,
                        subtype="tool_result",
                        tool_use_id=result.tool_id,
                    )
                    tool_calls_list = self.api_service.get_last_tool_calls()
                    for tc in tool_calls_list:
                        if tc.id == result.tool_id:
                            msg = self.api_service.format_tool_result(
                                tc.id,
                                output_text,
                                is_error=not result.success,
                            )
                            self.conversation_history.append(
                                ConversationMessage(
                                    role=msg.get("role", "tool"),
                                    content=str(msg.get("content", result.output)),
                                    metadata={"tool_call_id": tc.id},
                                )
                            )
                            break
                    self._track_file_interaction(result)

                # Plugin/XML tool results from same response: batched user message
                history_results = list(xml_tool_results)
                if history_results:
                    batched = []
                    for result in history_results:
                        output = result.output if result.success else result.error
                        await self.conversation_logger.log_system_message(
                            f"Executed {result.tool_type} ({result.tool_id}): {output}",
                            parent_uuid=parent_uuid,
                            subtype="tool_result",
                            tool_use_id=result.tool_id,
                        )
                        tool_context = (
                            self.tool_executor.format_result_for_conversation(result)
                        )
                        batched.append(f"Tool result: {tool_context}")
                        self._track_file_interaction(result)
                    if batched:
                        import uuid as _uuid
                        tool_msg_uuid = str(_uuid.uuid4())
                        tool_msg = ConversationMessage(
                            role="user",
                            content="\n".join(batched),
                        )
                        self.conversation_history.append(tool_msg)
                        self._ingest_tool_results(
                            history_results, tool_msg_uuid,
                            message=tool_msg,
                        )
            else:
                # XML path: simple assistant message + batched tool results
                self._add_message_fn(
                    ConversationMessage(role="assistant", content=response),
                    parent_uuid=parent_uuid,
                )

                history_results_xml = list(xml_tool_results)
                if history_results_xml:
                    batched_tool_results = []
                    for result in history_results_xml:
                        output = result.output if result.success else result.error
                        await self.conversation_logger.log_system_message(
                            f"Executed {result.tool_type} ({result.tool_id}): {output}",
                            parent_uuid=parent_uuid,
                            subtype="tool_result",
                            tool_use_id=result.tool_id,
                        )
                        tool_context = self.tool_executor.format_result_for_conversation(
                            result
                        )
                        batched_tool_results.append(f"Tool result: {tool_context}")
                        self._track_file_interaction(result)
                    if batched_tool_results:
                        import uuid as _uuid
                        tool_msg_uuid = str(_uuid.uuid4())
                        tool_msg = ConversationMessage(
                            role="user",
                            content="\n".join(batched_tool_results),
                        )
                        self.conversation_history.append(tool_msg)
                        self._ingest_tool_results(
                            history_results_xml, tool_msg_uuid,
                            message=tool_msg,
                        )

            # Build error signature for circuit breaker (detect stuck loops)
            all_results = native_results + xml_tool_results
            failed = [r for r in all_results if not r.success]
            if failed:
                sig = "|".join(
                    f"{r.tool_type}:{(r.error or '')[:80]}" for r in failed
                )
                self._last_tool_error_sig = sig
            else:
                self._last_tool_error_sig = None

            # Step 10: Determine continuation
            # If tools executed, the LLM MUST see their results back. Natural
            # turn completion happens when the model returns no tool calls.
            if _tool_results_requiring_followup(all_results):
                self.turn_completed = False

        except asyncio.CancelledError:
            logger.info("Message processing cancelled by user")
            thinking_duration = time.time() - thinking_start
            self.renderer.update_thinking(False)
            self.renderer.clear_active_area()

            if not self.cancellation_message_shown:
                self.cancellation_message_shown = True
                self.message_display_service.display_cancellation_message()

            self.turn_completed = True
            self.stats["total_thinking_time"] += thinking_duration

        except Exception as e:
            logger.error(f"Error processing message: {type(e).__name__}: {e}")
            self.renderer.update_thinking(False)
            error_msg = str(e) or f"{type(e).__name__} (no details)"
            if "'str' object has no attribute 'get'" in error_msg:
                error_msg = (
                    "API format mismatch. Your profile's tool_format setting may be wrong.\n"
                    "Run /profile, press 'e' to edit, and check Tool Format matches your API."
                )
            self.message_display_service.display_error_message(error_msg)
            self.turn_completed = True

        return parent_uuid

    # ------------------------------------------------------------------
    # Shared helpers (used by both native and XML tool paths)
    # ------------------------------------------------------------------

    def _track_file_interaction(self, result: ToolExecutionResult) -> None:
        """Record a successful file operation in the conversation logger.

        Populates conversation_logger.file_interactions so that
        conversation_end.files_modified is accurate.

        Args:
            result: A tool execution result to check.
        """
        if not result.success or not result.tool_type.startswith("file_"):
            return
        file_path = result.metadata.get("file_path")
        if file_path:
            self.conversation_logger.record_file_interaction(
                file_path, result.tool_type
            )
        to_path = result.metadata.get("to_path")
        if to_path:
            self.conversation_logger.record_file_interaction(
                to_path, result.tool_type
            )

    def _ingest_tool_results(
        self,
        results: List[ToolExecutionResult],
        message_uuid: str,
        message: Optional[Any] = None,
    ) -> None:
        """Ingest heavy tool results into the context service ledger.

        Called after tool results are added to conversation history.
        Each result >= 8KB is added as a ledger entry.

        Args:
            results: The tool execution results to check.
            message_uuid: UUID of the conversation message containing
                the batched tool results.
            message: Optional ConversationMessage to tag with ctx_ids.
        """
        context_svc = None
        if self.event_bus:
            context_svc = self.event_bus.get_service("context_service")
        if context_svc is None:
            return

        for result in results:
            if not _should_ingest(result):
                continue

            output = result.output if result.success else result.error
            content_bytes = output.encode("utf-8", errors="replace")

            tool_type = result.tool_type or "unknown"
            if tool_type in ("read", "file_read"):
                kind = "file_read"
                label = "file read"
                file_path = None
                if hasattr(result, "metadata") and result.metadata:
                    label = result.metadata.get("file_path", label)
                    file_path = result.metadata.get("file_path")
            else:
                kind = "tool_result"
                label = tool_type
                file_path = None

            try:
                entry = context_svc.ingest_heavy_item(
                    kind=kind,
                    tool=tool_type,
                    label=label,
                    content=content_bytes,
                    message_uuid=message_uuid,
                    file_path=file_path,
                )
                if entry and message is not None:
                    ctx_ids = message.metadata.setdefault("ctx_ids", [])
                    ctx_ids.append(entry.ctx_id)
            except Exception as e:
                logger.warning(
                    f"Failed to ingest tool result into context "
                    f"service: {e}"
                )

    async def _emit_llm_response_and_handle(
        self,
        response_text: str,
        clean_response: str,
        thinking_duration: float,
        log_prefix: str = "",
        all_tools: Optional[list] = None,
        has_native_tools: bool = False,
    ) -> tuple:
        """Emit LLM_RESPONSE event and extract hub plugin modifications.

        Returns (clean_response, force_continue, suppress_display, turn_complete).
        """
        force_continue = False
        suppress_display = False
        turn_complete = False
        had_hub_tags = "<hub_msg" in (response_text or "")
        log_tag = f"{log_prefix}_" if log_prefix else ""

        if not self.event_bus:
            return clean_response, force_continue, suppress_display, turn_complete

        response_context = await self.event_bus.emit_with_hooks(
            EventType.LLM_RESPONSE,
            {
                "response_text": response_text,
                "clean_response": clean_response,
                "thinking_duration": thinking_duration,
                "tool_results": None,
                "all_tools": all_tools or [],
                "has_native_tools": has_native_tools,
                "turn_completed": self.turn_completed,
            },
            "llm_service",
        )

        if response_context:
            for phase in ["pre", "main", "post"]:
                phase_data = response_context.get(phase, {})
                final_data = phase_data.get("final_data", {})
                if final_data.get("force_continue"):
                    force_continue = True
                if final_data.get("suppress_display"):
                    suppress_display = True
                if final_data.get("turn_complete"):
                    turn_complete = True
                if "clean_response" in final_data:
                    clean_response = final_data["clean_response"]
                    if had_hub_tags:
                        logger.info(
                            f"[HUB_STRIP{log_tag}] phase={phase} "
                            f"has_tags={'<hub_msg' in clean_response} "
                            f"len={len(clean_response)} "
                            f"preview={clean_response[:80]!r}"
                        )

        return clean_response, force_continue, suppress_display, turn_complete

    async def _bridge_relay(self, clean_response: str) -> None:
        """Send response to external platform if last user message was from bridge."""
        if not clean_response or not self.event_bus:
            return
        try:
            hub = self.event_bus.get_service("hub_plugin")
            if hub and getattr(hub, "_bridge", None):
                history = getattr(
                    self.event_bus.get_service("llm_service"),
                    "conversation_history",
                    [],
                )
                for msg in reversed(history):
                    if msg.role == "user" and getattr(msg, "metadata", None):
                        if msg.metadata.get("bridge_platform"):
                            logger.info(
                                "Bridge relay: sending response to external platform"
                            )
                            await hub.bridge_send(clean_response)
                            break
                    elif msg.role == "user":
                        break
        except Exception as e:
            logger.debug(f"Bridge relay check: {e}")
