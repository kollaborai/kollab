"""Data models for the hub system."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .rosters import find_persona


class AgentState(Enum):
    BOOTING = "booting"
    CONNECTING = "connecting"
    REGISTERED = "registered"
    IDLE = "idle"
    WORKING = "working"
    DISCONNECTING = "disconnecting"
    DEAD = "dead"


class MessageScope(Enum):
    DIRECT = "direct"
    PROJECT = "project"
    TEAM = "team"
    BROADCAST = "broadcast"


# Identity whose agent bundle is the koordinator orchestrator.
COORDINATOR_IDENTITY = "koordinator"


def desired_bundle_for_identity(identity: str, default_bundle: str = "default") -> str:
    """Return the agent bundle a given hub identity should run.

    The koordinator orchestrator bundle (its system prompt plus the
    hub-spawn/hub-stop/hub-queue tools) belongs ONLY to the elected
    coordinator, i.e. the agent whose identity is ``koordinator``. Every
    persona runs its roster's ``agent`` (``coder`` for the gems), and so does a
    numbered one (``lapis-2``); anything unrecognised falls back to
    ``default_bundle``.

    This is the single source of truth used by the hub to reconcile a plain
    ``kollab`` launch's loaded bundle to its assigned mesh role, so it is a
    pure function with no side effects to keep it unit-testable.

    Args:
        identity: The hub identity the agent was assigned (e.g. "lapis").
        default_bundle: Fallback bundle for non-persona, non-coordinator names.

    Returns:
        The agent bundle name this identity should run.
    """
    if identity == COORDINATOR_IDENTITY:
        return COORDINATOR_IDENTITY
    persona = find_persona(identity)
    return persona.agent if persona and persona.agent else default_bundle


@dataclass
class HubMessage:
    """A message between agents."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: str = "message"
    action: str = "message"
    from_agent: str = ""
    from_identity: str = ""
    to: str = ""
    content: str = ""
    scope: str = MessageScope.DIRECT.value
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    force: bool = False
    """If True, this message breaks through the recipient's
    cooldown even if the sender is not the coordinator."""
    thread_id: str = ""
    """Conversation thread identifier. First message in a thread sets this
    to its own id; all replies carry the same thread_id. Empty = unthreaded."""
    reply_to: str = ""
    """ID of the specific message being replied to within the thread."""

    def __post_init__(self):
        # If no thread_id set, this message starts a new thread
        if not self.thread_id:
            self.thread_id = self.id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "action": self.action,
            "from_agent": self.from_agent,
            "from_identity": self.from_identity,
            "to": self.to,
            "content": self.content,
            "scope": self.scope,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "force": self.force,
            "thread_id": self.thread_id,
            "reply_to": self.reply_to,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HubMessage":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class WorkSlot:
    """A pending work item in the queue."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    task: str = ""
    project: str = ""
    priority: int = 5
    queued_at: float = field(default_factory=time.time)
    queued_by: str = ""
    assigned_to: Optional[str] = None
    status: str = "pending"
    context: str = ""
    required_capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "project": self.project,
            "priority": self.priority,
            "queued_at": self.queued_at,
            "queued_by": self.queued_by,
            "assigned_to": self.assigned_to,
            "status": self.status,
            "context": self.context,
            "required_capabilities": self.required_capabilities,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkSlot":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
