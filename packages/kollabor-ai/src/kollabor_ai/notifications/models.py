"""Env event data model for the agent notification system."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Single source of truth for the symbol table. Producers import from
# here and must never hardcode the character — keeping one table lets
# us swap the glyph without touching every call site.
SYMBOLS = {
    "capability": "\u25b2",   # ▲  permissions, tool grant/revoke, mcp connect/disconnect
    "joined": "+",             # +  peer came online
    "changed": "-",            # -  peer went offline or changed state
    "file": "~",               # ~  file edited, compaction fired, context event
    "task": "\u2714",          # ✔  task assigned, completed, approved, rejected
    "action": "\u25c9",        # ◉  someone needs something from you
    "message": "\u2709",       # ✉  inbound comms
    "external": "\u26a1",      # ⚡  external system event
}


class EnvKind(str, Enum):
    """Machine-readable event kinds for filtering and querying.

    Producers set ``kind`` so downstream code can branch programmatically
    (e.g. ``if event.kind == EnvKind.MCP_DISCONNECT``). Strings underlying
    the enum match the spec so serialized payloads stay stable.
    """

    PERMISSION = "permission"
    TOOL_GRANT = "tool_grant"
    TOOL_REVOKE = "tool_revoke"
    MCP_CONNECT = "mcp_connect"
    MCP_DISCONNECT = "mcp_disconnect"
    PEER_ONLINE = "peer_online"
    PEER_OFFLINE = "peer_offline"
    PEER_STATE = "peer_state"
    FILE_CHANGED = "file_changed"
    COMPACTION = "compaction"
    TASK_EVENT = "task_event"
    ACTION_NEEDED = "action_needed"
    MESSAGE = "message"
    EXTERNAL = "external"


@dataclass
class EnvEvent:
    """A single environment event.

    ``collapse_key`` lets repeated events fold into one line with a
    count. e.g. multiple edits to the same file by the same peer
    render as ``~ plugin.py (by lapis) x3``.
    """

    kind: EnvKind
    symbol: str
    message: str
    timestamp: float = field(default_factory=time.time)
    collapse_key: Optional[str] = None
    count: int = 1
