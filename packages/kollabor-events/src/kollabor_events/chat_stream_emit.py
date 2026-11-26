from __future__ import annotations

from typing import Any, Callable

from .chat_stream import Event, event_problems

LIFECYCLE_TYPES = frozenset({"input.state", "turn.state"})


def emit_chat_event(
    sink: Callable[[Event], Any],
    *,
    seq: int,
    conversation_uid: str,
    generation: str,
    event_type: str,
    data: dict[str, Any],
) -> Event:
    # Build one lifecycle event, check it against the contract, then hand it to sink.
    if event_type not in LIFECYCLE_TYPES:
        raise ValueError(
            f"emit_chat_event only emits lifecycle events, not {event_type!r}"
        )
    event: Event = {
        "seq": seq,
        "conversation_uid": conversation_uid,
        "generation": generation,
        "type": event_type,
        "data": dict(data),
    }
    problems = event_problems(event)
    if problems:
        raise ValueError("; ".join(problems))
    sink(event)
    return event
