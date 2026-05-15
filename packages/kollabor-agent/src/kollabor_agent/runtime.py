"""AgentRuntime - unified agent identity for kollab.

Merges the static definition (Agent from agent_manager.py) with the
live runtime state (AgentIdentity from hub/models.py) into one object
that represents everything about an AI agent across its entire lifecycle.

This is THE canonical representation of an agent. Phase 7 API transmits
it as JSON. Phase 8 mobile app renders it. Phase 5 skill router queries
it. Everything touches this one dataclass.

Field groups:
  definition   who this agent IS (loaded from agent bundle on disk)
  runtime      process-level state (pid, sockets, timestamps)
  hub          mesh/coordination state (identity, coordinator, peers)
  lineage      where this agent came from (parent, org, hierarchy)
  vault        persistent memory reference (stream, working, crystallized)
"""

import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, cast


class AgentLifecycle(Enum):
    """Full lifecycle states for an agent.

    State machine:
      BOOTING -> READY -> WORKING -> READY (loop)
                   |         |
                   v         v
                BLOCKED   THINKING
                   |         |
                   v         v
                 READY     READY
                   |
                   v
               DREAMING -> READY
                   |
                   v
              SUSPENDED -> READY (on resume)
                   |
                   v
                DYING -> DEAD

    BOOTING     process started, loading config/skills/vault
    READY       idle, waiting for input or messages
    WORKING     executing a task (tool calls, file ops, shell)
    THINKING    waiting on LLM response (streaming or not)
    BLOCKED     suspended, waiting on human input (question gate)
    DREAMING    processing vault -- distilling working memory
                into crystallized knowledge (background, future)
    SUSPENDED   paused by user or coordinator, can be resumed
    DYING       graceful shutdown in progress (saving vault, etc)
    DEAD        process exited, only vault remains
    """

    BOOTING = "booting"
    READY = "ready"
    WORKING = "working"
    THINKING = "thinking"
    BLOCKED = "blocked"
    DREAMING = "dreaming"
    SUSPENDED = "suspended"
    DYING = "dying"
    DEAD = "dead"

    # Backward compat aliases (hub/models.py AgentState used these)
    IDLE = "idle"
    CONNECTING = "connecting"
    REGISTERED = "registered"
    DISCONNECTING = "disconnecting"


class LaunchStrategy(Enum):
    """How this agent was (or should be) launched.

    INTERACTIVE  user started kollab in a terminal (has TUI)
    SUBPROCESS   spawned by another agent via Popen (detached)
    TMUX         legacy: spawned in a tmux session (unused, kept for compat)
    API          spawned by external API request (phase 7)
    """

    INTERACTIVE = "interactive"
    SUBPROCESS = "subprocess"
    TMUX = "tmux"
    API = "api"


def get_agent_tool_scope(agent: Any) -> Optional[List[str]]:
    """Return the active agent's bundle tool scope, if one is defined."""
    if agent is None:
        return None

    tools = getattr(agent, "tools", None)
    if tools:
        return list(tools)

    config = getattr(agent, "config", None)
    config_get = getattr(config, "get", None)
    if callable(config_get):
        tools = config_get("tools")
        if tools:
            return list(tools)

    return None


@dataclass
class AgentRuntime:
    """The ONE object that represents everything about an AI agent.

    Designed to be:
    - Fully serializable (to_dict/from_dict) for API/mobile
    - Extensible without breaking (new fields get defaults)
    - Queryable by skill router, hub, mobile app, API
    - The single source of truth for agent state

    Fields are grouped by concern. Each group can evolve independently
    as phases roll out.
    """

    # ── definition ──────────────────────────────────────────────
    # Static identity from the agent bundle on disk.
    # Loaded once from bundles/agents/<name>/ and rarely changes
    # during a session. This is WHO the agent is.

    name: str = "default"
    """Agent bundle name (directory name). Primary identity key.
    Used for agent discovery, skill routing, and config lookup."""

    description: str = ""
    """Human-readable description from agent.json.
    Displayed in agent lists, hub feed, mobile app."""

    profile: Optional[str] = None
    """Preferred LLM profile name (e.g. 'claude-sonnet').
    None means use the session default."""

    source: str = "global"
    """Where the agent bundle was loaded from: 'local' or 'global'.
    Local agents (.kollab/agents/) override global ones
    (~/.kollab/agents/) with the same name."""

    directory: Any = None
    """Path to the agent bundle directory.
    Can be Path (from Agent) or str (from deserialization).
    Callers can use Path operations (/ operator) on it."""

    default_skills: List[str] = field(default_factory=list)
    """Skills to auto-load when agent activates.
    From agent.json 'default_skills' array."""

    active_skills: List[str] = field(default_factory=list)
    """Currently loaded skill names.
    Mutates during session as skills are loaded/unloaded."""

    capabilities: List[str] = field(default_factory=list)
    """Capability tags for hub discovery and skill routing.
    e.g. ['code', 'test', 'review', 'deploy'].
    Phase 5 skill router uses these to match tasks to agents."""

    vault_enabled: bool = True
    """Whether persistent memory (vault) is active.
    When False, no stream/working/crystallized files are written.
    Some throwaway agents (one-shot tasks) don't need memory."""

    tools: List[str] = field(default_factory=list)
    """Allowed registry tool names from agent.json.
    Empty means no explicit bundle scope (legacy all-tools mode)."""

    # ── runtime ─────────────────────────────────────────────────
    # Process-level state. Changes every session.
    # Everything here is ephemeral -- dies with the process.

    agent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    """Unique ID for this process instance. 12-char hex.
    NOT the same across sessions -- each launch gets a new one.
    The identity is the persistent identity, not this."""

    pid: int = field(default_factory=os.getpid)
    """OS process ID. Used for liveness checks (kill -0)."""

    project: str = field(default_factory=lambda: os.getcwd())
    """Working directory (project root).
    Agents in the same project can see each other on the hub."""

    socket_path: str = ""
    """Unix socket path for hub messaging.
    e.g. /tmp/kollabor-hub/<agent_id>.sock
    Empty until hub plugin initializes the socket server."""

    state: str = AgentLifecycle.BOOTING.value
    """Current lifecycle state. String (not enum) for serialization.
    Use AgentLifecycle enum values when setting."""

    current_task: str = ""
    """What the agent is currently working on.
    Short description shown in hub roster, feed, mobile app.
    Empty when idle."""

    started_at: float = field(default_factory=time.time)
    """Unix timestamp when this process started.
    Used for uptime display and session tracking."""

    last_heartbeat: float = field(default_factory=time.time)
    """Unix timestamp of last heartbeat update.
    Stale detection: if (now - last_heartbeat) > 30s, agent
    is probably dead. Updated by presence manager every 5s."""

    launch_strategy: str = LaunchStrategy.INTERACTIVE.value
    """How this agent was launched. String for serialization.
    Determines behavior: INTERACTIVE has TUI, SUBPROCESS is
    detached, TMUX is legacy, API is phase 7."""

    session_log: str = ""
    """Path to this agent's conversation session JSONL file.
    Set by hub plugin at publish time from conversation_logger.
    Empty if logging is disabled or hub is not active.
    e.g. ~/.kollab/projects/Users_foo_dev_bar/conversations/2604121812-neural-spark.jsonl"""

    # ── hub ─────────────────────────────────────────────────────
    # Mesh coordination state. Managed by the hub plugin.
    # These fields only matter when hub is active.

    identity: str = ""
    """Hub identity (gem name or custom).
    This IS the agent's persistent identity on the mesh.
    Defaults to agent name if empty.
    Vault, presence, and all peer interactions key on this.
    Examples: 'lapis', 'peridot', 'jasper', 'director'."""

    is_coordinator: bool = False
    """Whether this agent won the flock() election.
    Coordinator assigns identities, manages work queue,
    resolves conflicts. Only one per project."""

    peer_count: int = 0
    """Number of known live peers on the hub.
    Cached here for quick access (status bar, mobile app).
    Updated on each heartbeat/discovery cycle."""

    waiting_since: Optional[float] = None
    """Unix timestamp when the agent entered waiting state.
    None if not waiting."""

    cooldown_until: Optional[float] = None
    """Unix timestamp when the cooldown expires.
    Non-coordinator peer messages sent before this time are
    rejected. None if no cooldown active."""

    waiting_reason: Optional[str] = None
    """Optional reason shown in /hub status and propagated to peers
    that try to message during cooldown."""

    # ── lineage ─────────────────────────────────────────────────
    # Where this agent came from. Who spawned it.
    # Critical for org hierarchy, skill routing (phase 5),
    # notification chains (phase 6), and debugging.

    parent_agent_id: str = ""
    """agent_id of the agent that spawned this one.
    Empty string means user-launched (no parent).
    Used for: capture requests, shutdown cascades,
    result delivery back to parent."""

    parent_identity: str = ""
    """Identity of the parent agent.
    Redundant with parent_agent_id but useful for display
    without a lookup. e.g. 'director' spawned 'backend-eng'."""

    org_name: str = ""
    """Organization this agent belongs to, if any.
    From the org JSON that launched it.
    e.g. 'engineering', 'startup'. Empty if solo agent."""

    org_role: str = ""
    """Role within the organization.
    e.g. 'Engineering Director', 'Backend Engineer'.
    Empty if solo agent. Shown in hub feed and mobile app."""

    org_level: str = ""
    """Hierarchy level: 'director', 'manager', 'member'.
    Used by phase 5 skill router for delegation decisions
    and phase 6 for notification escalation."""

    team_name: str = ""
    """Team within the organization.
    e.g. 'platform', 'applications'. Empty if no team."""

    reports_to: str = ""
    """Identity of this agent's manager/director.
    From org JSON 'reports_to' field.
    Phase 6 notifications escalate along this chain."""

    # ── timestamps ──────────────────────────────────────────────
    # Tracking when things happened. Useful for debugging,
    # mobile app timeline view, and vault session tracking.

    state_changed_at: float = field(default_factory=time.time)
    """When the state field last changed.
    Lets the mobile app show 'working for 2m 34s'."""

    # ── metadata ────────────────────────────────────────────────
    # Catch-all for phase-specific extensions.
    # New phases can stuff data here without schema changes.

    tags: Dict[str, str] = field(default_factory=dict)
    """Arbitrary key-value tags for filtering and grouping.
    e.g. {'priority': 'high', 'sprint': '2026-Q2-W14'}.
    Phase 5 skill router can filter on these."""

    # ── agent bridge ────────────────────────────────────────────
    # Reference to the original Agent object for backward compat.
    # Proxies skills, system_prompt, overrides_global, and all
    # skill methods that callers depend on. NOT serialized.

    _agent_ref: Any = field(default=None, repr=False)
    """Internal: reference to the original Agent dataclass.
    Used to proxy attributes that AgentRuntime doesn't own
    (skills dict, system_prompt, overrides_global, skill methods).
    Set by from_agent(). None for API-created runtimes."""

    @property
    def skills(self) -> dict:
        """Proxy to Agent.skills (Dict[str, Skill])."""
        if self._agent_ref and hasattr(self._agent_ref, "skills"):
            return cast(dict, self._agent_ref.skills)
        return {}

    @property
    def system_prompt(self) -> str:
        """Proxy to Agent.system_prompt."""
        if self._agent_ref and hasattr(self._agent_ref, "system_prompt"):
            return cast(str, self._agent_ref.system_prompt)
        return ""

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        if self._agent_ref and hasattr(self._agent_ref, "system_prompt"):
            self._agent_ref.system_prompt = value

    @property
    def overrides_global(self) -> bool:
        """Proxy to Agent.overrides_global."""
        if self._agent_ref and hasattr(self._agent_ref, "overrides_global"):
            return cast(bool, self._agent_ref.overrides_global)
        return False

    @property
    def config(self) -> Dict[str, Any]:
        """Compatibility view for callers that still read Agent.config."""
        return {
            "tools": self.tools,
            "profile": self.profile,
            "description": self.description,
            "default_skills": self.default_skills,
            "capabilities": self.capabilities,
            "vault_enabled": self.vault_enabled,
        }

    def list_skills(self) -> list:
        """Proxy to Agent.list_skills()."""
        if self._agent_ref and hasattr(self._agent_ref, "list_skills"):
            return cast(list, self._agent_ref.list_skills())
        return []

    def get_skill(self, name: str) -> Any:
        """Proxy to Agent.get_skill()."""
        if self._agent_ref and hasattr(self._agent_ref, "get_skill"):
            return self._agent_ref.get_skill(name)
        return None

    def load_skill(self, name: str) -> bool:
        """Proxy to Agent.load_skill()."""
        if self._agent_ref and hasattr(self._agent_ref, "load_skill"):
            result = cast(bool, self._agent_ref.load_skill(name))
            self.active_skills = list(self._agent_ref.active_skills)
            return result
        return False

    def unload_skill(self, name: str) -> bool:
        """Proxy to Agent.unload_skill()."""
        if self._agent_ref and hasattr(self._agent_ref, "unload_skill"):
            result = cast(bool, self._agent_ref.unload_skill(name))
            self.active_skills = list(self._agent_ref.active_skills)
            return result
        return False

    def get_full_system_prompt(self, **kwargs: Any) -> str:
        """Proxy to Agent.get_full_system_prompt().

        Passes through agent_manager and event_bus kwargs for trender rendering.
        """
        if self._agent_ref and hasattr(self._agent_ref, "get_full_system_prompt"):
            return cast(str, self._agent_ref.get_full_system_prompt(**kwargs))
        return self.system_prompt

    # ─────────────────────────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON transmission.

        Groups fields for readability in API responses.
        Every field is included -- no secrets to hide here.
        """
        return {
            # definition
            "name": self.name,
            "description": self.description,
            "profile": self.profile,
            "source": self.source,
            "directory": str(self.directory) if self.directory else None,
            "default_skills": self.default_skills,
            "active_skills": self.active_skills,
            "capabilities": self.capabilities,
            "vault_enabled": self.vault_enabled,
            "tools": self.tools,
            # runtime
            "agent_id": self.agent_id,
            "pid": self.pid,
            "project": self.project,
            "socket_path": self.socket_path,
            "state": self.state,
            "current_task": self.current_task,
            "started_at": self.started_at,
            "last_heartbeat": self.last_heartbeat,
            "launch_strategy": self.launch_strategy,
            # hub
            "identity": self.identity,
            "is_coordinator": self.is_coordinator,
            "peer_count": self.peer_count,
            # lineage
            "parent_agent_id": self.parent_agent_id,
            "parent_identity": self.parent_identity,
            "org_name": self.org_name,
            "org_role": self.org_role,
            "org_level": self.org_level,
            "team_name": self.team_name,
            "reports_to": self.reports_to,
            # timestamps
            "state_changed_at": self.state_changed_at,
            # metadata
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentRuntime":
        """Deserialize from dict.

        Tolerant of missing keys -- new fields get defaults,
        unknown keys are silently ignored. This is critical for
        forward/backward compat when API and mobile app versions
        diverge from the CLI version.
        """
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    # ─────────────────────────────────────────────────────────────
    # Convenience
    # ─────────────────────────────────────────────────────────────

    @property
    def agent_name(self) -> str:
        """Backward compat alias for name (AgentIdentity used agent_name)."""
        return self.name

    @property
    def profile_name(self) -> str:
        """Backward compat alias for profile (AgentIdentity used profile_name)."""
        return self.profile or ""

    def effective_identity(self) -> str:
        """The identity to use on the hub.

        Falls back to agent name if no explicit identity set.
        This is the persistent identity that keys vaults, presence,
        and all peer interactions.
        """
        return self.identity or self.name

    def set_state(self, new_state: AgentLifecycle) -> None:
        """Update state with automatic timestamp tracking."""
        self.state = new_state.value
        self.state_changed_at = time.time()

    def uptime_seconds(self) -> float:
        """Seconds since this agent process started."""
        return time.time() - self.started_at

    def state_duration_seconds(self) -> float:
        """Seconds since the last state change.

        Useful for display: 'thinking for 4s', 'blocked for 2m'.
        """
        return time.time() - self.state_changed_at

    def is_alive(self) -> bool:
        """Check if the agent's process is still running."""
        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def is_stale(self, timeout: float = 30.0) -> bool:
        """Check if heartbeat is stale (likely dead)."""
        return (time.time() - self.last_heartbeat) > timeout

    def is_user_launched(self) -> bool:
        """True if the human started this agent directly."""
        return not self.parent_agent_id

    def is_in_org(self) -> bool:
        """True if this agent is part of an organization."""
        return bool(self.org_name)

    def heartbeat(self) -> None:
        """Update heartbeat timestamp."""
        self.last_heartbeat = time.time()

    # ─────────────────────────────────────────────────────────────
    # Conversions to/from legacy types
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def from_agent(cls, agent: Any) -> "AgentRuntime":
        """Create AgentRuntime from a legacy Agent dataclass.

        Used during migration. The Agent object provides the
        definition fields; runtime/hub/lineage fields get defaults.
        """
        return cls(
            # definition
            name=agent.name,
            description=agent.description,
            profile=agent.profile,
            source=agent.source,
            directory=agent.directory,
            default_skills=list(agent.default_skills),
            active_skills=list(agent.active_skills),
            capabilities=list(agent.capabilities),
            vault_enabled=agent.vault_enabled,
            tools=list(getattr(agent, "tools", [])),
            # hub -- agent.identity carries over
            identity=agent.identity,
            # bridge -- keep original Agent for skill/prompt proxying
            _agent_ref=agent,
        )

    @classmethod
    def from_agent_identity(cls, identity: Any, agent: Any = None) -> "AgentRuntime":
        """Create AgentRuntime from a legacy AgentIdentity + optional Agent.

        Used during migration. Merges both legacy objects into one.
        If agent is None, definition fields stay at defaults.
        """
        rt = cls(
            # runtime (from identity)
            agent_id=identity.agent_id,
            pid=identity.pid,
            project=identity.project,
            socket_path=identity.socket_path,
            state=identity.state,
            current_task=identity.current_task,
            started_at=identity.started_at,
            last_heartbeat=identity.last_heartbeat,
            capabilities=list(identity.capabilities),
            # hub (from identity)
            identity=identity.identity,
            is_coordinator=identity.is_coordinator,
        )

        # Overlay agent definition if provided
        if agent is not None:
            rt.name = agent.name
            rt.description = agent.description
            rt.profile = agent.profile
            rt.source = agent.source
            rt.directory = str(agent.directory) if agent.directory else None
            rt.default_skills = list(agent.default_skills)
            rt.active_skills = list(agent.active_skills)
            rt.vault_enabled = agent.vault_enabled
            rt.tools = list(getattr(agent, "tools", []))
            # capabilities: prefer agent's if identity's is empty
            if not rt.capabilities and agent.capabilities:
                rt.capabilities = list(agent.capabilities)

        return rt

    def to_presence_dict(self) -> Dict[str, Any]:
        """Serialize to the format expected by presence files.

        Backward-compatible with AgentIdentity.to_dict() so
        existing presence readers can parse it. Adds new fields
        that old readers will ignore.
        """
        return {
            # fields that match AgentIdentity exactly
            "agent_id": self.agent_id,
            "identity": self.effective_identity(),
            "pid": self.pid,
            "project": self.project,
            "socket_path": self.socket_path,
            "agent_name": self.name,
            "profile_name": self.profile or "",
            "started_at": self.started_at,
            "last_heartbeat": self.last_heartbeat,
            "state": self.state,
            "is_coordinator": self.is_coordinator,
            "current_task": self.current_task,
            "capabilities": self.capabilities,
            # new fields (ignored by old readers)
            "org_name": self.org_name,
            "org_role": self.org_role,
            "org_level": self.org_level,
            "team_name": self.team_name,
            "reports_to": self.reports_to,
            "parent_agent_id": self.parent_agent_id,
            "launch_strategy": self.launch_strategy,
            "session_log": self.session_log,
            "waiting_since": self.waiting_since,
            "cooldown_until": self.cooldown_until,
            "waiting_reason": self.waiting_reason,
        }

    @classmethod
    def from_presence_dict(cls, data: Dict[str, Any]) -> "AgentRuntime":
        """Reconstruct AgentRuntime from presence JSON.

        Maps the AgentIdentity field names back to AgentRuntime fields.
        Presence JSON uses 'agent_name' and 'profile_name' where
        AgentRuntime uses 'name' and 'profile'. Tolerates missing
        and unknown keys for forward/backward compat.
        """
        mapped: Dict[str, Any] = {}

        # direct 1:1 fields
        direct = [
            "agent_id",
            "identity",
            "pid",
            "project",
            "socket_path",
            "started_at",
            "last_heartbeat",
            "state",
            "is_coordinator",
            "current_task",
            "capabilities",
            "org_name",
            "org_role",
            "org_level",
            "team_name",
            "reports_to",
            "parent_agent_id",
            "launch_strategy",
            "session_log",
            "waiting_since",
            "cooldown_until",
            "waiting_reason",
        ]
        for key in direct:
            if key in data:
                mapped[key] = data[key]

        # renamed fields: presence -> runtime
        if "agent_name" in data:
            mapped["name"] = data["agent_name"]
        if "profile_name" in data:
            mapped["profile"] = data["profile_name"] or None

        return cls(**mapped)

    def __repr__(self) -> str:
        identity = self.effective_identity()
        return (
            f"AgentRuntime("
            f"{identity}|{self.name}, "
            f"state={self.state}, "
            f"id={self.agent_id})"
        )
