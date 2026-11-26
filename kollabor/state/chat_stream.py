"""Per-conversation event log for the web chat stream (#1451)."""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from kollabor_events.chat_stream import (
    EVENT_TYPES,
    REPLAY_WINDOW,
    Event,
    event_problems,
)
from kollabor_events.chat_stream_emit import emit_chat_event

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
    return {key: copy.deepcopy(value) for key, value in row.items() if key != "_history_index"}


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

    def publish(self, event_type: str, data: dict[str, Any]) -> Event:
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
                    event_type=event_type,
                    data=payload,
                )
            else:
                event = {
                    "seq": seq,
                    "conversation_uid": self.conversation_uid,
                    "type": event_type,
                    "data": payload,
                }
                problems = event_problems(event)
                if problems:
                    raise ValueError("; ".join(problems))

            self._record_locked(event)
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
    ) -> list[Event]:
        """Return missed events, or one fresh snapshot when replay is unsafe."""
        with self._lock:
            if (
                after is None
                or isinstance(after, bool)
                or not isinstance(after, int)
                or after < 0
                or after > self._last_seq
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
        self._inputs: dict[str, dict[str, dict[str, Any]]] = {}
        self._turns: dict[str, dict[str, dict[str, Any]]] = {}
        self._input_scopes: dict[tuple[str, str], tuple[str, str, str]] = {}
        self._input_text: dict[tuple[str, str], str] = {}
        self._input_attachments: dict[tuple[str, str], list[dict[str, str]]] = {}
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
        return {
            "events": self._log().read(
                after=after,
                conversation_uid=conversation_uid,
            )
        }

    async def add_input(self, params: dict[str, Any]) -> dict[str, Any]:
        async with self._input_lock:
            return await self._add_input(params)

    async def _add_input(self, params: dict[str, Any]) -> dict[str, Any]:
        self.attach_display_tap()
        input_id, text, attachments = self._validate_input(params)
        uid = self._conversation_uid()
        runtime = self._queue_runtime()
        scope = await runtime.input_scope(uid)
        known = self._inputs.setdefault(uid, {}).get(input_id)
        owner = self._input_scopes.get((uid, input_id))
        if owner != scope and (owner is not None or known is not None):
            return {"error": "input was not admitted"}
        if known is not None:
            previous_text = self._input_text.get((uid, input_id), text)
            previous_attachments = self._input_attachments.get(
                (uid, input_id), attachments
            )
            if previous_text != text or previous_attachments != attachments:
                return {"error": "input id is already bound to different content"}
            return {"id": input_id, "state": known["state"]}

        result = await runtime.add_input(
            {"id": input_id, "text": text, "attachments": attachments}
        )
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

        self._input_scopes[(uid, input_id)] = scope
        self._input_text[(uid, input_id)] = text
        self._input_attachments[(uid, input_id)] = copy.deepcopy(attachments)
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
            "parts": self._input_parts(text, attachments),
            "created_at": self._timestamp(),
        }
        self._upsert(uid, row)
        return {"id": input_id, "state": state}

    async def cancel_queued_input(self, params: dict[str, Any]) -> dict[str, Any]:
        input_id = params.get("id")
        if not isinstance(input_id, str) or not input_id:
            return {"found": False, "cancelled": False}
        uid = self._conversation_uid()
        existing = self._inputs.setdefault(uid, {}).get(input_id)
        if existing is not None and existing.get("state") != "queued":
            return {"found": True, "cancelled": False}
        result = await self._queue_runtime().cancel_queued_input({"id": input_id})
        if not isinstance(result, dict) or result.get("found") is not True:
            return {"found": False, "cancelled": False}
        cancelled = result.get("cancelled") is True
        existing = self._inputs.setdefault(uid, {}).get(input_id)
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
        return {"found": True, "cancelled": cancelled}

    async def admit_queued_batch(self, inputs, scope, enqueue):
        """Publish queued rows and lifecycle after an atomic queue admission."""
        normalized = []
        for item in inputs:
            if not isinstance(item, dict):
                raise ValueError("queued input must be an object")
            input_id, text, attachments = self._validate_input(
                {
                    "id": item.get("id"),
                    "text": item.get("text"),
                    "attachments": item.get("attachments", []),
                }
            )
            normalized.append((input_id, text, attachments))
        if not isinstance(scope, tuple) or len(scope) != 3:
            raise ValueError("queued input scope is invalid")

        async with self._input_lock:
            keys = [(scope[1], input_id) for input_id, _, _ in normalized]
            if any(
                self._input_scopes.get(key, scope) != scope
                or (
                    key[1] in self._inputs.get(key[0], {})
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
                prior = self._inputs.setdefault(uid, {}).get(input_id)
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
                            "parts": self._input_parts(text, attachments),
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
            input_id in self._inputs.get(uid, {})
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
                    key[1] in self._inputs.get(key[0], {})
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
        runtime = self._queue_runtime()
        scope = await runtime.input_scope(uid)
        current = self._inputs.get(uid, {}).get(input_id, {})
        if current.get("state") in {"started", "joined", "ended"} and current.get(
            "turn_id"
        ):
            if self._input_scopes.get((uid, input_id)) != scope:
                return {"found": False}
            return {
                "found": True,
                "accepted": False,
                "state": current["state"],
                "turn_id": current["turn_id"],
            }
        result = await runtime.send_now({"id": input_id})
        if await runtime.input_scope(uid) != scope:
            return {"found": False}
        current = self._inputs.get(uid, {}).get(input_id, {})
        owner = self._input_scopes.get((uid, input_id))
        if owner != scope:
            return {"found": False}
        if (
            (not isinstance(result, dict) or result.get("found") is not True)
            and current.get("state") in {"started", "joined", "ended"}
            and current.get("turn_id")
        ):
            return {
                "found": True,
                "accepted": False,
                "state": current["state"],
                "turn_id": current["turn_id"],
            }
        if not isinstance(result, dict) or result.get("found") is not True:
            return {"found": False}
        state = result.get("state")
        turn_id = result.get("turn_id")
        if state in {"started", "joined"} and isinstance(turn_id, str):
            uid = self._conversation_uid()
            self._set_input_state(uid, input_id, state, turn_id=turn_id)
            self._set_turn_state(uid, turn_id, "running", input_id=input_id)
        current = self._inputs.get(uid, {}).get(input_id, {})
        return {
            "found": True,
            "accepted": result.get("accepted") is True,
            "state": current.get("state", state or "queued"),
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
                self._inputs.setdefault(uid, {})[input_id] = copy.deepcopy(data)
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
        input_id = event.get("request_id")
        if not isinstance(input_id, str) or not input_id:
            input_id = self._transcript_input_id(uid, text)
        if input_id is None:
            input_id = f"daemon-{uuid.uuid4().hex}"
        turn_id = self._event_turn_id(event) or self._input_turns.get((uid, input_id))
        if turn_id is None:
            turn_id = self._active_turns.get(uid)
        if turn_id is None:
            turn_id = f"turn-{uuid.uuid4().hex}"
        self._ensure_input_row(uid, input_id, text=text, turn_id=turn_id)
        prior = self._inputs.setdefault(uid, {}).get(input_id, {})
        if prior.get("state") == "ended":
            return
        current = self._turns.setdefault(uid, {}).get(turn_id, {})
        joined = current.get(
            "state"
        ) == "running" and input_id not in self._turn_inputs.get((uid, turn_id), [])
        state = "joined" if joined else "started"
        if prior.get("state") != state or prior.get("turn_id") != turn_id:
            self._set_input_state(uid, input_id, state, turn_id=turn_id)
        self._input_turns[(uid, input_id)] = turn_id
        self._add_turn_input(uid, turn_id, input_id)
        row = self._rows.setdefault(uid, {}).get(input_id)
        parts = self._input_parts(text)
        if row is not None:
            parts.extend(
                copy.deepcopy(part)
                for part in row.get("parts", [])
                if isinstance(part, dict) and part.get("type") == "image"
            )
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
            turn_id = self._input_turns.get((uid, input_ids[0]))
        if turn_id is None:
            turn_id = self._active_turns.get(uid)
        if turn_id is None:
            return
        key = (uid, turn_id)
        input_ids = list(dict.fromkeys((*self._turn_inputs.get(key, []), *input_ids)))
        if not input_ids and turn_id not in self._turns.get(uid, {}):
            return
        stop_reason = str(event.get("stop_reason") or "")
        if stop_reason in {"cancelled", "canceled", "interrupted", "stop"}:
            outcome = "cancelled"
        elif (
            stop_reason in {"error", "failed", "exception"} or key in self._turn_errors
        ):
            outcome = "failed"
        else:
            outcome = "completed"
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

    def _on_turn_error(self, uid: str, event: dict[str, Any]) -> None:
        message = event.get("message")
        turn_id = self._event_turn_id(event) or self._active_turns.get(uid)
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
        name = name if isinstance(name, str) and name else "Tool"
        metadata = event.get("metadata")
        acp_data = metadata.get("acp") if isinstance(metadata, dict) else None
        acp_data = acp_data if isinstance(acp_data, dict) else {}
        raw_input = acp_data.get("rawInput")
        args = (
            raw_input
            if raw_input is not None
            else event.get("input", {})
        )
        part: dict[str, Any] = {
            "type": "tool-call",
            "tool_call_id": tool_id,
            "tool_name": name,
            "args": args,
        }
        if event.get("type") == "tool_start":
            status = "running"
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
        if isinstance(history, list):
            compaction_stamps = []
            has_unparseable_compaction = False
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
                stamp = _parse_chat_timestamp(timestamp)
                if stamp is None:
                    has_unparseable_compaction = True
                    break
                compaction_stamps.append(stamp)
            if compaction_stamps and not has_unparseable_compaction:
                boundary = max(compaction_stamps)
                manager_rows = getattr(manager, "chat_stream_rows", None)
                if isinstance(manager_rows, list):
                    retained_rows = []
                    for row in manager_rows:
                        if not isinstance(row, dict):
                            continue
                        row_stamp = _parse_chat_timestamp(row.get("created_at"))
                        if row_stamp is not None and row_stamp <= boundary:
                            continue
                        if row_stamp is not None:
                            preceding = []
                            for index, message in enumerate(history):
                                if isinstance(message, dict):
                                    timestamp = message.get("timestamp")
                                else:
                                    timestamp = getattr(message, "timestamp", None)
                                message_stamp = _parse_chat_timestamp(timestamp)
                                if message_stamp is not None and message_stamp <= row_stamp:
                                    preceding.append(index)
                            row["_history_index"] = max(preceding, default=0)
                        retained_rows.append(row)
                    manager_rows[:] = retained_rows
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
        ]
        rows = merge_chat_stream_rows(self._transcript_rows(uid), state_rows)
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
        marker = {
            "id": f"marker-{uuid.uuid4().hex}",
            "seq": self._log(uid).next_seq,
            "kind": "marker",
            "role": "system",
            "turn_id": active_turn,
            "status": "done",
            "parts": [{"type": "text", "text": "Earlier messages compacted"}],
            "created_at": self._timestamp(),
        }
        replacement_rows = [marker, *rows][-REPLAY_WINDOW:]
        event = self._log(uid).publish("replace", {"rows": replacement_rows})
        self._rows[uid] = {row["id"]: copy.deepcopy(row) for row in replacement_rows}
        return event

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

        def current_uid() -> str:
            return uid if uid is not None else self._conversation_uid()

        def current_compaction_seq() -> int:
            log = self._logs.get(current_uid())
            return log.last_replace_seq if log is not None else 0

        return ChatStreamRowPager(
            conversation_uid_provider=current_uid,
            snapshot_rows_provider=lambda: self._snapshot_rows(current_uid()),
            transcript_rows_provider=lambda: self._transcript_rows(current_uid()),
            row_log_provider=lambda: list(self._rows.get(current_uid(), {}).values()),
            compaction_seq_provider=current_compaction_seq,
        )

    def _snapshot_components(self, uid: str) -> tuple[
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
        rows_by_id = {
            row["id"]: row
            for row in merge_chat_stream_rows(
                self._transcript_rows(uid),
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
            turn_id = row.get("turn_id")
            reply = history_replies.get(turn_id)
            persisted_outcome = row.get("status")
            if persisted_outcome in {"completed", "failed", "cancelled"}:
                info = {
                    "id": input_id,
                    "state": "ended",
                    "turn_id": turn_id,
                    "outcome": persisted_outcome,
                }
            elif reply is not None:
                status = reply.get("status")
                outcome = (
                    "cancelled"
                    if status in {"cancelled", "canceled"}
                    else "failed"
                    if status in {"failed", "error"}
                    else "completed"
                )
                info = {
                    "id": input_id,
                    "state": "ended",
                    "turn_id": turn_id,
                    "outcome": outcome,
                }
            else:
                state_name = row.get("status")
                if state_name not in {"queued", "started", "joined"}:
                    state_name = "started" if turn_id else "queued"
                info = {
                    "id": input_id,
                    "state": state_name,
                    "turn_id": turn_id,
                }
            inputs[input_id] = info
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

    def _snapshot_rows(self, uid: str) -> list[dict[str, Any]]:
        return self._snapshot_components(uid)[0]

    def _snapshot(self, uid: str) -> dict[str, Any]:
        rows, turns, inputs, state = self._snapshot_components(uid)
        older = self.row_pager(uid=uid).snapshot_older_cursor()
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

        def field(message: Any, name: str, default: Any = None) -> Any:
            if isinstance(message, dict):
                return message.get(name, default)
            return getattr(message, name, default)

        rows = []
        for index, message in enumerate(messages):
            role = field(message, "role")
            if not isinstance(role, str):
                continue
            metadata = field(message, "metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            if metadata.get("hidden") is True:
                continue
            source_role = role
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
            if metadata.get("context_compaction") or metadata.get("compaction_summary"):
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
            content = field(message, "content", "")
            image_store = getattr(llm_service, "_image_store", None)
            resolve_image = getattr(image_store, "resolve", None)
            hub_note = self._hub_note_part(metadata.get("hub_note"))
            parts = (
                [hub_note]
                if hub_note is not None
                else self._transcript_parts(content, resolve_image)
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
                    and candidate_content == content
                ):
                    manager_uuid = field(candidate, "uuid")
            fallback_identity = f"{uid}:{source_role}:{timestamp}:{content!r}"
            history_key = metadata.get("acp_turn_key")
            if not isinstance(history_key, str) or not history_key:
                history_key = None
            request_id = metadata.get("request_id")
            if history_key is None and kind in {"reply", "tool"}:
                search_ids = [request_id] if isinstance(request_id, str) else None
                history_key = self._turn_history_key(
                    uid, turn_id, input_ids=search_ids
                )
            input_id = metadata.get("input_id")
            hub_message_id = metadata.get("hub_message_id")
            tool_id = metadata.get("tool_id")
            if kind == "input":
                row_id = next(
                    (
                        candidate
                        for candidate in (hub_message_id, request_id, input_id)
                        if isinstance(candidate, str) and candidate
                    ),
                    None,
                )
            elif kind == "reply":
                explicit_reply_id = metadata.get("reply_id")
                row_id = (
                    explicit_reply_id
                    if isinstance(explicit_reply_id, str) and explicit_reply_id
                    else (
                        self._reply_row_id(uid, turn_id, history_key)
                        if turn_id is not None
                        else None
                    )
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
            if kind == "input" and chat_stream_outcome in {
                "completed",
                "failed",
                "cancelled",
            }:
                row_status = chat_stream_outcome
            if kind == "reply" and stop_reason is not None and not row_status:
                row_status = (
                    "done"
                    if stop_reason == "end_turn"
                    else "cancelled"
                    if str(stop_reason).lower() in {"cancelled", "canceled"}
                    else "failed"
                )
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
            calls = metadata.get("tool_calls")
            if source_role != "assistant" or not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id") or call.get("tool_call_id")
                if isinstance(call_id, int) and not isinstance(call_id, bool):
                    call_id = str(call_id)
                if not isinstance(call_id, str) or not call_id:
                    continue
                name = call.get("name") or call.get("tool_name")
                if not isinstance(name, str) or not name:
                    name = "Tool"
                args = call.get("input")
                if args is None:
                    args = call.get("args", {})
                part: dict[str, Any] = {
                    "type": "tool-call",
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "args": copy.deepcopy(args),
                }
                result = call.get("result")
                if result is not None:
                    part["result"] = copy.deepcopy(result)
                is_error = call.get("is_error")
                if not isinstance(is_error, bool):
                    is_error = call.get("status") == "failed"
                part["is_error"] = is_error
                call_status = call.get("status")
                if call_status in {"running", "in_progress"}:
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
    ) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []
        if not isinstance(content, list):
            return [{"type": "text", "text": str(content)}]

        parts: list[dict[str, Any]] = []
        supported = {"text", "reasoning", "tool-call", "prompt"}
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
                        media_id = source.get("media_id")
                        if isinstance(media_id, str) and callable(resolve_image):
                            url = resolve_image(media_id)
                if not isinstance(url, str):
                    url = raw_part.get("image")
                if isinstance(url, str) and url:
                    parts.append({"type": "image", "url": url})
                continue
            if part_type == "image_url":
                image_url = raw_part.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if isinstance(url, str) and url:
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
        problems = event_problems(
            {"seq": 1, "conversation_uid": uid, "type": "turn.state", "data": data}
        )
        if problems:
            raise ValueError("; ".join(problems))
        self._turns[uid][turn_id] = copy.deepcopy(data)
        self._log(uid).publish("turn.state", data)

    def _upsert(self, uid: str, row: dict[str, Any]) -> None:
        event = self._log(uid).publish("row.upsert", row)
        self._rows.setdefault(uid, {})[row["id"]] = copy.deepcopy(event["data"])

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

    def _conversation_uid(self) -> str:
        getter = getattr(self._state_service, "get_conversation_uid", None)
        uid = getter() if callable(getter) else None
        if not isinstance(uid, str) or not uid:
            raise RuntimeError("conversation identity is unavailable")
        return uid

    @staticmethod
    def _validate_input(params: Any) -> tuple[str, str, list[dict[str, Any]]]:
        if not isinstance(params, dict):
            raise ValueError("input must be an object")
        input_id, text, attachments = (
            params.get("id"),
            params.get("text"),
            params.get("attachments", []),
        )
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
                for key in ("name", "content_type", "data_url")
            ):
                raise ValueError("invalid input attachment")
            copied.append(
                {key: attachment[key] for key in ("name", "content_type", "data_url")}
            )
        if not text.strip() and not copied:
            raise ValueError("input text or an attachment is required")
        return input_id, text, copied

    @staticmethod
    def _input_parts(
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        parts = [{"type": "text", "text": text}] if text else []
        for attachment in attachments or []:
            data_url = attachment.get("data_url")
            if isinstance(data_url, str) and data_url:
                parts.append({"type": "image", "url": data_url})
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
