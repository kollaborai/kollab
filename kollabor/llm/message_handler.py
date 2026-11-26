"""Message and event handler for LLMService.

This module contains all event handling methods that process incoming
messages and events from the event bus, delegating to LLMService's
orchestration methods.
"""

import asyncio
import inspect
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List

from kollabor.user_input_source import HUMAN_USER_INPUT_SOURCES, YES_SOURCES
from kollabor_agent.queue_processor import (
    MAX_CONTINUATION_TURNS,
    RepeatedToolErrorBreaker,
)
from kollabor_agent.voice_admission import validate_voice_admission_context
from kollabor_ai.message_content import content_to_text
from kollabor_tui.display_tap import publish_semantic
from kollabor_tui.turn_context import current_turn

from .agent_hud import agent_hud_hub_metadata, format_agent_hud

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# On an ACP chat these native-only events are refused; ACP input goes through
# submit_human_input (#579).
ACP_SUBMIT_REASON = "acp_requires_submit_human_input"


class MessageHandler:
    """Handles incoming events and messages for the LLM service.

    This class encapsulates all event handler methods that process
    incoming events from the event bus (USER_INPUT, CANCEL_REQUEST,
    ADD_MESSAGE, TRIGGER_LLM_CONTINUE, CONTEXT_INJECTION).

    Takes a single coordinator reference instead of individual dependencies.
    """

    # Cooldown after ESC cancel: hub continues are blocked for this many seconds
    HUB_CONTINUE_COOLDOWN = 5.0

    # Boot-hold policy for user input arriving before startup completes.
    # First hold waits ~immediately; one delayed retry (30s) follows. After
    # STARTUP_HOLD_MAX_ATTEMPTS the message is marked {final: False,
    # undelivered: True} instead of being dropped silently.
    STARTUP_HOLD_MAX_ATTEMPTS = 2
    STARTUP_HOLD_INITIAL_WAIT = 30
    STARTUP_HOLD_RETRY_WAIT = 30

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
        self._retry_wake_data: dict[str, Any] | None = None

    @staticmethod
    def _merge_retry_wake_data(
        current: dict[str, Any] | None, incoming: dict[str, Any]
    ) -> dict[str, Any]:
        """Retain every hub id and structured note while one retry is pending."""
        merged = {**(current or {}), **incoming}
        request_ids: list[str] = []
        notes: dict[str, dict[str, Any]] = {}
        for source in (current or {}, incoming):
            values = source.get("pending_hub_message_ids")
            if isinstance(values, list):
                request_ids.extend(
                    value for value in values if isinstance(value, str) and value
                )
            for key in ("hub_message_id", "request_id"):
                value = source.get(key)
                if isinstance(value, str) and value and (
                    source.get("hub_message_id")
                    or isinstance(source.get("hub_note"), dict)
                    or source.get("input_source") == "hub"
                ):
                    request_ids.append(value)
            entries = source.get("pending_hub_notes")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    message_id = entry.get("message_id")
                    hub_note = entry.get("hub_note")
                    if (
                        isinstance(message_id, str)
                        and message_id
                        and isinstance(hub_note, dict)
                    ):
                        notes[message_id] = entry
            message_id = source.get("hub_message_id") or source.get("request_id")
            hub_note = source.get("hub_note")
            if isinstance(message_id, str) and message_id and isinstance(hub_note, dict):
                notes[message_id] = {
                    "message_id": message_id,
                    "hub_note": hub_note,
                    "admission": source.get("hub_admission", "input"),
                }
        merged["pending_hub_message_ids"] = list(dict.fromkeys(request_ids))
        if notes:
            merged["pending_hub_notes"] = list(notes.values())
        primary = current or incoming
        primary_id = primary.get("hub_message_id") or primary.get("request_id")
        if isinstance(primary_id, str) and primary_id:
            merged["hub_message_id"] = primary_id
            merged["request_id"] = primary_id
            primary_note = primary.get("hub_note")
            if isinstance(primary_note, dict):
                merged["hub_note"] = primary_note
                merged["hub_admission"] = primary.get("hub_admission", "input")
        return merged

    @staticmethod
    def _hub_input_ids(
        data: dict[str, Any], hud_metadata: dict[str, Any] | None = None
    ) -> list[str]:
        """Get the hub inputs that should share the native wake's lifecycle."""
        is_hub_wake = bool(
            data.get("hub_message_id")
            or isinstance(data.get("hub_note"), dict)
            or data.get("input_source") == "hub"
            or isinstance(data.get("pending_hub_message_ids"), list)
        )
        if not is_hub_wake:
            return []
        ids: list[str] = []
        candidates = data.get("pending_hub_message_ids")
        if isinstance(candidates, list):
            ids.extend(value for value in candidates if isinstance(value, str) and value)
        candidates = data.get("request_ids")
        if isinstance(candidates, list):
            ids.extend(value for value in candidates if isinstance(value, str) and value)
        for key in ("hub_message_id", "request_id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                ids.append(value)
        ids = list(dict.fromkeys(ids))

        admissions: dict[str, str] = {}
        sources = [data]
        if isinstance(hud_metadata, dict):
            sources.append(hud_metadata)
        for source in sources:
            entries = source.get("pending_hub_notes")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    message_id = entry.get("message_id")
                    admission = entry.get("admission")
                    if isinstance(message_id, str) and isinstance(admission, str):
                        admissions[message_id] = admission
            entries = source.get("hub_notes")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    message_id = entry.get("id")
                    admission = entry.get("admission")
                    if isinstance(message_id, str) and isinstance(admission, str):
                        admissions[message_id] = admission
            message_id = source.get("hub_message_id")
            admission = source.get("hub_admission")
            if isinstance(message_id, str) and isinstance(admission, str):
                admissions[message_id] = admission
        return [message_id for message_id in ids if admissions.get(message_id) != "note"]

    @staticmethod
    def _hub_notes_by_id(
        data: dict[str, Any], hud_metadata: dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Return the structured notes available for a claimed native wake."""
        notes: dict[str, dict[str, Any]] = {}
        for source in (data, hud_metadata or {}):
            entries = source.get("pending_hub_notes")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    message_id = entry.get("message_id")
                    note = entry.get("hub_note")
                    if not isinstance(message_id, str):
                        message_id = entry.get("id")
                    if not isinstance(note, dict):
                        note = entry.get("part")
                    if isinstance(message_id, str) and message_id and isinstance(note, dict):
                        notes[message_id] = note
            entries = source.get("hub_notes")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    message_id = entry.get("id")
                    note = entry.get("part")
                    if isinstance(message_id, str) and message_id and isinstance(note, dict):
                        notes[message_id] = note
            message_id = source.get("hub_message_id") or source.get("request_id")
            note = source.get("hub_note")
            if isinstance(message_id, str) and message_id and isinstance(note, dict):
                notes[message_id] = note
        return notes

    def _downgrade_hub_wake(self, data: dict[str, Any]) -> None:
        """Keep a wake visible as a note when it did not start its own turn."""
        coord = self._coordinator
        notes: dict[str, dict[str, Any]] = {}
        entries = data.get("pending_hub_notes")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                message_id = entry.get("message_id")
                hub_note = entry.get("hub_note")
                if isinstance(message_id, str) and isinstance(hub_note, dict):
                    notes[message_id] = hub_note
        message_id = data.get("hub_message_id") or data.get("request_id")
        hub_note = data.get("hub_note")
        if isinstance(message_id, str) and message_id and isinstance(hub_note, dict):
            notes[message_id] = hub_note
        for message_id, note in notes.items():
            publish_semantic(
                coord.event_bus,
                "hub_message",
                message_id=message_id,
                request_id=message_id,
                input_source="hub",
                hub_note=note,
                hub_admission="note",
            )

    async def _persist_hub_input_row(
        self,
        input_id: str,
        note: dict[str, Any],
        turn_id: str,
        status: str,
    ) -> None:
        """Persist native Hub input lifecycle beside, never inside, model history."""
        coord = self._coordinator
        manager = getattr(coord, "conversation_manager", None)
        rows = getattr(manager, "chat_stream_rows", None)
        existing = next(
            (
                row
                for row in rows or []
                if isinstance(row, dict) and row.get("id") == input_id
            ),
            None,
        )
        from kollabor_ai.conversation_logger import utc_stamp

        row = {
            "id": input_id,
            "seq": existing.get("seq", 0) if isinstance(existing, dict) else 0,
            "kind": "input",
            "role": "user",
            "turn_id": turn_id,
            "status": status,
            "parts": [dict(note)],
            "created_at": (
                existing.get("created_at")
                if isinstance(existing, dict)
                and isinstance(existing.get("created_at"), str)
                else utc_stamp()
            ),
        }
        record_row = getattr(manager, "record_hub_chat_stream_row", None)
        if callable(record_row):
            try:
                recorded_row = record_row(
                    row, history=getattr(coord, "conversation_history", None)
                )
                if isinstance(recorded_row, dict):
                    row = recorded_row
            except Exception:
                logger.debug("Could not update native Hub row in memory", exc_info=True)

        conversation_logger = getattr(coord, "conversation_logger", None)
        log_system_message = getattr(
            conversation_logger, "log_system_message", None
        )
        if not callable(log_system_message):
            return
        try:
            result = log_system_message(
                "",
                parent_uuid=getattr(coord, "current_parent_uuid", None),
                subtype="hub_chat_stream_row",
                chat_stream_row=row,
            )
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Could not persist native Hub row update", exc_info=True)

    async def _report_detached_chain_break(self, reason: str) -> None:
        """Notify a detached agent's parent when the tool loop is abandoned."""
        event_bus = getattr(self._coordinator, "event_bus", None)
        if event_bus is None or not hasattr(event_bus, "get_service"):
            return
        try:
            hub = event_bus.get_service("hub_plugin")
            if hub is None:
                return
            report = getattr(hub, "report_detached_chain_break", None)
            if not callable(report):
                return
            result = report(reason=reason)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("detached chain-break notification failed", exc_info=True)

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
        display_text = content_to_text(message)
        if display_text.strip():
            try:
                await self._coordinator.context_service.trigger_context_injection(
                    display_text
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
        message = data.get("message", "")
        display_text = content_to_text(message)
        if not display_text.strip():
            return {"status": "empty_message"}

        pre_displayed = bool(data.get("message_pre_displayed", False))

        # Wait for startup to complete before processing user input.
        # If startup is still pending, echo the user's message immediately
        # so typed input is visible during boot, but keep LLM processing gated.
        startup_ready = self._coordinator.event_bus.get_service("startup_ready")
        if startup_ready and not startup_ready.is_set():
            if not pre_displayed:
                self._coordinator.message_display_service.display_user_message(
                    display_text
                )
                data["message_pre_displayed"] = True
                pre_displayed = True
            # Bounded re-enqueue: the first wait is immediate-ish, then one
            # delayed retry. A message is never dropped silently — after the
            # cap it is marked undelivered so callers (and the voice seam)
            # can distinguish "held during boot" from "lost".
            held_attempts = int(data.get("startup_hold_attempts", 0))
            if held_attempts >= self.STARTUP_HOLD_MAX_ATTEMPTS:
                logger.error(
                    "Startup still pending after %d holds; marking message "
                    "undelivered",
                    held_attempts,
                )
                data["final"] = False
                data["undelivered"] = True
                return {
                    "status": "startup_timeout",
                    "final": False,
                    "undelivered": True,
                }
            data["startup_hold_attempts"] = held_attempts + 1
            hold_timeout = (
                self.STARTUP_HOLD_INITIAL_WAIT
                if held_attempts == 0
                else self.STARTUP_HOLD_RETRY_WAIT
            )
            try:
                await asyncio.wait_for(startup_ready.wait(), timeout=hold_timeout)
            except asyncio.TimeoutError:
                logger.error(
                    "Timed out waiting for startup to complete (attempt %d)",
                    held_attempts + 1,
                )
                # Re-enqueue the same event data so the message survives boot
                # instead of being dropped. The incremented attempts counter
                # rides along in `data` and bounds the loop.
                try:
                    await self._coordinator.event_bus.emit(
                        "user_input",
                        dict(data),
                    )
                except Exception as emit_error:  # pragma: no cover - defensive
                    logger.error(
                        "Failed to re-enqueue held user input: %s", emit_error
                    )
                    data["final"] = False
                    data["undelivered"] = True
                    return {
                        "status": "startup_timeout",
                        "final": False,
                        "undelivered": True,
                    }
                return {"status": "startup_hold", "requeued": True}

        origin_context = data.get("origin_context")
        claims_voice_origin = (
            isinstance(origin_context, dict)
            and origin_context.get("source") == "livekit_voice"
        )
        if claims_voice_origin and not validate_voice_admission_context(origin_context):
            return {"status": "rejected", "reason": "Invalid or expired voice admission"}
        if not claims_voice_origin:
            origin_context = None
        origin_kwargs = {"origin_context": origin_context} if origin_context else {}
        input_source = getattr(event, "source", None)
        source_value = getattr(input_source, "value", input_source)
        origin_kwargs["operator_source"] = (
            str(source_value)
            if source_value in HUMAN_USER_INPUT_SOURCES
            else None
        )
        # Only a person answering live (YES_SOURCES) answers what the agent asked
        # them. A hub peer's message (the "agent" source) is a turn like the user's,
        # but its words never do.
        if getattr(event, "source", None) in YES_SOURCES:
            origin_kwargs["may_answer"] = True

        request_id = data.get("request_id")
        if isinstance(request_id, str) and request_id:
            origin_kwargs["request_id"] = request_id  # the client follows the turn by it (#551)

        if data.get("source") == "voice" and data.get("voice"):
            plugin = self._coordinator.event_bus.get_service("voice_plugin")
            if plugin is None:
                return {"status": "rejected", "reason": "Voice admission is unavailable"}
            # The turn's grant comes only from the admission (VoicePlugin.admit),
            # never from the event: the voice tool policy governs it either way.
            return await plugin.admit(
                data,
                lambda tool_context=None: self._coordinator.process_user_input(
                    message,
                    pre_displayed=pre_displayed,
                    voice=data["voice"],
                    **{**origin_kwargs, "origin_context": tool_context},
                ),
            )

        thread_id = data.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            origin_kwargs["thread_id"] = thread_id
        admission_origin = data.get("origin_context")
        carries_hub_identity = source_value == "hub_message" or (
            source_value == "agent"
            and isinstance(admission_origin, dict)
            and admission_origin.get("authenticated") is True
        )
        if carries_hub_identity and isinstance(admission_origin, dict):
            hub_note = admission_origin.get("hub_note")
            if isinstance(hub_note, dict):
                origin_kwargs["hub_note"] = dict(hub_note)
                hub_message_id = admission_origin.get("hub_message_id") or request_id
                if isinstance(hub_message_id, str) and hub_message_id:
                    origin_kwargs["hub_message_id"] = hub_message_id
        queue_handoff = data.get("queue_handoff")
        if queue_handoff is not None:
            from .queue_handoff import QueueHandoffContext

            if (
                not isinstance(queue_handoff, QueueHandoffContext)
                or request_id != queue_handoff.request_id
            ):
                return {"status": "rejected", "reason": "invalid_queue_capability"}
            origin_kwargs["queue_handoff"] = queue_handoff
        queue_handoff_batch = data.get("queue_handoff_batch")
        if queue_handoff_batch is not None:
            origin_kwargs["queue_handoff_batch"] = queue_handoff_batch
        force_next_turn = data.get("force_next_turn", False)
        if not isinstance(force_next_turn, bool):
            return {"status": "rejected", "reason": "invalid_force_send"}
        if force_next_turn:
            origin_kwargs["force_next_turn"] = True
            origin_kwargs["force_reservation"] = data.get("force_reservation")
            origin_kwargs["force_gate_token"] = data.get("force_gate_token")
        result = await self._coordinator.process_user_input(
            message, pre_displayed=pre_displayed, **origin_kwargs
        )
        return result  # type: ignore[no-any-return]

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
                        lambda: state_svc.cancel_current_request(),
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
                            lambda: hub._exit_waiting_state(),
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
        if trigger_llm and self._acp_owns_chat():
            # Refuse before any history, log or display write: a native reply
            # would answer behind the ACP agent's back (#579).
            self._coordinator.message_display_service.display_error_message(
                "ACP owns this chat, so these messages were not added and no native "
                "reply was started. Send them from the chat to reach the ACP agent."
            )
            return {"success": False, "error": ACP_SUBMIT_REASON}
        parent_uuid = options.get("parent_uuid", self._coordinator.current_parent_uuid)

        coord = self._coordinator
        mds = coord.message_display_service

        try:
            # Show loading indicator
            if show_loading:
                mds.show_loading(loading_message)

            display_sequence: List[tuple] = []
            # Messages whose history write failed. The batch still runs, but it is not a success.
            history_failures = 0

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

                # Log message. A failed write leaves the chain on the previous
                # record; the rest of the batch still reaches history.
                if log_messages:
                    conv_logger = coord.conversation_logger
                    log_content = content_to_text(content)
                    try:
                        if role == "user":
                            parent_uuid = await conv_logger.log_user_message(
                                log_content, parent_uuid=parent_uuid
                            )
                        elif role == "assistant":
                            parent_uuid = await conv_logger.log_assistant_message(
                                log_content,
                                parent_uuid=parent_uuid,
                                model=coord.api_service.model,
                                thinking_content=None,
                            )
                        elif role == "system":
                            await conv_logger.log_system_message(
                                log_content, parent_uuid=parent_uuid
                            )
                    except Exception as exc:
                        history_failures += 1
                        logger.error("Message not written to history: %s", exc)

                # Build display sequence
                if display_messages and role in ("user", "assistant", "system"):
                    visible = content_to_text(content)
                    if role == "assistant":
                        from kollabor_ai.response_channels import display_response_text

                        visible = display_response_text(visible)
                    display_sequence.append((role, visible, {}))

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
                            lambda: coord._process_queue(), name="process_queue"
                        )

            logger.info(
                f"ADD_MESSAGE: Processed {len(messages)} messages, trigger_llm={trigger_llm}"
            )
            result = {
                "success": history_failures == 0,
                "message_count": len(messages),
                "parent_uuid": parent_uuid,
                "llm_triggered": trigger_llm,
            }
            if history_failures:
                result["error"] = f"{history_failures} message(s) not written to history"
            return result

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
        # Never log a relay: address (it carries a device key), whoever sent it.
        logger.info(
            "TRIGGER_LLM_CONTINUE: Received from %s",
            re.sub(r"relay:\S+", "relay:<address>", str(source)),
        )

        # Don't trigger in pipe mode (would interfere with normal flow)
        renderer = coord.renderer
        if hasattr(renderer, "pipe_mode") and getattr(renderer, "pipe_mode", False):
            logger.info("TRIGGER_LLM_CONTINUE: Pipe mode active, skipping")
            self._downgrade_hub_wake(data)
            return {"status": "pipe_mode"}

        # ACP owns the chat: a native continuation, now or from a retry, would
        # answer behind the ACP agent's back. Refuse before scheduling anything (#579).
        if self._acp_owns_chat():
            self._downgrade_hub_wake(data)
            coord.message_display_service.display_error_message(
                "A message from another agent or a background task was not run natively, "
                "because ACP owns this chat. Send it from the chat to reach the ACP agent."
            )
            return {"status": "rejected", "reason": ACP_SUBMIT_REASON}

        # Cooldown (user hit ESC to get a word in) or a half-typed message:
        # wait it out below instead of dropping the wake. A dropped wake left
        # the agent silent for good.
        blocked = self._hub_continue_blocked()

        try:
            # Hub continue: wraps _continue_conversation in a tool loop
            # so that if the LLM executes tools, it processes the results
            # (mirrors the process_queue continuation loop behavior).
            async def _hub_continue(wake_data: dict[str, Any] | None = None):
                wake_data = wake_data if isinstance(wake_data, dict) else data
                qp = coord._queue_processor
                if qp.cancel_processing:
                    if qp.is_processing or qp.cancel_origin == "remote_task":
                        logger.info("Hub continue: cancel flag set, skipping")
                        self._downgrade_hub_wake(wake_data)
                        return
                    # ESC latch from a drain that already stopped: the cooldown
                    # gave the human their window, so a new wake runs.
                    qp.cancel_processing = False
                if qp.is_processing:
                    logger.info("Hub continue: a turn is already running, it sees the message")
                    self._downgrade_hub_wake(wake_data)
                    return
                # The hub wake is a new admitted turn. Its HUD row and all
                # continuation writes must use a fresh ticket, not whichever
                # queue turn last populated the shared cursor.
                turn_serial = coord.claim_turn_serial()
                coord._continue_serial = turn_serial
                # Fresh chain (#676): the wake's first continuation parents
                # on the pointer (the mention reply this wake answers), not
                # the previous chain's tail.
                coord._batch_tail_uuid = None
                qp.is_processing = True
                # Set turn_completed=False so _continue_conversation does not
                # inject pending_agent_hud as a new user message. Setting True
                # here caused the session-dying bug: _continue_conversation
                # saw turn_completed=True, injected HUD nudges as user input,
                # the LLM responded to the nudge text instead of continuing
                # work, produced no tool calls, and the session went silent.
                qp.turn_completed = False
                # A hub wake starts a new operator-driven turn, not the
                # block-requested continuation (that runs through the queue
                # drain's continue_conversation, which keeps the flag). Reset
                # the Stop-hook budget: a turn that died mid-block (ESC/cancel)
                # must not leak stop_hook_active: true into this wake's Stop
                # payload — a parity hook reads it as its own continuation and
                # under-blocks once — and the block budget must not carry over
                # (#357).
                qp._stop_hook_active = False
                qp._stop_hook_continuations = 0
                # Wall-clock checkpoints only — never kill a working chain.
                # Force-completing on a timer orphaned the last turn's tool
                # results (they were in history but the LLM never got called
                # to see them) and the session went silent until an external
                # trigger (the "agent stops mid-tool-call" bug, 2026-07-03).
                # Chains end via: model completing the turn, user input
                # arriving (break below), ESC (cancel_processing), or the
                # error breaks. The checkpoint just logs progress every 5min.
                chain_start = time.monotonic()
                checkpoint_at = chain_start + 300
                turn_id = None
                turn_context_token = None
                previous_active_request_ids = getattr(qp, "active_request_ids", ())
                turn_tap = None
                turn_listener = None
                terminal_seen = False
                terminal_stop_reason = None
                terminal_reason = None
                try:
                    # A message that arrived while busy was buffered as HUD,
                    # and turn_completed=False keeps _continue_conversation
                    # from injecting it. Deliver it once at chain start.
                    pop_pending = getattr(coord, "pop_pending_agent_hud", None)
                    hud_entries = pop_pending() if callable(pop_pending) else None
                    if isinstance(hud_entries, (list, tuple)):
                        hud = format_agent_hud(hud_entries)
                        hud_metadata = agent_hud_hub_metadata(hud_entries)
                    else:
                        drain = getattr(coord, "drain_pending_agent_hud", None)
                        hud = drain() if callable(drain) else ""
                        hud_metadata = {}
                    input_ids = self._hub_input_ids(wake_data, hud_metadata)
                    hub_notes = self._hub_notes_by_id(wake_data, hud_metadata)
                    turn_id = f"native-{uuid.uuid4().hex}" if input_ids else None
                    if turn_id is not None:
                        qp.active_request_ids = tuple(input_ids)
                        current_turn_context = {
                            **current_turn.get(),
                            "turn_id": turn_id,
                            "request_id": input_ids[0],
                            "request_ids": list(input_ids),
                            "input_source": "hub",
                        }
                        turn_context_token = current_turn.set(current_turn_context)

                        # The producer first records every native message as
                        # a note. Promote only the messages this claimed turn
                        # actually answers, before publishing turn_start.
                        for input_id in input_ids:
                            note = hub_notes.get(input_id)
                            if isinstance(note, dict):
                                await self._persist_hub_input_row(
                                    input_id, note, turn_id, "started"
                                )
                                publish_semantic(
                                    coord.event_bus,
                                    "hub_message",
                                    message_id=input_id,
                                    request_id=input_id,
                                    input_source="hub",
                                    hub_note=note,
                                    hub_admission="input",
                                )
                        for history_message in getattr(
                            coord, "conversation_history", []
                        ):
                            if isinstance(history_message, dict):
                                message_metadata = history_message.get("metadata")
                            else:
                                message_metadata = getattr(
                                    history_message, "metadata", None
                                )
                            if (
                                isinstance(message_metadata, dict)
                                and message_metadata.get("hub_message_id") in input_ids
                            ):
                                message_metadata["hub_admission"] = "input"

                        get_service = getattr(coord.event_bus, "get_service", None)
                        if callable(get_service):
                            try:
                                turn_tap = get_service("display_tap")
                            except Exception:
                                turn_tap = None
                        add_listener = getattr(turn_tap, "add_listener", None)
                        if callable(add_listener):

                            def observe_turn_terminal(event):
                                nonlocal terminal_seen, terminal_stop_reason
                                if (
                                    isinstance(event, dict)
                                    and event.get("type") == "turn_complete"
                                    and event.get("turn_id") == turn_id
                                ):
                                    terminal_seen = True
                                    reason = event.get("stop_reason")
                                    if isinstance(reason, str) and reason:
                                        terminal_stop_reason = reason

                            turn_listener = observe_turn_terminal
                            add_listener(turn_listener)
                        publish_semantic(
                            coord.event_bus,
                            "turn_start",
                            request_id=input_ids[0],
                            request_ids=list(input_ids),
                            turn_id=turn_id,
                            input_source="hub",
                        )
                    if isinstance(hud, str) and hud:
                        from kollabor_events.data_models import ConversationMessage

                        hub_note = wake_data.get("hub_note")
                        hub_message_id = wake_data.get("hub_message_id") or wake_data.get("request_id")
                        metadata = {
                            "agent_hud": True,
                            "agent_hud_sources": ["hub"],
                            "stream_context_only": True,
                            **hud_metadata,
                        }
                        if isinstance(hub_note, dict):
                            metadata.update(
                                {
                                    "hub_message_id": hub_message_id,
                                    "hub_note": dict(hub_note),
                                    "hub_admission": "input",
                                }
                            )
                        hud_uuid = coord._add_conversation_message(
                            ConversationMessage(
                                role="user",
                                content=hud,
                                metadata=metadata,
                            ),
                            parent_uuid=coord.current_parent_uuid,
                            publish_user_event=False,
                        )
                        coord.publish_cursor(hud_uuid, turn_serial)
                    try:
                        await coord._continue_conversation()
                    except Exception as e:
                        logger.error(f"Hub continue error (turn 1): {e}")
                        terminal_reason = "error"
                        await self._report_detached_chain_break(
                            reason="conversation continuation failed"
                        )
                        qp.turn_completed = True
                    turn_count = 0
                    # Same identical-error breaker as the queue drain loop; the
                    # turn above counts toward the run.
                    stuck = RepeatedToolErrorBreaker()
                    stuck.observe(qp._last_tool_error_sig)
                    while not qp.turn_completed and not qp.cancel_processing:
                        # User input takes priority: yield the chain so the
                        # finally-block drain processes the queued message.
                        # Pending tool results stay in history and are
                        # consumed by that message's turn — nothing orphans.
                        if not qp.processing_queue.empty():
                            logger.info(
                                "Hub continue: user message arrived, yielding chain to queue drain"
                            )
                            terminal_reason = "interrupted"
                            break
                        if time.monotonic() > checkpoint_at:
                            elapsed = int(time.monotonic() - chain_start)
                            logger.warning(
                                f"Hub continue: chain still working after "
                                f"{elapsed}s ({turn_count} turns) — not "
                                f"killing; ESC or new input interrupts"
                            )
                            checkpoint_at = time.monotonic() + 300
                        if turn_count >= MAX_CONTINUATION_TURNS:
                            # No-progress spin backstop. A healthy chain
                            # completes or yields long before this; hitting
                            # it means turn_completed never flipped, so stop
                            # rather than loop forever.
                            logger.error(
                                f"Hub continue: hit MAX_CONTINUATION_TURNS "
                                f"({MAX_CONTINUATION_TURNS}) without completing "
                                f"— stopping runaway chain"
                            )
                            await self._report_detached_chain_break(
                                reason="maximum continuation turns reached"
                            )
                            terminal_reason = "error"
                            qp.turn_completed = True
                            break
                        turn_count += 1
                        # DEBUG, not INFO: this fires every continuation turn.
                        # At INFO it was 98.8% of a 4.3GB/day log (2026-07-03).
                        # Progress is surfaced by the 5-min checkpoint WARNING.
                        logger.debug(
                            f"Hub continue: tool results pending, continuing (turn {turn_count})"
                        )
                        try:
                            await coord._continue_conversation()
                        except Exception as e:
                            logger.error(f"Hub continue error (turn {turn_count}): {e}")
                            terminal_reason = "error"
                            await self._report_detached_chain_break(
                                reason="conversation continuation failed"
                            )
                            qp.turn_completed = True
                            break
                        if stuck.observe(qp._last_tool_error_sig):
                            logger.warning(
                                f"Hub continue: {stuck.count} identical tool "
                                f"errors in a row, breaking chain"
                            )
                            coord.message_display_service.display_error_message(
                                f"Stuck loop detected: same tool error repeated "
                                f"{stuck.count} times. Breaking."
                            )
                            await self._report_detached_chain_break(
                                reason=f"{stuck.count} identical tool errors"
                            )
                            terminal_reason = "error"
                            qp.turn_completed = True
                            break
                    if qp.cancel_processing and terminal_reason is None:
                        terminal_reason = "cancelled"
                except asyncio.CancelledError:
                    terminal_reason = terminal_reason or "cancelled"
                    raise
                except Exception:
                    terminal_reason = terminal_reason or "error"
                    raise
                finally:
                    if turn_id is not None:
                        if terminal_reason is None:
                            if terminal_stop_reason is not None:
                                terminal_reason = terminal_stop_reason
                            elif qp.cancel_processing:
                                terminal_reason = "cancelled"
                            elif qp.turn_completed:
                                terminal_reason = (
                                    "error"
                                    if getattr(qp, "last_turn_error", None)
                                    else "end_turn"
                                )
                            else:
                                terminal_reason = "interrupted"
                        if not terminal_seen:
                            publish_semantic(
                                coord.event_bus,
                                "turn_complete",
                                request_id=input_ids[0],
                                request_ids=list(input_ids),
                                turn_id=turn_id,
                                stop_reason=terminal_reason,
                            )
                        if terminal_reason in {
                            "cancelled",
                            "canceled",
                            "interrupted",
                            "stop",
                        }:
                            input_status = "cancelled"
                        elif terminal_reason in {"error", "failed", "exception"}:
                            input_status = "failed"
                        else:
                            input_status = "completed"
                        for input_id in input_ids:
                            note = hub_notes.get(input_id)
                            if isinstance(note, dict):
                                await self._persist_hub_input_row(
                                    input_id, note, turn_id, input_status
                                )
                    remove_listener = getattr(turn_tap, "remove_listener", None)
                    if turn_listener is not None and callable(remove_listener):
                        remove_listener(turn_listener)
                    if turn_context_token is not None:
                        try:
                            current_turn.reset(turn_context_token)
                        except (RuntimeError, ValueError):
                            pass
                    if (
                        turn_id is not None
                        and getattr(qp, "active_request_ids", ()) == tuple(input_ids)
                    ):
                        qp.active_request_ids = previous_active_request_ids
                    qp.is_processing = False  # ends the chain (note_chain_end)
                    # User messages arrive via process_user_input(), which
                    # enqueues them to processing_queue but SKIPS creating
                    # _process_queue() when is_processing is True.  After
                    # _hub_continue() releases is_processing, nobody restarts
                    # the queue — those messages sit permanently unprocessed
                    # (the "background terminal stalls user message delivery"
                    # bug, 2026-06-20).  Re-launch _process_queue() here so
                    # any messages that piled up while we held is_processing
                    # get consumed immediately.
                    if not qp.processing_queue.empty() and not qp.cancel_processing:
                        logger.info(
                            "hub_continue: draining %d user message(s) from queue "
                            "that arrived while is_processing was held",
                            qp.processing_queue.qsize(),
                        )
                        coord.create_background_task(
                            lambda: coord._process_queue(),
                            name="process_queue_drain_after_hub_continue",
                        )

            if coord.is_processing or blocked:
                # Coalesce: only one pending retry at a time. Peer messages
                # arriving during a busy turn get added to conversation_history
                # by the hub plugin before this handler runs, so a single
                # post-processing continuation will see all of them.
                if self._retry_pending:
                    self._retry_wake_data = self._merge_retry_wake_data(
                        self._retry_wake_data, data
                    )
                    logger.info(
                        "TRIGGER_LLM_CONTINUE: retry already queued, coalescing"
                    )
                    return {"status": "coalesced"}

                self._retry_pending = True
                self._retry_wake_data = self._merge_retry_wake_data(None, data)
                logger.info(
                    "TRIGGER_LLM_CONTINUE: %s, queuing for retry",
                    blocked or "Processing active",
                )

                async def _retry_continue():
                    try:
                        # Wait for the active chain to finish — do NOT cancel
                        # it. Setting cancel_processing here aborted healthy
                        # long-running chains 5min after any hub message
                        # arrived (second cause of the "agent stops
                        # mid-tool-call" bug). A busy session is working, not
                        # stuck; if it truly never finishes, give up on the
                        # RETRY only — the triggering message is already in
                        # conversation history and the next turn will see it.
                        retry_start = time.monotonic()
                        checkpoint_at = retry_start + 300
                        give_up_at = retry_start + 3600  # drop retry, not session
                        # A chain running at trigger time may end without
                        # answering, so it gets a retry. A turn that STARTS
                        # while we wait (the human submitting their input,
                        # another wake) already has the message in history.
                        initial_busy = coord.is_processing
                        while True:
                            now = time.monotonic()
                            if not coord.is_processing:
                                initial_busy = False
                                if not self._hub_continue_blocked():
                                    break
                            elif not initial_busy:
                                logger.info(
                                    "TRIGGER_LLM_CONTINUE: a later turn took the message"
                                )
                                self._downgrade_hub_wake(
                                    self._retry_wake_data or data
                                )
                                return
                            if now > give_up_at:
                                logger.warning(
                                    "TRIGGER_LLM_CONTINUE: processing busy for "
                                    ">1h, dropping retry (message remains in "
                                    "history for the next turn)"
                                )
                                self._downgrade_hub_wake(
                                    self._retry_wake_data or data
                                )
                                return
                            if now > checkpoint_at:
                                logger.info(
                                    "TRIGGER_LLM_CONTINUE: still waiting for "
                                    "active chain to finish "
                                    f"({int(now - retry_start)}s)"
                                )
                                checkpoint_at = now + 300
                            await asyncio.sleep(1)
                        logger.info(
                            "TRIGGER_LLM_CONTINUE: Retrying after processing completed"
                        )
                        if not coord.is_processing:
                            retry_data = self._retry_wake_data or data
                            if self._acp_owns_chat():
                                # ACP was selected while this retry waited (#579).
                                self._downgrade_hub_wake(retry_data)
                                coord.message_display_service.display_error_message(
                                    "ACP took over this chat while a native reply was waiting, "
                                    "so that reply was not started. Send your message from the "
                                    "chat to reach the ACP agent."
                                )
                                return
                            coord.create_background_task(
                                lambda: _hub_continue(retry_data),
                                name="continue_conversation_retry",
                            )
                    finally:
                        self._retry_pending = False
                        self._retry_wake_data = None

                coord.create_background_task(
                    lambda: _retry_continue(), name="trigger_continue_retry"
                )
                return {"status": "queued_for_retry"}

            if not coord.is_processing:
                coord.create_background_task(
                    lambda: _hub_continue(data), name="continue_conversation_hub"
                )

            logger.info("TRIGGER_LLM_CONTINUE: Triggered LLM processing")
            return {"status": "triggered", "source": source}

        except Exception as e:
            logger.error(f"Error in TRIGGER_LLM_CONTINUE handler: {e}")
            self._downgrade_hub_wake(data)
            return {"status": "error", "error": str(e)}

    def _acp_owns_chat(self) -> bool:
        """Whether an ACP agent owns this chat: the test process_user_input applies."""
        coord = self._coordinator
        selected_acp, _ = coord._profile_selects_acp()
        return bool(selected_acp or coord._active_acp() is not None)

    def _hub_continue_blocked(self) -> str:
        """Why a hub wake must wait right now ("" when it may run)."""
        if time.monotonic() < self._hub_continue_paused_until:
            return "Cooldown active"
        if self._user_is_typing():
            return "User is typing"
        return ""

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
