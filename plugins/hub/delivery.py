"""Hub message delivery policy and trace helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SenderContext:
    """Inputs needed to decide whether a sender may route a hub message."""

    sender: str
    is_self: bool
    is_coordinator: bool
    is_remote: bool
    approval_state: str
    same_project: bool
    force: bool = False


@dataclass(frozen=True)
class DeliveryDecision:
    """Routing decision for a hub sender."""

    mode: str  # deliver, reject, quarantine
    reason: str
    wake_allowed: bool
    trace_level: str = "info"


class DeliveryPolicy:
    """Centralized hub sender policy.

    Local same-project agents default to delivery-with-warning when DNS
    freshness is missing. Remote unknown agents quarantine by default.
    """

    def __init__(self, *, strict_local_unknown: bool = False):
        self.strict_local_unknown = strict_local_unknown

    def decide_sender(self, context: SenderContext) -> DeliveryDecision:
        if context.is_coordinator:
            return DeliveryDecision("deliver", "coordinator sender", True)
        if context.is_self:
            return DeliveryDecision("deliver", "local self sender", True)
        if context.force:
            return DeliveryDecision("deliver", "force sender override", True, "warning")
        if context.approval_state == "rejected":
            return DeliveryDecision("reject", "sender rejected", False, "warning")
        if context.approval_state in {"approved", "auto_approved"}:
            return DeliveryDecision("deliver", "sender approved", True)
        if context.is_remote:
            return DeliveryDecision(
                "quarantine",
                "remote unknown sender",
                False,
                "warning",
            )
        if context.same_project and not self.strict_local_unknown:
            return DeliveryDecision(
                "deliver",
                "local unknown same project",
                True,
                "warning",
            )
        return DeliveryDecision(
            "reject",
            "local unknown sender in strict mode",
            False,
            "warning",
        )


class DeliveryTrace:
    """Append-only JSONL trace for hub delivery decisions."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        message_id: str,
        event: str,
        sender: str,
        target: str,
        detail: str,
    ) -> None:
        payload = {
            "ts": time.time(),
            "message_id": message_id,
            "event": event,
            "sender": sender,
            "target": target,
            "detail": detail,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def summary(self, *, recent_limit: int = 5) -> dict[str, Any]:
        """Return compact counts and capped recent decisions."""
        counts: dict[str, int] = {}
        recent: list[dict[str, Any]] = []
        total = 0

        if not self.path.exists():
            return {
                "path": str(self.path),
                "total": 0,
                "counts": counts,
                "recent": recent,
            }

        with open(self.path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue

                event = str(payload.get("event") or "unknown")
                counts[event] = counts.get(event, 0) + 1
                total += 1
                recent.append(payload)
                if len(recent) > recent_limit:
                    recent.pop(0)

        return {
            "path": str(self.path),
            "total": total,
            "counts": counts,
            "recent": recent,
        }
