"""DisplayTap - pub/sub bus for live terminal attach.

Intercepts rendered display output and streams it to subscribers
(attach clients) over unix sockets. Maintains a ring buffer of
recent events so new attachers get instant context on connect.

Thread-safe: publish() is called from the main render thread,
subscribers are managed from asyncio tasks.
"""

import json
import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def resolve_tap(source: Any) -> Optional["DisplayTap"]:
    """Find the DisplayTap reachable from an event bus, a renderer, or a tap.

    Callers in the LLM and tool layers hold one of those three, never the tap
    directly. Returns None when there is no tap (normal for a plain TUI run).
    """
    if source is None or isinstance(source, DisplayTap):
        return source

    get_service = getattr(source, "get_service", None)
    if callable(get_service):
        try:
            return get_service("display_tap")
        except Exception:
            return None

    coordinator = getattr(source, "message_coordinator", None)
    return getattr(coordinator, "_display_tap", None)


# A failed turn's text as clients show it. A provider or harness can fail with
# its whole output (an HTML page, a log of many MB); past the engine's 16 MB
# frame limit the turn's completion was dropped and its chat waited (#543).
# Both ends stay: a harness log often names the cause on its last line, and
# the model fallback reads it there ("usage limit").
_ERROR_TEXT_LIMIT = 8192


def _cut_text(text: str, limit: int) -> str:
    """``text`` with its middle cut past ``limit`` characters; both ends stay."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]} … ({len(text):,} characters) … {text[-half:]}"


# The engine reads each daemon line up to kollabor_rpc.transport.LARGE_BUFFER_LIMIT
# (16 MiB) and drops a longer one, which left its chat incomplete or waiting (#544).
# An event over this budget has its long text cut, so the line the attach stream
# writes stays under that limit, with room for the fields publish adds after the check.
_FRAME_BUDGET_BYTES = 15 * 1024 * 1024
# The longest text one string keeps when cut. Cuts halve it until the event fits,
# and stop at the floor. An event that does not fit even at the floor is not cut.
_FRAME_TEXT_LIMIT = 1024 * 1024
_FRAME_TEXT_FLOOR = 1024


def _cut_strings(node: Any, limit: int) -> Any:
    """A copy of ``node`` with every string past ``limit`` characters cut."""
    if isinstance(node, str):
        return _cut_text(node, limit)
    if isinstance(node, dict):
        return {key: _cut_strings(value, limit) for key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return [_cut_strings(value, limit) for value in node]
    return node


def _within_frame_budget(event: Dict[str, Any]) -> Dict[str, Any]:
    """The event to publish: itself, or a copy with its text cut to fit the budget.

    The type, ids and seq are kept, and each cut says how much it removed, so a
    chat shows the start and end of a long output instead of losing the update.
    """
    try:
        if len(json.dumps(event, default=str)) <= _FRAME_BUDGET_BYTES:
            return event
    except (TypeError, ValueError):
        return event  # not serializable: published as before
    # Cut everything to the floor once. If even that does not fit, the event's
    # bulk is not text (a long list of short strings): cutting would lose its
    # text without rescuing the frame, so it goes out whole, as before.
    floor_cut = _cut_strings(event, _FRAME_TEXT_FLOOR)
    if len(json.dumps(floor_cut, default=str)) > _FRAME_BUDGET_BYTES:
        return event
    limit = _FRAME_TEXT_LIMIT
    while limit > _FRAME_TEXT_FLOOR:
        cut = _cut_strings(event, limit)
        if len(json.dumps(cut, default=str)) <= _FRAME_BUDGET_BYTES:
            return cut
        limit //= 2
    return floor_cut


def publish_semantic(source: Any, event_type: str, **fields: Any) -> None:
    """Publish a machine-readable turn event alongside the rendered output.

    ``{"type": "output", "rendered": ...}`` events carry ANSI for terminal
    attach clients. These carry the same turn as structured data so non-terminal
    clients (kollabor-engine, the web UI) can follow a turn without parsing
    escape codes. Event names and field names match ``kollabor_engine.sse`` so
    there is one vocabulary rather than two that drift.

    Never raises - a broken tap must not take down a turn.
    """
    tap = resolve_tap(source)
    if tap is None:
        return
    if event_type in ("turn_complete", "error"):
        for key in ("error", "message"):
            text = fields.get(key)
            if isinstance(text, str):
                fields[key] = _cut_text(text, _ERROR_TEXT_LIMIT)
    try:
        tap.publish({"type": event_type, **fields})
    except Exception:
        logger.debug("semantic event publish failed: %s", event_type, exc_info=True)


class DisplayTap:
    """Pub/sub tap for display events.

    The message coordinator and terminal renderer publish events here.
    Attach clients subscribe and receive a live stream of everything
    that gets displayed.

    Thread-safe: publish() is called from the render thread,
    subscribers consume from asyncio tasks via queue.Queue (stdlib).
    """

    def __init__(
        self,
        history_size: int = 200,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self._history: deque = deque(maxlen=history_size)
        self._subscribers: Dict[str, queue.Queue] = {}
        self._next_seq = 1
        self._lock = threading.Lock()
        # The owner's own view of every event. Not a subscriber: the count of
        # those says whether a window is attached.
        self._on_event = on_event
        self._listeners: tuple = ()
        # Names the live conversation (its durable uid). Every event is stamped
        # with it, and replay and live delivery drop the other conversations'
        # events, so a /resume or a new session never shows the old text (#525).
        self.conversation_source: Optional[Callable[[], Optional[str]]] = None

    def _live_conversation(self) -> Optional[str]:
        source = self.conversation_source
        if source is None:
            return None
        try:
            return source() or None
        except Exception:
            logger.debug("DisplayTap conversation_source failed", exc_info=True)
            return None

    def in_live_conversation(self, event: Dict[str, Any]) -> bool:
        """False for an event another conversation produced (or none is named).

        Without a ``conversation_source`` nothing is scoped. With one, an event
        with no conversation (published before identity existed) is not assumed
        to belong to the live one.
        """
        if self.conversation_source is None:
            return True
        return event.get("conversation_uid") == self._live_conversation()

    def publish(self, event: Dict[str, Any]) -> None:
        """Publish a display event to all subscribers.

        Thread-safe. Called from the render thread (not async).
        Uses stdlib queue.Queue which is safe across threads.
        """
        event = dict(event)
        from .turn_context import current_turn
        context = current_turn.get()
        completion = event.pop("_client_completion", None) or context.get("_client_completion")
        details = event.get("details")
        if isinstance(details, dict):
            for key in ("request_id", "turn_id", "input_source", "generation"):
                if details.get(key) is not None:
                    if event.get(key) is None:
                        event[key] = details[key]
        for key, value in context.items():
            if key != "_client_completion" and event.get(key) is None:
                event[key] = value
        event.setdefault("ts", time.time())
        if event.get("conversation_uid") is None:
            conversation = self._live_conversation()
            if conversation is not None:
                event["conversation_uid"] = conversation
        event = _within_frame_budget(event)
        with self._lock:
            if (event.get("type") == "turn_complete" and completion is not None
                    and event.get("request_id") == completion["request_id"]
                    and event.get("turn_id") == completion["turn_id"]):
                # One client turn may end through broker cancellation and later
                # through its native task. Fence both before any subscriber sees it.
                if completion["completed"]:
                    return
                completion["completed"] = True
                if completion["cancelled"]:
                    event["stop_reason"] = "cancelled"
            event["seq"] = self._next_seq
            self._next_seq += 1
            self._history.append(event)

            # Deliver under the same lock: each subscriber gets events in seq
            # order, and an attached engine drops what it has seen by seq (#543).
            dead = []
            for client_id, q in self._subscribers.items():
                try:
                    q.put_nowait(event)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except (queue.Empty, queue.Full):
                        pass
                except Exception:
                    dead.append(client_id)

            for client_id in dead:
                self._subscribers.pop(client_id, None)

        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                logger.debug("DisplayTap on_event failed", exc_info=True)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                logger.debug("DisplayTap listener failed", exc_info=True)

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Call ``callback`` with every published event, as ``on_event`` is.

        For in-process watchers (the model fallback); unlike a subscriber, a
        listener never counts as an attached window.
        """
        with self._lock:
            self._listeners = (*self._listeners, callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            # ==, not is: each read of a bound method makes a new object (#548).
            self._listeners = tuple(c for c in self._listeners if c != callback)

    def subscribe(self, client_id: str) -> queue.Queue:
        """Add a subscriber. Returns thread-safe queue to consume from."""
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers[client_id] = q
        logger.info(
            f"DisplayTap: subscriber added: {client_id} (total: {len(self._subscribers)})"
        )
        return q

    def unsubscribe(self, client_id: str) -> None:
        """Remove a subscriber."""
        with self._lock:
            self._subscribers.pop(client_id, None)
        logger.info(
            f"DisplayTap: subscriber removed: {client_id} (total: {len(self._subscribers)})"
        )

    def get_snapshot(self) -> List[Dict[str, Any]]:
        """Get the live conversation's recent history for catch-up on connect."""
        return [event for event in list(self._history) if self.in_live_conversation(event)]

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)
