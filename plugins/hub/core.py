"""Hub Plugin Core - Main lifecycle and initialization.

HubPlugin manages the peer-to-peer agent mesh with social awareness.
This module contains only the core plugin class and lifecycle methods.

Tool handlers are split into domain-specific modules in tools/ directory.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from kollabor_agent.runtime import AgentRuntime
from kollabor_events import EventType, Hook, HookPriority
from kollabor_plugins import BasePlugin

from .change_feed import ChangeFeed
from .coordinator import CoordinatorElection, IdentityAssigner, WorkQueue
from .crystal_store import CrystalStore
from .messenger import AgentSocketServer
from .notifier import HubNotifier
from .nudge_engine import NudgeEngine
from .presence import PresenceManager
from .scratchpad import Scratchpad
from .session_state import SessionState
from .task_ledger import TaskLedger

# Tool handlers - split by domain
from .tools import (
    ContextTools,
    CronTools,
    CrystalTools,
    FeedTools,
    FileTools,
    MessagingTools,
    ScratchpadTools,
    SpawnTools,
    StateTools,
    TaskTools,
)
from .vault import AgentVault

logger = logging.getLogger(__name__)


class HubPlugin(BasePlugin):
    """Zero-config agent mesh with elected coordinator.

    Every kollab instance runs this plugin. On startup:
    1. Creates a presence file and socket server
    2. Tries to become coordinator (flock)
    3. Discovers other agents
    4. Injects agent roster into system prompt
    5. Agents can message each other via socket or /hub command

    The social layer: agents see each other in their system prompt
    and naturally collaborate - asking for help, delegating work,
    sharing findings.
    """

    def __init__(
        self,
        name: str = "hub",
        event_bus=None,
        renderer=None,
        config=None,
    ):
        self.name = name
        self.version = "0.1.0"
        self.description = "Agent mesh with social awareness"
        self.enabled = True

        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self.command_registry = None

        # Core components
        self._identity: Optional[AgentRuntime] = None
        self._presence: Optional[PresenceManager] = None
        self._election: Optional[CoordinatorElection] = None
        self._socket_server: Optional[AgentSocketServer] = None
        self._rpc_server: Optional[Any] = None  # kollabor_rpc.RpcServer
        self._work_queue: Optional[WorkQueue] = None
        self._designator = IdentityAssigner()

        self._conversation_manager = None
        self._llm_service = None

        # Vault (persistent memory across sessions)
        self._vault: Optional[AgentVault] = None
        self._crystal_store: Optional[CrystalStore] = None
        self._nudge_engine: Optional[NudgeEngine] = None
        self._scratchpad: Optional[Scratchpad] = None

        # Session state
        self._session_state: Optional[SessionState] = None
        self._waiting_since: Optional[float] = None  # Waiting state start time

        # Task ledger (task_* tools)
        self._task_ledger: Optional[TaskLedger] = None

        # Feed generator
        self._feed: Optional[ChangeFeed] = None

        # Notification system
        self._notifier: Optional[HubNotifier] = None

        # Cron jobs (hub_cron_* tools)
        self._cron_jobs: Dict[str, Any] = {}

        # Messaging bridge (cross-runtime)
        self._messaging_bridge: Optional[Any] = None

        # Tool handler domains
        self._context_tools: Optional[ContextTools] = None
        self._crystal_tools: Optional[CrystalTools] = None
        self._messaging_tools: Optional[MessagingTools] = None
        self._scratchpad_tools: Optional[ScratchpadTools] = None
        self._state_tools: Optional[StateTools] = None
        self._task_tools: Optional[TaskTools] = None
        self._file_tools: Optional[FileTools] = None
        self._feed_tools: Optional[FeedTools] = None
        self._spawn_tools: Optional[SpawnTools] = None
        self._cron_tools: Optional[CronTools] = None

        # Loops
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._mailbox_task: Optional[asyncio.Task] = None
        self._dreaming_task: Optional[asyncio.Task] = None
        self._autosave_task: Optional[asyncio.Task] = None
        self._messaging_bridge_task: Optional[asyncio.Task] = None
        self._cron_task: Optional[asyncio.Task] = None

        # State tracking
        self._started = False
        self._shutdown = False

    def get_default_config(self) -> Dict[str, Any]:
        """Default configuration for hub plugin."""
        return {
            "enabled": True,
            "heartbeat_interval": 30.0,
            "mailbox_check_interval": 2.0,
            "dreaming_interval": 300.0,
            "vault_autosave_interval": 60.0,
            "feed_max_age": 24.0,
            "max_history_injection": 50,
        }

    def get_config_widgets(self) -> Dict[str, Any]:
        """Return config widgets for hub plugin."""
        return {
            "title": "Hub Plugin",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.hub.enabled",
                    "help": "Enable agent mesh and social collaboration",
                },
                {
                    "type": "slider",
                    "label": "Heartbeat Interval",
                    "config_path": "plugins.hub.heartbeat_interval",
                    "min_value": 10,
                    "max_value": 300,
                    "step": 10,
                    "help": "Agent heartbeat interval (seconds)",
                },
                {
                    "type": "slider",
                    "label": "Dreaming Interval",
                    "config_path": "plugins.hub.dreaming_interval",
                    "min_value": 60,
                    "max_value": 3600,
                    "step": 60,
                    "help": "Agent dreaming interval (seconds)",
                },
                {
                    "type": "slider",
                    "label": "Vault Autosave",
                    "config_path": "plugins.hub.vault_autosave_interval",
                    "min_value": 30,
                    "max_value": 600,
                    "step": 30,
                    "help": "Vault autosave interval (seconds)",
                },
            ],
        }

    async def initialize(self, args=None, **kwargs) -> None:
        """Initialize the hub plugin.

        Called during app startup. Sets up services and registers hooks.
        """
        # Extract services from kwargs
        self.event_bus = kwargs.get("event_bus")
        self.renderer = kwargs.get("renderer")
        self.config = kwargs.get("config")
        self.command_registry = kwargs.get("command_registry")
        self._conversation_manager = kwargs.get("conversation_manager")
        self._llm_service = kwargs.get("llm_service")

        # Defer actual initialization to _do_initialize
        # This allows all plugins to load first
        pass

    async def _do_initialize(self, args=None, **kwargs) -> None:
        """Perform actual hub initialization.

        Called after all plugins are loaded. This is where we
        start the socket server, join the mesh, etc.
        """
        if self._started:
            return

        logger.info("Initializing HubPlugin...")

        # Register pipeline tools
        self._register_pipeline_tools()

        # Start hub system
        await self._start_hub()

        self._started = True
        logger.info("HubPlugin initialized")

    def _register_pipeline_tools(self) -> None:
        """Register all pipeline tools with tool executor."""
        # Initialize tool handler domains
        self._context_tools = ContextTools(self)
        self._crystal_tools = CrystalTools(self)
        self._messaging_tools = MessagingTools(self)
        self._scratchpad_tools = ScratchpadTools(self)
        self._state_tools = StateTools(self)
        self._task_tools = TaskTools(self)
        self._file_tools = FileTools(self)
        self._feed_tools = FeedTools(self)
        self._spawn_tools = SpawnTools(self)
        self._cron_tools = CronTools(self)

        # Register tool tags and handlers
        for tool_instance in [
            self._context_tools,
            self._crystal_tools,
            self._messaging_tools,
            self._scratchpad_tools,
            self._state_tools,
            self._task_tools,
            self._file_tools,
            self._feed_tools,
            self._spawn_tools,
            self._cron_tools,
        ]:
            tool_instance.register_tags()
            tool_instance.register_handlers()

    async def register_hooks(self) -> None:
        """Register hub plugin hooks."""
        # Register LLM response hook for message parsing
        hook = Hook(
            plugin_name=self.name,
            name="parse_hub_messages",
            event_type=EventType.LLM_RESPONSE,
            callback=self._parse_hub_messages,
            priority=HookPriority.LLM.value,
        )
        await self.event_bus.register_hook(hook)

        # Register startup complete hook
        hook = Hook(
            plugin_name=self.name,
            name="start_hub",
            event_type=EventType.SYSTEM_READY,
            callback=self._on_system_ready,
            priority=HookPriority.NORMAL.value,
        )
        await self.event_bus.register_hook(hook)

    async def _on_system_ready(self, data, event):
        """Called when system is ready."""
        # Start deferred initialization
        await self._do_initialize()
        return data

    async def _start_hub(self) -> None:
        """Start the hub system.

        This creates the socket server, presence file, and joins
        the agent mesh.
        """
        # Implementation from original _start_hub method
        # TODO: Move to messaging/startup.py
        pass

    async def shutdown(self) -> None:
        """Shutdown the hub plugin."""
        self._shutdown = True

        # Stop all loops
        for task in [
            self._heartbeat_task,
            self._mailbox_task,
            self._dreaming_task,
            self._autosave_task,
            self._messaging_bridge_task,
            self._cron_task,
        ]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop socket server
        if self._socket_server:
            await self._socket_server.stop()

        # Cleanup presence file
        if self._presence:
            self._presence.cleanup()

        logger.info("HubPlugin shutdown complete")

    # === Waiting State Management ===

    async def _enter_waiting_state(self, reason: Optional[str] = None) -> None:
        """Put this agent into waiting state.

        Sets state, waiting_since, cooldown_until, and waiting_reason on
        the presence record and writes it to disk so peers see the
        updated state.

        Args:
            reason: Optional explanation string from the agent.
        """
        if not self._identity:
            return

        import time

        from .presence_states import PresenceState

        # Coordinator should never enter cooldown — must always be reachable
        is_coordinator = self._identity.identity == "koordinator"
        if is_coordinator:
            logger.info(
                f"Agent {self._identity.identity} is coordinator — "
                "skipping wait cooldown, staying reachable"
            )

        now = time.time()
        if is_coordinator:
            cooldown_secs = 0
        elif self.config:
            cooldown_secs = int(
                self.config.get("plugins.hub.wait_cooldown_seconds", 60)
            )
        else:
            cooldown_secs = 60

        self._identity.state = PresenceState.WAITING.value
        self._identity.waiting_since = now
        self._identity.cooldown_until = now + cooldown_secs
        self._identity.waiting_reason = reason

        # Persist to presence file
        if self._presence:
            self._presence.publish()

        # Reset loop counter so the agent doesn't immediately trigger
        # a loop nudge on wake-up
        if self._nudge_engine and self._identity:
            self._nudge_engine.reset_loop_counter(self._identity.identity)

        logger.info(
            f"Agent {self._identity.identity} entered waiting state"
            + (f" (reason: {reason})" if reason else "")
            + (
                f", cooldown until {now + cooldown_secs:.0f}"
                if cooldown_secs > 0
                else " (no cooldown — coordinator)"
            )
        )

        # Hub notification: alert when agent enters waiting
        if self._notifier and hasattr(self._notifier, "_backend") and self._notifier._backend:
            try:
                msg = (
                    f"Agent '{self._identity.identity}' entered waiting state"
                    + (f" (reason: {reason})" if reason else "")
                    + f". Cooldown: {cooldown_secs}s."
                )
                await self._notifier._backend.send(
                    msg,
                    {
                        "identity": self._identity.identity,
                        "event": "waiting_state_entered",
                        "reason": reason or "",
                        "cooldown_seconds": cooldown_secs,
                        "timestamp": now,
                    },
                )
            except Exception as e:
                logger.debug(f"waiting-state notification failed: {e}")

    async def _exit_waiting_state(self) -> None:
        """Transition agent from waiting back to active."""
        if not self._identity:
            return

        from .presence_states import PresenceState

        waiting_since = self._identity.waiting_since
        self._identity.state = PresenceState.ACTIVE.value
        self._identity.waiting_since = None
        self._identity.cooldown_until = None
        self._identity.waiting_reason = None

        if self._presence:
            self._presence.publish()

        # Reset loop counter on wake
        if self._nudge_engine and self._identity:
            self._nudge_engine.reset_loop_counter(self._identity.identity)

        logger.info(
            f"Agent {self._identity.identity} woke from waiting state"
        )

        # Render a [wake: ...] catch-up header from the env queue
        # so agents see what they missed while parked.
        try:
            await self._emit_wake_env_header(waiting_since)
        except Exception as e:
            logger.debug("wake env header failed: %s", e)

    async def _emit_wake_env_header(self, waiting_since: Optional[float]) -> None:
        """Drain the env queue and inject a [wake: Ns idle, K events] header."""
        if self.event_bus is None:
            return
        queue = self.event_bus.get_service("env_queue")
        if queue is None:
            return

        import time

        if waiting_since:
            duration = int(time.time() - waiting_since)
        else:
            duration = 0
        m, s = divmod(duration, 60)

        events = queue.drain()
        if not events:
            header = f"[wake: {m}m{s}s idle, no events]"
        else:
            plural = "s" if len(events) != 1 else ""
            lines = [f"[wake: {m}m{s}s idle, {len(events)} event{plural}]"]
            for e in events:
                suffix = f" x{e.count}" if e.count > 1 else ""
                lines.append(f"  {e.symbol} {e.message}{suffix}")
            header = "\n".join(lines)

        llm = self.event_bus.get_service("llm_service")
        if llm and hasattr(llm, "inject_system_message"):
            try:
                await llm.inject_system_message(header, subtype="wake_header")
            except Exception as e:
                logger.debug("system message injection failed: %s", e)
