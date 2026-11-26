"""Per-conversation event log for the web chat stream (#1451)."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

from kollabor_events.chat_stream import (
    EVENT_TYPES,
    REPLAY_WINDOW,
    Event,
    event_problems,
    outcome_for_stop_reason,
)
from kollabor_events.chat_stream_emit import emit_chat_event

from .chat_stream_history import turn_history_key_index
from .chat_stream_rows import (
    ChatStreamRowPager,
    merge_chat_stream_rows,
    register_chat_stream_rows_handler,
)

logger = logging.getLogger(__name__)


def _parse_chat_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value:
        try:
            stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return stamp.astimezone(timezone.utc)


def _public_chat_stream_row(row: dict[str, Any]) -> dict[str, Any]:
    """Remove manager-only persistence metadata before exposing a row."""
    return {
        key: copy.deepcopy(value)
        for key, value in row.items()
        if not key.startswith("_history_")
    }


class ChatStreamLog:
    """Keep ordered events and produce snapshots for one conversation."""

    def __init__(
        self,
        conversation_uid: str,
        *,
        snapshot_provider: Callable[[], dict[str, Any]],
        replay_window: int = REPLAY_WINDOW,
        on_event: Callable[[Event], Any] | None = None,
    ) -> None:
        if not conversation_uid:
            raise ValueError("conversation_uid is required")
        if isinstance(replay_window, bool) or replay_window < 1:
            raise ValueError("replay_window must be a positive integer")
        self.conversation_uid = conversation_uid
        # Sequence numbers are process-local. A fresh random generation keeps
        # cursors from an earlier daemon incarnation from matching this log.
        self.generation = uuid.uuid4().hex
        self._snapshot_provider = snapshot_provider
        self._replay_window = replay_window
        self._on_event = on_event
        self._events: deque[Event] = deque()
        self._current_turn_id: str | None = None
        self._current_turn_events: dict[int, Event] = {}
        self._last_seq = 0
        self._last_replace_seq = 0
        self._lock = threading.RLock()

    @property
    def next_seq(self) -> int:
        with self._lock:
            return self._last_seq + 1

    @property
    def last_replace_seq(self) -> int:
        """Sequence of the most recent transcript replacement or zero."""
        with self._lock:
            return self._last_replace_seq

    def publish(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        on_commit: Callable[[Event], Any] | None = None,
    ) -> Event:
        """Validate, sequence, retain and publish one conversation event."""
        if event_type not in EVENT_TYPES or event_type == "snapshot":
            raise ValueError(f"unsupported logged chat event type {event_type!r}")
        if not isinstance(data, dict):
            raise ValueError("chat event data must be an object")

        with self._lock:
            seq = self._last_seq + 1
            payload = copy.deepcopy(data)
            if event_type == "row.upsert":
                payload["seq"] = seq

            if event_type in {"input.state", "turn.state"}:
                event = emit_chat_event(
                    lambda _event: None,
                    seq=seq,
                    conversation_uid=self.conversation_uid,
                    generation=self.generation,
                    event_type=event_type,
                    data=payload,
                )
            else:
                event = {
                    "seq": seq,
                    "conversation_uid": self.conversation_uid,
                    "generation": self.generation,
                    "type": event_type,
                    "data": payload,
                }
                problems = event_problems(event)
                if problems:
                    raise ValueError("; ".join(problems))

            self._record_locked(event)
            if on_commit is not None:
                on_commit(copy.deepcopy(event))
            # Serialize live delivery with sequence assignment. Otherwise a
            # later producer thread can publish seq N+1 before this event.
            if self._on_event is not None:
                try:
                    self._on_event(copy.deepcopy(event))
                except Exception:
                    logger.debug("chat stream live publish failed", exc_info=True)
        return copy.deepcopy(event)

    def read(
        self,
        *,
        after: int | None = None,
        conversation_uid: str | None = None,
        generation: str | None = None,
    ) -> list[Event]:
        """Return missed events, or one fresh snapshot when replay is unsafe."""
        with self._lock:
            if (
                after is None
                or isinstance(after, bool)
                or not isinstance(after, int)
                or after < 0
                or after > self._last_seq
                or generation != self.generation
                or (
                    conversation_uid is not None
                    and conversation_uid != self.conversation_uid
                )
            ):
                return [self._snapshot_event_locked()]

            events = self._retained_events_locked()
            first_seq = events[0]["seq"] if events else self._last_seq + 1
            if after < first_seq - 1:
                return [self._snapshot_event_locked()]
            return [copy.deepcopy(event) for event in events if event["seq"] > after]

    def _snapshot_event_locked(self) -> Event:
        state = copy.deepcopy(self._snapshot_provider() or {})
        rows = state.get("rows", [])
        turns = state.get("turns", [])
        inputs = state.get("inputs", [])
        if (
            not isinstance(rows, list)
            or not isinstance(turns, list)
            or not isinstance(inputs, list)
        ):
            raise ValueError("chat snapshot rows, turns and inputs must be lists")
        event: Event = {
            # A snapshot starts a new subscriber's sequence view. `next_seq`
            # carries the shared log cursor to use for subsequent events.
            "seq": 0,
            "conversation_uid": self.conversation_uid,
            "generation": self.generation,
            "type": "snapshot",
            "data": {
                "rows": rows,
                "next_seq": self._last_seq + 1,
                "older": state.get("older"),
                "turns": turns,
                "inputs": inputs,
            },
        }
        problems = event_problems(event)
        if problems:
            raise ValueError("; ".join(problems))
        return event

    def _record_locked(self, event: Event) -> None:
        self._last_seq = event["seq"]
        if event["type"] == "replace":
            self._last_replace_seq = event["seq"]
        self._events.append(event)

        if event["type"] == "turn.state":
            data = event["data"]
            if data["state"] == "running":
                if self._current_turn_id != data["turn_id"]:
                    self._current_turn_id = data["turn_id"]
                    self._current_turn_events = {}
                self._current_turn_events[event["seq"]] = event
            elif data["turn_id"] == self._current_turn_id:
                self._current_turn_events[event["seq"]] = event
                self._current_turn_id = None
                self._current_turn_events = {}
        elif self._current_turn_id is not None:
            self._current_turn_events[event["seq"]] = event

        floor = max(1, self._last_seq - self._replay_window + 1)
        protected = self._current_turn_events
        while self._events and self._events[0]["seq"] < floor:
            oldest = self._events[0]
            if oldest["seq"] in protected:
                break
            self._events.popleft()

    def _retained_events_locked(self) -> list[Event]:
        by_seq = {event["seq"]: event for event in self._events}
        by_seq.update(self._current_turn_events)
        return [by_seq[seq] for seq in sorted(by_seq)]


class ChatStreamService:
    """Connect the event log to daemon transcript, queue and socket events."""

    def __init__(self, state_service: Any) -> None:
        self._state_service = state_service
        self._event_bus = getattr(state_service, "_event_bus", None)
        self._logs: dict[str, ChatStreamLog] = {}
        self._rows: dict[str, dict[str, dict[str, Any]]] = {}
        # Snapshot lifecycle projection. This is rebuilt when an older saved
        # transcript is restored, so stale running states cannot shadow it.
        self._inputs: dict[str, dict[str, dict[str, Any]]] = {}
        # Daemon-lifetime admission/idempotency state, kept separately from the
        # transcript projection so accepted retries retain their owner binding.
        self._admission_inputs: dict[str, dict[str, dict[str, Any]]] = {}
        self._turns: dict[str, dict[str, dict[str, Any]]] = {}
        self._input_scopes: dict[tuple[str, str], tuple[str, str, str]] = {}
        self._input_text: dict[tuple[str, str], str] = {}
        self._input_attachments: dict[tuple[str, str], list[dict[str, str]]] = {}
        self._input_goal_reply_ids: dict[tuple[str, str], str | None] = {}
        # This set tracks queued native inputs only. Once admitted, native
        # origin is stored alongside the lifecycle record.
        self._native_plugin_inputs: set[tuple[str, str]] = set()
        self._goal_reply_owners: dict[
            str, tuple[str, str, tuple[str, str, str], float]
        ] = {}
        self._input_lock = asyncio.Lock()
        self._turn_inputs: dict[tuple[str, str], list[str]] = {}
        self._input_turns: dict[tuple[str, str], str] = {}
        self._active_turns: dict[str, str] = {}
        self._reply_rows: dict[tuple[str, str], str] = {}
        self._reply_text: dict[tuple[str, str], str] = {}
        self._reply_segment_offsets: dict[tuple[str, str], int] = {}
        self._reply_after_tool: set[tuple[str, str]] = set()
        self._reply_row_ids: dict[tuple[str, str], list[str]] = {}
        self._turn_history_keys: dict[tuple[str, str], str] = {}
        self._turn_errors: dict[tuple[str, str], str] = {}
        self._display_tap: Any = None
        self._display_tap_listener: Any = None
        register_service = getattr(self._event_bus, "register_service", None)
        if callable(register_service):
            register_service("chat_stream", self)

    def subscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        self.attach_display_tap()
        after = params.get("after")
        conversation_uid = params.get("conversation_uid")
        generation = params.get("generation")
        return {
            "events": self._log().read(
                after=after,
                conversation_uid=conversation_uid,
                generation=generation,
            )
        }

    async def add_input(self, params: dict[str, Any]) -> dict[str, Any]:
        async with self._input_lock:
            return await self._add_input(params)

    async def _add_input(self, params: dict[str, Any]) -> dict[str, Any]:
        self.attach_display_tap()
        try:
            input_id, text, attachments, goal_reply_id = self._validate_input(params)
        except ValueError:
            return {"error": "input was not admitted"}
        uid = self._conversation_uid()
        runtime = self._queue_runtime()
        scope = await runtime.input_scope(uid)
        known = self._admission_inputs.setdefault(uid, {}).get(input_id)
        owner = self._input_scopes.get((uid, input_id))
        if owner != scope and (owner is not None or known is not None):
            return {"error": "input was not admitted"}
        if known is not None:
            previous_text = self._input_text.get((uid, input_id), text)
            previous_attachments = self._input_attachments.get(
                (uid, input_id), attachments
            )
            previous_goal_reply_id = self._input_goal_reply_ids.get(
                (uid, input_id)
            )
            if (
                previous_text != text
                or previous_attachments != attachments
                or previous_goal_reply_id != goal_reply_id
            ):
                return {"error": "input id is already bound to different content"}
            return {"id": input_id, "state": known["state"]}
        self._prune_goal_reply_owners()
        if goal_reply_id is not None:
            existing_goal_owner = self._goal_reply_owners.get(goal_reply_id)
            if existing_goal_owner is not None:
                old_uid, old_input_id, old_scope, _created = existing_goal_owner
                if (old_uid, old_input_id, old_scope) != (uid, input_id, scope):
                    return {"error": "input was not admitted"}
            elif len(self._goal_reply_owners) >= 256:
                return {"error": "input was not admitted"}

        runtime_params = {"id": input_id, "text": text, "attachments": attachments}
        if goal_reply_id is not None:
            runtime_params["goal_reply_id"] = goal_reply_id
        result = await runtime.add_input(runtime_params)
        if not isinstance(result, dict) or result.get("accepted") is not True:
            reason = result.get("reason") if isinstance(result, dict) else None
            return {
                "error": (
                    "input id is already bound to different content"
                    if reason == "input_id_conflict"
                    else "input was not admitted"
                )
            }

        state = result.get("state", "queued")
        if state not in {"queued", "started", "joined"}:
            return {"error": "daemon returned an invalid input state"}
        turn_id = result.get("turn_id")
        if state in {"started", "joined"} and not isinstance(turn_id, str):
            return {"error": "daemon omitted the input turn"}
        attachment_refs = result.get("attachment_refs", [])
        if (
            not isinstance(attachment_refs, list)
            or len(attachment_refs) != len(attachments)
            or any(not isinstance(ref, str) or not ref for ref in attachment_refs)
        ):
            return {"error": "daemon omitted input image references"}
        display_attachments = [
            {
                "name": attachment["name"],
                "content_type": attachment["content_type"],
                "source_ref": ref,
            }
            for attachment, ref in zip(attachments, attachment_refs)
        ]

        self._input_scopes[(uid, input_id)] = scope
        self._input_text[(uid, input_id)] = text
        self._input_attachments[(uid, input_id)] = copy.deepcopy(attachments)
        self._input_goal_reply_ids[(uid, input_id)] = goal_reply_id
        if goal_reply_id is not None:
            self._goal_reply_owners[goal_reply_id] = (
                uid,
                input_id,
                scope,
                time.monotonic(),
            )
        self._set_input_state(
            uid,
            input_id,
            state,
            turn_id=turn_id,
        )
        row = {
            "id": input_id,
            "seq": 0,
            "kind": "input",
            "role": "user",
            "turn_id": turn_id,
            "status": state,
            "parts": self._input_parts(
                text, display_attachments, conversation_uid=uid, input_id=input_id
            ),
            "created_at": self._timestamp(),
        }
        self._upsert(uid, row)
        return {"id": input_id, "state": state}

    def begin_ordered_command(
        self, input_id: str, conversation_uid: str, queue_scope_id: str
    ) -> str | None:
        """Publish neutral command progress for one claimed queue input."""
        if (
            not isinstance(input_id, str)
            or not input_id
            or conversation_uid != self._conversation_uid()
        ):
            return None
        owner = self._input_scopes.get((conversation_uid, input_id))
        current = self._admission_inputs.get(conversation_uid, {}).get(input_id, {})
        if (
            owner is None
            or owner[0] != queue_scope_id
            or current.get("state") != "queued"
        ):
            return None
        turn_id = f"command-{uuid.uuid4().hex}"
        self._input_turns[(conversation_uid, input_id)] = turn_id
        self._add_turn_input(conversation_uid, turn_id, input_id)
        self._set_input_state(conversation_uid, input_id, "started", turn_id=turn_id)
        row = self._rows.get(conversation_uid, {}).get(input_id)
        if row is not None:
            self._upsert(
                conversation_uid,
                {**row, "turn_id": turn_id, "status": "started"},
            )
        self._set_turn_state(conversation_uid, turn_id, "running", input_id=input_id)
        self._active_turns[conversation_uid] = turn_id
        return turn_id

    def finish_ordered_command(
        self,
        input_id: str,
        conversation_uid: str,
        turn_id: str,
        outcome: str,
    ) -> None:
        """End one ordered slash-command lifecycle without exposing its result."""
        if outcome not in {"completed", "failed", "cancelled"}:
            outcome = "failed"
        current = self._admission_inputs.get(conversation_uid, {}).get(input_id, {})
        if current.get("state") == "ended":
            return
        if current.get("turn_id") not in (None, turn_id):
            return
        self._set_turn_state(conversation_uid, turn_id, "ended", input_id=input_id)
        self._set_input_state(
            conversation_uid,
            input_id,
            "ended",
            turn_id=turn_id,
            outcome=outcome,
        )
        row = self._rows.get(conversation_uid, {}).get(input_id)
        if row is not None:
            self._upsert(
                conversation_uid,
                {**row, "turn_id": turn_id, "status": outcome},
            )
        if self._active_turns.get(conversation_uid) == turn_id:
            self._active_turns.pop(conversation_uid, None)
        reply_id = self._input_goal_reply_ids.get((conversation_uid, input_id))
        if reply_id is not None:
            owner = self._goal_reply_owners.get(reply_id)
            if owner is not None:
                self._goal_reply_owners[reply_id] = (*owner[:3], time.monotonic())

    async def authorize_goal_reply(self, reply_id: str) -> bool | None:
        """Check a queued goal capability against its authenticated input scope.

        ``None`` means the capability belongs to the older non-queue transport,
        whose authorization remains the unguessable reply capability itself.
        """
        self._prune_goal_reply_owners()
        owner = self._goal_reply_owners.get(reply_id)
        if owner is None:
            return None
        uid, _input_id, expected_scope, _created = owner
        if uid != self._conversation_uid():
            return False
        try:
            actual_scope = await self._queue_runtime().input_scope(uid)
        except Exception:
            return False
        return actual_scope == expected_scope

    async def poll_goal_reply(self, reply_id: str) -> dict[str, Any] | None:
        """Poll an ordered goal reply after checking its queue owner scope."""
        self._prune_goal_reply_owners()
        owner = self._goal_reply_owners.get(reply_id)
        if owner is None:
            return None
        if await self.authorize_goal_reply(reply_id) is not True:
            return {"status": "missing", "message": "goal reply expired or is unavailable"}
        uid, input_id, _scope, _created = owner
        mailbox = getattr(self._state_service, "_goal_reply_mailbox", None)
        if mailbox is not None:
            result = mailbox.poll(reply_id, uid)
            if result.get("status") != "missing":
                return result
        current = self._admission_inputs.get(uid, {}).get(input_id, {})
        if current.get("state") in {"queued", "started", "joined"}:
            return {"status": "pending"}
        outcome = current.get("outcome")
        message = (
            "Goal command cancelled before execution."
            if outcome == "cancelled"
            else "Goal command could not be completed."
        )
        return {"status": "complete", "success": False, "message": message}

    async def cancel_goal_reply(self, reply_id: str) -> dict[str, Any] | None:
        """Cancel one owner-bound queued command or its private running task."""
        self._prune_goal_reply_owners()
        owner = self._goal_reply_owners.get(reply_id)
        if owner is None:
            return None
        if await self.authorize_goal_reply(reply_id) is not True:
            return {"cancelled": False, "reason": "goal reply is unavailable"}
        uid, input_id, _scope, _created = owner
        removed = await self.cancel_queued_input(
            {"id": input_id}, expected_conversation_uid=uid
        )
        if removed.get("cancelled") is True:
            return removed
        mailbox = getattr(self._state_service, "_goal_reply_mailbox", None)
        if mailbox is None:
            from .goal_replies import GoalReplyMailbox

            mailbox = self._state_service._goal_reply_mailbox = GoalReplyMailbox()
        return mailbox.cancel(reply_id)

    def _prune_goal_reply_owners(self) -> None:
        now = time.monotonic()
        self._goal_reply_owners = {
            reply_id: owner
            for reply_id, owner in self._goal_reply_owners.items()
            if (
                self._admission_inputs.get(owner[0], {})
                .get(owner[1], {})
                .get("state")
                != "ended"
                or now - owner[3] < 600.0
            )
        }

    async def cancel_queued_input(
        self,
        params: dict[str, Any],
        *,
        expected_conversation_uid: str | None = None,
    ) -> dict[str, Any]:
        input_id = params.get("id")
        if not isinstance(input_id, str) or not input_id:
            return {"found": False, "cancelled": False}
        uid = self._conversation_uid()
        if expected_conversation_uid is not None and expected_conversation_uid != uid:
            return {"found": False, "cancelled": False}
        existing = self._admission_inputs.setdefault(uid, {}).get(input_id)
        if existing is not None and existing.get("state") != "queued":
            return {"found": True, "cancelled": False}
        if (uid, input_id) in self._native_plugin_inputs:
            processor = getattr(self._llm_service(), "_queue_processor", None)
            cancel_native = getattr(
                processor, "cancel_queued_plugin_input", None
            )
            if not callable(cancel_native):
                return {"found": False, "cancelled": False}
            cancelled = await cancel_native(input_id)
            if not cancelled:
                return {"found": True, "cancelled": False}
            self._set_input_state(
                uid,
                input_id,
                "ended",
                turn_id=None,
                outcome="cancelled",
            )
            row = self._rows.setdefault(uid, {}).get(input_id)
            if row is not None:
                self._upsert(uid, {**row, "status": "cancelled"})
            return {"found": True, "cancelled": True}
        runtime_params = {"id": input_id}
        if expected_conversation_uid is not None:
            runtime_params["expected_conversation_uid"] = expected_conversation_uid
        result = await self._queue_runtime().cancel_queued_input(runtime_params)
        if not isinstance(result, dict) or result.get("found") is not True:
            return {"found": False, "cancelled": False}
        cancelled = result.get("cancelled") is True
        existing = self._admission_inputs.setdefault(uid, {}).get(input_id)
        if cancelled and existing is not None and existing.get("state") == "queued":
            self._set_input_state(
                uid,
                input_id,
                "ended",
                turn_id=None,
                outcome="cancelled",
            )
            row = self._rows.setdefault(uid, {}).get(input_id)
            if row is not None:
                updated = {**row, "status": "cancelled"}
                self._upsert(uid, updated)
            reply_id = self._input_goal_reply_ids.get((uid, input_id))
            if reply_id is not None:
                owner = self._goal_reply_owners.get(reply_id)
                if owner is not None:
                    self._goal_reply_owners[reply_id] = (
                        *owner[:3],
                        time.monotonic(),
                    )
        return {"found": True, "cancelled": cancelled}

    async def admit_queued_batch(self, inputs, scope, enqueue):
        """Publish queued rows and lifecycle after an atomic queue admission."""
        normalized = []
        for item in inputs:
            if not isinstance(item, dict):
                raise ValueError("queued input must be an object")
            input_id, text, attachments, goal_reply_id = self._validate_input(
                {
                    "id": item.get("id"),
                    "text": item.get("text"),
                    "attachments": item.get("attachments", []),
                }
            )
            if goal_reply_id is not None:
                raise ValueError("queued input cannot carry a private goal capability")
            normalized.append((input_id, text, attachments))
        if not isinstance(scope, tuple) or len(scope) != 3:
            raise ValueError("queued input scope is invalid")

        async with self._input_lock:
            keys = [(scope[1], input_id) for input_id, _, _ in normalized]
            if any(
                self._input_scopes.get(key, scope) != scope
                or (
                    key[1] in self._admission_inputs.get(key[0], {})
                    and key not in self._input_scopes
                )
                for key in keys
            ):
                raise ValueError("input ID is already owned")
            receipt = await enqueue()
            uid = scope[1]
            self.attach_display_tap()
            for (input_id, text, attachments), key in zip(normalized, keys):
                self._input_scopes[key] = scope
                prior = self._admission_inputs.setdefault(uid, {}).get(input_id)
                if prior is None:
                    self._input_text[key] = text
                    self._input_attachments[key] = copy.deepcopy(attachments)
                    self._set_input_state(uid, input_id, "queued", turn_id=None)
                    status = "queued"
                else:
                    status = prior.get("state") or "queued"
                if input_id not in self._rows.setdefault(uid, {}):
                    if status == "ended":
                        status = prior.get("outcome") or "ended"
                    self._upsert(
                        uid,
                        {
                            "id": input_id,
                            "seq": 0,
                            "kind": "input",
                            "role": "user",
                            "turn_id": (prior or {}).get("turn_id"),
                            "status": status,
                            "parts": self._input_parts(
                                text,
                                attachments,
                                conversation_uid=uid,
                                input_id=input_id,
                            ),
                            "created_at": self._timestamp(),
                        },
                    )
            return receipt

    def has_admitted_input(self, input_id: str, scope: tuple[str, str, str]) -> bool:
        """Whether this exact queue scope already has a projected chat input."""
        if (
            not isinstance(input_id, str)
            or not isinstance(scope, tuple)
            or len(scope) != 3
        ):
            return False
        uid = scope[1]
        key = (uid, input_id)
        return self._input_scopes.get(key) == scope and (
            input_id in self._admission_inputs.get(uid, {})
            or input_id in self._rows.get(uid, {})
        )

    def get_admitted_input(
        self, input_id: str, scope: tuple[str, str, str]
    ) -> dict[str, Any] | None:
        """Return stored content for an input owned by this exact queue scope."""
        if not self.has_admitted_input(input_id, scope):
            return None
        key = (scope[1], input_id)
        text = self._input_text.get(key)
        attachments = self._input_attachments.get(key)
        if not isinstance(text, str) or not isinstance(attachments, list):
            return None
        return {"text": text, "attachments": copy.deepcopy(attachments)}

    async def bind_queued_input_scopes(
        self, input_ids: list[str], scope: tuple[str, str, str]
    ) -> bool:
        """Reserve all selected IDs atomically before queue dispatch."""
        async with self._input_lock:
            keys = [(scope[1], input_id) for input_id in input_ids]
            if any(
                self._input_scopes.get(key, scope) != scope
                or (
                    key[1] in self._admission_inputs.get(key[0], {})
                    and key not in self._input_scopes
                )
                for key in keys
            ):
                return False
            for key in keys:
                self._input_scopes[key] = scope
            return True

    async def send_now(self, params: dict[str, Any]) -> dict[str, Any]:
        input_id = params.get("id")
        if not isinstance(input_id, str) or not input_id:
            return {"found": False}
        uid = self._conversation_uid()
        native_key = (uid, input_id)
        current = self._admission_inputs.get(uid, {}).get(input_id, {})
        if (
            current.get("_native_plugin") is True
            or native_key in self._native_plugin_inputs
        ):
            state = current.get("state")
            if state != "queued":
                result = {
                    "found": True,
                    "accepted": False,
                    "state": state or "queued",
                }
                turn_id = current.get("turn_id")
                if isinstance(turn_id, str) and turn_id:
                    result["turn_id"] = turn_id
                return result
            if (uid, input_id) in self._input_scopes:
                return {"found": False}
            send_native = getattr(
                self._llm_service(), "send_now_native_plugin_input", None
            )
            if not callable(send_native):
                return {"found": False}
            result = await send_native(input_id)
            if not isinstance(result, dict) or result.get("accepted") is not True:
                current = self._admission_inputs.get(uid, {}).get(input_id, {})
                current_state = current.get("state")
                current_turn_id = current.get("turn_id")
                if current_state in {"started", "joined", "ended"} and (
                    isinstance(current_turn_id, str) and current_turn_id
                ):
                    return {
                        "found": True,
                        "accepted": False,
                        "state": current_state,
                        "turn_id": current_turn_id,
                    }
            return (
                result
                if isinstance(result, dict) and result.get("found") is True
                else {"found": False}
            )
        runtime = self._queue_runtime()
        scope = await runtime.input_scope(uid)

        def moved_input_result() -> dict[str, Any] | None:
            current = self._admission_inputs.get(uid, {}).get(input_id, {})
            state = current.get("state")
            turn_id = current.get("turn_id")
            if state not in {"started", "joined", "ended"} or (
                state != "ended" and not turn_id
            ):
                return None
            if self._input_scopes.get((uid, input_id)) != scope:
                return {"found": False}
            result = {"found": True, "accepted": False, "state": state}
            if isinstance(turn_id, str) and turn_id:
                result["turn_id"] = turn_id
            return result

        current_result = moved_input_result()
        if current_result is not None:
            return current_result
        result = await runtime.send_now({"id": input_id})
        if await runtime.input_scope(uid) != scope:
            return {"found": False}
        current = self._admission_inputs.get(uid, {}).get(input_id, {})
        owner = self._input_scopes.get((uid, input_id))
        if owner != scope:
            return {"found": False}
        if not isinstance(result, dict) or result.get("found") is not True:
            current_result = moved_input_result()
            if current_result is not None:
                return current_result
        if not isinstance(result, dict) or result.get("found") is not True:
            return {"found": False}
        state = result.get("state")
        turn_id = result.get("turn_id")
        if state in {"started", "joined"} and isinstance(turn_id, str):
            uid = self._conversation_uid()
            self._set_input_state(uid, input_id, state, turn_id=turn_id)
            self._set_turn_state(uid, turn_id, "running", input_id=input_id)
        current = self._admission_inputs.get(uid, {}).get(input_id, {})
        return {
            "found": True,
            "accepted": result.get("accepted") is True,
            "state": current.get("state", state or "queued"),
            **(
                {"reason": result["reason"]}
                if isinstance(result.get("reason"), str) and result["reason"]
                else {}
            ),
            **({"turn_id": current["turn_id"]} if current.get("turn_id") else {}),
        }

    def record_event(self, event_type: str, data: dict[str, Any]) -> Event:
        """Record one producer event for the active daemon conversation."""
        uid = self._conversation_uid()
        with self._log(uid)._lock:
            return self._record_event_locked(uid, event_type, data)

    def _record_event_locked(
        self, uid: str, event_type: str, data: dict[str, Any]
    ) -> Event:
        if event_type == "turn.state":
            prior = self._turns.get(uid, {}).get(data.get("turn_id"))
            if prior is not None and prior.get("origin") != data.get("origin"):
                raise ValueError("turn origin is immutable")
        if event_type == "row.upsert":
            self._rows.setdefault(uid, {})[data["id"]] = copy.deepcopy(data)
        elif event_type == "row.append":
            row = self._rows.setdefault(uid, {}).get(data["id"])
            if row is not None:
                parts = row.setdefault("parts", [])
                part_index = data["part"]
                while len(parts) <= part_index:
                    parts.append({"type": "text", "text": ""})
                part = parts[part_index]
                if not isinstance(part, dict) or part.get("type") != "text":
                    part = {"type": "text", "text": ""}
                    parts[part_index] = part
                part["text"] = str(part.get("text") or "") + data["text"]
        elif event_type == "replace":
            rows = data.get("rows", [])
            self._rows[uid] = {
                row["id"]: copy.deepcopy(row)
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            }
        elif event_type == "turn.state":
            turn_id = data.get("turn_id")
            if isinstance(turn_id, str):
                self._turns.setdefault(uid, {})[turn_id] = copy.deepcopy(data)
        elif event_type == "input.state":
            input_id = data.get("id")
            if isinstance(input_id, str):
                copied = copy.deepcopy(data)
                key = (uid, input_id)
                admitted = self._admission_inputs.setdefault(uid, {})
                prior = admitted.get(input_id, {})
                native_plugin = (
                    prior.get("_native_plugin") is True
                    or key in self._native_plugin_inputs
                )
                self._inputs.setdefault(uid, {})[input_id] = copied
                admission_data = copy.deepcopy(copied)
                if native_plugin:
                    admission_data["_native_plugin"] = True
                    if copied.get("state") == "queued":
                        self._native_plugin_inputs.add(key)
                    else:
                        self._native_plugin_inputs.discard(key)
                admitted[input_id] = admission_data
        return self._log(uid).publish(event_type, data)

    def attach_display_tap(self) -> None:
        """Listen to daemon semantic events and translate turns into rows."""
        get_service = getattr(self._event_bus, "get_service", None)
        tap = get_service("display_tap") if callable(get_service) else None
        if tap is self._display_tap and self._display_tap_listener is not None:
            return
        self.detach_display_tap()
        add_listener = getattr(tap, "add_listener", None)
        if not callable(add_listener):
            return
        add_listener(self._on_display_event)
        self._display_tap = tap
        self._display_tap_listener = self._on_display_event

    def detach_display_tap(self) -> None:
        tap = self._display_tap
        listener = self._display_tap_listener
        self._display_tap = None
        self._display_tap_listener = None
        remove_listener = getattr(tap, "remove_listener", None)
        if callable(remove_listener) and listener is not None:
            try:
                remove_listener(listener)
            except Exception:
                logger.debug("chat stream DisplayTap unsubscribe failed", exc_info=True)

    def _on_display_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        uid = event.get("conversation_uid") or self._conversation_uid()
        if not isinstance(uid, str) or not uid or uid != self._conversation_uid():
            return
        with self._log(uid)._lock:
            self._dispatch_display_event(uid, event)

    def _dispatch_display_event(self, uid: str, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "turn_start":
            self._on_turn_start(uid, event)
        elif kind == "user_message":
            self._on_user_message(uid, event)
        elif kind == "reply_segment_start":
            turn_id = self._event_turn_id(event) or self._active_turns.get(uid)
            if turn_id is not None:
                key = (uid, turn_id)
                self._reply_segment_offsets[key] = len(self._reply_text.get(key, ""))
        elif kind in {"token", "response"}:
            self._on_reply_text(uid, event)
        elif kind in {"tool_start", "tool_result"}:
            self._on_tool_event(uid, event)
        elif kind == "turn_complete":
            origin = event.get("origin")
            if (
                isinstance(origin, dict)
                and origin.get("kind") == "goal"
                and not event.get("goal_attempt_terminal")
            ):
                return
            self._on_turn_complete(uid, event)
        elif kind == "error":
            self._on_turn_error(uid, event)
        elif kind == "hub_message":
            self._on_hub_message(uid, event)
        elif kind == "permission_request":
            self._on_permission_prompt(uid, event)
        elif kind == "permission_parked":
            self._on_permission_parked(uid, event)
        elif kind in {
            "permission_granted",
            "permission_denied",
            "permission_cancelled",
        }:
            self._on_permission_resolved(uid, event)

    def _on_turn_start(self, uid: str, event: dict[str, Any]) -> None:
        turn_id = self._event_turn_id(event)
        if turn_id is None:
            return
        key = (uid, turn_id)
        self._turn_history_key(uid, turn_id, event=event)
        existing_turn = self._turns.setdefault(uid, {}).get(turn_id)
        already_running = (
            isinstance(existing_turn, dict) and existing_turn.get("state") == "running"
        )
        input_ids = self._event_input_ids(event)
        for input_id in input_ids:
            self._ensure_input_row(uid, input_id)
            prior = self._inputs.setdefault(uid, {}).get(input_id, {})
            if prior.get("state") == "ended":
                continue
            state = (
                "joined"
                if already_running and input_id not in self._turn_inputs.get(key, [])
                else "started"
            )
            self._set_input_state(uid, input_id, state, turn_id=turn_id)
            self._input_turns[(uid, input_id)] = turn_id
            self._add_turn_input(uid, turn_id, input_id)
            row = self._rows.setdefault(uid, {}).get(input_id)
            if row is not None:
                self._upsert(uid, {**row, "turn_id": turn_id, "status": state})
        self._set_turn_state(uid, turn_id, "running", origin=event.get("origin"))
        self._active_turns[uid] = turn_id

    def _on_user_message(self, uid: str, event: dict[str, Any]) -> None:
        text = event.get("text")
        if not isinstance(text, str):
            return
        # Plugin ADD_MESSAGE events carry an explicitly authored display
        # string alongside the model payload and generated input identity.
        # Handle that trusted projection before generic request IDs, whose
        # path intentionally refuses to infer authored text from `text`.
        display_text = event.get("display_text")
        input_id = event.get("input_id")
        if not isinstance(input_id, str) or not input_id:
            input_id = event.get("request_id")
        if (
            isinstance(display_text, str)
            and display_text.strip()
            and isinstance(input_id, str)
            and input_id
        ):
            self._on_plugin_user_message(uid, event, input_id, display_text)
            return
        input_ids = self._event_input_ids(event)
        if input_ids:
            records = event.get("operator_inputs")
            text_by_id = {
                record["request_id"]: record["text"]
                for record in records or []
                if isinstance(record, dict)
                and isinstance(record.get("request_id"), str)
                and record["request_id"] in input_ids
                and isinstance(record.get("text"), str)
            }
            for input_id in input_ids:
                input_text = text_by_id.get(input_id)
                authored_text = input_id in text_by_id
                if input_text is None:
                    existing = self._rows.setdefault(uid, {}).get(input_id)
                    existing_text = next(
                        (
                            part.get("text")
                            for part in (existing or {}).get("parts", [])
                            if isinstance(part, dict)
                            and part.get("type") == "text"
                            and isinstance(part.get("text"), str)
                        ),
                        None,
                    )
                    input_text = existing_text if existing_text is not None else ""
                self._on_user_message_input(
                    uid,
                    {
                        **event,
                        "request_id": input_id,
                        "request_ids": [],
                        "operator_inputs": [],
                        "text": input_text,
                        "_authored_text": authored_text,
                    },
                )
            return
        # A native TUI admission may have no client request ID. Its
        # operator-input record is the authored user text; the `text` field on
        # this event is the model payload and can include HUD context or image
        # placeholders. Give each untracked authored input its own live row.
        records = event.get("operator_inputs")
        if isinstance(records, list) and records:
            for record in records:
                if not isinstance(record, dict) or not isinstance(
                    record.get("text"), str
                ):
                    continue
                request_id = record.get("request_id")
                self._on_user_message_input(
                    uid,
                    {
                        **event,
                        "request_id": request_id if isinstance(request_id, str) else "",
                        "request_ids": [],
                        "operator_inputs": [],
                        "text": record["text"],
                        "_authored_text": True,
                    },
                )
            return
        # Plugin ADD_MESSAGE events can carry an explicit authored string
        # from the producer. This is separate from `text`, which may be a
        # model payload containing HUD context or image placeholders.
        if isinstance(display_text, str) and display_text.strip():
            if not isinstance(input_id, str) or not input_id:
                input_id = self._transcript_input_id(uid, display_text)
            if not isinstance(input_id, str) or not input_id:
                input_id = f"daemon-{uuid.uuid4().hex}"
            self._on_plugin_user_message(uid, event, input_id, display_text)
            return
        # Without a request ID or authenticated operator text, this event's
        # text is only the model payload. It may contain HUD context or image
        # placeholders and is not safe to project as authored input.

    def _on_plugin_user_message(
        self,
        uid: str,
        event: dict[str, Any],
        input_id: str,
        display_text: str,
    ) -> None:
        """Project an explicitly displayable plugin message without inventing a turn."""
        self._ensure_input_row(uid, input_id, text=display_text)
        prior = self._inputs.setdefault(uid, {}).get(input_id, {})
        if prior.get("state") == "ended":
            return

        queued = event.get("chat_stream_trigger_llm") is True
        state = "queued" if queued else "ended"
        outcome = None if queued else "completed"
        if queued:
            key = (uid, input_id)
            self._native_plugin_inputs.add(key)
            admitted = self._admission_inputs.get(uid, {}).get(input_id)
            if admitted is not None:
                admitted["_native_plugin"] = True
        row = self._rows.setdefault(uid, {}).get(input_id)
        if row is not None:
            parts = self._input_parts(display_text)
            row_status = "queued" if queued else "completed"
            if (
                row.get("turn_id") is not None
                or row.get("status") != row_status
                or row.get("parts") != parts
            ):
                self._upsert(
                    uid,
                    {
                        **row,
                        "turn_id": None,
                        "status": row_status,
                        "parts": parts,
                    },
                )
        if prior.get("state") != state or prior.get("turn_id") is not None:
            self._set_input_state(
                uid,
                input_id,
                state,
                turn_id=None,
                outcome=outcome,
            )

    def _on_user_message_input(self, uid: str, event: dict[str, Any]) -> None:
        text = event.get("text")
        if not isinstance(text, str):
            return
        input_id = event.get("request_id")
        if not isinstance(input_id, str) or not input_id:
            input_id = self._transcript_input_id(uid, text)
        if input_id is None:
            input_id = f"daemon-{uuid.uuid4().hex}"
        authored_text = event.get("_authored_text") is True
        display_text = text if authored_text else ""
        turn_id = self._event_turn_id(event) or self._input_turns.get((uid, input_id))
        if turn_id is None:
            turn_id = self._active_turns.get(uid)
        if turn_id is None:
            turn_id = f"turn-{uuid.uuid4().hex}"
        self._ensure_input_row(uid, input_id, text=display_text, turn_id=turn_id)
        prior = self._inputs.setdefault(uid, {}).get(input_id, {})
        if prior.get("state") == "ended":
            return
        current = self._turns.setdefault(uid, {}).get(turn_id, {})
        joined = current.get(
            "state"
        ) == "running" and input_id not in self._turn_inputs.get((uid, turn_id), [])
        state = "joined" if joined else "started"
        if prior.get("turn_id") == turn_id and prior.get("state") in {
            "started",
            "joined",
        }:
            # _on_turn_start already classified a newly admitted input as
            # joined. The native queue can re-enter LOOP 1 under this same
            # turn, so the later user_message must preserve that state.
            state = prior["state"]
        if prior.get("state") != state or prior.get("turn_id") != turn_id:
            self._set_input_state(uid, input_id, state, turn_id=turn_id)
        self._input_turns[(uid, input_id)] = turn_id
        self._add_turn_input(uid, turn_id, input_id)
        row = self._rows.setdefault(uid, {}).get(input_id)
        parts = copy.deepcopy(row.get("parts", [])) if row is not None else []
        has_text = any(
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and part["text"]
            for part in parts
        )
        if authored_text and text and not has_text:
            non_text_parts = [
                part
                for part in parts
                if not isinstance(part, dict) or part.get("type") != "text"
            ]
            parts = self._input_parts(text)
            parts.extend(non_text_parts)
        if row is not None and (
            row.get("status") != state or row.get("parts") != parts
        ):
            self._upsert(
                uid,
                {
                    **row,
                    "turn_id": turn_id,
                    "status": state,
                    "parts": parts,
                },
            )
        if current.get("state") != "running" or joined:
            self._set_turn_state(uid, turn_id, "running")
        self._active_turns[uid] = turn_id

    def _on_reply_text(self, uid: str, event: dict[str, Any]) -> None:
        text = event.get("text")
        if not isinstance(text, str) or (
            not text and event.get("type") != "response"
        ):
            return
        turn_id = self._event_turn_id(event)
        input_ids = self._event_input_ids(event)
        if turn_id is None and input_ids:
            turn_id = self._input_turns.get((uid, input_ids[0]))
        if turn_id is None:
            turn_id = self._active_turns.get(uid)
        if turn_id is None:
            return
        key = (uid, turn_id)
        if key in self._reply_after_tool:
            self._reply_after_tool.discard(key)
            self._new_reply_row(
                uid,
                turn_id,
                history_key=self._turn_history_key(
                    uid, turn_id, event=event, input_ids=input_ids
                ),
            )
            self._reply_segment_offsets[key] = 0
        if event.get("type") == "response":
            offset = self._reply_segment_offsets.get(key, 0)
            prefix = self._reply_text.get(key, "")[:offset]
            self._upsert_reply(
                uid,
                turn_id,
                prefix + text,
                status="streaming",
                history_key=self._turn_history_key(
                    uid, turn_id, event=event, input_ids=input_ids
                ),
            )
            return
        row_id = self._reply_rows.get((uid, turn_id))
        if row_id is None:
            row_id = self._new_reply_row(
                uid,
                turn_id,
                history_key=self._turn_history_key(
                    uid, turn_id, event=event, input_ids=input_ids
                ),
            )
        self._reply_text[(uid, turn_id)] = (
            self._reply_text.get((uid, turn_id), "") + text
        )
        row = self._rows.setdefault(uid, {}).get(row_id)
        part = 0
        if row is None:
            return
        self.record_event("row.append", {"id": row_id, "part": part, "text": text})

    def _on_turn_complete(self, uid: str, event: dict[str, Any]) -> None:
        turn_id = self._event_turn_id(event)
        input_ids = self._event_input_ids(event)
        if turn_id is None and input_ids:
            input_turns = {
                input_id: self._input_turns.get((uid, input_id))
                for input_id in input_ids
            }
            bound_turn_ids = set(input_turns.values()) - {None}
            if not bound_turn_ids:
                self._end_unbound_inputs(uid, event, input_ids)
                return
            if len(bound_turn_ids) != 1:
                self._end_unbound_inputs(
                    uid,
                    event,
                    [
                        input_id
                        for input_id in input_ids
                        if input_turns[input_id] is None
                    ],
                )
                return
            turn_id = next(iter(bound_turn_ids))
            unbound_ids = [
                input_id
                for input_id in input_ids
                if input_turns[input_id] is None
            ]
            self._end_unbound_inputs(uid, event, unbound_ids)
            input_ids = [
                input_id
                for input_id in input_ids
                if input_turns[input_id] == turn_id
            ]
        if turn_id is None:
            turn_id = self._active_turns.get(uid)
        if turn_id is None:
            return
        key = (uid, turn_id)
        input_ids = list(dict.fromkeys((*self._turn_inputs.get(key, []), *input_ids)))
        if not input_ids and turn_id not in self._turns.get(uid, {}):
            return
        outcome = outcome_for_stop_reason(event.get("stop_reason"))
        if key in self._turn_errors and outcome == "completed":
            outcome = "failed"
        self._upsert_reply(
            uid,
            turn_id,
            self._reply_text.get(key, ""),
            status=outcome,
            history_key=self._turn_history_key(
                uid, turn_id, event=event, input_ids=input_ids
            ),
        )
        for row_id in self._reply_row_ids.get(key, [])[:-1]:
            row = self._rows.get(uid, {}).get(row_id)
            if row is not None:
                self._upsert(uid, {**row, "status": outcome})
        self._reconcile_silent_reply(uid, turn_id)
        self._reply_after_tool.discard(key)
        self._set_turn_state(uid, turn_id, "ended")
        for input_id in input_ids:
            prior = self._inputs.setdefault(uid, {}).get(input_id, {})
            if prior.get("state") == "ended":
                continue
            self._ensure_input_row(uid, input_id, turn_id=turn_id)
            self._set_input_state(
                uid,
                input_id,
                "ended",
                turn_id=turn_id,
                outcome=outcome,
            )
            row = self._rows.setdefault(uid, {}).get(input_id)
            if row is not None:
                self._upsert(uid, {**row, "turn_id": turn_id, "status": outcome})
        if self._active_turns.get(uid) == turn_id:
            self._active_turns.pop(uid, None)

    def _end_unbound_inputs(
        self, uid: str, event: dict[str, Any], input_ids: list[str]
    ) -> None:
        """End named requests that never joined a turn without ending another."""
        outcome = outcome_for_stop_reason(event.get("stop_reason"))
        for input_id in dict.fromkeys(input_ids):
            if not input_id:
                continue
            prior = self._inputs.setdefault(uid, {}).get(input_id, {})
            if prior.get("state") == "ended":
                continue
            self._ensure_input_row(uid, input_id)
            self._set_input_state(
                uid,
                input_id,
                "ended",
                turn_id=None,
                outcome=outcome,
            )
            row = self._rows.setdefault(uid, {}).get(input_id)
            if row is not None:
                self._upsert(
                    uid,
                    {**row, "turn_id": None, "status": outcome},
                )

    def _reconcile_silent_reply(self, uid: str, turn_id: str) -> None:
        """Publish a live row update when persisted projection silences its period."""
        transcript_rows = {
            row["id"]: row
            for row in self._transcript_rows(uid)
            if row.get("kind") == "reply"
        }
        key = (uid, turn_id)
        latest_row_id = self._reply_rows.get(key)
        for row_id in self._reply_row_ids.get(key, []):
            cached = self._rows.get(uid, {}).get(row_id)
            projected = transcript_rows.get(row_id)
            if cached is None or projected is None or projected.get("parts"):
                continue
            parts = cached.get("parts")
            if (
                not isinstance(parts, list)
                or len(parts) != 1
                or not isinstance(parts[0], dict)
                or parts[0].get("type") != "text"
                or not isinstance(parts[0].get("text"), str)
                or parts[0]["text"].strip() != "."
            ):
                continue
            if row_id == latest_row_id:
                self._reply_text[key] = ""
            self._upsert(uid, {**cached, "parts": []})

    def _on_turn_error(self, uid: str, event: dict[str, Any]) -> None:
        message = event.get("message")
        turn_id = self._event_turn_id(event)
        if turn_id is None:
            input_ids = self._event_input_ids(event)
            bound_turn_ids = {
                self._input_turns.get((uid, input_id)) for input_id in input_ids
            } - {None}
            if len(bound_turn_ids) == 1:
                turn_id = next(iter(bound_turn_ids))
            elif not input_ids:
                turn_id = self._active_turns.get(uid)
        if not isinstance(message, str) or not message:
            return
        if turn_id:
            self._turn_errors[(uid, turn_id)] = message
            marker_key = f"{uid}:{turn_id}"
        else:
            marker_key = f"{uid}:{uuid.uuid4().hex}"
        self._upsert(
            uid,
            {
                "id": f"marker-error-{uuid.uuid5(uuid.NAMESPACE_URL, marker_key).hex}",
                "seq": 0,
                "kind": "marker",
                "role": "system",
                "turn_id": turn_id,
                "status": "failed",
                "parts": [{"type": "text", "text": message}],
                "created_at": self._timestamp(),
            },
        )

    def _on_hub_message(self, uid: str, event: dict[str, Any]) -> None:
        if event.get("input_source") == "steering":
            input_id = event.get("message_id")
            joined_request_id = event.get("joined_request_id")
            joined_turn_id = self._event_turn_id(
                {"turn_id": event.get("joined_turn_id")}
            )
            has_join_identity = (
                "joined_request_id" in event or "joined_turn_id" in event
            )
            if has_join_identity:
                if (
                    isinstance(input_id, str)
                    and input_id
                    and isinstance(joined_request_id, str)
                    and joined_request_id
                    and joined_request_id != input_id
                    and joined_turn_id is not None
                    and self._register_joined_turn(
                        uid, joined_request_id, joined_turn_id
                    )
                ):
                    self._join_input(uid, input_id, joined_turn_id)
                return
            turn_id = self._active_turns.get(uid)
            if isinstance(input_id, str) and input_id and turn_id:
                self._join_input(uid, input_id, turn_id)
        note = self._hub_note_part(event.get("hub_note"))
        if note is not None:
            self._on_structured_hub_message(uid, event, note)
        elif event.get("input_source") != "steering" and isinstance(
            event.get("text"), str
        ):
            self._on_hub_note(uid, event)

    def _on_structured_hub_message(
        self, uid: str, event: dict[str, Any], note: dict[str, Any]
    ) -> None:
        row_id = event.get("message_id")
        if not isinstance(row_id, str) or not row_id:
            return
        is_input = event.get("hub_admission") == "input"
        prior = self._inputs.setdefault(uid, {}).get(row_id)
        if prior is not None and not is_input:
            prior_state = prior.get("state")
            if prior_state in {"started", "joined", "ended"}:
                # A late note update must not turn an already-running or
                # completed input back into a note row.
                is_input = True
        turn_id = (prior or {}).get("turn_id") or self._event_turn_id(event)
        if is_input:
            state = (prior or {}).get("state") or "queued"
            status = (
                (prior or {}).get("outcome")
                if state == "ended"
                else state
            )
        else:
            state = None
            status = "done"
        self._upsert(
            uid,
            {
                "id": row_id,
                "seq": 0,
                "kind": "input" if is_input else "note",
                "role": "user" if is_input else "system",
                "turn_id": turn_id,
                "status": status or "ended",
                "parts": [note],
                "created_at": self._timestamp(),
            },
        )
        if is_input and prior is None:
            self._set_input_state(uid, row_id, "queued", turn_id=turn_id)

    def _on_hub_note(self, uid: str, event: dict[str, Any]) -> None:
        text = event.get("text")
        if not isinstance(text, str) or not text:
            return
        row_id = event.get("message_id")
        if not isinstance(row_id, str) or not row_id:
            row_id = f"note-{uuid.uuid4().hex}"
        row = {
            "id": row_id,
            "seq": 0,
            "kind": "note",
            "role": "system",
            "turn_id": self._event_turn_id(event),
            "status": "done",
            "parts": [{"type": "text", "text": text}],
            "created_at": self._timestamp(),
        }
        self._upsert(uid, row)

    def _on_tool_event(self, uid: str, event: dict[str, Any]) -> None:
        tool_id = event.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id:
            tool_id = uuid.uuid4().hex
        turn_id = self._event_turn_id(event) or self._active_turns.get(uid)
        if event.get("type") == "tool_start" and turn_id is not None:
            key = (uid, turn_id)
            if key in self._reply_rows:
                self._reply_after_tool.add(key)
        history_key = self._turn_history_key(
            uid, turn_id, event=event, input_ids=self._event_input_ids(event)
        )
        row_id = self._tool_row_id(uid, turn_id, tool_id, history_key)
        name = event.get("tool_name")
        name = name if isinstance(name, str) and name else None
        metadata = event.get("metadata")
        acp_data = metadata.get("acp") if isinstance(metadata, dict) else None
        acp_data = acp_data if isinstance(acp_data, dict) else {}
        raw_input = acp_data.get("rawInput")
        existing_row = self._rows.get(uid, {}).get(row_id)
        existing_parts = existing_row.get("parts") if isinstance(existing_row, dict) else None
        existing_part = (
            existing_parts[0]
            if isinstance(existing_parts, list)
            and existing_parts
            and isinstance(existing_parts[0], dict)
            and existing_parts[0].get("type") == "tool-call"
            and existing_parts[0].get("tool_call_id") == tool_id
            else None
        )
        part: dict[str, Any] = copy.deepcopy(existing_part) if existing_part else {}
        part.update({"type": "tool-call", "tool_call_id": tool_id})
        if name is not None:
            part["tool_name"] = name
        elif not isinstance(part.get("tool_name"), str) or not part["tool_name"]:
            part["tool_name"] = "Tool"
        if raw_input is not None:
            part["args"] = copy.deepcopy(raw_input)
        elif event.get("input") is not None:
            part["args"] = copy.deepcopy(event["input"])
        elif "args" not in part:
            part["args"] = {}
        if event.get("type") == "tool_start":
            status = "running"
            part.pop("result", None)
            part.pop("is_error", None)
        else:
            status = "completed" if event.get("success") is True else "failed"
            result = acp_data.get("rawOutput")
            if result is None:
                result = acp_data.get("content")
            if result is None:
                result = event.get("output")
            if result is not None:
                part["result"] = copy.deepcopy(result)
            part["is_error"] = status == "failed"
        self._upsert(
            uid,
            {
                "id": row_id,
                "seq": 0,
                "kind": "tool",
                "role": "assistant",
                "turn_id": turn_id,
                "status": status,
                "parts": [part],
                "created_at": self._timestamp(),
            },
        )

    def _on_permission_prompt(self, uid: str, event: dict[str, Any]) -> None:
        details = event.get("details")
        details = details if isinstance(details, dict) else {}
        prompt_id = (
            details.get("permission_id")
            or event.get("permission_id")
            or event.get("tool_id")
        )
        if not isinstance(prompt_id, str) or not prompt_id:
            prompt_id = uuid.uuid4().hex
        prompt_data = copy.deepcopy(details)
        prompt_data.setdefault(
            "title", details.get("tool_name") or "Permission requested"
        )
        self._upsert(
            uid,
            {
                "id": f"prompt-{prompt_id}",
                "seq": 0,
                "kind": "prompt",
                "role": "system",
                "turn_id": self._event_turn_id(event)
                or self._event_turn_id(details),
                "status": "open",
                "parts": [
                    {"type": "prompt", "prompt_id": prompt_id, "data": prompt_data}
                ],
                "created_at": self._timestamp(),
            },
        )

    def _on_permission_resolved(self, uid: str, event: dict[str, Any]) -> None:
        prompt_id = event.get("permission_id") or event.get("tool_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            return
        row_id = f"prompt-{prompt_id}"
        row = self._rows.setdefault(uid, {}).get(row_id)
        if row is not None:
            status = str(event.get("type")).removeprefix("permission_")
            parts = copy.deepcopy(row.get("parts", []))
            if parts and isinstance(parts[0], dict) and parts[0].get("type") == "prompt":
                data = parts[0].get("data")
                data = copy.deepcopy(data) if isinstance(data, dict) else {}
                data["resolution"] = {
                    key: copy.deepcopy(value)
                    for key, value in event.items()
                    if key not in {"ts", "seq", "conversation_uid"}
                }
                parts[0]["data"] = data
            self._upsert(
                uid,
                {
                    **row,
                    "turn_id": self._event_turn_id(event) or row.get("turn_id"),
                    "status": status,
                    "parts": parts,
                },
            )

    def _on_permission_parked(self, uid: str, event: dict[str, Any]) -> None:
        prompt_id = event.get("permission_id") or event.get("tool_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            return
        row_id = f"prompt-{prompt_id}"
        row = self._rows.setdefault(uid, {}).get(row_id)
        if row is None:
            return
        parts = copy.deepcopy(row.get("parts", []))
        if not parts or not isinstance(parts[0], dict) or parts[0].get("type") != "prompt":
            return
        data = parts[0].get("data")
        data = copy.deepcopy(data) if isinstance(data, dict) else {}
        if data.get("permission_id") not in (None, prompt_id):
            return
        tool_id = event.get("tool_id")
        if isinstance(tool_id, str) and data.get("tool_id") not in (None, tool_id):
            return
        request_id = event.get("request_id")
        if isinstance(request_id, str) and data.get("request_id") not in (None, request_id):
            return
        data["parked"] = True
        parts[0]["data"] = data
        self._upsert(
            uid,
            {
                **row,
                "turn_id": self._event_turn_id(event) or row.get("turn_id"),
                "status": "open",
                "parts": parts,
            },
        )

    def _join_input(self, uid: str, input_id: str, turn_id: str) -> None:
        self._ensure_input_row(uid, input_id)
        prior = self._inputs.setdefault(uid, {}).get(input_id, {})
        if prior.get("state") == "ended":
            return
        prior_turn_id = prior.get("turn_id")
        if prior_turn_id is not None and prior_turn_id != turn_id:
            return
        if prior.get("state") != "joined":
            self._set_input_state(uid, input_id, "joined", turn_id=turn_id)
        self._input_turns[(uid, input_id)] = turn_id
        self._add_turn_input(uid, turn_id, input_id)
        row = self._rows.setdefault(uid, {}).get(input_id)
        if row is not None and (
            row.get("turn_id") != turn_id or row.get("status") != "joined"
        ):
            self._upsert(uid, {**row, "turn_id": turn_id, "status": "joined"})
        self._set_turn_state(uid, turn_id, "running")

    def _register_joined_turn(self, uid: str, request_id: str, turn_id: str) -> bool:
        """Recover the active turn identity carried by an ACP steer admission."""
        prior = self._inputs.setdefault(uid, {}).get(request_id, {})
        if prior.get("state") == "ended":
            return False
        prior_turn_id = prior.get("turn_id")
        if prior_turn_id is not None and prior_turn_id != turn_id:
            return False

        self._ensure_input_row(uid, request_id)
        if prior.get("state") not in {"started", "joined"}:
            self._set_input_state(uid, request_id, "started", turn_id=turn_id)
        self._input_turns[(uid, request_id)] = turn_id
        self._add_turn_input(uid, turn_id, request_id)
        row = self._rows.setdefault(uid, {}).get(request_id)
        state = self._inputs[uid][request_id]["state"]
        if row is not None and (
            row.get("turn_id") != turn_id or row.get("status") != state
        ):
            self._upsert(uid, {**row, "turn_id": turn_id, "status": state})
        self._set_turn_state(uid, turn_id, "running")
        self._active_turns[uid] = turn_id
        return True

    def _ensure_input_row(
        self,
        uid: str,
        input_id: str,
        *,
        text: str = "",
        turn_id: str | None = None,
    ) -> None:
        if input_id in self._rows.setdefault(uid, {}):
            return
        transcript_row = next(
            (row for row in self._transcript_rows(uid) if row.get("id") == input_id),
            None,
        )
        row = transcript_row or {
            "id": input_id,
            "seq": 0,
            "kind": "input",
            "role": "user",
            "turn_id": turn_id,
            "status": "queued",
            "parts": [{"type": "text", "text": text}],
            "created_at": self._timestamp(),
        }
        self._upsert(uid, row)

    def _transcript_input_id(self, uid: str, text: str) -> str | None:
        for row in reversed(self._transcript_rows(uid)):
            if row.get("kind") != "input":
                continue
            parts = row.get("parts") or []
            if parts and isinstance(parts[0], dict) and parts[0].get("text") == text:
                return row["id"]
        return None

    def _add_turn_input(self, uid: str, turn_id: str, input_id: str) -> None:
        values = self._turn_inputs.setdefault((uid, turn_id), [])
        if input_id not in values:
            values.append(input_id)

    def _new_reply_row(
        self, uid: str, turn_id: str, *, history_key: str | None = None
    ) -> str:
        history_key = history_key or self._turn_history_keys.get((uid, turn_id))
        key = (uid, turn_id)
        row_ids = self._reply_row_ids.setdefault(key, [])
        if row_ids:
            identity = (
                f"{uid}:acp:{history_key}"
                if isinstance(history_key, str) and history_key
                else f"{uid}:turn:{turn_id}"
            )
            segment_identity = f"{identity}:segment:{len(row_ids)}"
            row_id = f"reply-{uuid.uuid5(uuid.NAMESPACE_URL, segment_identity).hex}"
        else:
            row_id = self._reply_row_id(uid, turn_id, history_key)
        row_ids.append(row_id)
        self._reply_rows[key] = row_id
        self._reply_text[(uid, turn_id)] = ""
        self._upsert(
            uid,
            {
                "id": row_id,
                "seq": 0,
                "kind": "reply",
                "role": "assistant",
                "turn_id": turn_id,
                "status": "streaming",
                "parts": [{"type": "text", "text": ""}],
                "created_at": self._timestamp(),
            },
        )
        return row_id

    def _upsert_reply(
        self,
        uid: str,
        turn_id: str,
        text: str,
        *,
        status: str,
        history_key: str | None = None,
    ) -> None:
        row_id = self._reply_rows.get((uid, turn_id))
        if row_id is None:
            row_id = self._new_reply_row(uid, turn_id, history_key=history_key)
        row = self._rows.setdefault(uid, {}).get(row_id)
        if row is None:
            return
        current_text = self._reply_text.get((uid, turn_id), "")
        if text != current_text:
            current_text = text
            self._reply_text[(uid, turn_id)] = text
        self._upsert(
            uid,
            {
                **row,
                "status": status,
                "parts": [{"type": "text", "text": current_text}],
            },
        )

    @staticmethod
    def _event_input_ids(event: dict[str, Any]) -> list[str]:
        ids = event.get("request_ids")
        values = (
            [value for value in ids if isinstance(value, str) and value]
            if isinstance(ids, list)
            else []
        )
        request_id = event.get("request_id")
        if isinstance(request_id, str) and request_id and request_id not in values:
            values.append(request_id)
        return values

    @staticmethod
    def _event_turn_id(event: dict[str, Any]) -> str | None:
        value = event.get("turn_id")
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (str, int)) and str(value):
            return str(value)
        return None

    def _turn_history_key(
        self,
        uid: str,
        turn_id: str | None,
        *,
        event: dict[str, Any] | None = None,
        input_ids: list[str] | None = None,
    ) -> str | None:
        if turn_id is None:
            return None
        key = (uid, turn_id)
        candidate = event.get("acp_turn_key") if isinstance(event, dict) else None
        if isinstance(candidate, str) and candidate:
            self._turn_history_keys[key] = candidate
            return candidate
        known = self._turn_history_keys.get(key)
        if known:
            return known

        llm_service = getattr(self._state_service, "_llm_service", None)
        manager = getattr(llm_service, "conversation_manager", None)
        messages = getattr(llm_service, "conversation_history", None)
        if not isinstance(messages, list):
            messages = getattr(manager, "messages", None)
        if not isinstance(messages, list):
            return None

        candidates = set(input_ids or ())
        for message in reversed(messages):
            metadata = (
                message.get("metadata")
                if isinstance(message, dict)
                else getattr(message, "metadata", None)
            )
            if not isinstance(metadata, dict):
                continue
            if self._event_turn_id(metadata) != turn_id:
                continue
            request_id = metadata.get("request_id")
            if candidates and request_id not in candidates:
                continue
            history_key = metadata.get("acp_turn_key")
            if isinstance(history_key, str) and history_key:
                self._turn_history_keys[key] = history_key
                return history_key
        return None

    @staticmethod
    def _reply_row_id(
        uid: str, turn_id: str, history_key: str | None = None
    ) -> str:
        identity = (
            f"{uid}:acp:{history_key}"
            if isinstance(history_key, str) and history_key
            else f"{uid}:turn:{turn_id}"
        )
        return f"reply-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"

    @staticmethod
    def _reply_segment_row_id(
        uid: str,
        turn_id: str,
        segment: int,
        history_key: str | None = None,
    ) -> str:
        identity = (
            f"{uid}:acp:{history_key}"
            if isinstance(history_key, str) and history_key
            else f"{uid}:turn:{turn_id}"
        )
        return f"reply-{uuid.uuid5(uuid.NAMESPACE_URL, f'{identity}:segment:{segment}').hex}"

    @staticmethod
    def _tool_row_id(
        uid: str,
        turn_id: str | None,
        tool_id: str,
        history_key: str | None = None,
    ) -> str:
        identity = history_key if isinstance(history_key, str) and history_key else turn_id or ""
        return f"tool-{uuid.uuid5(uuid.NAMESPACE_URL, f'{uid}:{identity}:{tool_id}').hex}"

    @staticmethod
    def _hub_note_part(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("type") != "hub-note":
            return None
        if (
            not isinstance(value.get("from"), str)
            or not value["from"]
            or not isinstance(value.get("to"), str)
            or not isinstance(value.get("text"), str)
            or not isinstance(value.get("observed"), bool)
        ):
            return None
        part = copy.deepcopy(value)
        for field, check in (
            ("kind", lambda item: isinstance(item, str)),
            ("thread_id", lambda item: isinstance(item, str)),
            ("reply_to", lambda item: isinstance(item, str)),
            ("is_direct", lambda item: isinstance(item, bool)),
            (
                "presence",
                lambda item: isinstance(item, str)
                and item in {"online", "offline"},
            ),
        ):
            optional = part.get(field)
            if optional is not None and not check(optional):
                part.pop(field, None)
        return part

    def replace_transcript(self) -> Event:
        """Replace the row window after a daemon transcript compaction."""
        uid = self._conversation_uid()
        llm_service = getattr(self._state_service, "_llm_service", None)
        manager = getattr(llm_service, "conversation_manager", None)
        history = getattr(llm_service, "conversation_history", None)
        if not isinstance(history, list):
            history = getattr(manager, "messages", None)
        dropped_row_ids: set[str] = set()
        if isinstance(history, list):
            compaction_stamps = []
            saw_compaction = False
            has_unparseable_compaction = False
            compaction_snapshot_count = None
            compaction_retained_positions: set[int] = set()
            has_compaction_position_metadata = False
            compaction_position_map: dict[int, int] = {}
            compaction_output_count = None
            compaction_snapshot_row_revision = None
            compaction_id = None
            compaction_source_id = None
            for message in history:
                if isinstance(message, dict):
                    metadata = message.get("metadata")
                    timestamp = message.get("timestamp")
                else:
                    metadata = getattr(message, "metadata", None)
                    timestamp = getattr(message, "timestamp", None)
                if not isinstance(metadata, dict) or not (
                    metadata.get("context_compaction")
                    or metadata.get("compaction_summary")
                ):
                    continue
                saw_compaction = True
                snapshot_count = metadata.get("compaction_snapshot_count")
                retained_positions = metadata.get("compaction_retained_positions")
                if (
                    isinstance(snapshot_count, int)
                    and not isinstance(snapshot_count, bool)
                    and snapshot_count >= 0
                    and isinstance(retained_positions, list)
                    and all(
                        isinstance(position, int)
                        and not isinstance(position, bool)
                        and position >= 0
                        for position in retained_positions
                    )
                ):
                    compaction_snapshot_count = snapshot_count
                    compaction_retained_positions = set(retained_positions)
                    has_compaction_position_metadata = True
                    raw_position_map = metadata.get(
                        "compaction_retained_position_map"
                    )
                    if isinstance(raw_position_map, dict):
                        compaction_position_map = {
                            int(source): target
                            for source, target in raw_position_map.items()
                            if isinstance(source, str)
                            and source.isdigit()
                            and isinstance(target, int)
                            and not isinstance(target, bool)
                            and target >= 0
                        }
                    output_count = metadata.get("compaction_output_count")
                    if (
                        isinstance(output_count, int)
                        and not isinstance(output_count, bool)
                        and output_count >= 0
                    ):
                        compaction_output_count = output_count
                    snapshot_revision = metadata.get(
                        "compaction_snapshot_row_revision"
                    )
                    if (
                        isinstance(snapshot_revision, int)
                        and not isinstance(snapshot_revision, bool)
                        and snapshot_revision >= 0
                    ):
                        compaction_snapshot_row_revision = snapshot_revision
                value = metadata.get("compaction_id")
                compaction_id = value if isinstance(value, str) and value else None
                value = metadata.get("compaction_source_id")
                compaction_source_id = (
                    value if isinstance(value, str) and value else None
                )
                stamp = _parse_chat_timestamp(timestamp)
                if stamp is None:
                    has_unparseable_compaction = True
                    break
                compaction_stamps.append(stamp)
            if has_compaction_position_metadata or (
                compaction_stamps and not has_unparseable_compaction
            ):
                boundary = (
                    max(compaction_stamps)
                    if compaction_stamps and not has_unparseable_compaction
                    else None
                )
                manager_rows = getattr(manager, "chat_stream_rows", None)
                if isinstance(manager_rows, list):
                    retained_rows = []
                    for row in manager_rows:
                        if not isinstance(row, dict):
                            continue
                        row_stamp = _parse_chat_timestamp(row.get("created_at"))
                        row_position = row.get("_history_index")
                        if has_compaction_position_metadata:
                            row_coordinate = row.get("_history_compaction_id")
                            if compaction_id and row_coordinate == compaction_id:
                                keep_row = True
                            elif (
                                row_coordinate is not None
                                and row_coordinate != compaction_source_id
                            ):
                                # Rows already tagged for another history
                                # generation cannot be compared to this map.
                                keep_row = True
                            elif (
                                isinstance(row_position, int)
                                and not isinstance(row_position, bool)
                                and row_position >= 0
                            ):
                                revision = row.get("_history_row_revision")
                                is_concurrent = (
                                    isinstance(revision, int)
                                    and not isinstance(revision, bool)
                                    and compaction_snapshot_row_revision is not None
                                    and revision > compaction_snapshot_row_revision
                                )
                                # Legacy rows have no revision watermark. Keep
                                # their old tail behavior until a new durable
                                # coordinate is written for them.
                                if (
                                    not isinstance(revision, int)
                                    and row_position >= compaction_snapshot_count
                                ):
                                    is_concurrent = True
                                if is_concurrent:
                                    keep_row = True
                                    if (
                                        compaction_id
                                        and compaction_output_count is not None
                                    ):
                                        row["_history_index"] = (
                                            compaction_output_count
                                            + row_position
                                            - compaction_snapshot_count
                                        )
                                        row["_history_compaction_id"] = compaction_id
                                elif compaction_position_map:
                                    left_position = compaction_position_map.get(
                                        row_position - 1
                                    )
                                    right_position = compaction_position_map.get(
                                        row_position
                                    )
                                    if left_position is not None:
                                        keep_row = True
                                        row["_history_index"] = left_position + 1
                                    elif right_position is not None:
                                        keep_row = True
                                        row["_history_index"] = right_position
                                    else:
                                        keep_row = False
                                    if keep_row and compaction_id:
                                        row["_history_compaction_id"] = compaction_id
                                else:
                                    adjacent_to_retained = (
                                        row_position - 1 in compaction_retained_positions
                                        or row_position in compaction_retained_positions
                                    )
                                    keep_row = (
                                        row_position >= compaction_snapshot_count
                                        or adjacent_to_retained
                                    )
                                    if keep_row and compaction_id:
                                        row["_history_compaction_id"] = compaction_id
                            else:
                                # Legacy rows have no source position. Preserve
                                # the historical timestamp fallback for them.
                                keep_row = (
                                    row_stamp is None
                                    or boundary is None
                                    or row_stamp > boundary
                                )
                        else:
                            keep_row = (
                                row_stamp is None
                                or boundary is None
                                or row_stamp > boundary
                            )
                        if not keep_row:
                            row_id = row.get("id")
                            if isinstance(row_id, str):
                                dropped_row_ids.add(row_id)
                            continue
                        if row_stamp is not None and not has_compaction_position_metadata:
                            preceding = []
                            for index, message in enumerate(history):
                                if isinstance(message, dict):
                                    timestamp = message.get("timestamp")
                                else:
                                    timestamp = getattr(message, "timestamp", None)
                                message_stamp = _parse_chat_timestamp(timestamp)
                                if message_stamp is not None and message_stamp <= row_stamp:
                                    preceding.append(index)
                            row["_history_index"] = len(preceding)
                        retained_rows.append(row)
                    manager_rows[:] = retained_rows
            if saw_compaction:
                set_coordinate = getattr(
                    manager, "set_chat_stream_history_coordinate", None
                )
                if callable(set_coordinate):
                    set_coordinate(compaction_id, len(history))
        custom = getattr(self._state_service, "chat_stream_snapshot", None)
        state = custom() if callable(custom) else {}
        state_rows = state.get("rows") if isinstance(state, dict) else None
        if not isinstance(state_rows, list):
            state_rows = []
        state_rows = [
            _public_chat_stream_row(row)
            for row in state_rows
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and row["id"]
            and row["id"] not in dropped_row_ids
        ]
        row_cache = self._rows.get(uid, {})
        for row_id in dropped_row_ids:
            row_cache.pop(row_id, None)
        rows = merge_chat_stream_rows(self._transcript_rows(uid), state_rows)
        compaction_marker_parts = [
            {"type": "text", "text": "Earlier messages compacted"}
        ]
        compaction_markers = [
            row
            for row in rows
            if row.get("kind") == "marker"
            and row.get("parts") == compaction_marker_parts
        ]
        cached_compaction_markers = [
            row
            for row in self._rows.get(uid, {}).values()
            if row.get("kind") == "marker"
            and row.get("parts") == compaction_marker_parts
        ]
        if compaction_markers:
            marker = copy.deepcopy(compaction_markers[-1])
            compaction_marker_ids = {
                row["id"] for row in [*compaction_markers, *cached_compaction_markers]
            }
            marker["seq"] = self._log(uid).next_seq
        elif cached_compaction_markers:
            marker = copy.deepcopy(cached_compaction_markers[-1])
            compaction_marker_ids = {row["id"] for row in cached_compaction_markers}
            marker["seq"] = self._log(uid).next_seq
        else:
            compaction_marker_ids = set()
            marker = {
                "id": f"marker-{uuid.uuid4().hex}",
                "seq": self._log(uid).next_seq,
                "kind": "marker",
                "role": "system",
                "turn_id": None,
                "status": "done",
                "parts": compaction_marker_parts,
                "created_at": self._timestamp(),
            }
        active_turn = self._active_turns.get(uid)
        live_rows = []
        for row in self._rows.get(uid, {}).values():
            row_id = row.get("id")
            input_info = self._inputs.get(uid, {}).get(row_id, {})
            if (
                row.get("turn_id") == active_turn
                or input_info.get("state") in {"queued", "started", "joined"}
                or row.get("kind") == "note"
            ):
                live_rows.append(row)
        rows = merge_chat_stream_rows(rows, live_rows)
        rows = [row for row in rows if row.get("id") not in compaction_marker_ids]
        if marker["turn_id"] is None:
            marker["turn_id"] = active_turn
        replacement_rows = [marker, *rows][-REPLAY_WINDOW:]
        event = self._log(uid).publish("replace", {"rows": replacement_rows})
        self._rows[uid] = {row["id"]: copy.deepcopy(row) for row in replacement_rows}
        return event

    def publish_conversation_reset(self) -> Event:
        """Publish restored state on a fresh generation for this conversation."""
        uid = self._conversation_uid()
        self._reset_conversation_cache(uid)
        # A restored session can reuse a UID whose old in-memory stream belongs
        # to a later visit. Start a new sequence space so subscribers discard
        # their stale cursors and request the canonical restored snapshot.
        self._logs.pop(uid, None)
        rows = self._snapshot_rows(uid)

        def store_rows(_event: Event) -> None:
            self._rows[uid] = {row["id"]: copy.deepcopy(row) for row in rows}

        # The engine detects the new conversation UID on this live event and
        # asks the daemon for a full snapshot, which also carries turns/inputs.
        return self._log(uid).publish(
            "replace", {"rows": rows}, on_commit=store_rows
        )

    def _reset_conversation_cache(self, uid: str) -> None:
        """Rebuild transcript projections while retaining admission ownership."""
        previous_rows = self._rows.get(uid, {})
        admitted_inputs = self._admission_inputs.get(uid, {})
        queued_ids = {
            input_id
            for input_id, info in admitted_inputs.items()
            if info.get("state") == "queued"
            and (uid, input_id) in self._input_scopes
        }
        queued_rows = {
            input_id: copy.deepcopy(row)
            for input_id, row in previous_rows.items()
            if input_id in queued_ids
            and row.get("kind") == "input"
            and row.get("id") == input_id
        }
        if queued_rows:
            # Accepted work outlives a transcript restore. Keep its existing
            # safe row (including authenticated attachment references) so the
            # queue supervisor can still claim and project it.
            self._rows[uid] = queued_rows
        else:
            self._rows.pop(uid, None)

        # Rebuild live projection from retained accepted work. In particular,
        # stale started entries stay in the admission ledger for idempotency,
        # but cannot shadow lifecycle reconstructed from the restored history.
        self._inputs[uid] = {
            input_id: copy.deepcopy(admitted_inputs[input_id])
            for input_id in queued_ids
        }

        self._turns.pop(uid, None)
        self._active_turns.pop(uid, None)

        uid_scoped_maps = (
            self._turn_inputs,
            self._input_turns,
            self._reply_rows,
            self._reply_text,
            self._reply_segment_offsets,
            self._reply_row_ids,
            self._turn_history_keys,
            self._turn_errors,
        )
        for cache in uid_scoped_maps:
            for key in tuple(cache):
                if isinstance(key, tuple) and key and key[0] == uid:
                    cache.pop(key, None)
        self._reply_after_tool = {
            key for key in self._reply_after_tool if key[0] != uid
        }

        transcript_rows = self._transcript_rows(uid)
        restored_inputs = self._snapshot_components(
            uid, transcript_rows=transcript_rows
        )[2]
        self._inputs[uid] = {
            item["id"]: copy.deepcopy(item)
            for item in restored_inputs
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        transcript_replies = {
            row["turn_id"]: row
            for row in transcript_rows
            if row.get("kind") == "reply" and row.get("turn_id")
        }
        for row in transcript_rows:
            input_id = row.get("id")
            if row.get("kind") != "input" or not isinstance(input_id, str):
                continue
            admitted = admitted_inputs.get(input_id)
            restored = self._input_lifecycle_from_history(row, transcript_replies)
            if (
                admitted is not None
                and admitted.get("state") != "queued"
                and restored.get("state") == "ended"
            ):
                # Stable request IDs can appear in a restored transcript after
                # a later visit left a stale started admission projection.
                # Keep its owner/content binding, but return the canonical
                # terminal lifecycle for subsequent idempotent retries.
                if admitted.get("_native_plugin") is True:
                    restored["_native_plugin"] = True
                admitted_inputs[input_id] = copy.deepcopy(restored)

    def _log(self, uid: str | None = None) -> ChatStreamLog:
        uid = uid or self._conversation_uid()
        if uid not in self._logs:
            self._logs[uid] = ChatStreamLog(
                uid,
                snapshot_provider=lambda uid=uid: self._snapshot(uid),
                on_event=self._publish_live,
            )
        return self._logs[uid]

    def row_pager(self, *, uid: str | None = None) -> ChatStreamRowPager:
        """Build a pager over this conversation's transcript and live rows."""
        return self._row_pager(uid=uid)

    def _row_pager(
        self,
        *,
        uid: str | None = None,
        snapshot_rows: list[dict[str, Any]] | None = None,
        transcript_rows: list[dict[str, Any]] | None = None,
    ) -> ChatStreamRowPager:
        """Build a pager, optionally reusing rows already projected by a snapshot."""

        def current_uid() -> str:
            return uid if uid is not None else self._conversation_uid()

        def current_compaction_seq() -> int:
            log = self._logs.get(current_uid())
            return log.last_replace_seq if log is not None else 0

        return ChatStreamRowPager(
            conversation_uid_provider=current_uid,
            snapshot_rows_provider=lambda: (
                snapshot_rows
                if snapshot_rows is not None
                else self._snapshot_rows(current_uid())
            ),
            transcript_rows_provider=lambda: (
                transcript_rows
                if transcript_rows is not None
                else self._transcript_rows(current_uid())
            ),
            row_log_provider=lambda: list(self._rows.get(current_uid(), {}).values()),
            compaction_seq_provider=current_compaction_seq,
        )

    def _snapshot_components(
        self,
        uid: str,
        *,
        transcript_rows: list[dict[str, Any]] | None = None,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        custom = getattr(self._state_service, "chat_stream_snapshot", None)
        state = custom() if callable(custom) and uid == self._conversation_uid() else {}
        if not isinstance(state, dict):
            state = {}
        state_rows = state.get("rows")
        if not isinstance(state_rows, list):
            state_rows = []
        state_rows = [
            _public_chat_stream_row(row)
            for row in state_rows
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and row["id"]
        ]
        if transcript_rows is None:
            transcript_rows = self._transcript_rows(uid)
        rows_by_id = {
            row["id"]: row
            for row in merge_chat_stream_rows(
                transcript_rows,
                list(self._rows.get(uid, {}).values()),
                state_rows,
            )
        }
        turns = {
            turn["turn_id"]: turn
            for turn in state.get("turns", [])
            if isinstance(turn, dict) and turn.get("turn_id")
        }
        turns.update(copy.deepcopy(self._turns.get(uid, {})))
        inputs = {
            item["id"]: item
            for item in state.get("inputs", [])
            if isinstance(item, dict) and item.get("id")
        }
        inputs.update(copy.deepcopy(self._inputs.get(uid, {})))
        snapshot_rows = list(rows_by_id.values())[-REPLAY_WINDOW:]
        visible_input_ids = {
            row["id"] for row in snapshot_rows if row.get("kind") == "input"
        }
        history_replies = {
            row["turn_id"]: row
            for row in rows_by_id.values()
            if row.get("kind") == "reply" and row.get("turn_id")
        }
        for row in snapshot_rows:
            if row.get("kind") != "input":
                continue
            input_id = row.get("id")
            if not isinstance(input_id, str) or input_id in inputs:
                continue
            inputs[input_id] = self._input_lifecycle_from_history(
                row, history_replies
            )
        inputs = {
            input_id: info
            for input_id, info in inputs.items()
            if input_id in visible_input_ids
        }

        turn_ids = list(
            dict.fromkeys(
                row["turn_id"]
                for row in snapshot_rows
                if row.get("kind") in {"input", "reply", "tool"}
                and isinstance(row.get("turn_id"), str)
                and row["turn_id"]
            )
        )
        for turn_id, current in turns.items():
            current_inputs = current.get("input_ids", [])
            turns[turn_id] = {
                **current,
                "input_ids": [
                    input_id
                    for input_id in current_inputs
                    if input_id in visible_input_ids
                ],
            }
        for turn_id in turn_ids:
            current = turns.get(turn_id, {})
            input_ids = list(current.get("input_ids", []))
            input_ids.extend(
                item["id"]
                for item in inputs.values()
                if item.get("turn_id") == turn_id and item["id"] not in input_ids
            )
            reply = history_replies.get(turn_id)
            persisted_terminal_input = any(
                item.get("turn_id") == turn_id and item.get("state") == "ended"
                for item in inputs.values()
            )
            turn_state = (
                "ended"
                if persisted_terminal_input
                or (
                    reply is not None
                    and reply.get("status") not in {"streaming", "running"}
                )
                else current.get("state") or "running"
            )
            turn_snapshot = {
                "turn_id": turn_id,
                "state": turn_state,
                "input_ids": input_ids,
            }
            if isinstance(current.get("origin"), dict):
                turn_snapshot["origin"] = copy.deepcopy(current["origin"])
            turns[turn_id] = turn_snapshot
        return (
            snapshot_rows,
            list(turns.values()),
            list(inputs.values()),
            state,
        )

    @staticmethod
    def _input_lifecycle_from_history(
        row: dict[str, Any], history_replies: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Project one input's lifecycle from its persisted transcript rows."""
        input_id = row.get("id")
        turn_id = row.get("turn_id")
        persisted_outcome = row.get("status")
        if persisted_outcome in {"completed", "failed", "cancelled"}:
            return {
                "id": input_id,
                "state": "ended",
                "turn_id": turn_id,
                "outcome": persisted_outcome,
            }
        if persisted_outcome == "done":
            return {
                "id": input_id,
                "state": "ended",
                "turn_id": turn_id,
                "outcome": "completed",
            }
        reply = history_replies.get(turn_id)
        if reply is not None:
            status = reply.get("status")
            outcome = (
                "cancelled"
                if status in {"cancelled", "canceled"}
                else "failed"
                if status in {"failed", "error"}
                else "completed"
            )
            return {
                "id": input_id,
                "state": "ended",
                "turn_id": turn_id,
                "outcome": outcome,
            }
        state_name = row.get("status")
        if state_name not in {"queued", "started", "joined"}:
            state_name = "started" if turn_id else "queued"
        return {"id": input_id, "state": state_name, "turn_id": turn_id}

    def _snapshot_rows(self, uid: str) -> list[dict[str, Any]]:
        return self._snapshot_components(uid)[0]

    def _snapshot(self, uid: str) -> dict[str, Any]:
        transcript_rows = self._transcript_rows(uid)
        rows, turns, inputs, state = self._snapshot_components(
            uid, transcript_rows=transcript_rows
        )
        older = self._row_pager(
            uid=uid,
            snapshot_rows=rows,
            transcript_rows=transcript_rows,
        ).snapshot_older_cursor()
        return {
            "rows": rows,
            "turns": turns,
            "inputs": inputs,
            "older": older if older is not None else state.get("older"),
        }

    def _transcript_rows(self, uid: str) -> list[dict[str, Any]]:
        if uid != self._conversation_uid():
            return []
        llm_service = getattr(self._state_service, "_llm_service", None)
        manager = getattr(llm_service, "conversation_manager", None)
        manager_messages = getattr(manager, "messages", None)
        messages = getattr(llm_service, "conversation_history", None)
        if not isinstance(messages, list):
            messages = manager_messages
        if not isinstance(messages, list):
            messages = []

        turn_history_by_turn, turn_history_by_request = turn_history_key_index(
            messages, self._event_turn_id
        )

        def field(message: Any, name: str, default: Any = None) -> Any:
            if isinstance(message, dict):
                return message.get(name, default)
            return getattr(message, name, default)

        from kollabor_ai.response_channels import turn_display_texts

        voice_display = turn_display_texts(
            [
                {
                    "role": field(message, "role"),
                    "content": field(message, "content", ""),
                    "metadata": field(message, "metadata", {}),
                }
                for message in messages
            ]
        )

        def normalize_tool_id(value: Any) -> str | None:
            if isinstance(value, int) and not isinstance(value, bool):
                return str(value)
            return value if isinstance(value, str) and value else None

        def call_id_for(call: Any) -> str | None:
            if not isinstance(call, dict):
                return None
            return normalize_tool_id(call.get("id") or call.get("tool_call_id"))

        pending_tool_calls: dict[str, list[tuple[int, int, str | None]]] = {}
        pending_xml_tool_calls: dict[str, list[tuple[int, int, str | None]]] = {}
        tool_results: dict[tuple[int, int], dict[str, Any]] = {}
        xml_tool_results: dict[tuple[int, int], dict[str, Any]] = {}
        for message_index, message in enumerate(messages):
            role = field(message, "role")
            metadata = field(message, "metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            if role == "assistant":
                calls = metadata.get("tool_calls")
                turn_id = self._event_turn_id(metadata)
                if isinstance(calls, list):
                    for call_index, call in enumerate(calls):
                        call_id = call_id_for(call)
                        if call_id is None:
                            continue
                        pending_tool_calls.setdefault(call_id, []).append(
                            (message_index, call_index, turn_id)
                        )
                xml_calls = metadata.get("xml_tool_calls")
                if isinstance(xml_calls, list):
                    for call_index, call in enumerate(xml_calls):
                        call_id = call_id_for(call)
                        if call_id is None:
                            continue
                        pending_xml_tool_calls.setdefault(call_id, []).append(
                            (message_index, call_index, turn_id)
                        )
                continue
            if metadata.get("tool_output_batch") is True:
                results = metadata.get("xml_tool_results")
                if isinstance(results, list):
                    for result in results:
                        if not isinstance(result, dict):
                            continue
                        call_id = normalize_tool_id(result.get("id"))
                        owners = (
                            pending_xml_tool_calls.get(call_id)
                            if call_id is not None
                            else None
                        )
                        if not owners:
                            continue
                        result_turn_id = self._event_turn_id(metadata)
                        owner_index = next(
                            (
                                candidate
                                for candidate in range(len(owners))
                                if result_turn_id is None
                                or owners[candidate][2] == result_turn_id
                            ),
                            None,
                        )
                        if owner_index is None:
                            continue
                        assistant_index, call_index, _turn_id = owners.pop(owner_index)
                        xml_tool_results[(assistant_index, call_index)] = {
                            "result": copy.deepcopy(result.get("content", "")),
                            "is_error": result.get("is_error") is True,
                        }
                continue
            if role != "tool":
                continue
            call_id = normalize_tool_id(metadata.get("tool_call_id"))
            owners = pending_tool_calls.get(call_id) if call_id is not None else None
            if not owners:
                continue
            result_turn_id = self._event_turn_id(metadata)
            owner_index = next(
                (
                    owner_index
                    for owner_index in range(len(owners) - 1, -1, -1)
                    if result_turn_id is None
                    or owners[owner_index][2] == result_turn_id
                ),
                None,
            )
            if owner_index is None:
                continue
            assistant_index, call_index, _turn_id = owners.pop(owner_index)
            tool_results[(assistant_index, call_index)] = {
                "result": copy.deepcopy(field(message, "content", "")),
                "is_error": metadata.get("is_error") is True
                or metadata.get("status") == "failed",
            }

        rows = []
        reply_segments: dict[tuple[str, str | None], int] = {}
        for index, message in enumerate(messages):
            role = field(message, "role")
            if not isinstance(role, str):
                continue
            metadata = field(message, "metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            if (
                metadata.get("hidden") is True
                or metadata.get("system_initialization") is True
                or metadata.get("tool_output_batch") is True
                # A triggered plugin row has no queue controls until the
                # matching TaggedInput is admitted. It joins the transcript
                # projection when the queue publishes that admission.
                or metadata.get("chat_stream_pending_input_id")
            ):
                continue
            source_role = role
            is_agent_hud = metadata.get("agent_hud") is True
            if is_agent_hud:
                if metadata.get("stream_context_only") is True:
                    continue
                raw_hub_notes = metadata.get("hub_notes")
                if isinstance(raw_hub_notes, list):
                    hub_notes = raw_hub_notes
                else:
                    hub_notes = []
                    hub_note = self._hub_note_part(metadata.get("hub_note"))
                    hub_message_id = metadata.get("hub_message_id")
                    if hub_note is not None and isinstance(hub_message_id, str):
                        hub_notes.append(
                            {
                                "id": hub_message_id,
                                "part": hub_note,
                                "admission": metadata.get("hub_admission"),
                            }
                        )
                timestamp = field(message, "timestamp")
                if hasattr(timestamp, "isoformat"):
                    timestamp = timestamp.isoformat()
                if not isinstance(timestamp, str):
                    timestamp = self._timestamp()
                turn_id = self._event_turn_id(metadata)
                for entry in hub_notes:
                    if not isinstance(entry, dict):
                        continue
                    hub_message_id = entry.get("id")
                    hub_note = self._hub_note_part(entry.get("part"))
                    if (
                        not isinstance(hub_message_id, str)
                        or not hub_message_id
                        or hub_note is None
                    ):
                        continue
                    is_input = entry.get("admission") == "input"
                    rows.append(
                        {
                            "id": hub_message_id,
                            "seq": len(rows),
                            "kind": "input" if is_input else "note",
                            "role": "user" if is_input else "system",
                            "turn_id": turn_id,
                            "status": (
                                metadata.get("status")
                                if isinstance(metadata.get("status"), str)
                                else "completed" if is_input else "done"
                            ),
                            "parts": [hub_note],
                            "created_at": timestamp,
                        }
                    )
                continue
            tool_call_id = metadata.get("tool_call_id")
            if metadata.get("stream_context_only") is True or (
                source_role == "tool"
                and isinstance(tool_call_id, str)
                and bool(tool_call_id)
            ):
                continue
            role = (
                source_role
                if source_role in {"user", "assistant", "system"}
                else "system"
            )
            kind = metadata.get("kind")
            handoff = metadata.get("queue_handoff")
            if isinstance(handoff, dict) and not metadata.get("request_id"):
                metadata = {**metadata, "request_id": handoff.get("request_id")}
            has_hub_identity = bool(
                metadata.get("agent_hud")
                or metadata.get("hub_message")
                or metadata.get("hub_message_id")
            )
            is_compaction = bool(
                metadata.get("context_compaction")
                or metadata.get("compaction_summary")
            )
            if is_compaction:
                kind, role = "marker", "system"
            elif has_hub_identity:
                if metadata.get("hub_admission") == "input":
                    kind, role = "input", "user"
                else:
                    kind, role = "note", "system"
            elif source_role == "tool":
                kind, role = "tool", "assistant"
            if kind not in {"input", "reply", "tool", "note", "prompt", "marker"}:
                kind = (
                    "input"
                    if source_role == "user"
                    else (
                        "tool"
                        if source_role == "tool"
                        else "reply" if source_role == "assistant" else "note"
                    )
            )
            raw_content = field(message, "content", "")
            projected = voice_display[index]
            plugin_display_text = metadata.get("chat_stream_display_text")
            content = (
                projected
                if source_role == "assistant" and projected is not None
                else plugin_display_text
                if source_role == "user" and isinstance(plugin_display_text, str)
                else metadata.get("display_content", raw_content)
            )
            image_store = getattr(llm_service, "_image_store", None)
            resolve_image = getattr(image_store, "resolve", None)
            conversation_logger = getattr(llm_service, "conversation_logger", None)
            persist_managed_image = None
            conversations_dir = getattr(conversation_logger, "conversations_dir", None)
            session_id = getattr(conversation_logger, "session_id", None)
            if (
                callable(resolve_image)
                and conversations_dir is not None
                and isinstance(session_id, str)
                and session_id
            ):
                def persist_managed_image(media_id: str) -> str | None:
                    data_url = resolve_image(media_id)
                    if not isinstance(data_url, str):
                        return None
                    try:
                        from kollabor_engine.media_store import save_managed_upload_media

                        record = save_managed_upload_media(
                            conversations_dir, session_id, media_id, data_url
                        )
                    except Exception:
                        logger.debug(
                            "Could not persist managed chat image %s",
                            media_id,
                            exc_info=True,
                        )
                        return None
                    return f"/media/{quote(record.ref, safe='')}"
            handoff_request_id = (
                handoff.get("request_id") if isinstance(handoff, dict) else None
            )
            handoff_image_urls = self._handoff_image_urls(handoff)
            hub_note = self._hub_note_part(metadata.get("hub_note"))
            saved_parts = metadata.get("chat_stream_parts")
            parts = (
                [{"type": "text", "text": "Earlier messages compacted"}]
                if is_compaction
                else (
                    [hub_note]
                    if hub_note is not None
                    else self._transcript_parts(
                        saved_parts if isinstance(saved_parts, list) else content,
                        None if isinstance(saved_parts, list) else resolve_image,
                        handoff_image_urls,
                        persist_managed_image,
                    )
                )
            )
            turn_id = self._event_turn_id(metadata)
            timestamp = field(message, "timestamp")
            if hasattr(timestamp, "isoformat"):
                timestamp = timestamp.isoformat()
            if not isinstance(timestamp, str):
                timestamp = self._timestamp()
            manager_uuid = None
            if isinstance(manager_messages, list) and index < len(manager_messages):
                candidate = manager_messages[index]
                candidate_metadata = field(candidate, "metadata", {})
                candidate_content = field(candidate, "content", None)
                candidate_role = field(candidate, "role")
                if (
                    isinstance(candidate_metadata, dict)
                    and candidate_metadata == metadata
                    and candidate_role == source_role
                    and candidate_content == raw_content
                ):
                    manager_uuid = field(candidate, "uuid")
            fallback_identity = f"{uid}:{source_role}:{timestamp}:{raw_content!r}"
            history_key = metadata.get("acp_turn_key")
            if not isinstance(history_key, str) or not history_key:
                history_key = None
            request_id = metadata.get("request_id")
            request_ids = metadata.get("request_ids")
            input_row_ids = list(
                dict.fromkeys(
                    value
                    for value in request_ids
                    if isinstance(value, str) and value
                )
            ) if isinstance(request_ids, (list, tuple)) else []
            if isinstance(request_id, str) and request_id and request_id not in input_row_ids:
                input_row_ids.insert(0, request_id)
            if history_key is None and kind in {"reply", "tool"}:
                history_key = (
                    self._turn_history_keys.get((uid, turn_id))
                    if turn_id is not None
                    else None
                )
                if history_key is None:
                    history_key = (
                        turn_history_by_request.get((turn_id, request_id))
                        if isinstance(request_id, str)
                        else turn_history_by_turn.get(turn_id)
                    )
                    if history_key is not None and turn_id is not None:
                        self._turn_history_keys[(uid, turn_id)] = history_key
            input_id = metadata.get("input_id")
            hub_message_id = metadata.get("hub_message_id")
            tool_id = metadata.get("tool_id")
            calls = metadata.get("tool_calls")
            calls = calls if isinstance(calls, list) else []
            xml_calls = metadata.get("xml_tool_calls")
            xml_calls = xml_calls if isinstance(xml_calls, list) else []
            display_calls = [
                ("native", call_index, call)
                for call_index, call in enumerate(calls)
            ] + [
                ("xml", call_index, call)
                for call_index, call in enumerate(xml_calls)
            ]
            explicit_input_row_ids = False
            if kind == "input":
                if isinstance(hub_message_id, str) and hub_message_id:
                    input_row_ids = [hub_message_id]
                elif not input_row_ids:
                    input_row_ids = [
                        candidate
                        for candidate in (input_id,)
                        if isinstance(candidate, str) and candidate
                    ]
                explicit_input_row_ids = bool(input_row_ids)
                row_id = input_row_ids[0] if input_row_ids else None
            elif kind == "reply":
                explicit_reply_id = metadata.get("reply_id")
                segment_index = None
                skip_empty_tool_reply = (
                    source_role == "assistant"
                    and not parts
                    and bool(display_calls)
                )
                if isinstance(turn_id, str) and turn_id and not skip_empty_tool_reply:
                    segment_key = (turn_id, history_key)
                    segment_index = reply_segments.get(segment_key, 0)
                    reply_segments[segment_key] = segment_index + 1
                row_id = (
                    explicit_reply_id
                    if isinstance(explicit_reply_id, str) and explicit_reply_id
                    else (
                        (
                            self._reply_row_id(uid, turn_id, history_key)
                            if segment_index in (None, 0)
                            else self._reply_segment_row_id(
                                uid, turn_id, segment_index, history_key
                            )
                        )
                        if turn_id is not None and not skip_empty_tool_reply
                        else None
                    )
                )
            elif kind == "marker" and is_compaction:
                compaction_id = metadata.get("compaction_id")
                row_id = (
                    f"marker-{uuid.uuid5(uuid.NAMESPACE_URL, f'{uid}:compaction:{compaction_id}').hex}"
                    if isinstance(compaction_id, str) and compaction_id
                    else None
                )
            elif kind == "tool" and isinstance(tool_id, str) and tool_id:
                row_id = tool_id
            else:
                row_id = next(
                    (
                        candidate
                        for candidate in (hub_message_id, field(message, "uuid"), manager_uuid)
                        if isinstance(candidate, str) and candidate
                    ),
                    None,
                )
            row_id = row_id or f"transcript-{uuid.uuid5(uuid.NAMESPACE_URL, fallback_identity).hex}"
            row_status = metadata.get("status")
            stop_reason = metadata.get("stop_reason")
            chat_stream_outcome = metadata.get("chat_stream_outcome")
            if kind == "input":
                if chat_stream_outcome in {"completed", "failed", "cancelled"}:
                    row_status = chat_stream_outcome
                elif row_status in (None, "done"):
                    row_status = "completed"
            if kind == "reply" and stop_reason is not None and not row_status:
                row_status = outcome_for_stop_reason(stop_reason)
            if kind == "input":
                operator_inputs = metadata.get("operator_inputs")
                text_by_id = {
                    record["request_id"]: record["text"]
                    for record in operator_inputs or []
                    if isinstance(record, dict)
                    and isinstance(record.get("request_id"), str)
                    and isinstance(record.get("text"), str)
                }
                stream_inputs = metadata.get("chat_stream_inputs")
                content_by_id = {
                    record["request_id"]: record["content"]
                    for record in stream_inputs or []
                    if isinstance(record, dict)
                    and isinstance(record.get("request_id"), str)
                    and record.get("request_id")
                    and "content" in record
                }
                input_row_ids = input_row_ids or [row_id]
                # A single tracked request does not prove the provider turn
                # contained a single original item: an untracked item can be
                # folded into the same history row. Keep legacy combined
                # images only when the producer persisted that exact count,
                # or the history shape itself proves a standalone legacy row.
                input_count = metadata.get("chat_stream_input_count")
                has_input_count = (
                    type(input_count) is int and input_count > 0
                )
                single_input_proven = (
                    len(input_row_ids) == 1
                    and (
                        (has_input_count and input_count == 1)
                        or (
                            not has_input_count
                            and (
                                metadata.get("provider") == "acp"
                                or (
                                    hub_note is not None
                                    and metadata.get("hub_admission") == "input"
                                )
                                or not explicit_input_row_ids
                                or "request_ids" not in metadata
                            )
                        )
                    )
                )
                for input_row_id in input_row_ids:
                    input_image_urls = (
                        handoff_image_urls
                        if input_row_id == handoff_request_id
                        else []
                    )
                    saved_parts = metadata.get("chat_stream_parts")
                    if isinstance(saved_parts, list):
                        input_parts = self._transcript_parts(
                            saved_parts, None, input_image_urls, persist_managed_image
                        )
                    elif input_row_id in content_by_id:
                        input_parts = self._transcript_parts(
                            content_by_id[input_row_id],
                            resolve_image,
                            input_image_urls,
                            persist_managed_image,
                        )
                    elif (
                        hub_note is not None
                        and metadata.get("hub_admission") == "input"
                    ):
                        input_parts = [copy.deepcopy(hub_note)]
                    elif (original_text := text_by_id.get(input_row_id)) is not None:
                        # When old combined history has no per-input content
                        # map, retain its authenticated text but do not guess
                        # which input owned an image from the combined turn.
                        input_parts = self._transcript_parts(
                            original_text,
                            resolve_image,
                            input_image_urls,
                            persist_managed_image,
                        )
                        if single_input_proven:
                            input_parts.extend(
                                copy.deepcopy(part)
                                for part in parts
                                if isinstance(part, dict)
                                and part.get("type") == "image"
                            )
                    elif single_input_proven:
                        input_parts = parts
                    else:
                        # The row names one input from a known batch, but no
                        # per-input text or content map ties these parts to it.
                        input_parts = []
                    rows.append(
                        {
                            "id": input_row_id,
                            "seq": len(rows),
                            "kind": kind,
                            "role": role,
                            "turn_id": turn_id,
                            "status": str(row_status or "done"),
                            "parts": input_parts,
                            "created_at": timestamp,
                        }
                    )
            else:
                if not (
                    kind == "reply"
                    and source_role == "assistant"
                    and not parts
                    and bool(display_calls)
                ):
                    rows.append(
                        {
                            "id": row_id,
                            "seq": len(rows),
                            "kind": kind,
                            "role": role,
                            "turn_id": turn_id,
                            "status": str(row_status or "done"),
                            "parts": parts,
                            "created_at": timestamp,
                        }
                    )
            if source_role != "assistant" or not display_calls:
                continue
            for call_source, call_index, call in display_calls:
                if not isinstance(call, dict):
                    continue
                call_id = call_id_for(call)
                if call_id is None:
                    continue
                name = call.get("name") or call.get("tool_name")
                function = call.get("function")
                if isinstance(function, dict):
                    name = name or function.get("name")
                if not isinstance(name, str) or not name:
                    name = "Tool"
                args = call.get("input")
                if args is None:
                    args = call.get("args")
                if args is None and isinstance(function, dict):
                    raw_args = function.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = raw_args
                    elif raw_args is not None:
                        args = copy.deepcopy(raw_args)
                if args is None:
                    args = {}
                part: dict[str, Any] = {
                    "type": "tool-call",
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "args": copy.deepcopy(args),
                }
                result = call.get("result")
                call_result = (
                    xml_tool_results.get((index, call_index))
                    if call_source == "xml"
                    else tool_results.get((index, call_index))
                )
                if result is None and call_result is not None:
                    result = call_result["result"]
                if result is not None:
                    part["result"] = copy.deepcopy(result)
                is_error = call.get("is_error")
                if call_result is not None:
                    is_error = call_result["is_error"]
                elif not isinstance(is_error, bool):
                    is_error = call.get("status") == "failed"
                part["is_error"] = is_error
                call_status = call.get("status")
                if call_result is not None:
                    tool_status = "failed" if call_result["is_error"] else "completed"
                elif call_status in {"running", "in_progress"}:
                    tool_status = "running"
                elif call_status in {"failed", "completed", "cancelled"}:
                    tool_status = call_status
                else:
                    tool_status = "completed"
                rows.append(
                    {
                        "id": self._tool_row_id(uid, turn_id, call_id, history_key),
                        "seq": len(rows),
                        "kind": "tool",
                        "role": "assistant",
                        "turn_id": turn_id,
                        "status": tool_status,
                        "parts": [part],
                        "created_at": timestamp,
                    }
                )

        # Structured native Hub rows survive restart independently of model
        # history. Keep them in the display transcript only; the manager owns
        # validation and never places these rows in `messages` or context.
        manager_rows = getattr(manager, "chat_stream_rows", None)
        native_rows = []
        if isinstance(manager_rows, list):
            for row in manager_rows:
                if not isinstance(row, dict) or row.get("kind") not in {
                    "input",
                    "note",
                }:
                    continue
                parts = row.get("parts")
                if (
                    not isinstance(parts, list)
                    or len(parts) != 1
                    or not isinstance(parts[0], dict)
                    or parts[0].get("type") != "hub-note"
                ):
                    continue
                role = "user" if row["kind"] == "input" else "system"
                if row.get("role") != role:
                    continue
                public_row = _public_chat_stream_row(row)
                if event_problems(
                    {
                        "seq": 0,
                        "conversation_uid": uid,
                        # Validation-only event for durable native rows. The
                        # transport supplies its live generation separately.
                        "generation": "native-hub-row-validation",
                        "type": "row.upsert",
                        "data": public_row,
                    }
                ):
                    continue
                native_rows.append(public_row)
        return merge_chat_stream_rows(rows, native_rows)

    @staticmethod
    def _transcript_parts(
        content: Any,
        resolve_image: Callable[[str], str | None] | None,
        durable_image_urls: list[str] | None = None,
        persist_managed_image: Callable[[str], str | None] | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}] if content else []
            parts.extend(
                {"type": "image", "url": url}
                for url in durable_image_urls or []
                if isinstance(url, str) and url and not url.lower().startswith("data:")
            )
            return parts
        if not isinstance(content, list):
            return [{"type": "text", "text": str(content)}]

        parts: list[dict[str, Any]] = []
        supported = {"text", "reasoning", "tool-call", "prompt"}
        durable_urls = iter(durable_image_urls or [])
        for raw_part in content:
            if not isinstance(raw_part, dict):
                parts.append({"type": "text", "text": str(raw_part)})
                continue
            part_type = raw_part.get("type")
            if part_type in supported:
                parts.append(copy.deepcopy(raw_part))
                continue
            if part_type == "image":
                source = raw_part.get("source")
                url = raw_part.get("url")
                if isinstance(source, dict):
                    if source.get("kind") == "url":
                        url = source.get("url")
                    elif source.get("kind") == "managed_upload":
                        try:
                            url = next(durable_urls)
                        except StopIteration:
                            media_id = source.get("media_id")
                            if isinstance(media_id, str):
                                if callable(persist_managed_image):
                                    url = persist_managed_image(media_id)
                                if url is None and callable(resolve_image):
                                    url = resolve_image(media_id)
                if not isinstance(url, str):
                    url = raw_part.get("image")
                if isinstance(url, str) and url and not url.lower().startswith("data:"):
                    parts.append({"type": "image", "url": url})
                continue
            if part_type == "image_url":
                image_url = raw_part.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if isinstance(url, str) and url and not url.lower().startswith("data:"):
                    parts.append({"type": "image", "url": url})
                continue
            if part_type in {"tool_use", "tool_call"}:
                tool_id = raw_part.get("id") or raw_part.get("tool_call_id")
                tool_name = raw_part.get("name") or raw_part.get("tool_name")
                if isinstance(tool_id, str) and isinstance(tool_name, str):
                    parts.append(
                        {
                            "type": "tool-call",
                            "tool_call_id": tool_id,
                            "tool_name": tool_name,
                            "args": raw_part.get("input", raw_part.get("args")),
                        }
                    )
                continue
            text = raw_part.get("text")
            if isinstance(text, str):
                parts.append({"type": "text", "text": text})
        return parts

    @staticmethod
    def _handoff_image_urls(handoff: Any) -> list[str]:
        if not isinstance(handoff, dict):
            return []
        values = handoff.get("media_refs")
        if not isinstance(values, list):
            return []
        return [
            f"/media/{quote(entry['ref'], safe='')}"
            for entry in values
            if isinstance(entry, dict)
            and isinstance(entry.get("source_ref"), str)
            and entry["source_ref"]
            and isinstance(entry.get("ref"), str)
            and entry["ref"]
        ]

    def _set_input_state(
        self,
        uid: str,
        input_id: str,
        state: str,
        *,
        turn_id: str | None,
        outcome: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"id": input_id, "state": state, "turn_id": turn_id}
        if outcome is not None:
            data["outcome"] = outcome
        self._inputs.setdefault(uid, {})[input_id] = data
        key = (uid, input_id)
        admitted = self._admission_inputs.setdefault(uid, {})
        prior = admitted.get(input_id, {})
        native_plugin = (
            prior.get("_native_plugin") is True or key in self._native_plugin_inputs
        )
        admission_data = copy.deepcopy(data)
        if native_plugin:
            admission_data["_native_plugin"] = True
            if state == "queued":
                self._native_plugin_inputs.add(key)
            elif state in {"started", "joined", "ended"}:
                self._native_plugin_inputs.discard(key)
        admitted[input_id] = admission_data
        self._log(uid).publish("input.state", data)

    def _set_turn_state(
        self,
        uid: str,
        turn_id: str,
        state: str,
        *,
        input_id: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> None:
        with self._log(uid)._lock:
            self._set_turn_state_locked(
                uid, turn_id, state, input_id=input_id, origin=origin
            )

    def _set_turn_state_locked(
        self,
        uid: str,
        turn_id: str,
        state: str,
        *,
        input_id: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> None:
        previous = self._turns.setdefault(uid, {}).get(turn_id, {})
        previous_origin = previous.get("origin")
        if origin is None:
            origin = previous_origin
        elif previous_origin is not None and previous_origin != origin:
            raise ValueError("turn origin is immutable")
        input_ids = list(
            dict.fromkeys(
                (
                    *previous.get("input_ids", []),
                    *self._turn_inputs.get((uid, turn_id), []),
                )
            )
        )
        if input_id and input_id not in input_ids:
            input_ids.append(input_id)
        data = {
            "turn_id": turn_id,
            "state": state,
            "input_ids": input_ids,
        }
        if origin is not None:
            data["origin"] = copy.deepcopy(origin)
        log = self._log(uid)
        problems = event_problems(
            {
                "seq": 1,
                "conversation_uid": uid,
                "generation": log.generation,
                "type": "turn.state",
                "data": data,
            }
        )
        if problems:
            raise ValueError("; ".join(problems))
        self._turns[uid][turn_id] = copy.deepcopy(data)
        log.publish("turn.state", data)

    def _upsert(self, uid: str, row: dict[str, Any]) -> None:
        def store_row(event: Event) -> None:
            self._rows.setdefault(uid, {})[event["data"]["id"]] = copy.deepcopy(
                event["data"]
            )

        self._log(uid).publish("row.upsert", row, on_commit=store_row)

    def _publish_live(self, event: Event) -> None:
        get_service = getattr(self._event_bus, "get_service", None)
        tap = get_service("display_tap") if callable(get_service) else None
        publish = getattr(tap, "publish", None)
        if callable(publish):
            publish({"type": "chat_stream", "event": event})

    def _queue_runtime(self) -> Any:
        get_service = getattr(self._event_bus, "get_service", None)
        runtime = (
            get_service("web_message_queue_runtime") if callable(get_service) else None
        )
        if runtime is None:
            raise RuntimeError("daemon input queue is unavailable")
        return runtime

    def _llm_service(self) -> Any:
        get_service = getattr(self._event_bus, "get_service", None)
        registered = get_service("llm_service") if callable(get_service) else None
        return registered or getattr(self._state_service, "_llm_service", None)

    def _conversation_uid(self) -> str:
        getter = getattr(self._state_service, "get_conversation_uid", None)
        uid = getter() if callable(getter) else None
        if not isinstance(uid, str) or not uid:
            raise RuntimeError("conversation identity is unavailable")
        return uid

    @staticmethod
    def _validate_input(
        params: Any,
    ) -> tuple[str, str, list[dict[str, Any]], str | None]:
        if not isinstance(params, dict):
            raise ValueError("input must be an object")
        input_id, text, attachments = (
            params.get("id"),
            params.get("text"),
            params.get("attachments", []),
        )
        goal_reply_id = params.get("goal_reply_id")
        if not isinstance(input_id, str) or not input_id or len(input_id) > 256:
            raise ValueError("input id is required")
        if not isinstance(text, str):
            raise ValueError("input text must be a string")
        if not isinstance(attachments, list) or len(attachments) > 20:
            raise ValueError("input attachments must be a bounded list")
        copied = []
        for attachment in attachments:
            if not isinstance(attachment, dict) or any(
                not isinstance(attachment.get(key), str) or not attachment[key]
                for key in ("name", "content_type")
            ):
                raise ValueError("invalid input attachment")
            data_url = attachment.get("data_url")
            source_ref = attachment.get("source_ref")
            if (isinstance(data_url, str) and data_url) == (
                isinstance(source_ref, str) and bool(source_ref)
            ):
                raise ValueError("invalid input attachment")
            copied.append({"name": attachment["name"], "content_type": attachment["content_type"]})
            if isinstance(data_url, str) and data_url:
                copied[-1]["data_url"] = data_url
            else:
                copied[-1]["source_ref"] = source_ref
        if not text.strip() and not copied:
            raise ValueError("input text or an attachment is required")
        from kollabor.state.goal_replies import GOAL_REPLY_PATTERN, is_goal_input

        if is_goal_input(text):
            if (
                not isinstance(goal_reply_id, str)
                or GOAL_REPLY_PATTERN.fullmatch(goal_reply_id) is None
            ):
                raise ValueError("goal input needs a private reply capability")
        elif goal_reply_id is not None:
            raise ValueError("private goal reply capability is only valid for goal input")
        return input_id, text, copied, goal_reply_id

    @staticmethod
    def _input_parts(
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        *,
        conversation_uid: str | None = None,
        input_id: str | None = None,
    ) -> list[dict[str, str]]:
        parts = [{"type": "text", "text": text}] if text else []
        for attachment in attachments or []:
            source_ref = attachment.get("source_ref")
            if (
                isinstance(source_ref, str)
                and source_ref
                and isinstance(conversation_uid, str)
                and conversation_uid
                and isinstance(input_id, str)
                and input_id
            ):
                image_ref = urlencode(
                    {
                        "conversation_uid": conversation_uid,
                        "request_id": input_id,
                        "ref": source_ref,
                    }
                )
                parts.append(
                    {"type": "image", "url": f"kollab-queue-image:?{image_ref}"}
                )
        return parts

    @staticmethod
    def _timestamp() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def register_chat_stream_handlers(
    rpc_server: Any, stream_service: ChatStreamService
) -> None:
    """Register the daemon-side actions used by the engine routes."""
    handlers = {
        "state.chat_stream.subscribe": stream_service.subscribe,
        "state.chat_stream.add_input": stream_service.add_input,
        "state.chat_stream.cancel_queued_input": stream_service.cancel_queued_input,
        "state.chat_stream.send_now": stream_service.send_now,
    }
    for method, handler in handlers.items():
        try:
            rpc_server.register(method, handler)
        except ValueError:
            logger.debug("chat stream rpc handler %s already registered", method)
    try:
        register_chat_stream_rows_handler(rpc_server, stream_service.row_pager())
    except ValueError:
        logger.debug(
            "chat stream rpc handler state.chat_stream.rows already registered"
        )
