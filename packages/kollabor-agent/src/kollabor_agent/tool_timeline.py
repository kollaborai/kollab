"""Tool execution timeline event contract."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

TIMELINE_METADATA_KEY = "timeline"


@dataclass(frozen=True)
class ToolTimelineEvent:
    """Compact, replayable tool lifecycle event."""

    phase: str
    tool_id: str
    tool_type: str
    detail: str = ""
    success: bool | None = None
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "phase": self.phase,
            "tool_id": self.tool_id,
            "tool_type": self.tool_type,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }
        if self.success is not None:
            data["success"] = self.success
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data


class ToolTimeline:
    """Append-only in-memory timeline for one process/session."""

    def __init__(self) -> None:
        self._events: list[ToolTimelineEvent] = []

    def record(self, event: ToolTimelineEvent) -> None:
        self._events.append(event)

    def record_phase(
        self,
        phase: str,
        *,
        tool_id: str,
        tool_type: str,
        detail: str = "",
        success: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolTimelineEvent:
        event = ToolTimelineEvent(
            phase=phase,
            tool_id=tool_id,
            tool_type=tool_type,
            detail=detail,
            success=success,
            metadata=metadata or {},
        )
        self.record(event)
        return event

    def to_dicts(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self._events]


def timeline_event_dict(
    phase: str,
    *,
    tool_id: str,
    tool_type: str,
    detail: str = "",
    success: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a serializable timeline event without storing it globally."""
    return ToolTimelineEvent(
        phase=phase,
        tool_id=tool_id,
        tool_type=tool_type,
        detail=detail,
        success=success,
        metadata=metadata or {},
    ).to_dict()


def summarize_timeline_events(events: list[dict[str, Any]], limit: int = 8) -> str:
    """Render compact timeline lines for conversation/debug replay."""
    if not events:
        return ""

    visible = events[-limit:]
    lines: list[str] = []
    hidden_count = len(events) - len(visible)
    if hidden_count > 0:
        lines.append(f"... {hidden_count} earlier timeline events")

    for event in visible:
        phase = str(event.get("phase", "event")).replace("_", " ")
        detail = str(event.get("detail", "")).strip()
        if detail:
            lines.append(f"{phase}: {detail}")
        else:
            lines.append(phase)
    return "\n".join(lines)
