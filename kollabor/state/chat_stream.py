"""Per-conversation event log for the web chat stream (#1451)."""

from __future__ import annotations

import copy
import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from kollabor_events.chat_stream import (
    EVENT_TYPES,
    REPLAY_WINDOW,
    Event,
    event_problems,
)
from kollabor_events.chat_stream_emit import emit_chat_event

logger = logging.getLogger(__name__)


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
        self._lock = threading.RLock()

    @property
    def next_seq(self) -> int:
        with self._lock:
            return self._last_seq + 1

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
            "seq": self._last_seq,
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
        self._events.append(event)

        if event["type"] == "turn.state":
            data = event["data"]
            if data["state"] == "running":
                self._current_turn_id = data["turn_id"]
                self._current_turn_events = {event["seq"]: event}
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
