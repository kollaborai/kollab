"""Compact agent-facing HUD diffs for non-human context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


AGENT_HUD_OPEN = "<agent_hud>"
AGENT_HUD_CLOSE = "</agent_hud>"


@dataclass(frozen=True)
class AgentHudEntry:
    """One changed HUD item waiting for the next model-visible turn."""

    section: str
    label: str
    content: str


def normalize_hud_label(value: str, fallback: str = "info") -> str:
    """Return a compact label safe to put inside a HUD block header."""
    cleaned = []
    for char in (value or fallback).strip():
        if char.isalnum() or char in {"_", "-", ".", ":", ">", "/"}:
            cleaned.append(char)
        elif cleaned and cleaned[-1] != "_":
            cleaned.append("_")
    label = "".join(cleaned).strip("_")
    return label or fallback


def _strip_legacy_wrappers(content: str) -> str:
    """Normalize old wrapper content into HUD body text."""
    body = (content or "").strip()
    legacy_pairs = (
        ("<sys_msg>", "</sys_msg>"),
        ("<system_messages>", "</system_messages>"),
    )
    for start, end in legacy_pairs:
        if body.startswith(start) and body.rstrip().endswith(end):
            return body.removeprefix(start).removesuffix(end).strip()
    return body


def _format_body(content: str) -> str:
    body = _strip_legacy_wrappers(content)
    if not body:
        return "+"
    lines = body.splitlines()
    formatted = [f"+ {lines[0]}"]
    formatted.extend(f"  {line}" for line in lines[1:])
    return "\n".join(formatted)


def format_agent_hud(entries: Iterable[AgentHudEntry]) -> str:
    """Format changed HUD entries as one compact model-facing block."""
    blocks: list[str] = []
    for entry in entries:
        section = normalize_hud_label(entry.section, fallback="state")
        label = normalize_hud_label(entry.label, fallback="info")
        blocks.append(f"[{section}:{label}]\n{_format_body(entry.content)}")

    if not blocks:
        return ""
    return f"{AGENT_HUD_OPEN}\n" + "\n\n".join(blocks) + f"\n{AGENT_HUD_CLOSE}"


def merge_agent_hud_with_user_message(
    entries: Iterable[AgentHudEntry],
    user_message: str,
) -> str:
    """Prepend pending HUD diffs to a real user/hub turn."""
    hud = format_agent_hud(entries)
    body = (user_message or "").strip()
    if not hud:
        return body
    if not body:
        return hud
    return f"{hud}\n\n{body}"
