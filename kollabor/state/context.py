"""ConversationContext: a named conversation context for the multi-context daemon.

Part of phase 4.5 step 6 (option C -- scoped-down multi-context).

Each context is a snapshot of:
  - the conversation history (list of MessageDto)
  - the active profile name
  - the active agent name
  - the system prompt
  - metadata for display (created_at, last_active_at, name)

Exactly one context is "live" at any moment. The live context's
conversation_history lives inside LLMCoordinator as self.conversation_history.
The ContextRegistry handles switching by snapshotting the current
live state back into the old context and loading the new context's
snapshot into LLMCoordinator.

Non-live contexts sit dormant in the registry until selected. There
is no background activity on dormant contexts -- hub messages,
nudges, and any other daemon-internal injection all land in the
current live context.

Wire-safe: inherits Snapshot, round-trips through to_dict/from_dict.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .snapshots import MessageDto, Snapshot


@dataclass
class ConversationContext(Snapshot):
    """A named conversation context held in the daemon's registry.

    Name is the unique key within a daemon instance. Leading/trailing
    whitespace is stripped at construction time; empty names raise.

    ``archived`` contexts are hidden from list_contexts by default but
    their history is preserved on disk. Archiving is the "soft delete"
    operation -- the full history stays around until the user runs
    explicit cleanup.
    """

    name: str = ""
    conversation_history: list[MessageDto] = field(default_factory=list)
    active_profile_name: str = ""
    active_agent_name: str = ""
    system_prompt: str = ""
    created_at: float = 0.0
    last_active_at: float = 0.0
    archived: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize name: strip whitespace. Empty names are allowed at
        # the dataclass level (for snapshot round-trips where the name
        # field may be absent); validation lives in ContextRegistry.
        if isinstance(self.name, str):
            self.name = self.name.strip()
        # Seed timestamps if the caller didn't provide them. Safe default
        # is "now". Tests that care about exact values can set them.
        if self.created_at == 0.0:
            self.created_at = time.time()
        if self.last_active_at == 0.0:
            self.last_active_at = self.created_at

    @property
    def message_count(self) -> int:
        """Number of messages in the context's history."""
        return len(self.conversation_history)

    def touch(self) -> None:
        """Update last_active_at to now.

        Called by ContextRegistry.attach_to when the context becomes
        the live context. Lets list_contexts show recent activity.
        """
        self.last_active_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for RPC transit.

        Overrides the base to_dict so nested MessageDto instances get
        serialized correctly. Everything else is a plain field.
        """
        return {
            "name": self.name,
            "conversation_history": [
                m.to_dict() if hasattr(m, "to_dict") else dict(m)
                for m in self.conversation_history
            ],
            "active_profile_name": self.active_profile_name,
            "active_agent_name": self.active_agent_name,
            "system_prompt": self.system_prompt,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "archived": self.archived,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationContext":
        """Reconstruct from a dict (typically an RPC reply payload).

        Tolerates missing keys -- every field has a default. Unknown
        keys are silently ignored so forward-compat doesn't break.
        """
        raw_messages = data.get("conversation_history", []) or []
        messages: list[MessageDto] = []
        for m in raw_messages:
            if isinstance(m, MessageDto):
                messages.append(m)
            elif isinstance(m, dict):
                messages.append(MessageDto.from_dict(m))
        return cls(
            name=data.get("name", "") or "",
            conversation_history=messages,
            active_profile_name=data.get("active_profile_name", "") or "",
            active_agent_name=data.get("active_agent_name", "") or "",
            system_prompt=data.get("system_prompt", "") or "",
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_active_at=float(data.get("last_active_at", 0.0) or 0.0),
            archived=bool(data.get("archived", False)),
            metadata=dict(data.get("metadata", {}) or {}),
        )


@dataclass
class ContextListSnapshot(Snapshot):
    """The full registry of contexts on a daemon.

    ``active`` is the name of the currently live context. Contexts
    contain ONLY summary info (name, counts, timestamps) -- not the
    full message history -- because this snapshot is used for display
    (list menus, status widgets) where the history would be wasteful.
    Use get_context(name) or get_active_context() to fetch the full
    ConversationContext including history.
    """

    active: str = ""
    contexts: list[ConversationContext] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "contexts": [c.to_dict() for c in self.contexts],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextListSnapshot":
        return cls(
            active=data.get("active", "") or "",
            contexts=[
                ConversationContext.from_dict(c) for c in data.get("contexts", []) or []
            ],
        )
