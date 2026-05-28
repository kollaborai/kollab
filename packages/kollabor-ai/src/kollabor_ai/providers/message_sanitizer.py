"""Helpers for keeping local message metadata out of provider payloads."""

from __future__ import annotations

from typing import Any, Dict, List

LOCAL_ONLY_MESSAGE_KEYS = frozenset(
    {
        "agent_hud",
        "agent_hud_sources",
    }
)


def strip_local_message_metadata(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [strip_local_message_metadata_from_message(msg) for msg in messages]


def strip_local_message_metadata_from_message(
    message: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        key: value
        for key, value in message.items()
        if key not in LOCAL_ONLY_MESSAGE_KEYS
    }
