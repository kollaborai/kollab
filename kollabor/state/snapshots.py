"""Wire-safe DTO types for StateService.

All snapshots inherit from Snapshot base which provides to_dict/from_dict for
cleanly crossing the daemon/client JSON boundary. These are NOT the same as
internal types like ConversationMessage (which may contain non-serializable
references) - snapshots are flat dicts suitable for json.dumps.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, field
from typing import Any


@dataclass
class Snapshot:
    """Base class for all state snapshots that cross the boundary.

    Provides round-trip serialization via to_dict / from_dict. Subclasses
    should be simple dataclasses with JSON-serializable fields only
    (str, int, float, bool, None, list, dict of same).
    """

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dict suitable for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        """Reconstruct from a dict. Subclasses override if they contain nested snapshots."""
        return cls(**data)


@dataclass
class MessageDto(Snapshot):
    """A single conversation message in wire format.

    Matches the shape of kollabor_events.data_models.ConversationMessage
    but with all fields flattened to JSON-safe types. timestamp is an ISO
    string (not a datetime) and metadata is always a dict.
    """

    role: str
    content: str
    timestamp: str = ""  # ISO 8601 string
    metadata: dict[str, Any] = field(default_factory=dict)
    thinking: str | None = None


@dataclass
class ConversationSnapshot(Snapshot):
    """A point-in-time snapshot of conversation state.

    Used by /save and anything else that needs the full conversation.
    """

    messages: list[MessageDto] = field(default_factory=list)
    session_id: str = ""
    started_at: str = ""  # ISO 8601 string
    message_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [m.to_dict() for m in self.messages],
            "session_id": self.session_id,
            "started_at": self.started_at,
            "message_count": self.message_count,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationSnapshot":
        raw_messages = data.get("messages", [])
        return cls(
            messages=[MessageDto.from_dict(m) for m in raw_messages],
            session_id=data.get("session_id", ""),
            started_at=data.get("started_at", ""),
            message_count=data.get("message_count", 0),
            metadata=data.get("metadata", {}),
        )


@dataclass
class SessionStats(Snapshot):
    """LLM session statistics displayed in status widgets and /status.

    Field names match llm_coordinator.py session_stats dict to minimize
    translation. "messages" is the count of conversation messages, not
    a list.
    """

    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cache_read_tokens: int = 0
    thinking_duration: float = 0.0
    cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    session_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionStats":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ProfileSnapshot(Snapshot):
    """A single LLM profile in wire format.

    Captures the fields /profile show and the model/profile status widgets
    need to display. Does NOT include api_key (which is sensitive and stays
    on the daemon).
    """

    name: str
    model: str = ""
    provider: str = ""
    endpoint: str = ""  # base_url
    supports_tools: bool = True
    temperature: float = 0.7
    description: str = ""
    is_active: bool = False


@dataclass
class ProfileListSnapshot(Snapshot):
    """The full profile registry and which one is active."""

    active: str = ""
    profiles: list[ProfileSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "profiles": [p.to_dict() for p in self.profiles],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileListSnapshot":
        return cls(
            active=data.get("active", ""),
            profiles=[ProfileSnapshot.from_dict(p) for p in data.get("profiles", [])],
        )


# === Phase 3: permission/mcp/hub/processing/system snapshots ===


@dataclass
class PermissionSnapshot(Snapshot):
    """Snapshot of the tool permission manager state.

    approval_mode is the enum name as a string (e.g. "DEFAULT",
    "CONFIRM_ALL", "AUTO_APPROVE_EDITS", "TRUST_ALL"). stats is a flat
    dict of counters the widget shows inline.
    """

    approval_mode: str = "DEFAULT"
    session_approvals_count: int = 0
    project_approvals_count: int = 0
    stats: dict[str, int] = field(default_factory=dict)


@dataclass
class McpServerInfo(Snapshot):
    """Info about a single MCP server connection for status widgets and /mcp show."""

    name: str = ""
    enabled: bool = False
    connected: bool = False
    tool_count: int = 0
    # Tool names exposed by this server. Empty list when phase-3 widgets
    # consume the snapshot -- they only need counts. The /mcp show command
    # populates and reads this list for the full per-server tool listing.
    tools: list[str] = field(default_factory=list)


@dataclass
class McpSnapshot(Snapshot):
    """Snapshot of MCP integration state.

    Mirrors what render_mcp shows: aggregate counts plus a per-server
    list with enabled/connected/tool_count info.
    """

    total_servers: int = 0
    total_tools: int = 0
    connected_servers: int = 0
    servers: list[McpServerInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_servers": self.total_servers,
            "total_tools": self.total_tools,
            "connected_servers": self.connected_servers,
            "servers": [s.to_dict() for s in self.servers],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "McpSnapshot":
        return cls(
            total_servers=data.get("total_servers", 0),
            total_tools=data.get("total_tools", 0),
            connected_servers=data.get("connected_servers", 0),
            servers=[McpServerInfo.from_dict(s) for s in data.get("servers", [])],
        )


@dataclass
class HubPeer(Snapshot):
    """A single agent in the hub roster."""

    identity: str = ""
    state: str = ""
    current_task: str = ""
    is_coordinator: bool = False


@dataclass
class HubSnapshot(Snapshot):
    """Snapshot of hub plugin state: identity + roster.

    my_* fields describe this agent; roster lists all visible peers
    (including self in some hub configurations).
    """

    my_identity: str = ""
    my_agent_id: str = ""
    my_is_coordinator: bool = False
    peer_count: int = 0
    roster: list[HubPeer] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "my_identity": self.my_identity,
            "my_agent_id": self.my_agent_id,
            "my_is_coordinator": self.my_is_coordinator,
            "peer_count": self.peer_count,
            "roster": [p.to_dict() for p in self.roster],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HubSnapshot":
        return cls(
            my_identity=data.get("my_identity", ""),
            my_agent_id=data.get("my_agent_id", ""),
            my_is_coordinator=data.get("my_is_coordinator", False),
            peer_count=data.get("peer_count", 0),
            roster=[HubPeer.from_dict(p) for p in data.get("roster", [])],
        )


@dataclass
class ProcessingSnapshot(Snapshot):
    """Snapshot of LLM processing / queue / task state.

    Combines is_processing, queue metrics, pending tool count, and the
    background task count. Used by status widgets that show busy/idle
    state and queue depth.
    """

    is_processing: bool = False
    queue_size: int = 0
    queue_max: int = 0
    dropped_messages: int = 0
    pending_tools_count: int = 0
    bg_tasks_count: int = 0
    circuit_breaker_state: str = "closed"


@dataclass
class SystemInfoSnapshot(Snapshot):
    """Snapshot of daemon-level system info: cwd, git branch, terminal sessions, pid, uptime.

    cwd and git_branch describe the daemon's working directory, which in
    attach mode is what the client wants to see (the daemon is what's
    actually running the work).

    Phase 4.5 step 7 extension: adds python/platform/arch + command
    registry stats so /status modal can render the daemon's view in
    attach mode instead of the client's local shadow.
    """

    cwd: str = ""
    git_branch: str = ""
    tmux_sessions_count: int = 0  # legacy field name — counts subprocess sessions, not tmux
    daemon_pid: int = 0
    daemon_uptime_seconds: float = 0.0
    # Phase 4.5 step 7 additions
    python_version: str = ""
    platform_name: str = ""
    platform_arch: str = ""
    total_commands: int = 0
    enabled_commands: int = 0
    command_categories: int = 0
    plugin_count: int = 0


# === Phase 4.5: agent / skill / system_prompt snapshots ===


@dataclass
class AgentSnapshot(Snapshot):
    """A single agent in wire format.

    Captures what /agent list and the agent status widget need. Includes
    active_skills as a list of skill names (not SkillInfo because the
    status display only needs the names). Use list_skills() for detail.
    """

    name: str = ""
    description: str = ""
    profile: str = ""
    # Skills currently loaded for this agent (names only).
    active_skills: list[str] = field(default_factory=list)
    # All skills this agent knows about (names only).
    available_skills: list[str] = field(default_factory=list)
    # Default skills loaded when this agent becomes active.
    default_skills: list[str] = field(default_factory=list)
    is_active: bool = False
    source: str = ""  # "global" | "local" | "bundled"


@dataclass
class AgentListSnapshot(Snapshot):
    """The full agent registry with the active one marked."""

    active: str = ""
    agents: list[AgentSnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "agents": [a.to_dict() for a in self.agents],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentListSnapshot":
        return cls(
            active=data.get("active", ""),
            agents=[AgentSnapshot.from_dict(a) for a in data.get("agents", [])],
        )


@dataclass
class SkillInfo(Snapshot):
    """A single skill in wire format.

    Captures what /skills list shows. content is NOT included because
    skill bodies can be large -- fetch with get_skill_content when
    needed (e.g. during activation).
    """

    name: str = ""
    description: str = ""
    # Whether this skill is currently loaded on the active agent.
    active: bool = False
    # Source tier: "bundled" | "global" | "local"
    source: str = ""
    # Length of the skill content in characters (for display sizing).
    content_length: int = 0


@dataclass
class SkillListSnapshot(Snapshot):
    """All skills available to a given agent.

    Used by /skills list in both local and attach mode.
    """

    agent_name: str = ""
    skills: list[SkillInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "skills": [s.to_dict() for s in self.skills],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillListSnapshot":
        return cls(
            agent_name=data.get("agent_name", ""),
            skills=[SkillInfo.from_dict(s) for s in data.get("skills", [])],
        )


@dataclass
class SystemPromptSnapshot(Snapshot):
    """Snapshot of the active system prompt.

    source tells the caller where the prompt came from:
      "agent"   -- composed from active agent definition + skills
      "file"    -- loaded from a user-provided --system-prompt file
      "env"     -- KOLLAB_SYSTEM_PROMPT env var
      "default" -- built-in fallback

    content is the FULL rendered system prompt (post-trender expansion).
    size_chars is included separately to let widgets/clients size the
    display without measuring the full string.
    """

    source: str = ""
    path: str = ""  # Absolute path when source == "file", empty otherwise.
    content: str = ""
    size_chars: int = 0
    # Names of <trender> tags successfully rendered (for debugging).
    rendered_tags: list[str] = field(default_factory=list)
