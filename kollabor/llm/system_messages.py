"""Formatting helpers for internal system/context injections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


SYS_MSG_OPEN = "<sys_msg>"
SYS_MSG_CLOSE = "</sys_msg>"
SYSTEM_MESSAGES_OPEN = "<system_messages>"
SYSTEM_MESSAGES_CLOSE = "</system_messages>"


@dataclass(frozen=True)
class PendingSystemMessage:
    """One queued internal message waiting to ride with the next user turn."""

    subtype: str
    content: str


def normalize_system_subtype(subtype: str) -> str:
    """Return a compact machine label for injected system messages."""
    cleaned = []
    for char in (subtype or "injection").strip():
        if char.isalnum() or char in {"_", "-", ".", ":"}:
            cleaned.append(char)
        elif cleaned and cleaned[-1] != "_":
            cleaned.append("_")
    value = "".join(cleaned).strip("_")
    return value or "injection"


def format_system_message(content: str, subtype: str = "injection") -> str:
    """Wrap internal context in one recognizable model-facing format.

    Kollab may still send these as role="user" for provider compatibility,
    so the content itself needs to make the internal/system intent obvious.
    """
    body = (content or "").strip()
    if body.startswith(SYS_MSG_OPEN) and body.rstrip().endswith(SYS_MSG_CLOSE):
        return body

    label = normalize_system_subtype(subtype)
    if body:
        return f"{SYS_MSG_OPEN}\n[system:{label}]\n{body}\n{SYS_MSG_CLOSE}"
    return f"{SYS_MSG_OPEN}\n[system:{label}]\n{SYS_MSG_CLOSE}"


def format_system_message_batch(
    messages: Iterable[PendingSystemMessage],
) -> str:
    """Format queued system messages compactly for a combined user turn."""
    blocks: list[str] = []
    for message in messages:
        label = normalize_system_subtype(message.subtype)
        body = (message.content or "").strip()
        if body.startswith(SYS_MSG_OPEN) and body.rstrip().endswith(SYS_MSG_CLOSE):
            body = (
                body.removeprefix(SYS_MSG_OPEN)
                .removesuffix(SYS_MSG_CLOSE)
                .strip()
            )
        if body:
            blocks.append(f"[{label}]\n{body}")
        else:
            blocks.append(f"[{label}]")

    if not blocks:
        return ""
    return (
        f"{SYSTEM_MESSAGES_OPEN}\n"
        + "\n\n".join(blocks)
        + f"\n{SYSTEM_MESSAGES_CLOSE}"
    )


def merge_system_messages_with_user_message(
    messages: Iterable[PendingSystemMessage],
    user_message: str,
) -> str:
    """Prepend queued system context to a real user/hub message."""
    system_block = format_system_message_batch(messages)
    body = (user_message or "").strip()
    if not system_block:
        return body
    if not body:
        return system_block
    return f"{system_block}\n\n{body}"
