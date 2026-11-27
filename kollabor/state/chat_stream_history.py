"""Indexes used while projecting persisted chat history into stream rows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def turn_history_key_index(
    messages: list[Any], turn_id_for: Callable[[dict[str, Any]], str | None]
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Index ACP turn keys once, by turn and by request when available.

    Messages are visited newest first, matching the previous reverse history
    lookup. Missing keys need no special sentinel: lookups in the completed
    dictionaries make both hits and misses constant time.
    """
    by_turn: dict[str, str] = {}
    by_request: dict[tuple[str, str], str] = {}

    for message in reversed(messages):
        metadata = (
            message.get("metadata")
            if isinstance(message, dict)
            else getattr(message, "metadata", None)
        )
        if not isinstance(metadata, dict):
            continue
        history_key = metadata.get("acp_turn_key")
        if not isinstance(history_key, str) or not history_key:
            continue
        turn_id = turn_id_for(metadata)
        if turn_id is None:
            continue

        by_turn.setdefault(turn_id, history_key)
        request_id = metadata.get("request_id")
        if isinstance(request_id, str):
            by_request.setdefault((turn_id, request_id), history_key)

    return by_turn, by_request
