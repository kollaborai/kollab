"""WidgetState: typed container for widget-facing daemon state.

Widgets read daemon state from ``ctx.remote_state``, historically a plain
dict. WidgetState keeps that flat dict contract, adds freshness metadata,
and gives every writer one safe merge path.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class WidgetState:
    """Typed snapshot of all state widgets can read."""

    # --- Freshness metadata ---
    _source: str = ""
    _updated_at: float = 0.0
    _stale: bool = False
    _degraded: bool = False
    _present_fields: set[str] = field(default_factory=set, repr=False, compare=False)

    # --- Session stats ---
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    session: str = ""

    # --- Processing ---
    is_processing: bool = False
    bg_tasks: int = 0
    pending_tools: int = 0

    # --- System ---
    tmux_sessions: int = 0
    cwd: str = ""
    git_branch: str = ""
    daemon_pid: int = 0
    daemon_uptime: float = 0.0
    runtime_mode: str = ""

    # --- Hub ---
    hub_identity: str = ""
    hub_is_coordinator: bool = False
    hub_peers: int = 0

    # --- MCP ---
    mcp: dict[str, Any] = field(default_factory=dict)

    # --- Profile ---
    profile_name: str = ""
    model: str = ""
    provider: str = ""
    endpoint: str = ""

    # --- Permissions ---
    approval_mode: str = "DEFAULT"

    # --- Agent / Skills ---
    agent: str = ""
    skills: str = ""

    @classmethod
    def state_fields(cls) -> set[str]:
        """Return public widget field names, excluding metadata."""
        return {f.name for f in fields(cls) if not f.name.startswith("_")}

    def to_dict(self) -> dict[str, Any]:
        """Convert to a flat dict for ctx.remote_state assignment."""
        data = asdict(self)
        data.pop("_present_fields", None)
        return data

    @classmethod
    def from_flat_dict(
        cls,
        d: dict[str, Any],
        *,
        source: str = "",
        stale: bool = False,
        degraded: bool = False,
        updated_at: float | None = None,
    ) -> "WidgetState":
        """Construct from a legacy flat dict.

        Unknown keys are ignored. The DisplayTap ``type`` key is deliberately
        stripped so event metadata never leaks into widget state.
        """
        raw = dict(d or {})
        raw.pop("type", None)

        known = cls.state_fields()
        filtered = {k: v for k, v in raw.items() if k in known}

        state = cls(**filtered)
        state._source = source or str(raw.get("_source") or "")
        state._updated_at = (
            float(updated_at)
            if updated_at is not None
            else float(raw.get("_updated_at") or time.monotonic())
        )
        state._stale = bool(raw.get("_stale", stale))
        state._degraded = bool(raw.get("_degraded", degraded))
        state._present_fields = set(filtered)
        return state

    def update_from(
        self,
        other: "WidgetState",
        *,
        source: str = "",
    ) -> "WidgetState":
        """Return a merged WidgetState without mutating self.

        If ``other`` came from from_flat_dict(), only fields present in that
        payload are merged. That preserves refresher-owned fields when the
        legacy DisplayTap path sends a partial state_snapshot. For manually
        constructed states, non-default fields are treated as present.
        """
        merged = WidgetState.from_flat_dict(self.to_dict(), source=self._source)
        present = set(other._present_fields)
        if not present:
            default = WidgetState()
            for name in self.state_fields():
                if getattr(other, name) != getattr(default, name):
                    present.add(name)

        for name in present:
            setattr(merged, name, getattr(other, name))

        merged._source = source or other._source or self._source
        merged._updated_at = time.monotonic()
        merged._stale = other._stale
        merged._degraded = other._degraded
        merged._present_fields = set(self._present_fields) | present
        return merged

    def merge_flat_dict(
        self,
        d: dict[str, Any],
        *,
        source: str = "",
    ) -> "WidgetState":
        """Merge a flat dict into this state."""
        other = WidgetState.from_flat_dict(d, source=source)
        return self.update_from(other, source=source)
