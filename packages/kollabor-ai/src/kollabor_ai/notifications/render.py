"""Render drained env events into the ``[env: N events]`` block."""

from __future__ import annotations

from typing import List

from .models import EnvEvent


def render_env_block(events: List[EnvEvent]) -> str:
    """Format a flat list of env events.

    Empty input renders as an empty string so callers can skip
    prepending without a separate check.
    """
    if not events:
        return ""
    plural = "s" if len(events) != 1 else ""
    lines = [f"[env: {len(events)} event{plural}]"]
    for e in events:
        suffix = f" x{e.count}" if e.count > 1 else ""
        lines.append(f"  {e.symbol} {e.message}{suffix}")
    return "\n".join(lines)
