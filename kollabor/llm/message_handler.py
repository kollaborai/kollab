"""Message and event handler for LLMService.

This module contains all event handling methods that process incoming
messages and events from the event bus, delegating to LLMService's
orchestration methods.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MessageHandler:
    """Handles incoming events and messages for the LLM service.

    This class encapsulates all event handler methods that process
    incoming events from the event bus (USER_INPUT, CANCEL_REQUEST,
    ADD_MESSAGE, TRIGGER_LLM_CONTINUE, CONTEXT_INJECTION).

    Takes a single coordinator reference instead of individual dependencies.
    """

    # Cooldown after ESC cancel: hub continues are blocked for this many seconds
    HUB_CONTINUE_COOLDOWN = 5.0

    def __init__(self, coordinator):
        """Initialize the message handler.

        Args:
            coordinator: LLMService coordinator (provides event_bus, renderer,
                context_service, conversation_logger, message_display_service,
                api_service, conversation_history, and state properties)
        """
        self._coordinator = coordinator
        self._hub_continue_paused_until: float = 0.0
        self._retry_pending: bool = False

    async def handle_context_injection(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle context injection before user input processing.

        Scans user input for keyword triggers and auto-loads relevant context.

        Args:
            data: Event data containing user message
            event: The event object

        Returns:
            Unmodified data (context is injected as system message)
        """
        message = data.get("message", "")
        if message.strip():
            try:
                await self._coordinator.context_service.trigger_context_injection(
                    message
                )
            except Exception as e:
                logger.error(f"Context injection failed: {e}")
        return data

    async def handle_user_input(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle user input hook callback.

        This is called by the event bus when user input occurs.
        Gates on startup_ready to hold messages typed during initialization.

        Args:
            data: Event data containing user message
            event: The event object

        Returns:
            Result of processing
        """
        # Wait for startup to complete before processing user input
        startup_ready = self._coordinator.event_bus.get_service("startup_ready")
        if startup_ready and not startup_ready.is_set():
            try:
                await asyncio.wait_for(startup_ready.wait(), timeout=30)
            except asyncio.TimeoutError:
                logger.error("Timed out waiting for startup to complete")
                return {"status": "startup_timeout"}

        message = data.get("message", "")
        if message.strip():
            result = await self._coordinator.process_user_input(message)
            return result  # type: ignore[no-any-return]
        return {"status": "empty_message"}

    async def handle_cancel_request(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle cancel request hook callback.

        This is called by the event bus when a cancellation request occurs.

        Args:
            data: Event data containing cancellation reason
            event: The event object

        Returns:
            Result of cancellation
        """
        reason = data.get("reason", "unknown")
        source = data.get("source", "unknown")

        # Check if we're in pipe mode - ignore cancel requests from stdin
        renderer = self._coordinator.renderer
        if hasattr(renderer, "pipe_mode") and getattr(renderer, "pipe_mode", False):
            logger.info(
                f"LLM SERVICE: Ignoring cancel request in pipe mode (from {source}: {reason})"
            )
            return {"status": "ignored", "reason": "pipe_mode"}

        logger.info(f"LLM SERVICE: Cancel request hook called! From {source}: {reason}")
        logger.info(
            f"LLM SERVICE: Currently processing: {self._coordinator.is_processing}"
        )

        # Cancel current request (local coordinator)
        self._coordinator.cancel_current_request()

        # In attach mode the real processing is on the daemon, not this client.
        # Forward the cancel via the state service RPC so the daemon stops too.
        try:
            event_bus = self._coordinator.event_bus
            if event_bus:
                state_svc = event_bus.get_service("state_service")
                if state_svc and hasattr(state_svc, "cancel_current_request"):
                    self._coordinator.create_background_task(
                        state_svc.cancel_current_request(),
                        name="esc_cancel_daemon",
                    )
                    logger.info("ESC cancel forwarded to daemon via state_service RPC")
        except Exception as e:
            logger.debug(f"Could not forward cancel to daemon: {e}")

        # If the agent is externally parked, clear the waiting state so the
        # next user message gets processed instead of being ignored.
        if not self._coordinator.is_processing:
            try:
                hub = (
                    self._coordinator.event_bus.get_service("hub_plugin")
                    if self._coordinator.event_bus
                    else None
                )
                if hub and hasattr(hub, "_identity") and hub._identity:
                    if hub._identity.state == "waiting":
                        self._coordinator.create_background_task(
                            hub._exit_waiting_state(),
                            name="esc_clear_hub_waiting",
                        )
                        logger.info("ESC cleared hub waiting state (agent was parked)")
            except Exception as e:
                logger.debug(f"Could not clear hub waiting state on ESC: {e}")

        # Set hub continue cooldown so agents don't immediately re-trigger
        self._hub_continue_paused_until = time.monotonic() + self.HUB_CONTINUE_COOLDOWN
        logger.info(
            f"LLM SERVICE: Cancellation flag set: {self._coordinator.cancel_processing}, "
            f"hub continue paused for {self.HUB_CONTINUE_COOLDOWN}s"
        )
        return {"status": "cancelled", "reason": reason}

    async def handle_add_message(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle ADD_MESSAGE event - inject messages into conversation.

        This allows plugins to inject messages into the conversation that:
        - Get added to AI-visible history
        - Get logged to conversation logger
        - Get displayed to user with loading indicator
        - Optionally trigger LLM response

        Args:
            data: Event data with messages array and options
            event: The event object

        Returns:
            Result dict with status and message count
        """
        messages = data.get("messages", [])
        options = data.get("options", {})

        if not messages:
            return {"success": False, "error": "No messages provided"}

        show_loading = options.get("show_loading", True)
        loading_message = options.get("loading_message", "Loading...")
        log_messages = options.get("log_messages", True)
        add_to_history = options.get("add_to_history", True)
        display_messages = options.get("display_messages", True)
        trigger_llm = options.get("trigger_llm", False)
        parent_uuid = options.get("parent_uuid", self._coordinator.current_parent_uuid)

        coord = self._coordinator
        mds = coord.message_display_service

        try:
            # Show loading indicator
            if show_loading:
                mds.show_loading(loading_message)

            display_sequence: List[tuple] = []

            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")

                # Add to conversation history
                if add_to_history:
                    from kollabor_events.data_models import ConversationMessage

                    coord._add_conversation_message(
                        ConversationMessage(role=role, content=content),
                        parent_uuid=parent_uuid,
                    )

                # Log message
                if log_messages:
                    conv_logger = coord.conversation_logger
                    if role == "user":
                        parent_uuid = await conv_logger.log_user_message(
                            content, parent_uuid=parent_uuid
                        )
                    elif role == "assistant":
                        parent_uuid = await conv_logger.log_assistant_message(
                            content,
                            parent_uuid=parent_uuid,
                            model=coord.api_service.model,
                            thinking_content=None,
                        )
                    elif role == "system":
                        await conv_logger.log_system_message(
                            content, parent_uuid=parent_uuid
                        )

                # Build display sequence
                if display_messages and role in ("user", "assistant", "system"):
                    display_sequence.append((role, content, {}))

            # CRITICAL FIX: Display messages BEFORE hiding loading indicator
            # The loading indicator must remain active until messages are fully rendered.
            # If we hide loading first, the render loop may clear the screen before
            # messages are displayed, causing them to disappear.
            if display_messages and display_sequence:
                mds.message_coordinator.display_message_sequence(display_sequence)
                # Wait for messages to render before hiding loading
                await asyncio.sleep(0.1)

            # Now hide loading after messages are displayed
            if show_loading:
                mds.hide_loading()

            # Update session stats
            session_stats = coord.session_stats
            if hasattr(session_stats, "__setitem__"):
                session_stats["messages"] += len(messages)

            # Optionally trigger LLM response
            if trigger_llm:
                # Find the last user message to process
                last_user_msg = None
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        last_user_msg = msg.get("content", "")
                        break

                if last_user_msg:
                    await coord._enqueue_with_overflow_strategy(last_user_msg)
                    if not coord.is_processing:
                        # Trigger queue processing via background task
                        coord.create_background_task(
                            coord._process_queue(), name="process_queue"
                        )

            logger.info(
                f"ADD_MESSAGE: Processed {len(messages)} messages, trigger_llm={trigger_llm}"
            )
            return {
                "success": True,
                "message_count": len(messages),
                "parent_uuid": parent_uuid,
                "llm_triggered": trigger_llm,
            }

        except Exception as e:
            # Ensure loading is hidden on error
            if show_loading:
                mds.hide_loading()
            logger.error(f"Error in ADD_MESSAGE handler: {e}")
            return {"success": False, "error": str(e)}

    async def handle_llm_continue(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle TRIGGER_LLM_CONTINUE event - trigger LLM to process injected messages.

        This is called when plugins inject messages asynchronously (e.g., when a
        background agent completes) and want the LLM to respond to them.

        The message has already been added to conversation history by MessageInjector.
        This handler triggers the LLM to process and respond.

        Args:
            data: Event data (may contain source info)
            event: The event object

        Returns:
            Result dict with status
        """
        source = data.get("source", "unknown")
        coord = self._coordinator
        logger.info(f"TRIGGER_LLM_CONTINUE: Received from {source}")

        # Don't trigger in pipe mode (would interfere with normal flow)
        renderer = coord.renderer
        if hasattr(renderer, "pipe_mode") and getattr(renderer, "pipe_mode", False):
            logger.info("TRIGGER_LLM_CONTINUE: Pipe mode active, skipping")
            return {"status": "pipe_mode"}

        # Don't trigger during cooldown (user hit ESC to get a word in)
        if time.monotonic() < self._hub_continue_paused_until:
            logger.info("TRIGGER_LLM_CONTINUE: Cooldown active, skipping")
            return {"status": "cooldown"}

        # Don't trigger if user is typing (they're composing a message)
        if self._user_is_typing():
            logger.info("TRIGGER_LLM_CONTINUE: User is typing, deferring")
            return {"status": "user_typing"}

        try:
            # Hub continue: wraps _continue_conversation in a tool loop
            # so that if the LLM executes tools, it processes the results
            # (mirrors the process_queue continuation loop behavior).
            async def _hub_continue():
                qp = coord._queue_processor
                if qp.cancel_processing:
                    logger.info("Hub continue: cancel flag set, skipping")
                    return
                # Re-check cooldown and typing at execution time
                if time.monotonic() < self._hub_continue_paused_until:
                    logger.info("Hub continue: cooldown active at exec time, skipping")
                    return
                if self._user_is_typing():
                    logger.info("Hub continue: user typing at exec time, skipping")
                    return
                qp.is_processing = True
                qp.turn_completed = True
                hub_deadline = time.monotonic() + 300  # 5min max
                try:
                    await coord._continue_conversation()
                    turn_count = 0
                    while not qp.turn_completed and not qp.cancel_processing:
                        if time.monotonic() > hub_deadline:
                            logger.warning(
                                "Hub continue: exceeded 300s deadline, "
                                "forcing turn completion"
                            )
                            qp.turn_completed = True
                            break
                        turn_count += 1
                        logger.info(
                            f"Hub continue: tool results pending, continuing (turn {turn_count})"
                        )
                        try:
                            await coord._continue_conversation()
                        except Exception as e:
                            logger.error(f"Hub continue error (turn {turn_count}): {e}")
                            break
                finally:
                    qp.is_processing = False

            if coord.is_processing:
                # Coalesce: only one pending retry at a time. Peer messages
                # arriving during a busy turn get added to conversation_history
                # by the hub plugin before this handler runs, so a single
                # post-processing continuation will see all of them.
                if self._retry_pending:
                    logger.info(
                        "TRIGGER_LLM_CONTINUE: retry already queued, coalescing"
                    )
                    return {"status": "coalesced"}

                self._retry_pending = True
                logger.info(
                    "TRIGGER_LLM_CONTINUE: Processing active, queuing for retry"
                )

                async def _retry_continue():
                    try:
                        retry_deadline = time.monotonic() + 300  # 5min max wait
                        while coord.is_processing:
                            if time.monotonic() > retry_deadline:
                                logger.warning(
                                    "TRIGGER_LLM_CONTINUE: processing stuck for >300s, "
                                    "cancelling stale request"
                                )
                                coord.cancel_processing = True
                                break
                            await asyncio.sleep(1)
                        if coord.cancel_processing:
                            logger.info(
                                "TRIGGER_LLM_CONTINUE: Cancelled by user, skipping retry"
                            )
                            return
                        if time.monotonic() < self._hub_continue_paused_until:
                            logger.info(
                                "TRIGGER_LLM_CONTINUE: Cooldown active, skipping retry"
                            )
                            return
                        if self._user_is_typing():
                            logger.info("TRIGGER_LLM_CONTINUE: User typing, skipping retry")
                            return
                        logger.info(
                            "TRIGGER_LLM_CONTINUE: Retrying after processing completed"
                        )
                        if not coord.is_processing:
                            coord.create_background_task(
                                _hub_continue(), name="continue_conversation_retry"
                            )
                    finally:
                        self._retry_pending = False

                coord.create_background_task(
                    _retry_continue(), name="trigger_continue_retry"
                )
                return {"status": "queued_for_retry"}

            if not coord.is_processing:
                coord.create_background_task(
                    _hub_continue(), name="continue_conversation_hub"
                )

            logger.info("TRIGGER_LLM_CONTINUE: Triggered LLM processing")
            return {"status": "triggered", "source": source}

        except Exception as e:
            logger.error(f"Error in TRIGGER_LLM_CONTINUE handler: {e}")
            return {"status": "error", "error": str(e)}

    def _user_is_typing(self) -> bool:
        """Check if user has text in the input buffer.

        If the user is composing a message, hub auto-continues should
        defer to avoid stealing focus from user input.
        """
        coord = self._coordinator
        renderer = getattr(coord, "renderer", None)
        if not renderer:
            return False
        input_handler = getattr(renderer, "input_handler", None)
        if not input_handler:
            # Try from the app level
            input_handler = getattr(coord, "input_handler", None)
        if not input_handler:
            return False
        buffer_mgr = getattr(input_handler, "buffer_manager", None)
        if not buffer_mgr:
            buffer_mgr = getattr(input_handler, "_buffer_manager", None)
        if not buffer_mgr:
            return False
        # Check if there's text in the input buffer
        content = getattr(buffer_mgr, "content", "")
        if callable(content):
            content = content()
        return bool(content and content.strip())
