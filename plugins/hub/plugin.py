"""Hub Plugin - peer-to-peer agent mesh with social awareness.

Agents join the hub automatically on startup. They discover each other,
share context, and collaborate through natural conversation injection.
The LLM sees other agents in its system prompt and can proactively
offer help or coordinate work.
"""

import asyncio
import collections
import hashlib
import json
import logging
import os
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from kollabor_agent.runtime import AgentLifecycle, AgentRuntime
from kollabor_events import EventType, Hook, HookPriority
from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    SubcommandInfo,
)
from kollabor_plugins import BasePlugin

from .change_feed import DEFAULT_FEED_MAX_AGE, ChangeFeed
from .coordinator import CoordinatorElection, IdentityAssigner, WorkQueue
from .crystal_store import CrystalStore, normalize_crystal_id
from .messaging_bridge import BridgeManager, IncomingMessage, MessagingBridge
from .messenger import AgentMessenger, AgentSocketServer
from .models import POOL_BY_NAME, POOL_IDENTITIES, AgentState, HubMessage, MessageScope
from .notifier import HubNotifier
from .nudge_engine import NudgeEngine
from .presence import PresenceManager, get_messages_dir
from .scratchpad import Scratchpad
from .session_state import SessionState, SessionStateManager
from .task_ledger import TaskLedger
from .vault import AgentVault

# Agent DNS (discovery, identity, trust) — guarded: PyNaCl is optional
try:
    from .dns.capabilities import CapabilityRegistry
    from .dns.identity import IdentityManager
    from .dns.models import AgentRecord, CapabilityEntry, Endorsement
    from .dns.registry import AgentRegistry
    from .dns.reputation import ReputationTracker
    from .dns.storage import DNSStorage
    _DNS_AVAILABLE = True
except ImportError as _dns_import_err:
    DNSStorage = None  # type: ignore[assignment,misc]
    IdentityManager = None  # type: ignore[assignment,misc]
    AgentRegistry = None  # type: ignore[assignment,misc]
    ReputationTracker = None  # type: ignore[assignment,misc]
    CapabilityRegistry = None  # type: ignore[assignment,misc]
    AgentRecord = None  # type: ignore[assignment,misc]
    CapabilityEntry = None  # type: ignore[assignment,misc]
    Endorsement = None  # type: ignore[assignment,misc]
    _DNS_AVAILABLE = False
    import logging as _logging
    _logging.getLogger(__name__).warning(
        f"Agent DNS disabled (PyNaCl not installed): {_dns_import_err}. "
        "Install with: pip install pynacl"
    )

logger = logging.getLogger(__name__)

STOP_GRACE_SECONDS = 1.5
STOP_TERM_SECONDS = 1.0
REMOTE_SHUTDOWN_WATCHDOG_SECONDS = 2.0


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


@dataclass
class HubCronJob:
    """A scheduled recurring message to a hub agent."""

    id: str
    target: str  # identity or "all"
    message: str
    interval_seconds: float
    next_fire: float
    recurring: bool = True
    created_at: float = field(default_factory=time.time)


def _parse_interval(s: str) -> float:
    """Parse interval string like '5m', '1h', '30s', '2h30m' to seconds.

    Supports: Ns (seconds), Nm (minutes), Nh (hours), or combos like '2h30m'.
    """
    s = s.strip().lower()
    if not s:
        raise ValueError("empty interval")

    # Try combined format: 2h30m, 1h15m30s, etc.
    pattern = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
    m = pattern.match(s)
    if m and any(m.groups()):
        hours = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        seconds = int(m.group(3) or 0)
        total = hours * 3600 + minutes * 60 + seconds
        if total <= 0:
            raise ValueError(f"interval must be positive: {s}")
        return float(total)

    raise ValueError(f"invalid interval format: {s} (use e.g. 30s, 5m, 1h, 2h30m)")



def _looks_like_crystal_id(value: Any) -> bool:
    """Return True when value resembles a crystal entry ID."""
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and (text.startswith("crys-") or text.isdigit())


def _looks_like_transport_tool_id(value: Any) -> bool:
    """Return True for likely native transport tool call IDs."""
    if value is None:
        return False
    text = str(value).strip()
    return text.startswith("call_") or text.startswith("tool-call-")


def _safe_semantic_id(tool_data: dict, primary_keys: List[str]) -> str:
    """Resolve a semantic ID without blindly consuming transport tool ids.

    Priority:
    1. explicit semantic keys
    2. legacy XML/body key 'id' only if it doesn't look like a transport id
    """
    for key in primary_keys:
        value = tool_data.get(key, "")
        if str(value).strip():
            return str(value).strip()

    legacy_id = str(tool_data.get("id", "")).strip()
    if legacy_id and not _looks_like_transport_tool_id(legacy_id):
        return legacy_id
    return ""


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
        self._rpc_server: Optional[Any] = None  # kollabor_rpc.RpcServer; see _start_hub
        self._work_queue: Optional[WorkQueue] = None
        self._designator = IdentityAssigner()

        self._conversation_manager = None
        self._llm_service = None

        # Vault (persistent memory across sessions)
        self._vault: Optional[AgentVault] = None
        self._crystal_store: Optional[CrystalStore] = None        # project-scoped
        self._global_crystal_store: Optional[CrystalStore] = None  # cross-project

        # Task ledger (compaction-proof task persistence)
        self._task_ledger: Optional[TaskLedger] = TaskLedger()

        # Change feed (file tracking + lane claims)
        self._change_feed: Optional[ChangeFeed] = None

        # Session state (serialize/rehydrate working context)
        self._session_state_mgr = SessionStateManager()

        # Scratchpad (live notes, survives compaction)
        self._scratchpad: Optional[Scratchpad] = None

        # Nudge engine (context-aware tool reminders)
        self._nudge_engine = NudgeEngine()

        # State
        self._roster: List[Dict] = []
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._mailbox_task: Optional[asyncio.Task] = None
        self._dreaming_task: Optional[asyncio.Task] = None
        self._notify_task: Optional[asyncio.Task] = None
        self._notifier: Optional[HubNotifier] = None
        self._cron_task: Optional[asyncio.Task] = None
        self._autosave_task: Optional[asyncio.Task] = None
        self._bridge_task: Optional[asyncio.Task] = None
        self._bridge: Optional[MessagingBridge] = None
        self._hub_cron_jobs: List[HubCronJob] = []
        self._recent_hub_msgs: Dict[str, float] = {}  # hash -> timestamp
        # Agent DNS
        self._dns_storage: Optional[Any] = None
        self._dns_identity: Optional[Any] = None
        self._dns_registry: Optional[Any] = None
        self._dns_reputation: Optional[Any] = None
        self._dns_capabilities: Optional[Any] = None
        self._dns_startup_time: float = 0.0
        # Active thread context: set when an incoming message has a thread_id.
        # _handle_hub_msg_tool uses this for <hub_reply> so agents don't need
        # to track thread IDs manually.
        self._active_thread_id: str = ""
        self._active_thread_msg_id: str = ""  # id of the last message in thread
        self._started = False
        self._starting = False
        self._last_activity_at: float = time.time()
        self._last_dream_at: float = 0.0
        self._last_autosave_at: float = 0.0

        # Loop prevention metrics (phase 3 observability)
        self._loop_metrics: Dict[str, int] = {
            "loop_nudges_fired": 0,
            "coordinator_breakthroughs": 0,
            "force_breakthroughs": 0,
            "cooldown_rejections": 0,
            "waiting_state_entries": 0,
            "waiting_state_exits": 0,
        }
        self._waiting_durations: List[float] = []  # track durations for avg

        # Message deduplication (LRU via OrderedDict, capped at 1000)
        self._seen_messages: collections.OrderedDict = collections.OrderedDict()

        # Synchronize conversation_history appends across async paths
        self._history_lock = asyncio.Lock()

        self.logger = logger

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            "plugins": {
                "hub": {
                    "enabled": True,
                    "heartbeat_interval": 5,
                    "identity": "",
                    "auto_help": True,
                    "mailbox_poll_interval": 5,
                    "dreaming_enabled": True,
                    "dreaming_idle_threshold": 300,
                    "dreaming_interval": 3600,
                    "dreaming_stream_depth": 100,
                    "notify_enabled": False,
                    "notify_channel": "webhook",
                    "notify_url": "",
                    "notify_idle_threshold": 300,
                    "notify_telegram_token": "",
                    "notify_telegram_chat_id": "",
                    "notify_cooldown": 1800,
                    "vault_autosave_interval": 60,
                    "bridge_enabled": False,
                    "bridge_platform": "telegram",
                    "bridge_token": "",
                    "bridge_chat_id": "",
                    "bridge_user_id": "",
                    "bridge_poll_interval": 2,
                    "bridge_target_agent": "",
                    "route_untagged_to_coordinator": True,
                    "allowed_runtimes": ["kollab"],
                    "require_auth": False,
                    "authority": "kollabor.ai",
                    "wait_cooldown_seconds": 60,
                    "project_scoped": True,
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "Hub (Agent Mesh)",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.hub.enabled",
                    "help": "Enable agent hub for multi-agent collaboration",
                },
                {
                    "type": "checkbox",
                    "label": "Per-project Hub",
                    "config_path": "plugins.hub.project_scoped",
                    "help": (
                        "Keep hub state per project (presence, vaults, "
                        "sockets). Agents in other projects stay invisible. "
                        "Requires restart."
                    ),
                },
                {
                    "type": "slider",
                    "label": "Heartbeat Interval",
                    "config_path": "plugins.hub.heartbeat_interval",
                    "min_value": 2,
                    "max_value": 30,
                    "step": 1,
                    "help": "Seconds between heartbeat updates",
                },
                {
                    "type": "text_input",
                    "label": "Identity",
                    "config_path": "plugins.hub.identity",
                    "placeholder": "auto",
                    "help": "Custom identity (leave empty for auto-assign)",
                },
                {
                    "type": "text_input",
                    "label": "Authority Domain",
                    "config_path": "plugins.hub.authority",
                    "placeholder": "kollabor.ai",
                    "help": "Authority domain for agent AID (e.g. kollabor.ai or mentiko.com)",
                },
                {
                    "type": "text_input",
                    "label": "Allowed Runtimes",
                    "config_path": "plugins.hub.allowed_runtimes",
                    "placeholder": '["kollab"]',
                    "help": "JSON list of runtimes auto-approved for mesh (default: kollab only)",
                },
                {
                    "type": "checkbox",
                    "label": "Require Socket Auth",
                    "config_path": "plugins.hub.require_auth",
                    "help": (
                        "Require Ed25519 challenge-response handshake on "
                        "socket connections. Off by default; enable for production."
                    ),
                },
                {
                    "type": "checkbox",
                    "label": "Auto Help",
                    "config_path": "plugins.hub.auto_help",
                    "help": "Agent proactively offers help to peers",
                },
                {
                    "type": "checkbox",
                    "label": "Dreaming",
                    "config_path": "plugins.hub.dreaming_enabled",
                    "help": "Agent dreams when idle (distills vault insights)",
                },
                {
                    "type": "slider",
                    "label": "Dream Idle Threshold",
                    "config_path": "plugins.hub.dreaming_idle_threshold",
                    "min_value": 60,
                    "max_value": 1800,
                    "step": 60,
                    "help": "Seconds of idle before dreaming starts",
                },
                {
                    "type": "slider",
                    "label": "Dream Interval",
                    "config_path": "plugins.hub.dreaming_interval",
                    "min_value": 600,
                    "max_value": 7200,
                    "step": 300,
                    "help": "Minimum seconds between dreams",
                },
                {
                    "type": "spinbox",
                    "label": "Dream Stream Depth",
                    "config_path": "plugins.hub.dreaming_stream_depth",
                    "min_value": 20,
                    "max_value": 500,
                    "step": 10,
                    "help": "Number of stream entries to review when dreaming",
                },
                # --- Notification Channel ---
                {
                    "type": "checkbox",
                    "label": "Notifications",
                    "config_path": "plugins.hub.notify_enabled",
                    "help": "Alert when agent is idle beyond threshold",
                },
                {
                    "type": "text_input",
                    "label": "Notify Channel",
                    "config_path": "plugins.hub.notify_channel",
                    "placeholder": "webhook",
                    "help": "Notification backend: webhook or telegram",
                },
                {
                    "type": "text_input",
                    "label": "Webhook URL",
                    "config_path": "plugins.hub.notify_url",
                    "placeholder": "https://...",
                    "help": "URL for webhook notifications",
                },
                {
                    "type": "slider",
                    "label": "Notify Idle Threshold",
                    "config_path": "plugins.hub.notify_idle_threshold",
                    "min_value": 60,
                    "max_value": 3600,
                    "step": 60,
                    "help": "Seconds of idle before sending notification",
                },
                {
                    "type": "text_input",
                    "label": "Telegram Bot Token",
                    "config_path": "plugins.hub.notify_telegram_token",
                    "placeholder": "optional",
                    "help": "Telegram bot token (for telegram channel)",
                },
                {
                    "type": "text_input",
                    "label": "Telegram Chat ID",
                    "config_path": "plugins.hub.notify_telegram_chat_id",
                    "placeholder": "optional",
                    "help": "Telegram chat ID (for telegram channel)",
                },
                # --- Messaging Bridge ---
                {
                    "type": "checkbox",
                    "label": "Messaging Bridge",
                    "config_path": "plugins.hub.bridge_enabled",
                    "help": "Bidirectional messaging bridge (phone <-> agents)",
                },
                {
                    "type": "dropdown",
                    "label": "Bridge Platform",
                    "config_path": "plugins.hub.bridge_platform",
                    "options": ["telegram"],
                    "help": "Platform for messaging bridge",
                },
                {
                    "type": "text_input",
                    "label": "Bridge Token",
                    "config_path": "plugins.hub.bridge_token",
                    "placeholder": "bot token or API key",
                    "help": "Auth token for the bridge platform",
                },
                {
                    "type": "text_input",
                    "label": "Bridge Chat ID",
                    "config_path": "plugins.hub.bridge_chat_id",
                    "placeholder": "chat/channel ID",
                    "help": "Chat or channel to bridge messages to/from",
                },
                {
                    "type": "text_input",
                    "label": "Bridge Target Agent",
                    "config_path": "plugins.hub.bridge_target_agent",
                    "placeholder": "auto (self)",
                    "help": "Identity to route incoming bridge messages to",
                },
                {
                    "type": "slider",
                    "label": "Bridge Poll Interval",
                    "config_path": "plugins.hub.bridge_poll_interval",
                    "min_value": 1,
                    "max_value": 30,
                    "step": 1,
                    "help": "Seconds between polls for incoming messages",
                },
                # --- Routing ---
                {
                    "type": "checkbox",
                    "label": "Route Untagged to Coordinator",
                    "config_path": "plugins.hub.route_untagged_to_coordinator",
                    "help": "Auto-send untagged LLM responses to coordinator",
                },
                # --- Loop Prevention ---
                {
                    "type": "slider",
                    "label": "Wait Cooldown (seconds)",
                    "config_path": "plugins.hub.wait_cooldown_seconds",
                    "min_value": 10,
                    "max_value": 600,
                    "step": 10,
                    "help": "How long after <wait_for_user/> that peer messages are rejected",
                },
                {
                    "type": "slider",
                    "label": "Loop Detection Threshold (turns)",
                    "config_path": "plugins.hub.loop_detection_threshold",
                    "min_value": 2,
                    "max_value": 10,
                    "step": 1,
                    "help": "Consecutive hub-only turns before loop nudge fires",
                },
                {
                    "type": "checkbox",
                    "label": "Coordinator Auto-Breakthrough",
                    "config_path": "plugins.hub.coordinator_auto_breakthrough",
                    "help": "Coordinator messages bypass cooldown without force attribute",
                },
            ],
        }

    async def initialize(self, args=None, **kwargs) -> None:
        """Initialize the hub plugin."""
        try:
            await self._do_initialize(args, **kwargs)
        except Exception as e:
            logger.error(f"Hub plugin init failed: {e}", exc_info=True)
            self.enabled = False

    async def _do_initialize(self, args=None, **kwargs) -> None:
        """Internal initialize - wrapped for safety."""
        self._cli_args = args
        self.event_bus = kwargs.get("event_bus", self.event_bus)
        self.config = kwargs.get("config", self.config)
        self.command_registry = kwargs.get("command_registry")
        self._conversation_manager = kwargs.get("conversation_manager")

        # Project scoping: translate config flag into env var BEFORE anything
        # touches hub paths. Propagates to detached-daemon subprocess spawns
        # because env vars inherit. See plugins/hub/project_scope.py.
        try:
            scoped = bool(self.config.get("plugins.hub.project_scoped", True))

            # Wire loop detection threshold from config to nudge engine
            if self._nudge_engine and self.config:
                threshold = self.config.get("plugins.hub.loop_detection_threshold", 3)
                self._nudge_engine._loop_threshold = threshold
            os.environ["KOLLAB_HUB_PROJECT_SCOPED"] = "1" if scoped else "0"
        except Exception:
            pass

        if not self._is_enabled():
            logger.info("Hub plugin disabled")
            return

        # Pipe mode: don't register presence or start socket server.
        # One-shot queries shouldn't appear as agents on the hub mesh.
        if args and getattr(args, "pipe", False):
            logger.info("Hub plugin skipping presence — pipe mode")
            self.enabled = False
            return

        # Get profile/agent info
        profile_mgr = kwargs.get("profile_manager")
        agent_mgr = None
        try:
            agent_mgr = (
                self.event_bus.get_service("agent_manager") if self.event_bus else None
            )
        except Exception:
            pass

        # Create identity (AgentRuntime is the unified superset)
        self._identity = AgentRuntime(
            name=(
                (getattr(agent_mgr, "active_agent_name", None) or "default")
                if agent_mgr
                else "default"
            ),
            profile=(
                getattr(profile_mgr, "active_profile_name", "") if profile_mgr else ""
            )
            or None,
        )

        # Start presence + clean up orphan sockets from past crashes
        self._presence = PresenceManager(self._identity)
        PresenceManager.cleanup_stale_sockets()
        self._presence.startup_scan()

        # Archive stale vaults (identities unseen > 7 days)
        from .vault import archive_vaults

        archive_vaults(max_age_days=7)

        # Create display tap for live attach streaming
        from kollabor_tui.display_tap import DisplayTap

        self._display_tap = DisplayTap(history_size=200)
        if self.event_bus:
            self.event_bus.register_service("display_tap", self._display_tap)

        # Socket server is created in _start_hub() AFTER identity
        # assignment so the socket file is named by identity
        # (e.g. jarvis.sock) instead of a random hex hash.

        # Coordinator election
        self._election = CoordinatorElection()

        # Work queue
        self._work_queue = WorkQueue()

        # Register slash commands
        if self.command_registry:
            self._register_commands()

        # Phase 2: register hub XML tags with the unified tool pipeline
        self._register_pipeline_tools()

        logger.info("Hub plugin initialized")

    # ------------------------------------------------------------------
    # Phase 2: pipeline tool registration
    # ------------------------------------------------------------------
    # Hub XML tags are registered with response_parser so they get
    # extracted and stripped from display automatically.  Matching
    # handlers are registered with tool_executor so the pipeline
    # routes execution to us.  _parse_hub_messages still handles
    # tags that haven't been migrated yet.
    # ------------------------------------------------------------------

    def _register_pipeline_tools(self) -> None:
        """Register hub XML tags with response_parser and tool_executor.

        Only registers the services if both are available on the
        event_bus.  Silently skips if not (e.g. during testing).
        """
        if not self.event_bus:
            return

        response_parser = self.event_bus.get_service("response_parser")
        tool_executor = self.event_bus.get_service("tool_executor")
        if not response_parser or not tool_executor:
            logger.debug("pipeline services not available, skipping tag registration")
            return

        import re as _re

        # --- hub_msg ---
        # Matches: <hub_msg to="x">msg</hub_msg>
        #          <hub_msg to="x" wait="true">msg</hub_msg>
        #          <hub_msg to="x" force="true">msg</hub_msg>
        #          <hub_msg to="x" thread="tid">msg</hub_msg>
        #          <hub_msg to="x" reply_to="mid">msg</hub_msg>
        #          <hub_msg to="x">msg  (unclosed)
        hub_msg_pat = _re.compile(
            r'<hub_msg\s+to="([^"]+)"'
            r'(?:\s+wait="([^"]*)")?'
            r'(?:\s+force="([^"]*)")?'
            r'(?:\s+thread="([^"]*)")?'
            r'(?:\s+reply_to="([^"]*)")?'
            r'\s*>(.*?)(?:</hub_msg>|$)',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_hub_msg(m):
            return {
                "target": m.group(1),
                "wait_attr": (m.group(2) or "").lower(),
                "force_attr": (m.group(3) or "").lower(),
                "thread_id": (m.group(4) or "").strip(),
                "reply_to": (m.group(5) or "").strip(),
                "content": m.group(6).strip(),
            }

        response_parser.register_plugin_tag(
            "hub_msg",
            hub_msg_pat,
            "hub_msg",
            _extract_hub_msg,
        )
        tool_executor.register_plugin_handler("hub_msg", self._handle_hub_msg_tool)

        # --- hub_reply ---
        # Shorthand for replying in-thread. The agent doesn't need to track
        # thread_id manually — the hub injects it from the incoming message context.
        # <hub_reply to="x">msg</hub_reply>
        # <hub_reply to="x" wait="true">msg</hub_reply>
        hub_reply_pat = _re.compile(
            r'<hub_reply\s+to="([^"]+)"'
            r'(?:\s+wait="([^"]*)")?'
            r'\s*>(.*?)(?:</hub_reply>|$)',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_hub_reply(m):
            return {
                "target": m.group(1),
                "wait_attr": (m.group(2) or "").lower(),
                "content": m.group(3).strip(),
                "_is_reply": True,  # flag: use active thread context
            }

        response_parser.register_plugin_tag(
            "hub_reply",
            hub_reply_pat,
            "hub_reply",
            _extract_hub_reply,
        )
        tool_executor.register_plugin_handler("hub_reply", self._handle_hub_msg_tool)

        # --- hub_broadcast ---
        # Matches: <hub_broadcast>msg</hub_broadcast>
        #          <hub_broadcast force="true">msg</hub_broadcast>
        bc_pat = _re.compile(
            r'<hub_broadcast(?:\s+force="([^"]*)")?\s*>(.*?)</hub_broadcast>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_hub_broadcast(m):
            return {
                "content": m.group(2).strip(),
                "force_attr": (m.group(1) or "").lower(),
            }

        response_parser.register_plugin_tag(
            "hub_broadcast", bc_pat, "hub_broadcast", _extract_hub_broadcast
        )
        tool_executor.register_plugin_handler(
            "hub_broadcast", self._handle_hub_broadcast_tool
        )

        # --- hub_stop ---
        # Supports both body syntax and attribute syntax:
        #   <hub_stop>lapis</hub_stop>
        #   <hub_stop identity="lapis" />
        #   <hub_stop identity="all" />
        stop_pat = _re.compile(
            r"<hub_stop"
            r'(?:\s+identity=["\']([^"\']+)["\'])?'
            r"\s*(?:/>|>(.*?)</hub_stop>)",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_hub_stop(m):
            attr = m.group(1)  # identity="..." attr
            body = m.group(2)  # body text
            target = (attr or (body.strip() if body else "") or "").strip()
            return {"target": target}

        response_parser.register_plugin_tag(
            "hub_stop", stop_pat, "hub_stop", _extract_hub_stop
        )
        tool_executor.register_plugin_handler("hub_stop", self._handle_hub_stop_tool)

        # --- hub_status ---
        # Matches: <hub_status />, <hub_status/>, <hub_status></hub_status>
        status_pat = _re.compile(
            r"<hub_status\s*/?>|<hub_status>\s*</hub_status>",
            _re.IGNORECASE,
        )

        def _extract_hub_status(m):
            return {}

        response_parser.register_plugin_tag(
            "hub_status", status_pat, "hub_status", _extract_hub_status
        )
        tool_executor.register_plugin_handler(
            "hub_status", self._handle_hub_status_tool
        )

        # --- hub_restart (self-restart with same identity) ---
        # Matches: <hub_restart />, <hub_restart/>, <hub_restart></hub_restart>
        restart_pat = _re.compile(
            r"<hub_restart\s*/?>|<hub_restart>\s*</hub_restart>",
            _re.IGNORECASE,
        )

        def _extract_hub_restart(m):
            return {}

        response_parser.register_plugin_tag(
            "hub_restart", restart_pat, "hub_restart", _extract_hub_restart
        )
        tool_executor.register_plugin_handler(
            "hub_restart", self._handle_hub_restart_tool
        )

        # --- scratchpad (overwrite) ---
        sp_pat = _re.compile(
            r"<scratchpad>(.*?)</scratchpad>", _re.DOTALL | _re.IGNORECASE
        )

        def _extract_scratchpad(m):
            return {"content": m.group(1).strip()}

        response_parser.register_plugin_tag(
            "scratchpad", sp_pat, "scratchpad", _extract_scratchpad
        )
        tool_executor.register_plugin_handler(
            "scratchpad", self._handle_scratchpad_tool
        )

        # --- scratchpad_append ---
        spa_pat = _re.compile(
            r"<scratchpad_append>(.*?)</scratchpad_append>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_scratchpad_append(m):
            return {"content": m.group(1).strip()}

        response_parser.register_plugin_tag(
            "scratchpad_append", spa_pat, "scratchpad_append", _extract_scratchpad_append
        )
        tool_executor.register_plugin_handler(
            "scratchpad_append", self._handle_scratchpad_append_tool
        )

        # --- scratchpad_clear ---
        spc_pat = _re.compile(r"<scratchpad_clear\s*/>", _re.IGNORECASE)

        def _extract_scratchpad_clear(m):
            return {}

        response_parser.register_plugin_tag(
            "scratchpad_clear", spc_pat, "scratchpad_clear", _extract_scratchpad_clear
        )
        tool_executor.register_plugin_handler(
            "scratchpad_clear", self._handle_scratchpad_clear_tool
        )

        # --- scratchpad_get ---
        spg_pat = _re.compile(r"<scratchpad_get\s*/>", _re.IGNORECASE)

        def _extract_scratchpad_get(m):
            return {}

        response_parser.register_plugin_tag(
            "scratchpad_get", spg_pat, "scratchpad_get", _extract_scratchpad_get
        )
        tool_executor.register_plugin_handler(
            "scratchpad_get", self._handle_scratchpad_get_tool
        )

        # --- state_update ---
        su_pat = _re.compile(
            r"<state_update>(.*?)</state_update>", _re.DOTALL | _re.IGNORECASE
        )

        def _extract_state_update(m):
            return {"state": m.group(1).strip()}

        response_parser.register_plugin_tag(
            "state_update", su_pat, "state_update", _extract_state_update
        )
        tool_executor.register_plugin_handler(
            "state_update", self._handle_state_update_tool
        )

        # --- task_checkpoint ---
        tcp_pat = _re.compile(
            r'<task_checkpoint\s+id="([^"]+)">(.*?)</task_checkpoint>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_task_checkpoint(m):
            return {"task_id": m.group(1).strip(), "note": m.group(2).strip()}

        response_parser.register_plugin_tag(
            "task_checkpoint", tcp_pat, "task_checkpoint", _extract_task_checkpoint
        )
        tool_executor.register_plugin_handler(
            "task_checkpoint", self._handle_task_checkpoint_tool
        )

        # --- task_complete ---
        # Mixed form: id attribute + body (matches system prompt format)
        tcomp_pat = _re.compile(
            r'<task_complete\s+id="([^"]+)">(.*?)</task_complete>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_task_complete(m):
            return {"task_id": m.group(1).strip(), "result": m.group(2).strip()}

        response_parser.register_plugin_tag(
            "task_complete", tcomp_pat, "task_complete", _extract_task_complete
        )
        tool_executor.register_plugin_handler(
            "task_complete", self._handle_task_complete_tool
        )

        # --- task_approve ---
        # Mixed form: id attribute + body (matches system prompt format)
        ta_pat = _re.compile(
            r'<task_approve\s+id="([^"]+)">(.*?)</task_approve>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_task_approve(m):
            return {"task_id": m.group(1).strip(), "notes": m.group(2).strip()}

        response_parser.register_plugin_tag(
            "task_approve", ta_pat, "task_approve", _extract_task_approve
        )
        tool_executor.register_plugin_handler(
            "task_approve", self._handle_task_approve_tool
        )

        # --- task_reject ---
        # Mixed form: id attribute + body (matches system prompt format)
        tr_pat = _re.compile(
            r'<task_reject\s+id="([^"]+)">(.*?)</task_reject>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_task_reject(m):
            return {"task_id": m.group(1).strip(), "reason": m.group(2).strip()}

        response_parser.register_plugin_tag(
            "task_reject", tr_pat, "task_reject", _extract_task_reject
        )
        tool_executor.register_plugin_handler(
            "task_reject", self._handle_task_reject_tool
        )

        # --- Change feed tags ---

        # lane_claim (optional task attribute)
        def _extract_lane_claim(m):
            return {"task_desc": m.group(1) or "", "path": m.group(2).strip()}

        lc_pat = _re.compile(
            r'<lane_claim(?:\s+task="([^"]*)")?\s*>(.*?)</lane_claim>',
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "lane_claim", lc_pat, "lane_claim", _extract_lane_claim
        )
        tool_executor.register_plugin_handler(
            "lane_claim", self._handle_lane_claim_tool
        )

        # lane_release
        def _extract_lane_release(m):
            return {"path": m.group(1).strip()}

        lr_pat = _re.compile(
            r"<lane_release>(.*?)</lane_release>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "lane_release", lr_pat, "lane_release", _extract_lane_release
        )
        tool_executor.register_plugin_handler(
            "lane_release", self._handle_lane_release_tool
        )

        # file_changed
        def _extract_file_changed(m):
            return {"path": m.group(1).strip()}

        fc_pat = _re.compile(
            r"<file_changed>(.*?)</file_changed>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "file_changed", fc_pat, "file_changed", _extract_file_changed
        )
        tool_executor.register_plugin_handler(
            "file_changed", self._handle_file_changed_tool
        )

        # file_watch
        def _extract_file_watch(m):
            return {"pattern": m.group(1).strip()}

        fw_pat = _re.compile(
            r"<file_watch>(.*?)</file_watch>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "file_watch", fw_pat, "file_watch", _extract_file_watch
        )
        tool_executor.register_plugin_handler(
            "file_watch", self._handle_file_watch_tool
        )

        # file_unwatch
        def _extract_file_unwatch(m):
            return {"pattern": m.group(1).strip()}

        fuw_pat = _re.compile(
            r"<file_unwatch>(.*?)</file_unwatch>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "file_unwatch", fuw_pat, "file_unwatch", _extract_file_unwatch
        )
        tool_executor.register_plugin_handler(
            "file_unwatch", self._handle_file_unwatch_tool
        )

        # feed_recent (self-closing with limit attribute)
        def _extract_feed_recent(m):
            return {"limit": int(m.group(1))}

        fr_pat = _re.compile(
            r'<feed_recent\s+limit="(\d+)"\s*/>',
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "feed_recent", fr_pat, "feed_recent", _extract_feed_recent
        )
        tool_executor.register_plugin_handler(
            "feed_recent", self._handle_feed_recent_tool
        )

        # feed_file (self-closing with path attribute)
        def _extract_feed_file(m):
            return {"path": m.group(1)}

        ff_pat = _re.compile(
            r'<feed_file\s+path="([^"]+)"\s*/>',
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "feed_file", ff_pat, "feed_file", _extract_feed_file
        )
        tool_executor.register_plugin_handler(
            "feed_file", self._handle_feed_file_tool
        )

        # claims (self-closing, optional identity attribute)
        def _extract_claims(m):
            return {"target_identity": m.group(1) or ""}

        cl_pat = _re.compile(
            r'<claims(?:\s+identity="([^"]*)")?\s*/>',
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "claims", cl_pat, "claims", _extract_claims
        )
        tool_executor.register_plugin_handler(
            "claims", self._handle_claims_tool
        )

        # --- hub_agents (self-closing) ---
        def _extract_hub_agents(m):
            return {}

        agents_pat = _re.compile(
            r"<hub_agents\s*/?>|<hub_agents>\s*</hub_agents>",
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_agents", agents_pat, "hub_agents", _extract_hub_agents
        )
        tool_executor.register_plugin_handler(
            "hub_agents", self._handle_hub_agents_tool
        )

        # --- Agent operations tags ---

        # hub_spawn
        # Supports three spawn modes:
        #   <hub_spawn name="lapis">task</hub_spawn>
        #     → by identity: uses pool's agent_type for lapis
        #   <hub_spawn name="coder">task</hub_spawn>
        #     → by agent_type: picks next available gem with agent_type=coder
        #   <hub_spawn name="lapis" type="research">task</hub_spawn>
        #     → explicit identity + type override
        def _extract_hub_spawn(m):
            return {
                "name": m.group(1).strip(),
                "agent_type_override": (m.group(2) or "").strip(),
                "task": m.group(3).strip(),
            }

        spawn_pat = _re.compile(
            r'<hub_spawn\s+name="([^"]+)"'
            r'(?:\s+type="([^"]*)")?'
            r">(.*?)</hub_spawn>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_spawn", spawn_pat, "hub_spawn", _extract_hub_spawn
        )
        tool_executor.register_plugin_handler(
            "hub_spawn", self._handle_hub_spawn_tool
        )

        # hub_queue
        def _extract_hub_queue(m):
            return {"task": m.group(1).strip()}

        queue_pat = _re.compile(
            r"<hub_queue>(.*?)</hub_queue>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_queue", queue_pat, "hub_queue", _extract_hub_queue
        )
        tool_executor.register_plugin_handler(
            "hub_queue", self._handle_hub_queue_tool
        )

        # hub_claim (self-closing, optional id attribute)
        def _extract_hub_claim(m):
            return {"slot_id": m.group(1) or ""}

        claim_pat = _re.compile(
            r'<hub_claim(?:\s+id="([^"]*)")?\s*/>',
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_claim", claim_pat, "hub_claim", _extract_hub_claim
        )
        tool_executor.register_plugin_handler(
            "hub_claim", self._handle_hub_claim_tool
        )

        # hub_work (self-closing)
        def _extract_hub_work(m):
            return {}

        work_pat = _re.compile(
            r"<hub_work\s*/>",
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_work", work_pat, "hub_work", _extract_hub_work
        )
        tool_executor.register_plugin_handler(
            "hub_work", self._handle_hub_work_tool
        )

        # hub_vault (self-closing, optional name attribute)
        def _extract_hub_vault(m):
            return {"vault_name": m.group(1) or ""}

        vault_pat = _re.compile(
            r'<hub_vault(?:\s+name="([^"]*)")?\s*/>',
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_vault", vault_pat, "hub_vault", _extract_hub_vault
        )
        tool_executor.register_plugin_handler(
            "hub_vault", self._handle_hub_vault_tool
        )

        # hub_vaults (self-closing)
        def _extract_hub_vaults(m):
            return {}

        vaults_pat = _re.compile(
            r"<hub_vaults\s*/>",
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_vaults", vaults_pat, "hub_vaults", _extract_hub_vaults
        )
        tool_executor.register_plugin_handler(
            "hub_vaults", self._handle_hub_vaults_tool
        )

        # hub_cron_add (flexible attribute order)
        def _extract_hub_cron_add(m):
            attrs = m.group(1)
            msg = m.group(2).strip()
            i_match = _re.search(r'interval="([^"]+)"', attrs)
            return {
                "interval": i_match.group(1).strip() if i_match else "",
                "message": msg,
            }

        cron_add_pat = _re.compile(
            r"<hub_cron_add\s+([^>]+)>(.*?)</hub_cron_add>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_cron_add", cron_add_pat, "hub_cron_add", _extract_hub_cron_add
        )
        tool_executor.register_plugin_handler(
            "hub_cron_add", self._handle_hub_cron_add_tool
        )

        # hub_cron_list (self-closing)
        def _extract_hub_cron_list(m):
            return {}

        cron_list_pat = _re.compile(
            r"<hub_cron_list\s*/>",
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_cron_list", cron_list_pat, "hub_cron_list", _extract_hub_cron_list
        )
        tool_executor.register_plugin_handler(
            "hub_cron_list", self._handle_hub_cron_list_tool
        )

        # hub_cron_delete
        def _extract_hub_cron_delete(m):
            return {"job_id": m.group(1).strip()}

        cron_del_pat = _re.compile(
            r"<hub_cron_delete>(.*?)</hub_cron_delete>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_cron_delete", cron_del_pat, "hub_cron_delete", _extract_hub_cron_delete
        )
        tool_executor.register_plugin_handler(
            "hub_cron_delete", self._handle_hub_cron_delete_tool
        )

        # hub_capture (self-closing, optional lines attribute)
        def _extract_hub_capture(m):
            return {"cap_name": m.group(1).strip(), "cap_lines": m.group(2) or "50"}

        cap_pat = _re.compile(
            r'<hub_capture\s+name="([^"]+)"(?:\s+lines="(\d+)")?\s*/>',
            _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "hub_capture", cap_pat, "hub_capture", _extract_hub_capture
        )
        tool_executor.register_plugin_handler(
            "hub_capture", self._handle_hub_capture_tool
        )

        # --- vault_write ---
        # Matches: <vault_write>insight</vault_write>
        #          <vault_write keywords="a,b,c">insight</vault_write>
        vw_pat = _re.compile(
            r'<vault_write(?:\s+keywords="([^"]*)")?\s*>'
            r"(.*?)</vault_write>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_vault_write(m):
            keywords_str = m.group(1) or ""
            manual_kw = (
                [k.strip() for k in keywords_str.split(",") if k.strip()]
                if keywords_str
                else []
            )
            return {
                "content": m.group(2).strip(),
                "manual_keywords": manual_kw,
            }

        response_parser.register_plugin_tag(
            "vault_write", vw_pat, "vault_write", _extract_vault_write
        )
        tool_executor.register_plugin_handler(
            "vault_write", self._handle_vault_write_tool
        )

        # --- global_vault_write ---
        # Writes to the cross-project global crystal store.
        # Use sparingly: personality, general skills, identity-level insights.
        # Matches: <global_vault_write>insight</global_vault_write>
        #          <global_vault_write keywords="a,b">insight</global_vault_write>
        gvw_pat = _re.compile(
            r'<global_vault_write(?:\s+keywords="([^"]*)")?\s*>'
            r"(.*?)</global_vault_write>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_global_vault_write(m):
            keywords_str = m.group(1) or ""
            manual_kw = (
                [k.strip() for k in keywords_str.split(",") if k.strip()]
                if keywords_str
                else []
            )
            return {
                "content": m.group(2).strip(),
                "manual_keywords": manual_kw,
            }

        response_parser.register_plugin_tag(
            "global_vault_write",
            gvw_pat,
            "global_vault_write",
            _extract_global_vault_write,
        )
        tool_executor.register_plugin_handler(
            "global_vault_write", self._handle_global_vault_write_tool
        )

        # --- crystal_search ---
        cs_pat = _re.compile(
            r'<crystal_search\s+query="([^"]*)"(?:\s+limit="(\d+)")?\s*/>'
        )

        def _extract_crystal_search(m):
            return {
                "query": m.group(1),
                "limit": int(m.group(2)) if m.group(2) else 5,
            }

        response_parser.register_plugin_tag(
            "crystal_search", cs_pat, "crystal_search", _extract_crystal_search
        )
        tool_executor.register_plugin_handler(
            "crystal_search", self._handle_crystal_search_tool
        )

        # --- crystal_read ---
        cr_pat = _re.compile(r'<crystal_read\s+(?:entry_)?id="([^"]+)"\s*/>')

        def _extract_crystal_read(m):
            return {"entry_id": m.group(1)}

        response_parser.register_plugin_tag(
            "crystal_read", cr_pat, "crystal_read", _extract_crystal_read
        )
        tool_executor.register_plugin_handler(
            "crystal_read", self._handle_crystal_read_tool
        )

        # --- crystal_list ---
        cl_pat = _re.compile(
            r'<crystal_list(?:\s+limit="(\d+)")?(?:\s+offset="(\d+)")?\s*/>'
        )

        def _extract_crystal_list(m):
            return {
                "limit": int(m.group(1)) if m.group(1) else 20,
                "offset": int(m.group(2)) if m.group(2) else 0,
            }

        response_parser.register_plugin_tag(
            "crystal_list", cl_pat, "crystal_list", _extract_crystal_list
        )
        tool_executor.register_plugin_handler(
            "crystal_list", self._handle_crystal_list_tool
        )

        # --- crystal_edit ---
        # Attributes may appear in any order; extract them with secondary regexes
        # rather than positional groups so reversed attrs don't cause a miss.
        ce_pat = _re.compile(
            r"<crystal_edit\b([^>]*?)>(.*?)</crystal_edit>",
            _re.DOTALL | _re.IGNORECASE,
        )
        _ce_id_re = _re.compile(r'(?:entry_)?id="([^"]+)"', _re.IGNORECASE)
        _ce_summary_re = _re.compile(r'summary="([^"]*)"', _re.IGNORECASE)
        _ce_keywords_re = _re.compile(r'keywords="([^"]*)"', _re.IGNORECASE)

        def _extract_crystal_edit(m):
            attrs = m.group(1)
            id_match = _ce_id_re.search(attrs)
            summary_match = _ce_summary_re.search(attrs)
            keywords_match = _ce_keywords_re.search(attrs)
            keywords_raw = keywords_match.group(1) if keywords_match else None
            if keywords_raw is None:
                keywords = None
            elif keywords_raw == "":
                keywords = []
            else:
                keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
            return {
                "entry_id": id_match.group(1) if id_match else "",
                "content": m.group(2).strip(),
                "summary": summary_match.group(1) if summary_match else None,
                "keywords": keywords,
            }

        response_parser.register_plugin_tag(
            "crystal_edit", ce_pat, "crystal_edit", _extract_crystal_edit
        )
        tool_executor.register_plugin_handler(
            "crystal_edit", self._handle_crystal_edit_tool
        )

        # --- crystal_delete ---
        # Attributes may appear in any order (id/entry_id, reason are both optional
        # positionally). Use a lookahead-based approach to match the tag regardless
        # of attribute order so the regex fires even when the LLM writes reason first.
        cd_pat = _re.compile(
            r"<crystal_delete\b([^>]*?)/>",
            _re.DOTALL,
        )
        _cd_id_re = _re.compile(r'(?:entry_)?id="([^"]+)"')
        _cd_reason_re = _re.compile(r'reason="([^"]*)"')

        def _extract_crystal_delete(m):
            attrs = m.group(1)
            id_match = _cd_id_re.search(attrs)
            reason_match = _cd_reason_re.search(attrs)
            return {
                "entry_id": id_match.group(1) if id_match else "",
                "reason": reason_match.group(1) if reason_match else "",
            }

        response_parser.register_plugin_tag(
            "crystal_delete", cd_pat, "crystal_delete", _extract_crystal_delete
        )
        tool_executor.register_plugin_handler(
            "crystal_delete", self._handle_crystal_delete_tool
        )

        # --- Context service: curate ---
        curate_pat = _re.compile(
            r'<curate\s+id="([^"]+)"\s+decision="(keep|summary)"\s*>'
            r"(.*?)</curate>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_curate(m):
            return {
                "ctx_id": m.group(1).strip(),
                "decision": m.group(2),
                "body": m.group(3).strip(),
            }

        response_parser.register_plugin_tag(
            "curate", curate_pat, "curate", _extract_curate
        )
        tool_executor.register_plugin_handler(
            "curate", self._handle_curate_tool
        )

        # --- Context service: context query (body form) ---
        # xml_form="body", xml_tag="context_query"
        context_pat = _re.compile(
            r'<context_query>(.*?)</context_query>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_context(m):
            return {"query": m.group(1).strip()}

        response_parser.register_plugin_tag(
            "context_query", context_pat, "context_query", _extract_context
        )
        tool_executor.register_plugin_handler(
            "context_query", self._handle_context_query_tool
        )

        # --- Context service: evict ---
        evict_pat = _re.compile(
            r'<evict(?:\s+id="([^"]+)"\s*>|>)(.*?)</evict>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_evict(m):
            # Form 1: <evict id="ctx_id">reason</evict>
            # Form 2: <evict>identifier</evict> (body-only, no attribute)
            attr_id = m.group(1)  # None for body-only form
            body = m.group(2).strip() if m.group(2) else ""
            if attr_id:
                return {"ctx_id": attr_id.strip(), "reason": body}
            return {"ctx_id": body, "reason": ""}

        response_parser.register_plugin_tag(
            "evict", evict_pat, "evict", _extract_evict
        )
        tool_executor.register_plugin_handler(
            "evict", self._handle_evict_tool
        )

        # --- wait_for_user ---
        # Matches:
        #   <wait_for_user/>
        #   <wait_for_user>reason</wait_for_user>
        #   <wait_for_user message="reason" />
        def _extract_wait_for_user(m):
            # Group 1: message attribute (from <wait_for_user message="..."/>)
            # Group 2: body text (from <wait_for_user>text</wait_for_user>)
            attr_msg = m.group(1)
            body_msg = m.group(2)
            reason = attr_msg or body_msg or ""
            return {"reason": reason.strip()}

        wait_pat = _re.compile(
            r'<wait_for_user(?:\s+message="([^"]*)")?\s*/>'
            r"|<wait_for_user>(.*?)</wait_for_user>",
            _re.DOTALL | _re.IGNORECASE,
        )
        response_parser.register_plugin_tag(
            "wait_for_user", wait_pat, "wait_for_user", _extract_wait_for_user
        )
        tool_executor.register_plugin_handler(
            "wait_for_user", self._handle_wait_for_user_tool
        )

        # --- hub_ask_ctx ---
        # <hub_ask_ctx peer="lapis" />
        # <hub_ask_ctx peer="lapis" filter="file:kollabor/" />
        ask_ctx_pat = _re.compile(
            r'<hub_ask_ctx\s+peer="([^"]+)"'
            r'(?:\s+filter="([^"]*)")?'
            r"\s*/>",
            _re.IGNORECASE,
        )

        def _extract_hub_ask_ctx(m):
            return {
                "peer": m.group(1),
                "filter": (m.group(2) or "").strip(),
            }

        response_parser.register_plugin_tag(
            "hub_ask_ctx", ask_ctx_pat, "hub_ask_ctx", _extract_hub_ask_ctx
        )
        tool_executor.register_plugin_handler(
            "hub_ask_ctx", self._handle_hub_ask_ctx_tool
        )

        logger.info(
            "Registered 44 hub pipeline tags "
            "(hub_msg, hub_broadcast, hub_stop, hub_status, "
            "scratchpad*, state_update, task_*, change feed, "
            "agent ops, vault_write, global_vault_write, crystal_*, "
            "curate, context_query, evict, wait_for_user, hub_ask_ctx)"
        )

    async def _handle_curate_tool(self, tool_data: dict[str, Any]):
        """Handle <curate> tag — record agent's decision on a ledger entry."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        ctx_id = tool_data.get("ctx_id", "")
        decision = tool_data.get("decision", "")
        body = tool_data.get("body", "")

        context_svc = self._get_context_service()
        if context_svc is None:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="curate",
                success=False,
                error="context service not available",
            )

        if context_svc.set_decision(ctx_id, decision, body):
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="curate",
                success=True,
                output=(
                    f"[curate] {ctx_id} -> {decision} "
                    f"({len(body)} bytes recorded)"
                ),
            )
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="curate",
            success=False,
            error=f"{ctx_id} not found or invalid decision",
        )

    async def _handle_context_query_tool(self, tool_data: dict[str, Any]):
        """Handle <context/> tag — request ledger snapshot."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        filter_spec = tool_data.get("filter", "") or None

        context_svc = self._get_context_service()
        if context_svc is None:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="context_query",
                success=False,
                error="context service not available",
            )

        context_svc.request_context_snapshot(filter_spec=filter_spec)

        msg = "[context] snapshot requested"
        if filter_spec:
            msg += f" (filter: {filter_spec})"
        msg += " — will appear in next request"

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="context_query",
            success=True,
            output=msg,
        )

    async def _handle_evict_tool(self, tool_data: dict[str, Any]):
        """Handle <evict> tag — evict a ledger entry from history."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        ctx_id = tool_data.get("ctx_id", "")
        reason = tool_data.get("reason", "")

        context_svc = self._get_context_service()
        if context_svc is None:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="evict",
                success=False,
                error="context service not available",
            )

        if context_svc.evict(ctx_id, reason):
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="evict",
                success=True,
                output=(
                    f"[evict] {ctx_id} evicted from history "
                    f"(cache broken from this message forward)"
                ),
            )
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="evict",
            success=False,
            error=f"{ctx_id} not found",
        )

    async def _handle_hub_ask_ctx_tool(self, tool_data: dict[str, Any]):
        """Handle <hub_ask_ctx/> tag — query a peer's context summary."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        peer = tool_data.get("peer", "").strip()
        filter_str = tool_data.get("filter", "").strip() or None

        if not peer:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_ask_ctx",
                success=False,
                error="missing peer attribute",
            )

        context_svc = self._get_context_service()
        bridge = (
            context_svc.get_hub_bridge()
            if context_svc and hasattr(context_svc, "get_hub_bridge")
            else None
        )
        if bridge is None:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_ask_ctx",
                success=False,
                error="hub bridge not enabled (plugins.context_service.hub_broadcast_enabled)",
            )

        summary = bridge.handle_hub_ask_ctx(peer, filter_str)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_ask_ctx",
            success=True,
            output=summary,
        )

    async def _broadcast_context_ledger_update(
        self, payload: Dict[str, Any]
    ) -> None:
        """Control-plane broadcaster for context ledger updates.

        Uses action="context_ledger_update" so peers dispatch it
        without rendering as user-visible chat or vault stream entries.
        """
        if not self._presence or not self._identity:
            return
        msg = HubMessage(
            action="context_ledger_update",
            from_agent=self._identity.agent_id,
            from_identity=self._identity.identity,
            to="*",
            content="",
            scope=MessageScope.BROADCAST.value,
            metadata=payload,
        )
        try:
            agents = await self._presence.discover_agents_async()
        except Exception as e:
            logger.debug("discover_agents failed for ctx broadcast: %s", e)
            return
        my_id = self._identity.agent_id
        for agent in agents:
            if agent.agent_id == my_id:
                continue
            try:
                await self._deliver_to_agent(agent, msg)
            except Exception as e:
                logger.debug(
                    "ctx broadcast deliver to %s failed: %s",
                    agent.identity,
                    e,
                )

    def _wire_context_hub_bridge(self) -> None:
        """Instantiate HubBridge and attach it to the context service.

        No-op when plugins.context_service.hub_broadcast_enabled is
        false or the context service isn't available.
        """
        if self.config is None:
            return
        enabled = bool(
            self.config.get(
                "plugins.context_service.hub_broadcast_enabled", True
            )
        )
        if not enabled:
            return

        context_svc = self._get_context_service()
        if context_svc is None or not hasattr(context_svc, "set_hub_bridge"):
            return

        try:
            from kollabor_ai.context_service.hub_bridge import HubBridge
        except ImportError as e:
            logger.debug("HubBridge import failed: %s", e)
            return

        from .presence_states import PresenceState

        def _is_waiting() -> bool:
            return bool(
                self._identity
                and getattr(self._identity, "state", "")
                == PresenceState.WAITING.value
            )

        bridge = HubBridge(
            identity=(
                self._identity.identity if self._identity else "unknown"
            ),
            context_service=context_svc,
            broadcaster=self._broadcast_context_ledger_update,
            is_waiting=_is_waiting,
        )
        context_svc.set_hub_bridge(bridge)
        logger.info("Context hub bridge wired")

    async def _handle_wait_for_user_tool(self, tool_data: dict[str, Any]):
        """Handle <wait_for_user/> tag — put agent into waiting state."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        reason = tool_data.get("reason", "") or tool_data.get("message", "")
        await self._enter_waiting_state(reason or None)

        output = "[wait_for_user] parked"
        if reason:
            output += f" — {reason}"
        output += ". cooldown: 60s."

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="wait_for_user",
            success=True,
            output=output,
        )

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

        # Metrics
        self._loop_metrics["waiting_state_entries"] += 1

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
        if self._notifier and self._notifier._backend:
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

        # Metrics: record waiting duration
        self._loop_metrics["waiting_state_exits"] += 1
        if waiting_since:
            duration = time.time() - waiting_since
            self._waiting_durations.append(duration)
            # Keep only last 100 durations
            if len(self._waiting_durations) > 100:
                self._waiting_durations = self._waiting_durations[-100:]

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
                logger.debug("inject_system_message for wake failed: %s", e)

    def _get_context_service(self):
        """Get the context service from the event bus."""
        if self.event_bus is None:
            return None
        return self.event_bus.get_service("context_service")

    async def _handle_vault_write_tool(self, tool_data: dict[str, Any]):
        """Execute a vault_write tool -- save insight to crystal store."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        content = tool_data.get("content", "").strip()
        manual_keywords = tool_data.get("manual_keywords", [])

        if not content:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="vault_write",
                success=True,
                output="empty content, nothing saved",
            )

        if not self._crystal_store:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="vault_write",
                success=True,
                output="vault not initialized",
            )

        try:
            entry = self._crystal_store.add_entry(
                content, manual_keywords=manual_keywords
            )
            # Also log to stream
            if self._vault and self._identity:
                self._vault.append_stream(
                    "vault_write",
                    f"saved crystal entry {entry.id}: {entry.summary[:80]}",
                    from_agent=self._identity.identity,
                )
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="vault_write",
                success=True,
                output=f"saved as {entry.id}: {entry.summary[:80]}",
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="vault_write",
                success=False,
                output=f"vault_write error: {e}",
            )

    async def _handle_global_vault_write_tool(self, tool_data: dict[str, Any]):
        """Execute a global_vault_write tool -- save shared insight."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        content = tool_data.get("content", "").strip()
        manual_keywords = tool_data.get("manual_keywords", [])

        if not content:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="global_vault_write",
                success=True,
                output="empty content, nothing saved",
            )

        if not self._global_crystal_store:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="global_vault_write",
                success=True,
                output="vault not initialized",
            )

        try:
            entry = self._global_crystal_store.add_entry(
                content, manual_keywords=manual_keywords
            )
            if self._vault and self._identity:
                self._vault.append_stream(
                    "global_vault_write",
                    f"saved shared crystal {entry.id}: {entry.summary[:80]}",
                    from_agent=self._identity.identity,
                )
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="global_vault_write",
                success=True,
                output=f"saved to shared vault as {entry.id}: {entry.summary[:80]}",
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="global_vault_write",
                success=False,
                output=f"global_vault_write error: {e}",
            )

    async def _handle_crystal_search_tool(self, tool_data: dict[str, Any]):
        """Execute a crystal_search tool -- keyword search crystal entries."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._crystal_store:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_search",
                success=True,
                output="vault not initialized",
            )

        try:
            query = tool_data.get("query", "").strip()
            if not query:
                return ToolExecutionResult(
                    tool_id=tool_data.get("id", "unknown"),
                    tool_type="crystal_search",
                    success=True,
                    output="empty query",
                )

            limit = min(tool_data.get("limit", 5), 10)
            results = self._crystal_store.find_by_keywords(
                query.split(), top_k=limit
            )

            if not results:
                output = f"no matches for '{query}'"
            else:
                lines = [f"search results for '{query}' ({len(results)} matches):"]
                for entry in results:
                    lines.append(f"  {entry.summary_line()}")
                lines.append("use crystal_read to see full entry body.")
                output = "\n".join(lines)

            # Context limit: 2000 chars
            if len(output) > 2000:
                lines = output.split("\n")
                total_matches = len(results)
                truncated = []
                char_count = 0
                for line in lines:
                    if char_count + len(line) + 1 > 1950:
                        break
                    truncated.append(line)
                    char_count += len(line) + 1
                shown = len(truncated) - 2  # subtract header + footer
                remaining = total_matches - max(shown, 0)
                if remaining > 0:
                    truncated.append(
                        f"{remaining} more matches, narrow your query."
                    )
                else:
                    truncated.append("use crystal_read to see full entry body.")
                output = "\n".join(truncated)

            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_search",
                success=True,
                output=output,
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_search",
                success=False,
                output=f"crystal_search error: {e}",
            )

    async def _handle_crystal_read_tool(self, tool_data: dict[str, Any]):
        """Execute a crystal_read tool -- read full entry by ID."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._crystal_store:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_read",
                success=True,
                output="vault not initialized",
            )

        try:
            entry_id = normalize_crystal_id(
                _safe_semantic_id(tool_data, ["entry_id", "target_id", "crystal_id"])
            )
            entry = self._crystal_store.get_by_id(entry_id)
            tier = "project"
            # Fall back to global store so agents can read cross-project entries
            if not entry and self._global_crystal_store:
                entry = self._global_crystal_store.get_by_id(entry_id)
                tier = "global"

            if not entry:
                return ToolExecutionResult(
                    tool_id=tool_data.get("id", "unknown"),
                    tool_type="crystal_read",
                    success=True,
                    output=f"no crystal entry '{entry_id}' (searched project + global)",
                )

            lines = [
                f"[{entry.id}] {entry.summary}  [{tier}]",
                f"date: {entry.date}",
                f"keywords: {', '.join(entry.keywords)}",
                "---",
                entry.body,
            ]
            output = "\n".join(lines)

            # Context limit: 3000 chars
            if len(output) > 3000:
                body = entry.body
                truncation_note = f"[truncated, {len(body)} chars total]"
                header = (
                    f"[{entry.id}] {entry.summary}\n"
                    f"date: {entry.date}\n"
                    f"keywords: {', '.join(entry.keywords)}\n"
                    "---\n"
                )
                available = 3000 - len(header) - len(truncation_note) - 1
                output = header + body[:available] + "\n" + truncation_note

            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_read",
                success=True,
                output=output,
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_read",
                success=False,
                output=f"crystal_read error: {e}",
            )

    async def _handle_crystal_list_tool(self, tool_data: dict[str, Any]):
        """Execute a crystal_list tool -- list all crystal entries."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._crystal_store:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_list",
                success=True,
                output="vault not initialized",
            )

        try:
            limit = min(tool_data.get("limit", 20), 50)
            offset = tool_data.get("offset", 0)

            # Combine project + global entries with tier label.
            # Project entries first (most relevant to current work).
            project_entries = self._crystal_store.get_all()
            global_entries = (
                self._global_crystal_store.get_all()
                if self._global_crystal_store
                else []
            )
            tagged = [(e, "project") for e in project_entries] + [
                (e, "global") for e in global_entries
            ]

            if not tagged:
                return ToolExecutionResult(
                    tool_id=tool_data.get("id", "unknown"),
                    tool_type="crystal_list",
                    success=True,
                    output="no crystal entries",
                )

            total = len(tagged)
            page = tagged[offset : offset + limit]
            start = offset + 1
            end = offset + len(page)

            lines = [
                f"crystal entries ({len(project_entries)} project, "
                f"{len(global_entries)} global, showing {start}-{end}):"
            ]
            for entry, tier in page:
                lines.append(f"  [{tier}] {entry.summary_line()}")

            next_offset = offset + limit
            if next_offset < total:
                lines.append(
                    f'use crystal_list with offset="{next_offset}" for next page.'
                )

            output = "\n".join(lines)

            # Context limit: 3000 chars
            if len(output) > 3000:
                lines = output.split("\n")
                truncated = [lines[0]]
                char_count = len(lines[0]) + 1
                for line in lines[1:]:
                    if char_count + len(line) + 1 > 2950:
                        break
                    truncated.append(line)
                    char_count += len(line) + 1
                output = "\n".join(truncated)

            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_list",
                success=True,
                output=output,
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_list",
                success=False,
                output=f"crystal_list error: {e}",
            )

    async def _handle_crystal_edit_tool(self, tool_data: dict[str, Any]):
        """Execute a crystal_edit tool -- update an existing entry."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._crystal_store:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_edit",
                success=True,
                output="vault not initialized",
            )

        try:
            entry_id = normalize_crystal_id(
                _safe_semantic_id(tool_data, ["entry_id", "target_id", "crystal_id"])
            )
            body = tool_data.get("content", tool_data.get("body", "")).strip()
            if not body:
                return ToolExecutionResult(
                    tool_id=tool_data.get("id", "unknown"),
                    tool_type="crystal_edit",
                    success=True,
                    output="empty body",
                )

            summary = tool_data.get("summary")
            keywords = tool_data.get("keywords")
            entry = self._crystal_store.update_entry(
                entry_id, body, summary=summary, keywords=keywords
            )

            if not entry:
                return ToolExecutionResult(
                    tool_id=tool_data.get("id", "unknown"),
                    tool_type="crystal_edit",
                    success=True,
                    output=f"no crystal entry '{entry_id}'",
                )

            output = (
                f"updated {entry.id}: {entry.summary}\n"
                f"keywords: {', '.join(entry.keywords)} ({len(entry.keywords)})\n"
                f"body: {len(entry.body)} chars"
            )

            # Context limit: 500 chars
            if len(output) > 500:
                output = output[:497] + "..."

            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_edit",
                success=True,
                output=output,
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_edit",
                success=False,
                output=f"crystal_edit error: {e}",
            )

    async def _handle_crystal_delete_tool(self, tool_data: dict[str, Any]):
        """Execute a crystal_delete tool -- remove a crystal entry."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._crystal_store:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_delete",
                success=True,
                output="vault not initialized",
            )

        try:
            entry_id = normalize_crystal_id(
                _safe_semantic_id(tool_data, ["entry_id", "target_id", "crystal_id"])
            )
            removed = self._crystal_store.delete_entry(entry_id)

            if not removed:
                return ToolExecutionResult(
                    tool_id=tool_data.get("id", "unknown"),
                    tool_type="crystal_delete",
                    success=True,
                    output=f"no crystal entry '{entry_id}'",
                )

            # Log deletion to vault stream for audit trail
            if self._vault and self._identity:
                self._vault.append_stream(
                    "crystal_delete",
                    f"deleted {removed.id}: {removed.summary[:80]}",
                    from_agent=self._identity.identity,
                )

            output = f"deleted {removed.id}: {removed.summary}"

            # Context limit: 500 chars
            if len(output) > 500:
                output = output[:497] + "..."

            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_delete",
                success=True,
                output=output,
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="crystal_delete",
                success=False,
                output=f"crystal_delete error: {e}",
            )


    async def _handle_hub_msg_tool(self, tool_data: dict):
        """Execute a hub_msg tool extracted by the pipeline."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        target = tool_data.get("to", tool_data.get("target", ""))
        wait_attr = tool_data.get("wait", tool_data.get("wait_attr", ""))
        force_attr = tool_data.get("force", tool_data.get("force_attr", ""))
        content = tool_data.get("message", tool_data.get("content", ""))

        if not content:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_msg",
                success=True,
                output="",
            )

        # Auto-detect idle chatter
        any_wait = wait_attr in ("true", "yes", "1")
        if not any_wait:
            content_lower = content.lower().strip().rstrip(".")
            idle_phrases = (
                "standing by",
                "waiting for",
                "going quiet",
                "staying quiet",
            )
            if any(phrase in content_lower for phrase in idle_phrases):
                any_wait = True
                logger.info(
                    f"Auto-wait: idle chatter in hub_msg to {target}: "
                    f"{content[:60]!r}"
                )

        # Self-message guard
        my_ident = self._identity.identity if self._identity else ""
        if target == my_ident:
            coord = self._get_coordinator_identity()
            target = coord if coord else "coordinator"

        # Reject unrendered template syntax
        if "{" in target:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_msg",
                success=False,
                error=f"invalid target '{target}'. use a real agent identity name.",
            )

        # Dedup
        dedup_window = 120
        msg_hash = hashlib.md5(f"{target}:{content}".encode()).hexdigest()
        now = time.time()
        self._recent_hub_msgs = {
            k: v
            for k, v in self._recent_hub_msgs.items()
            if now - v < dedup_window
        }
        if msg_hash in self._recent_hub_msgs:
            logger.debug(f"hub_msg dedup: skipping duplicate to {target}")
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_msg",
                success=True,
                output="",  # silent -- prevents continuation loops
            )
        self._recent_hub_msgs[msg_hash] = now

        # Resolve thread context
        is_reply = tool_data.get("_is_reply", False)
        thread_id = tool_data.get("thread_id", "").strip()
        reply_to = tool_data.get("reply_to", "").strip()
        if is_reply and self._active_thread_id:
            # <hub_reply> — inherit active thread from last received message
            thread_id = thread_id or self._active_thread_id
            reply_to = reply_to or self._active_thread_msg_id

        # Route the message
        msg = HubMessage(
            action="message",
            from_agent=(self._identity.agent_id if self._identity else ""),
            from_identity=(self._identity.identity if self._identity else ""),
            to=target,
            content=content,
            scope=self._resolve_scope(target),
            force=force_attr in ("true", "yes", "1"),
            thread_id=thread_id,  # empty string = HubMessage.__post_init__ creates new thread
            reply_to=reply_to,
        )
        rejections = await self._route_message(msg)
        self._display_outgoing_message(target, content)

        # Bridge forward
        my_name = self._identity.identity if self._identity else "?"
        await self._bridge_forward(f"[{my_name} -> {target}] {content}")

        # Build output — check rejections first
        if rejections:
            parts = []
            for ident, reason in rejections:
                parts.append(f"{ident}: {reason}")
            output = (
                f"[hub_msg] rejected: {'; '.join(parts)}. "
                f"send with force=\"true\" to break through."
            )
        elif self._presence:
            known = self._presence.scan_all_presence()
            known_ids = {a.identity for a in known}
            if target not in known_ids and target not in ("all", "*", "everyone"):
                output = (
                    f"warning: '{target}' is not online. "
                    f"message broadcast but no matching agent. "
                    f"online: {', '.join(sorted(known_ids)) or 'none'}"
                )
            else:
                output = f"delivered to {target}"
        else:
            output = f"delivered to {target}"

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_msg",
            success=True,
            output=output,
            metadata={"wait": any_wait},
        )

    async def _handle_hub_broadcast_tool(self, tool_data: dict):
        """Execute a hub_broadcast tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        content = tool_data.get("message", tool_data.get("content", ""))
        if not content:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_broadcast",
                success=True,
                output="",
            )

        force_attr = tool_data.get("force", tool_data.get("force_attr", ""))
        result_text = await self._handle_broadcast_command(
            content, force=force_attr in ("true", "yes", "1")
        )
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_broadcast",
            success=True,
            output=result_text,
        )

    async def _handle_hub_stop_tool(self, tool_data: dict):
        """Execute a hub_stop tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        target = tool_data.get("target", "")
        if not target:
            target = tool_data.get("to", "")
        if not target:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_stop",
                success=False,
                error="empty target",
            )

        result_text = await self._handle_stop_command(target)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_stop",
            success=True,
            output=result_text,
        )

    async def _handle_hub_restart_tool(self, tool_data: dict):
        """Execute a hub_restart tool: self-restart with same identity.

        Schedules a clean shutdown + os.execvp to replace the current
        process image with a fresh kollab invocation. Same identity
        survives because vault persistence + rebirth reload context
        from disk. Same process group / terminal, so any attached TUI
        reconnects automatically once the new process registers on the
        hub socket.

        Strips --detached from argv before re-exec so we don't fork
        again (the detach was already consumed at original boot).
        """
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_restart",
                success=False,
                error="hub not initialized",
            )

        logger.info(
            f"{self._identity.identity} requested self-restart via hub_restart"
        )

        # Build the re-exec command. sys.argv[0] is main.py or the kollab
        # entry point; anything after is user-supplied flags. Drop --detached
        # so we don't fork a second daemon layer.
        import sys

        python = sys.executable
        argv = [a for a in sys.argv if a != "--detached"]
        exec_argv = [python] + argv

        self._self_restart_requested = True
        self._self_restart_cmd = exec_argv

        # Schedule the restart. We reuse the shutdown path so vault +
        # presence cleanup happens cleanly, then os.execvp replaces the
        # process image instead of os._exit.
        self._self_stop_requested = True  # triggers existing shutdown path
        asyncio.ensure_future(self._perform_self_restart())

        async def _self_restart_watchdog() -> None:
            try:
                await asyncio.sleep(8.0)
            except asyncio.CancelledError:
                return
            logger.warning(
                f"{self._identity.identity}: self-restart watchdog fired, "
                f"forcing execvp after graceful shutdown timeout"
            )
            try:
                os.execvp(exec_argv[0], exec_argv)
            except Exception as e:
                logger.error(f"execvp failed: {e}, falling back to exit")
                os._exit(0)

        asyncio.ensure_future(_self_restart_watchdog())

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_restart",
            success=True,
            output=f"{self._identity.identity} restarting...",
        )

    async def _perform_self_restart(self) -> None:
        """Run graceful shutdown, then os.execvp to replace this process."""
        try:
            await self.shutdown()
        except Exception as e:
            logger.error(f"Self-restart: shutdown error (continuing to exec): {e}")

        exec_argv = getattr(self, "_self_restart_cmd", None)
        if not exec_argv:
            logger.error("Self-restart: no exec_argv set, falling back to exit")
            os._exit(1)

        logger.info(f"Self-restart: execvp {exec_argv}")
        try:
            os.execvp(exec_argv[0], exec_argv)
        except Exception as e:
            logger.error(f"Self-restart execvp failed: {e}")
            os._exit(1)

    async def _handle_hub_status_tool(self, tool_data: dict):
        """Execute a hub_status tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._identity or not self._presence:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_status",
                success=False,
                error="hub not initialized",
            )

        status = self._format_status()
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_status",
            success=True,
            output=status,
        )

    async def _handle_hub_agents_tool(self, tool_data: dict):
        """Execute a hub_agents tool — list online agents."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._identity or not self._presence:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_agents",
                success=False,
                error="hub not initialized",
            )

        # Reuse the same status formatter as hub_status
        status = self._format_status()
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_agents",
            success=True,
            output=status,
        )

    async def _handle_scratchpad_tool(self, tool_data: dict):
        """Execute a scratchpad (overwrite) tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._scratchpad:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="scratchpad",
                success=False,
                error="scratchpad not initialized",
            )

        content = tool_data.get("content", "")
        self._scratchpad.write(content)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="scratchpad",
            success=True,
            output="scratchpad written",
        )

    async def _handle_scratchpad_append_tool(self, tool_data: dict):
        """Execute a scratchpad_append tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._scratchpad:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="scratchpad_append",
                success=False,
                error="scratchpad not initialized",
            )

        content = tool_data.get("content", "")
        self._scratchpad.append(content)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="scratchpad_append",
            success=True,
            output=f"scratchpad appended: {content[:60]}",
        )

    async def _handle_scratchpad_clear_tool(self, tool_data: dict):
        """Execute a scratchpad_clear tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._scratchpad:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="scratchpad_clear",
                success=False,
                error="scratchpad not initialized",
            )

        self._scratchpad.clear()
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="scratchpad_clear",
            success=True,
            output="scratchpad cleared",
        )

    async def _handle_scratchpad_get_tool(self, tool_data: dict):
        """Execute a scratchpad_get tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._scratchpad:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="scratchpad_get",
                success=False,
                error="scratchpad not initialized",
            )

        content = self._scratchpad.get()
        preview = content[:200] if content else "(empty)"
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="scratchpad_get",
            success=True,
            output=preview,
        )

    async def _handle_state_update_tool(self, tool_data: dict):
        """Execute a state_update tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._vault:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="state_update",
                success=False,
                error="vault not initialized",
            )

        state = tool_data.get("state", None)
        json_str = tool_data.get("json_str", "")
        try:
            if state is not None:
                updates = {"state": state}
            else:
                updates = json.loads(json_str)
            self._session_state_mgr.update_state(self._vault._vault_dir, updates)
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="state_update",
                success=True,
                output=f"saved: {list(updates.keys())}",
            )
        except json.JSONDecodeError as e:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="state_update",
                success=False,
                error=f"invalid json: {e}",
            )

    async def _handle_task_checkpoint_tool(self, tool_data: dict):
        """Execute a task_checkpoint tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._task_ledger or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="task_checkpoint",
                success=False,
                error="task ledger not initialized",
            )

        task_id = _safe_semantic_id(tool_data, ["task_id"])
        note = tool_data.get("progress", tool_data.get("note", ""))
        self._task_ledger.checkpoint(task_id, note)
        logger.info(f"Task {task_id} checkpoint: {note[:60]}")
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="task_checkpoint",
            success=True,
            output=f"task {task_id} checkpoint saved",
        )

    async def _handle_task_complete_tool(self, tool_data: dict):
        """Execute a task_complete tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._task_ledger or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="task_complete",
                success=False,
                error="task ledger not initialized",
            )

        task_id = _safe_semantic_id(tool_data, ["task_id"])
        result = tool_data.get("summary", tool_data.get("result", ""))
        card = self._task_ledger.request_qa(task_id, result)
        if card and self._presence:
            qa_msg = HubMessage(
                action="message",
                from_agent=(self._identity.agent_id if self._identity else ""),
                from_identity=self._identity.identity,
                to=card.report_to,
                content=(
                    f"[task {card.id} complete - QA needed]\n"
                    f"directive: {card.directive}\n"
                    f"result: {result}\n\n"
                    "review and approve with:"
                    f' <task_approve id="{card.id}">'
                    "notes</task_approve>\n"
                    "or reject:"
                    f' <task_reject id="{card.id}">'
                    "reason</task_reject>"
                ),
                scope=MessageScope.DIRECT.value,
            )
            agents = await self._presence.discover_agents_async()
            for a in agents:
                if a.identity == card.report_to:
                    await self._deliver_to_agent(a, qa_msg)
            output = f"task {task_id} marked complete, routed to {card.report_to} for QA"
        elif card:
            output = f"task {task_id} marked complete (presence unavailable, reviewer not notified)"
        else:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="task_complete",
                success=False,
                error=f"task {task_id} not found or not in claimable state",
            )

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="task_complete",
            success=True,
            output=output,
        )

    async def _handle_task_approve_tool(self, tool_data: dict):
        """Execute a task_approve tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._task_ledger or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="task_approve",
                success=False,
                error="task ledger not initialized",
            )

        task_id = _safe_semantic_id(tool_data, ["task_id"])
        notes = tool_data.get("notes", "")
        card = self._task_ledger.qa_approve(
            task_id, self._identity.identity, notes
        )
        if card and self._presence:
            approve_msg = HubMessage(
                action="message",
                from_agent=(self._identity.agent_id if self._identity else ""),
                from_identity=self._identity.identity,
                to=card.assignee,
                content=(
                    f"[task {card.id} QA PASSED]\n"
                    f"reviewer: {self._identity.identity}\n"
                    f"notes: {notes}"
                ),
                scope=MessageScope.DIRECT.value,
            )
            agents = await self._presence.discover_agents_async()
            for a in agents:
                if a.identity == card.assignee:
                    await self._deliver_to_agent(a, approve_msg)
            output = f"task {task_id} approved, notified {card.assignee}"
        elif card:
            output = f"task {task_id} approved (presence unavailable, assignee not notified)"
        else:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="task_approve",
                success=False,
                error=f"task {task_id} not found or not in QA state",
            )

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="task_approve",
            success=True,
            output=output,
        )

    async def _handle_task_reject_tool(self, tool_data: dict):
        """Execute a task_reject tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._task_ledger or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="task_reject",
                success=False,
                error="task ledger not initialized",
            )

        task_id = _safe_semantic_id(tool_data, ["task_id"])
        reason = tool_data.get("reason", "")
        card = self._task_ledger.qa_reject(
            task_id, self._identity.identity, reason
        )
        if card and self._presence:
            reject_msg = HubMessage(
                action="message",
                from_agent=(self._identity.agent_id if self._identity else ""),
                from_identity=self._identity.identity,
                to=card.assignee,
                content=(
                    f"[task {card.id} QA REJECTED - rework needed]\n"
                    f"reviewer: {self._identity.identity}\n"
                    f"reason: {reason}"
                ),
                scope=MessageScope.DIRECT.value,
            )
            agents = await self._presence.discover_agents_async()
            for a in agents:
                if a.identity == card.assignee:
                    await self._deliver_to_agent(a, reject_msg)
            output = f"task {task_id} rejected, returned to {card.assignee} for rework"
        elif card:
            output = f"task {task_id} rejected (presence unavailable, assignee not notified)"
        else:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="task_reject",
                success=False,
                error=f"task {task_id} not found or not in QA state",
            )

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="task_reject",
            success=True,
            output=output,
        )

    async def _handle_lane_claim_tool(self, tool_data: dict):
        """Execute a lane_claim tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="lane_claim",
                success=False,
                error="change feed not initialized",
            )

        identity = self._identity.identity
        path = tool_data.get("path", "")
        task_desc = tool_data.get("task", tool_data.get("task_desc", ""))
        result = self._change_feed.claim(identity, path, task_desc)
        status = result.get("status", "unknown")
        if status == "conflict":
            claimed_by = result.get("claimed_by", "?")
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="lane_claim",
                success=True,
                output=f"CONFLICT: {path} claimed by {claimed_by}",
            )
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="lane_claim",
            success=True,
            output=f"claimed: {path}",
        )

    async def _handle_lane_release_tool(self, tool_data: dict):
        """Execute a lane_release tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="lane_release",
                success=False,
                error="change feed not initialized",
            )

        identity = self._identity.identity
        path = tool_data.get("path", "")
        self._change_feed.release(identity, path)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="lane_release",
            success=True,
            output=f"released: {path}",
        )

    async def _handle_file_changed_tool(self, tool_data: dict):
        """Execute a file_changed tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="file_changed",
                success=False,
                error="change feed not initialized",
            )

        identity = self._identity.identity
        path = tool_data.get("path", "")
        self._change_feed.record_change(identity, path, "edited")

        # Notify subscribers
        subscribers = self._change_feed.get_subscribers_for(path)
        if subscribers and self._presence:
            agents = await self._presence.discover_agents_async()
            for sub_identity in subscribers:
                if sub_identity == identity:
                    continue
                target = next(
                    (a for a in agents if a.identity == sub_identity),
                    None,
                )
                if target:
                    notify_msg = HubMessage(
                        action="message",
                        from_agent=self._identity.agent_id,
                        from_identity=identity,
                        to=sub_identity,
                        content=f"[file watch] {identity} edited {path}",
                        scope=MessageScope.DIRECT.value,
                    )
                    await self._deliver_to_agent(target, notify_msg)

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="file_changed",
            success=True,
            output=f"recorded: {path}",
        )

    async def _handle_file_watch_tool(self, tool_data: dict):
        """Execute a file_watch tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="file_watch",
                success=False,
                error="change feed not initialized",
            )

        identity = self._identity.identity
        pattern = tool_data.get("path", tool_data.get("pattern", ""))
        self._change_feed.subscribe(identity, pattern)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="file_watch",
            success=True,
            output=f"watching: {pattern}",
        )

    async def _handle_file_unwatch_tool(self, tool_data: dict):
        """Execute a file_unwatch tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed or not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="file_unwatch",
                success=False,
                error="change feed not initialized",
            )

        identity = self._identity.identity
        pattern = tool_data.get("path", tool_data.get("pattern", ""))
        self._change_feed.unsubscribe(identity, pattern)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="file_unwatch",
            success=True,
            output=f"stopped: {pattern}",
        )

    async def _handle_feed_recent_tool(self, tool_data: dict):
        """Execute a feed_recent tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="feed_recent",
                success=False,
                error="change feed not initialized",
            )

        limit = tool_data.get("limit", 10)
        result = self._change_feed.get_recent(limit)
        entries = result.get("entries", [])
        if entries:
            lines = [f"last {len(entries)} changes:"]
            for entry in entries:
                lines.append(
                    f"  {entry.get('identity', '?')} "
                    f"{entry.get('action', '?')} "
                    f"{entry.get('path', '?')}"
                )
            output = "\n".join(lines)
        else:
            output = "no changes"
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="feed_recent",
            success=True,
            output=output,
        )

    async def _handle_feed_file_tool(self, tool_data: dict):
        """Execute a feed_file tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="feed_file",
                success=False,
                error="change feed not initialized",
            )

        path = tool_data.get("path", "")
        result = self._change_feed.get_changes_for_file(path)
        entries = result.get("entries", [])
        if entries:
            lines = [f"{path} ({len(entries)} changes):"]
            for entry in entries:
                lines.append(
                    f"  {entry.get('identity', '?')} "
                    f"{entry.get('action', '?')} "
                    f"{entry.get('path', '?')}"
                )
            output = "\n".join(lines)
        else:
            output = f"no changes for {path}"
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="feed_file",
            success=True,
            output=output,
        )

    async def _handle_claims_tool(self, tool_data: dict):
        """Execute a claims tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._change_feed:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="claims",
                success=False,
                error="change feed not initialized",
            )

        target_identity = tool_data.get("identity", tool_data.get("target_identity", "")) or None
        result = self._change_feed.get_claims(identity=target_identity)
        claims = result.get("claims", {})
        if claims:
            lines = [f"{len(claims)} active:"]
            for c in claims.values():
                lines.append(
                    f"  {c.get('identity', '?')} -> "
                    f"{c.get('path', '?')} "
                    f"(task: {c.get('task', '')})"
                )
            output = "\n".join(lines)
        else:
            output = "no active claims"
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="claims",
            success=True,
            output=output,
        )

    async def _handle_hub_spawn_tool(self, tool_data: dict):
        """Execute a hub_spawn tool.

        Three spawn modes:
          1. By identity: name="lapis" → uses pool's agent_type for lapis
          2. By agent_type: name="coder" → picks next available coder from pool
          3. Explicit: name="lapis" type="research" → identity + type override
        """
        from kollabor_agent.tool_executor import ToolExecutionResult

        if not self._identity:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_spawn",
                success=False,
                error="identity not initialized",
            )

        my_ident = self._identity.identity or ""
        raw_name = tool_data.get("name", "")
        requested_identity = (tool_data.get("identity", "") or "").strip()
        agent_type_override = (
            tool_data.get("agent_type_override", "")
            or tool_data.get("type", "")
        ).strip()
        task = tool_data.get("task", "")

        if raw_name == my_ident or requested_identity == my_ident:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_spawn",
                success=False,
                error="cannot spawn yourself",
            )
        if not task:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_spawn",
                success=False,
                error=f"task required for {raw_name}",
            )

        spawn_args = {
            "name": raw_name,
            "task": task,
            "agent_type_override": agent_type_override,
        }
        if requested_identity:
            spawn_args["identity"] = requested_identity

        result = await self._handle_spawn_command(spawn_args)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_spawn",
            success=True,
            output=result,
        )

    async def _handle_hub_queue_tool(self, tool_data: dict):
        """Execute a hub_queue tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        task = tool_data.get("task", "") or tool_data.get("description", "")
        if not task:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_queue",
                success=False,
                error="task description required",
            )

        result = self._handle_queue_command(task)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_queue",
            success=True,
            output=result,
        )

    async def _handle_hub_claim_tool(self, tool_data: dict):
        """Execute a hub_claim tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        slot_id = tool_data.get("slot_id", "") or tool_data.get("claim_id", "")
        result = await self._handle_claim_command(slot_id.strip())
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_claim",
            success=True,
            output=result,
        )

    async def _handle_hub_work_tool(self, tool_data: dict):
        """Execute a hub_work tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        result = self._format_work()
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_work",
            success=True,
            output=result,
        )

    async def _handle_hub_vault_tool(self, tool_data: dict):
        """Execute a hub_vault tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        vault_name = tool_data.get("vault_name", "") or tool_data.get("name", "")
        result = self._format_vault(vault_name.strip())
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_vault",
            success=True,
            output=result,
        )

    async def _handle_hub_vaults_tool(self, tool_data: dict):
        """Execute a hub_vaults tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        result = self._format_all_vaults()
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_vaults",
            success=True,
            output=result,
        )

    async def _handle_hub_cron_add_tool(self, tool_data: dict):
        """Execute a hub_cron_add tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        target = tool_data.get("target", "")
        interval = tool_data.get("interval", "")
        msg = tool_data.get("message", "")

        if not target:
            target = self._identity.identity if self._identity else ""

        if not target or not interval:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="hub_cron_add",
                success=False,
                error="requires target and interval attributes",
            )

        # Enforce 30s minimum for XML-originated cron
        try:
            secs = _parse_interval(interval)
            if secs < 30:
                return ToolExecutionResult(
                    tool_id=tool_data.get("id", "unknown"),
                    tool_type="hub_cron_add",
                    success=False,
                    error="minimum interval is 30s",
                )
        except ValueError:
            pass  # let _cron_add handle the error

        result = self._cron_add(f"{target} {interval} {msg}")
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_cron_add",
            success=True,
            output=result,
        )

    async def _handle_hub_cron_list_tool(self, tool_data: dict):
        """Execute a hub_cron_list tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        result = self._cron_list()
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_cron_list",
            success=True,
            output=result,
        )

    async def _handle_hub_cron_delete_tool(self, tool_data: dict):
        """Execute a hub_cron_delete tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        job_id = _safe_semantic_id(tool_data, ["job_id"])
        result = self._cron_delete(job_id)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_cron_delete",
            success=True,
            output=result,
        )

    async def _handle_hub_capture_tool(self, tool_data: dict):
        """Execute a hub_capture tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        cap_name = tool_data.get("cap_name", "") or tool_data.get("name", "")
        cap_lines = tool_data.get("cap_lines", "50") or str(tool_data.get("lines", "50"))
        args = cap_name.strip()
        if cap_lines:
            args += f" {cap_lines}"
        result = await self._handle_capture_command(args)
        # Truncate per-agent capture to 10k chars (2000 was too low —
        # oldest entries consumed the entire budget before recent work
        # became visible).  Entries are already newest-first.
        if len(result) > 10000:
            result = result[:10000] + "\n... (truncated)"
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="hub_capture",
            success=True,
            output=result,
        )

    async def register_hooks(self) -> None:
        """Register event hooks."""
        if not self.event_bus or not self._is_enabled():
            return

        # Inject roster into system prompt before each LLM call
        roster_hook = Hook(
            name="hub_roster_inject",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_PRE,
            callback=self._inject_roster_context,
            priority=HookPriority.PREPROCESSING.value,
        )
        await self.event_bus.register_hook(roster_hook)

        # Track when agent is working vs idle
        working_hook = Hook(
            name="hub_working_state",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_PRE,
            callback=self._set_working,
            priority=HookPriority.SYSTEM.value,
        )
        await self.event_bus.register_hook(working_hook)

        idle_hook = Hook(
            name="hub_idle_state",
            plugin_name=self.name,
            event_type=EventType.LLM_RESPONSE_POST,
            callback=self._set_idle,
            priority=HookPriority.POSTPROCESSING.value,
        )
        await self.event_bus.register_hook(idle_hook)

        # Parse <hub_msg> tags from LLM responses
        msg_hook = Hook(
            name="hub_msg_parser",
            plugin_name=self.name,
            event_type=EventType.LLM_RESPONSE_POST,
            callback=self._parse_hub_messages,
            priority=HookPriority.POSTPROCESSING.value,
        )
        await self.event_bus.register_hook(msg_hook)

        # Broadcast user input to all peers so they see what the human said
        broadcast_hook = Hook(
            name="hub_user_broadcast",
            plugin_name=self.name,
            event_type=EventType.USER_INPUT_POST,
            callback=self._broadcast_user_input,
            priority=HookPriority.POSTPROCESSING.value,
        )
        await self.event_bus.register_hook(broadcast_hook)

        # Nudge: inject relevant crystallized memories on user input
        nudge_hook = Hook(
            name="hub_crystal_nudge",
            plugin_name=self.name,
            event_type=EventType.USER_INPUT_POST,
            callback=self._crystal_nudge_on_input,
            priority=HookPriority.DISPLAY.value,
        )
        await self.event_bus.register_hook(nudge_hook)

        # Start the hub after all plugins initialized
        # Skip in attach mode - we're a viewer, not a peer on the mesh
        if self._cli_args and getattr(self._cli_args, "attach", None):
            logger.info("Hub: attach mode, skipping mesh join")
        else:

            async def _safe_start():
                try:
                    await self._start_hub()
                except Exception as e:
                    logger.error(f"Hub _start_hub failed: {e}", exc_info=True)

            _get_loop().call_soon(
                lambda: asyncio.ensure_future(_safe_start())
            )

    async def _start_hub(self) -> None:
        """Start hub services (called after all plugins init)."""
        if self._started or self._starting or not self._identity:
            return
        self._starting = True
        assert self._presence is not None
        assert self._election is not None

        try:
            # Clean stale state first (discovers and removes dead agents)
            self._presence.discover_agents()

            # Try to become coordinator
            is_coordinator = self._election.try_become_coordinator(self._identity)
            self._identity.is_coordinator = is_coordinator

            # Discover existing agents to get taken identities
            existing = self._presence.discover_agents()
            taken = [a.identity for a in existing]

            # Resolve active agent for identity and vault config
            agent_mgr = None
            active_agent = None
            try:
                agent_mgr = (
                    self.event_bus.get_service("agent_manager")
                    if self.event_bus
                    else None
                )
                if agent_mgr:
                    active_agent = agent_mgr.get_active_agent()
            except Exception:
                pass

            # Get identity
            # Priority: CLI --as > agent.json identity > config > auto-assign
            preferred = ""
            if (
                self._cli_args
                and hasattr(self._cli_args, "as_identity")
                and self._cli_args.as_identity
            ):
                preferred = self._cli_args.as_identity
            else:
                if active_agent:
                    # Use agent's identity field, or fall back to agent name
                    agent_ident = getattr(active_agent, "identity", "")
                    if agent_ident:
                        preferred = agent_ident
                    elif active_agent.name and active_agent.name != "default":
                        preferred = active_agent.name

                if not preferred and self.config:
                    preferred = self.config.get("plugins.hub.identity", "")

            # If preferred identity is taken, verify the holder is
            # truly alive (async socket ping). discover_agents does a
            # synchronous connect check, but the process may be mid-
            # shutdown (PID alive, socket draining). An async ping with
            # a short timeout is the definitive liveness test.
            if preferred and preferred in taken:
                holder = next(
                    (a for a in existing if a.identity == preferred),
                    None,
                )
                if holder:
                    still_alive = await AgentMessenger.ping_agent(
                        holder.socket_path, timeout=2.0
                    )
                    if not still_alive:
                        logger.info(
                            f"Force-claiming '{preferred}': holder "
                            f"pid={holder.pid} failed async ping"
                        )
                        # Remove stale presence + socket so it doesn't
                        # interfere with future discovery either
                        from .presence import get_presence_dir

                        stale_file = get_presence_dir() / f"{holder.agent_id}.json"
                        stale_file.unlink(missing_ok=True)
                        PresenceManager._cleanup_agent_socket(holder)
                        taken = [t for t in taken if t != preferred]

            self._identity.identity = self._designator.assign(taken, preferred)
            self._identity.state = AgentState.IDLE.value

            # Update coordinator state now that identity is populated
            # (try_become_coordinator runs before identity assignment)
            if self._identity.is_coordinator and self._election:
                self._election.update_identity(self._identity)

            # Create + start socket server NOW that we know the identity.
            # Socket file is named by identity (jarvis.sock, not a53d186b.sock).
            self._socket_server = AgentSocketServer(
                self._identity.agent_id,
                self._on_message_received,
                on_get_output=self._get_output_lines,
                on_shutdown=self._on_remote_shutdown,
                on_input_inject=self._inject_attacher_input,
                socket_name=self._identity.identity,
            )
            self._socket_server._display_tap = self._display_tap  # type: ignore[assignment]
            self._socket_server._identity_info = self._identity  # type: ignore[assignment]

            # === RPC server (phase 1 of daemon transparency refactor) ===
            # Instantiate RpcServer and install it on the socket server so
            # _handle_connection and _recv_input can dispatch "rpc_request"
            # frames to it. Phase 2 will register more handlers (StateService
            # operations) on this same instance from other subsystems.
            #
            # Local import: kollabor_rpc is in a sibling package and importing
            # it at module top can create cycles depending on load order.
            # plugin.py already uses this pattern for signal_daemon_ready
            # below — see the `from kollabor.daemon import signal_daemon_ready`
            # local import just after start().
            import os as _os
            import time as _time

            try:
                from kollabor_rpc import RpcServer
            except ImportError:
                RpcServer = None  # type: ignore[misc,assignment]
                logger.debug("kollabor_rpc not installed, RPC disabled (attach mode unavailable)")

            if RpcServer is not None:
                _rpc_started_at = _time.monotonic()

                self._rpc_server = RpcServer()
                self._socket_server._rpc_server = self._rpc_server  # type: ignore[assignment]
                if self.event_bus:
                    self.event_bus.register_service("rpc_server", self._rpc_server)

                async def _rpc_ping(params: dict) -> dict:
                    """Phase 1 proof-of-life handler."""
                    identity_str = ""
                    if self._identity is not None:
                        identity_str = getattr(self._identity, "identity", "") or ""
                    return {
                        "status": "ok",
                        "daemon_pid": _os.getpid(),
                        "uptime": _time.monotonic() - _rpc_started_at,
                        "identity": identity_str,
                    }

                self._rpc_server.register("ping", _rpc_ping)
                logger.info("rpc server initialized, ping registered")

                # === State RPC handlers (phase 2) ===
                state_service = (
                    self.event_bus.get_service("state_service") if self.event_bus else None
                )
                if state_service is not None:
                    try:
                        from kollabor.state import register_state_handlers

                        register_state_handlers(self._rpc_server, state_service)
                        logger.info("state rpc handlers registered from hub plugin path")
                    except Exception as e:
                        logger.warning(
                            f"failed to register state rpc handlers from hub: {e}"
                        )
                    try:
                        if hasattr(state_service, "set_context_identity"):
                            state_service.set_context_identity(self._identity.identity)
                    except Exception as e:
                        logger.debug(f"failed to push context identity: {e}")
                else:
                    logger.debug(
                        "state_service not yet on event bus, state rpc handlers deferred"
                    )

            try:
                socket_path = await self._socket_server.start()
                self._identity.socket_path = socket_path
            except RuntimeError as e:
                logger.error(f"socket_server.start failed: {e}")
                self.enabled = False
                self._started = False
                return

            # Signal daemon-ready if running in fork-daemon mode
            try:
                from kollabor.daemon import signal_daemon_ready

                signal_daemon_ready(socket_path)
            except Exception:
                pass  # Not in daemon mode, or import failed

            # Initialize vault for this identity (if enabled by agent config)
            _vault_enabled = True
            if active_agent:
                _vault_enabled = getattr(active_agent, "vault_enabled", True)

            if _vault_enabled:
                self._vault = AgentVault(self._identity.identity)
                self._vault.touch()
                # Project-scoped crystal store: work done in this repo
                self._crystal_store = CrystalStore(self._vault._vault_dir)
                # Global crystal store: cross-project personality + skills
                self._global_crystal_store = CrystalStore(self._vault.global_vault_dir)
            else:
                logger.info(f"Vault disabled for agent {self._identity.identity}")

            # Initialize Agent DNS (identity, registry, reputation, capabilities)
            if _DNS_AVAILABLE:
                try:
                    self._dns_startup_time = time.time()
                    self._dns_storage = DNSStorage()
                    self._dns_identity = IdentityManager(self._dns_storage)
                    self._dns_registry = AgentRegistry(self._dns_storage)
                    self._dns_reputation = ReputationTracker(self._dns_storage)
                    self._dns_capabilities = CapabilityRegistry(self._dns_storage)

                    _, pub_hex = self._dns_identity.get_or_create_keypair(
                        self._identity.identity
                    )

                    caps = []
                    for cap_name in getattr(self._identity, "capabilities", []):
                        caps.append(CapabilityEntry(name=cap_name))

                    pool_info = POOL_BY_NAME.get(
                        self._identity.identity.split("-")[0], None
                    )
                    caste = pool_info.caste if pool_info else ""

                    authority = "kollabor.ai"
                    if self.config:
                        authority = self.config.get("plugins.hub.authority", authority)

                    record = AgentRecord(
                        designation=self._identity.identity,
                        agent_id=self._identity.agent_id,
                        runtime="kollab",
                        authority=authority,
                        socket_path=self._identity.socket_path,
                        pid=os.getpid(),
                        project=os.getcwd(),
                        capabilities=caps,
                        protocols=["socket", "mcp"],
                        public_key=pub_hex,
                        is_coordinator=self._identity.is_coordinator,
                        caste=caste,
                    )

                    if self._identity.is_coordinator:
                        attestation = self._dns_identity.create_attestation(
                            subject=self._identity.identity,
                            issuer=self._identity.identity,
                            subject_public_key_hex=pub_hex,
                        )
                        record.attestation = attestation
                        self._dns_identity.publish_coordinator_key(
                            self._identity.identity
                        )
                        self._dns_storage.write_well_known(record)

                    self._dns_registry.register(record)

                    # Auto-approve if runtime is in whitelist
                    if self._dns_registry:
                        allowed = ["kollab"]
                        if self.config:
                            allowed = self.config.get(
                                "plugins.hub.allowed_runtimes", ["kollab"]
                            )
                        self._dns_registry.auto_approve_runtime(
                            self._identity.identity, allowed
                        )

                    if caps:
                        self._dns_capabilities.declare_many(
                            self._identity.identity, caps
                        )

                    logger.info(
                        f"Agent DNS initialized for {self._identity.identity} "
                        f"(pub={pub_hex[:16]}..., caste={caste})"
                    )

                    # Wire Ed25519 challenge-response auth into socket server
                    if self._socket_server:
                        require_auth = False  # off by default; enable via config
                        if self.config:
                            require_auth = self.config.get(
                                "plugins.hub.require_auth", False
                            )
                        self._socket_server.set_dns_auth(
                            registry=self._dns_registry,
                            identity_manager=self._dns_identity,
                            require_auth=require_auth,
                        )
                except Exception as e:
                    logger.warning(f"Agent DNS initialization failed: {e}")
            else:
                logger.info("Agent DNS skipped (PyNaCl not available)")

            # Initialize change feed (file tracking + lane claims)
            feed_max_age = DEFAULT_FEED_MAX_AGE
            if self.config:
                feed_max_age = self.config.get(
                    "plugins.hub.feed_max_age", DEFAULT_FEED_MAX_AGE
                )
            self._change_feed = ChangeFeed(feed_max_age=feed_max_age)
            purge_result = self._change_feed.startup_purge()
            if purge_result["purged"] > 0:
                logger.info(
                    f"change_feed startup purge: removed "
                    f"{purge_result['purged']} entries, "
                    f"{purge_result['remaining']} remaining"
                )

            # Initialize scratchpad (live notes, survives compaction)
            if self._vault:
                self._scratchpad = Scratchpad(self._vault._vault_dir)

            # Build rebirth context (vault + session state + scratchpad)
            if self._vault and self._vault.exists():
                rebirth_context = self._vault.get_rebirth_context(
                    crystal_store=self._crystal_store,
                    global_crystal_store=self._global_crystal_store,
                )

                # Append scratchpad to rebirth context
                if self._scratchpad:
                    pad = self._scratchpad.get()
                    if pad:
                        rebirth_context += (
                            "\n\n--- scratchpad ---\n"
                            f"{pad}\n"
                            "--- end scratchpad ---"
                        )

                # Append session state to rebirth context
                if self._vault:
                    state_prompt = self._session_state_mgr.get_injection_prompt(
                        self._vault._vault_dir
                    )
                    if state_prompt:
                        rebirth_context += f"\n\n{state_prompt}"

                # Release stale lane claims from previous session
                if self._change_feed and self._identity:
                    self._change_feed.release_all(self._identity.identity)
                # Inject rebirth context into conversation history
                llm_service = (
                    self.event_bus.get_service("llm_service")
                    if self.event_bus
                    else None
                )
                if llm_service and hasattr(llm_service, "inject_system_message"):
                    try:
                        async with self._history_lock:
                            await llm_service.inject_system_message(
                                (
                                    "<sys_msg>\n"
                                    f"{rebirth_context}\n"
                                    "use this context to inform your behavior. "
                                    "do not acknowledge or summarize what you "
                                    "just read. do not mention vault rehydration "
                                    "or rebirth. just pick up where you left off "
                                    "naturally.\n"
                                    "</sys_msg>"
                                ),
                                subtype="hub_rebirth",
                            )
                    except Exception:
                        pass

            # Log session start to vault
            if self._vault:
                self._vault.append_stream(
                    "session_start",
                    f"agent {self._identity.identity} started "
                    f"({'coordinator' if is_coordinator else 'peer'})",
                    from_agent=self._identity.identity,
                )

            # Set session log path so other agents can find our conversation file
            llm_svc = (
                self.event_bus.get_service("llm_service") if self.event_bus else None
            )
            if llm_svc and hasattr(llm_svc, "conversation_logger") and llm_svc.conversation_logger:
                self._identity.session_log = str(llm_svc.conversation_logger.session_file)

            # Publish presence
            self._presence.publish()

            # Register as service so status widgets can find us
            if self.event_bus:
                self.event_bus.register_service("hub_plugin", self)

            # Update roster
            roster_all = self._presence.get_roster_summary()
            self._roster = [
                a for a in roster_all if a.get("agent_id") != self._identity.agent_id
            ]

            # Start background tasks
            hb_interval = 5
            if self.config:
                hb_interval = self.config.get("plugins.hub.heartbeat_interval", 5)

            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(hb_interval)
            )
            self._mailbox_task = asyncio.create_task(self._mailbox_loop())

            # Start dreaming loop if enabled and vault is active
            dreaming_enabled = True
            if self.config:
                dreaming_enabled = self.config.get("plugins.hub.dreaming_enabled", True)
            if dreaming_enabled and self._vault:
                self._dreaming_task = asyncio.create_task(self._dreaming_loop())

            # Start notification loop if enabled
            notify_enabled = False
            if self.config:
                notify_enabled = self.config.get("plugins.hub.notify_enabled", False)
            if notify_enabled:
                self._notifier = HubNotifier(
                    config=self.config or {},
                    get_identity=lambda: (
                        self._identity.identity if self._identity else ""
                    ),
                    get_last_activity=lambda: self._last_activity_at,
                    get_state=lambda: (
                        self._identity.state if self._identity else "unknown"
                    ),
                )
                self._notify_task = asyncio.create_task(self._notifier.run())

            # Start messaging bridge loop if enabled
            bridge_enabled = False
            if self.config:
                bridge_enabled = self.config.get("plugins.hub.bridge_enabled", False)
            # Only coordinator runs the bridge (avoids 409 conflict
            # when multiple agents poll the same bot token)
            if bridge_enabled and self._identity.is_coordinator:
                self._bridge_task = asyncio.create_task(self._messaging_bridge_loop())

            # Start cron loop (always -- jobs are added at runtime)
            self._cron_task = asyncio.create_task(self._cron_loop())

            # Start vault autosave loop (always -- crash protection)
            if self._vault:
                self._autosave_task = asyncio.create_task(self._vault_autosave_loop())

            if self._conversation_manager:
                logger.info("Hub messaging ready (direct injection)")
            else:
                logger.warning("No conversation manager - hub messaging disabled")

            role = "coordinator" if is_coordinator else "agent"
            logger.info(
                f"Hub started: {self._identity.identity} ({role}), "
                f"{len(existing)} peers online"
            )

            # Wire context-service bridge now that identity is resolved
            # and all plugins are initialized. Deferred from initialize() to avoid
            # the race where context_service_plugin hadn't registered yet.
            self._wire_context_hub_bridge()

            # NOTE: hub trender tags (hub_identity, hub_roster, hub_work_queue)
            # render empty on first boot because the hub isn't ready yet.
            # rebuild_system_prompt() would fix this BUT it also re-runs
            # shell command trender tags (git log etc) which can produce
            # megabytes of output and blow the context window. Deferring
            # this fix until the context service handles prompt size caps.
            # The hub vault trender works via get_rebirth_context() which
            # has its own budget. Identity/roster/work_queue stay empty
            # on first boot but populate on attach or session resume.

            # Display identity in UI using theme system
            renderer = (
                self.event_bus.get_service("renderer") if self.event_bus else None
            )

            # Wire display tap to message coordinator for live attach
            if (
                renderer
                and hasattr(renderer, "message_coordinator")
                and self._display_tap
            ):
                renderer.message_coordinator._display_tap = self._display_tap

            if renderer and hasattr(renderer, "message_coordinator"):
                peer_names = [a.identity for a in existing]
                peer_list = ", ".join(peer_names) if peer_names else "none"
                try:
                    renderer.message_coordinator.display_message_sequence(
                        [
                            (
                                "system",
                                f"{self._identity.identity} ({role}) | peers: {peer_list}",
                                {"display_type": "success"},
                            )
                        ]
                    )
                except Exception:
                    pass

            # Forward own arrival to bridge
            await self._bridge_forward(f"[hub] {self._identity.identity} came online")

            # If coordinator and there's pending work + new agents, assign
            if is_coordinator:
                await self._try_assign_work()

            # Announce ourselves to all existing agents (triggers their LLM)
            if existing:
                await self._announce_to_peers(existing)

            # Launch org if --org CLI flag was passed
            if self._cli_args and hasattr(self._cli_args, "org") and self._cli_args.org:
                org_name = self._cli_args.org
                logger.info(f"Launching org from CLI flag: {org_name}")
                try:
                    result = await self._handle_org_command(org_name)
                    renderer = (
                        self.event_bus.get_service("renderer")
                        if self.event_bus
                        else None
                    )
                    if renderer and hasattr(renderer, "message_coordinator"):
                        renderer.message_coordinator.display_message_sequence(
                            [("system", result, {"display_type": "info"})]
                        )
                except Exception as e:
                    logger.error(f"--org launch failed: {e}", exc_info=True)

            self._started = True
        except Exception as e:
            logger.error(f"Hub startup failed: {e}", exc_info=True)
        finally:
            self._starting = False

    async def _heartbeat_loop(self, interval: float) -> None:
        """Periodically update presence and check for dead agents."""
        while True:
            try:
                await asyncio.sleep(interval)
                if self._identity:
                    assert self._presence is not None
                    # Ensure session_log is set (may not have been ready at first publish)
                    if not self._identity.session_log:
                        llm_svc = (
                            self.event_bus.get_service("llm_service")
                            if self.event_bus
                            else None
                        )
                        if (
                            llm_svc
                            and hasattr(llm_svc, "conversation_logger")
                            and llm_svc.conversation_logger
                        ):
                            self._identity.session_log = str(
                                llm_svc.conversation_logger.session_file
                            )
                    self._presence.heartbeat()

                    # Self-socket health check: detect if our socket
                    # vanished (OS cleanup, race with other agents'
                    # discover_agents, etc.) and attempt reconnection.
                    if self._socket_server and self._identity:
                        sock_path = str(self._socket_server.socket_path)
                        if sock_path and not os.path.exists(sock_path):
                            logger.warning(
                                f"Socket vanished: {sock_path}. "
                                "Attempting reconnection..."
                            )
                            try:
                                await self._socket_server.stop()
                                new_path = await self._socket_server.start()
                                self._identity.socket_path = new_path
                                logger.info(
                                    f"Socket reconnected: {new_path}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Socket reconnection failed: {e}"
                                )

                    # Refresh roster using async discovery (non-blocking
                    # socket checks). This also updates the presence
                    # manager's internal cache for sync callers.
                    await self._presence.discover_agents_async()
                    self._roster = self._presence.get_roster_summary()

                    # If coordinator, check for dead agents and clean up
                    if self._identity.is_coordinator:
                        await self._coordinator_cleanup()

                    # Refresh DNS liveness + sync trust scores
                    if self._dns_registry and self._presence:
                        try:
                            live_agents = self._presence.get_cached_agents()
                            self._dns_registry.refresh_liveness(live_agents)
                            if self._dns_reputation:
                                for record in self._dns_registry.get_all():
                                    trust = self._dns_reputation.get_trust(
                                        record.designation
                                    )
                                    record.trust_score = trust
                                self._dns_registry.save()
                        except Exception as e:
                            logger.debug(f"DNS liveness refresh error: {e}")

                    # Coordinator: drain and process pending reputation events
                    if (
                        self._identity.is_coordinator
                        and self._dns_reputation
                        and self._dns_storage
                    ):
                        try:
                            events = self._dns_storage.drain_reputation_events()
                            for ev in events:
                                self._dns_reputation.record_event(
                                    ev.get("designation", ""),
                                    ev.get("event_type", ""),
                                    ev.get("metadata", {}),
                                )
                        except Exception as e:
                            logger.debug(f"DNS reputation drain error: {e}")

                    # Cleanup expired lane claims
                    if self._change_feed:
                        self._change_feed.cleanup_expired()

                    # Publish state snapshot for attach clients
                    self._publish_state_snapshot()

                    # Vault autosave handled by _vault_autosave_loop (line 836)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")

    def _publish_state_snapshot(self) -> None:
        """Publish daemon state to DisplayTap for attach client widgets."""
        if not self._display_tap:
            return

        state = {}
        try:
            # LLM service state
            llm = self.event_bus.get_service("llm_service") if self.event_bus else None
            if llm:
                state["is_processing"] = getattr(llm, "is_processing", False)
                stats = getattr(llm, "session_stats", {})
                state["messages"] = stats.get("messages", 0)
                state["input_tokens"] = stats.get("input_tokens", 0)
                state["output_tokens"] = stats.get("output_tokens", 0)

                # Session name
                cm = getattr(llm, "conversation_manager", None)
                if cm:
                    state["session"] = getattr(cm, "current_session_id", "")

                # MCP
                mcp = getattr(llm, "mcp_integration", None)
                if mcp:
                    conns = getattr(mcp, "server_connections", {})
                    connected = len(
                        [c for c in conns.values() if getattr(c, "initialized", False)]
                    )
                    tools = len(getattr(mcp, "tool_registry", {}))
                    state["mcp"] = {"connected": connected, "tools": tools}

            # Agent name
            agent_mgr = None
            try:
                agent_mgr = (
                    self.event_bus.get_service("agent_manager")
                    if self.event_bus
                    else None
                )
            except Exception:
                pass
            if agent_mgr:
                agent = agent_mgr.get_active_agent()
                if agent:
                    state["agent"] = agent.name
                    hidden = {"system_prompt"}
                    all_skills = [
                        s.name for s in agent.list_skills() if s.name not in hidden
                    ]
                    active = set(agent.active_skills) - hidden
                    active_names = [s for s in all_skills if s in active]
                    if active_names:
                        state["skills"] = ", ".join(active_names)
                    elif all_skills:
                        state["skills"] = "no-skill"

            # Hub identity
            if self._identity:
                state["hub_identity"] = self._identity.identity
                state["hub_is_coordinator"] = self._identity.is_coordinator
                state["hub_peers"] = len(self._roster)

        except Exception as e:
            logger.debug(f"State snapshot error: {e}")

        if state:
            self._display_tap.publish({"type": "state_snapshot", **state})

    async def _mailbox_loop(self) -> None:
        """Check filesystem mailbox for messages."""
        poll_interval = 5
        if self.config:
            poll_interval = self.config.get("plugins.hub.mailbox_poll_interval", 5)
        while True:
            try:
                await asyncio.sleep(poll_interval)
                if self._identity:
                    messages = AgentMessenger.read_mailbox(self._identity.agent_id)
                    for msg in messages:
                        await self._on_message_received(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Mailbox check error: {e}")

    async def _dreaming_loop(self) -> None:
        """Periodically dream when agent is idle -- distill stream into insights.

        Checks every 60 seconds. If agent has been idle longer than the
        configured threshold and enough time has passed since the last dream,
        reads recent vault stream entries, sends them to the LLM for
        distillation, and appends insights to crystallized.md.
        """
        check_interval = 60
        while True:
            try:
                await asyncio.sleep(check_interval)
                if not self._identity or not self._vault:
                    continue

                # Read config thresholds
                idle_threshold = 300
                dream_interval = 3600
                stream_depth = 100
                if self.config:
                    idle_threshold = self.config.get(
                        "plugins.hub.dreaming_idle_threshold", 300
                    )
                    dream_interval = self.config.get(
                        "plugins.hub.dreaming_interval", 3600
                    )
                    stream_depth = self.config.get(
                        "plugins.hub.dreaming_stream_depth", 100
                    )

                now = time.time()
                idle_duration = now - self._last_activity_at
                since_last_dream = now - self._last_dream_at

                # Not idle long enough
                if idle_duration < idle_threshold:
                    continue

                # Too soon since last dream
                if since_last_dream < dream_interval:
                    continue

                # Don't dream if agent is actively working
                if self._identity.state not in (AgentState.IDLE.value, "ready"):
                    continue

                # Check there's actually stream data to review
                stream_entries = self._vault.get_recent_stream(stream_depth)
                if len(stream_entries) < 5:
                    continue

                await self._dream(stream_entries)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Dreaming loop error: {e}")

    async def _dream(self, stream_entries: List[Dict]) -> None:
        """Execute a single dreaming cycle.

        Reads stream, builds prompt, calls LLM, writes crystallized insights.
        Uses the existing provider directly for a background API call that
        does NOT interfere with the user's active conversation.
        """
        assert self._identity is not None
        assert self._vault is not None

        identity = self._identity.identity
        prev_state = self._identity.state
        self._identity.state = AgentLifecycle.DREAMING.value

        logger.info(
            f"dreaming: extracting insights from "
            f"{len(stream_entries)} stream entries"
        )

        # Log to vault
        self._vault.append_stream(
            "dreaming_start",
            f"dreaming cycle started ({len(stream_entries)} entries)",
            from_agent=identity,
        )

        try:
            # Build the dreaming prompt
            prompt = self._build_dreaming_prompt(stream_entries)

            # Try to make the LLM call
            insights = await self._dreaming_llm_call(prompt)

            if insights:
                # Write insights via crystal_store (structured + dedup)
                if self._crystal_store:
                    # Split multi-paragraph insights into individual entries
                    paragraphs = [
                        p.strip()
                        for p in insights.split("\n\n")
                        if p.strip() and len(p.strip()) > 50
                    ]
                    for paragraph in paragraphs:
                        self._crystal_store.add_entry(paragraph)
                    added = len(paragraphs)
                else:
                    # Fallback to raw append if crystal_store unavailable
                    self._vault.append_crystallized(insights)
                    added = 1

                # Update working memory from recent stream
                peers = (
                    [a.identity for a in self._presence.get_cached_agents()]
                    if self._presence
                    else []
                )
                self._vault.update_working_memory(
                    stream_entries,
                    self._identity.current_task,
                    peers,
                )

                self._vault.append_stream(
                    "dreaming_complete",
                    f"dreaming cycle complete, {added} insights written",
                    from_agent=identity,
                )
                logger.info(
                    "dreaming: %d insights written to crystallized.md",
                    added,
                )
            else:
                self._vault.append_stream(
                    "dreaming_skipped",
                    "dreaming cycle produced no insights (LLM unavailable)",
                    from_agent=identity,
                )
                logger.info("dreaming: no insights produced (LLM unavailable)")

        except Exception as e:
            logger.warning(f"dreaming: error during cycle: {e}")
            self._vault.append_stream(
                "dreaming_error",
                f"dreaming cycle error: {e}",
                from_agent=identity,
            )
        finally:
            self._last_dream_at = time.time()
            # Restore previous state (unless something else changed it)
            if self._identity.state == AgentLifecycle.DREAMING.value:
                self._identity.state = prev_state

    async def _vault_autosave_loop(self) -> None:
        """Periodically save working memory so crash != total loss.

        Runs every N seconds (default 300 = 5 min). Rebuilds working
        memory from recent stream entries, mirroring the same logic used
        in _dream() and shutdown(). No-op if vault is missing.
        """
        while True:
            try:
                # Read interval from config (allows runtime tuning)
                interval = 300
                if self.config:
                    interval = self.config.get(
                        "plugins.hub.vault_autosave_interval", 60
                    )
                await asyncio.sleep(interval)

                if not self._vault or not self._identity:
                    continue

                recent = self._vault.get_recent_stream(50)
                peers = (
                    [a.identity for a in self._presence.get_cached_agents()]
                    if self._presence
                    else []
                )
                self._vault.update_working_memory(
                    recent,
                    self._identity.current_task,
                    peers,
                )
                self._last_autosave_at = time.time()
                logger.debug(
                    f"vault autosave: working_memory updated "
                    f"({len(recent)} stream entries)"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Vault autosave error: {e}")

    async def _messaging_bridge_loop(self) -> None:
        """Background loop: connect to messaging bridge, poll for incoming.

        Incoming messages from the platform (e.g. Telegram) are routed
        as hub messages to the target agent (or self). Outgoing messages
        are handled separately -- the bridge.send() is called from
        _on_message_received when a message targets the bridge, or from
        the notifier when idle alerts fire.
        """
        if not self.config:
            return

        platform = self.config.get("plugins.hub.bridge_platform", "telegram")
        token = self.config.get("plugins.hub.bridge_token", "") or os.environ.get(
            "KOLLAB_HUB_BRIDGE_TOKEN", ""
        )
        chat_id = self.config.get("plugins.hub.bridge_chat_id", "") or os.environ.get(
            "KOLLAB_HUB_BRIDGE_CHAT_ID", ""
        )
        user_id = self.config.get("plugins.hub.bridge_user_id", "")
        poll_interval = self.config.get("plugins.hub.bridge_poll_interval", 2)

        if not token or not chat_id:
            logger.warning("bridge_enabled=True but token/chat_id not configured")
            return

        # Build bridge via factory
        kwargs: Dict[str, Any] = {"token": token, "chat_id": chat_id}
        if user_id:
            kwargs["user_id"] = user_id
        bridge = BridgeManager.create(platform, **kwargs)
        if not bridge:
            return

        # Connect
        connected = await bridge.connect()
        if not connected:
            logger.error(f"messaging bridge ({platform}) failed to connect")
            return

        self._bridge = bridge
        logger.info(
            f"messaging bridge loop started ({platform}, "
            f"poll every {poll_interval}s)"
        )

        try:
            while True:
                try:
                    messages = await bridge.poll()
                    for msg in messages:
                        await self._handle_bridge_incoming(msg)
                    # poll() already blocks via long-polling (30s for telegram).
                    # Only add extra sleep if no messages came back (backoff)
                    # or if the platform doesn't do long-polling.
                    if not messages:
                        await asyncio.sleep(poll_interval)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug(f"bridge poll error: {e}")
                    await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            pass
        finally:
            await bridge.disconnect()
            self._bridge = None

    async def _handle_bridge_incoming(self, msg: IncomingMessage) -> None:
        """Route an incoming bridge message as a hub message.

        The message arrives from an external platform (e.g. Telegram)
        and gets injected into the hub as if a human sent it.
        """
        if not self._identity:
            return

        target = ""
        if self.config:
            target = self.config.get("plugins.hub.bridge_target_agent", "")
        if not target:
            target = self._identity.identity

        # Build a hub message from the external platform message
        hub_msg = HubMessage(
            action="message",
            from_agent=f"bridge:{msg.platform}",
            from_identity=msg.sender_name or msg.platform,
            to=target,
            content=msg.text,
            scope=MessageScope.DIRECT.value,
            metadata={
                "bridge_platform": msg.platform,
                "bridge_sender_id": msg.sender_id,
                "bridge_sender_name": msg.sender_name,
            },
        )

        logger.info(
            f"bridge incoming ({msg.platform}): "
            f"{msg.sender_name} -> {target}: {msg.text[:80]}"
        )

        # Route through the standard message handler
        await self._on_message_received(hub_msg)

    async def bridge_send(self, text: str) -> bool:
        """Send a message through the active bridge (if connected).

        Convenience method for other hub components (notifier, commands)
        to push outgoing messages to the external platform.

        Returns True if sent successfully, False otherwise.
        """
        if not self._bridge:
            return False
        try:
            return await self._bridge.send(text)
        except Exception as e:
            logger.warning(f"bridge_send failed: {e}")
            return False

    async def _bridge_forward(self, text: str) -> None:
        """Forward a message to the bridge if connected. Truncates to 500 chars.

        Silently no-ops if bridge is not active. Never raises.
        """
        if not self._bridge:
            return
        try:
            truncated = text[:500] if len(text) > 500 else text
            await self._bridge.send(truncated)
        except Exception:
            pass

    async def _cron_loop(self) -> None:
        """Check and fire hub cron jobs + task reminders every 10 seconds."""
        while True:
            try:
                await asyncio.sleep(10)
                if not self._identity:
                    continue

                has_cron_jobs = bool(self._hub_cron_jobs)
                has_task_ledger = bool(self._task_ledger)

                if not has_cron_jobs and not has_task_ledger:
                    continue

                now = time.time()

                # --- Hub cron jobs ---
                if has_cron_jobs:
                    fired: List[str] = []
                    remove_ids: List[str] = []

                    for job in self._hub_cron_jobs:
                        if now >= job.next_fire:
                            target = job.target
                            scope = self._resolve_scope(target)
                            if target in ("all", "*"):
                                to = "*"
                                scope = MessageScope.BROADCAST.value
                            else:
                                to = target

                            msg = HubMessage(
                                action="message",
                                from_agent=self._identity.agent_id,
                                from_identity="hub-cron",
                                to=to,
                                content=f"[cron {job.id}] {job.message}",
                                scope=scope,
                            )
                            await self._route_message(msg)

                            # Self-targeted cron: _route_message skips self
                            # (open channel model), so deliver directly.
                            my_identity = (
                                self._identity.identity
                                if self._identity
                                else ""
                            )
                            if (
                                my_identity
                                and target == my_identity
                            ):
                                await self._on_message_received(msg)

                            logger.info(f"hub cron fired: {job.id}" f" -> {job.target}")
                            fired.append(job.id)

                            if job.recurring:
                                job.next_fire = now + job.interval_seconds
                            else:
                                remove_ids.append(job.id)

                    if remove_ids:
                        self._hub_cron_jobs = [
                            j for j in self._hub_cron_jobs if j.id not in remove_ids
                        ]

                # --- Task auto-cron: remind agents of due tasks ---
                if has_task_ledger and self._task_ledger is not None:
                    due_tasks = self._task_ledger.get_cron_due()
                    for task in due_tasks:
                        if task.assignee == self._identity.identity:
                            # Self-reminder: task is already in system
                            # prompt via roster injection, just touch
                            # updated_at to reset the cron timer
                            task.updated_at = time.time()
                            if self._task_ledger is not None:
                                self._task_ledger._save(task)
                        elif self._presence:
                            # Remote: send reminder to assignee
                            reminder_msg = HubMessage(
                                action="message",
                                from_agent=self._identity.agent_id,
                                from_identity="task-cron",
                                to=task.assignee,
                                content=(
                                    f"[task reminder: {task.id}]"
                                    f" {task.directive}\n"
                                    f"report to: {task.report_to}\n"
                                    f'use <task_checkpoint id="{task.id}">note</task_checkpoint> to save'
                                    f' progress or <task_complete id="{task.id}">result</task_complete>'
                                    " when done."
                                ),
                                scope=MessageScope.DIRECT.value,
                            )
                            agents = await self._presence.discover_agents_async()
                            for a in agents:
                                if a.identity == task.assignee:
                                    await self._deliver_to_agent(a, reminder_msg)
                            task.updated_at = time.time()
                            if self._task_ledger is not None:
                                self._task_ledger._save(task)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Cron loop error: {e}")

    def _build_dreaming_prompt(self, stream_entries: List[Dict]) -> str:
        """Build the prompt for the dreaming LLM call."""
        assert self._vault is not None

        # Format stream entries as readable text
        entry_lines = []
        for entry in stream_entries:
            ts = entry.get("ts", 0)
            ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
            etype = entry.get("type", "?")
            content = entry.get("content", "")[:300]
            frm = entry.get("from", "")
            to = entry.get("to", "")
            prefix = ""
            if frm:
                prefix = f"[{frm}"
                if to:
                    prefix += f" -> {to}"
                prefix += "] "
            entry_lines.append(f"  {ts_str} {etype}: {prefix}{content}")

        stream_text = "\n".join(entry_lines)

        # Get existing crystallized knowledge
        crystallized = self._vault.get_crystallized()
        crystal_section = crystallized if crystallized else "(none yet)"

        return (
            "You are reviewing your recent activity to extract durable insights.\n"
            "\n"
            "Recent activity (from vault stream):\n"
            f"{stream_text}\n"
            "\n"
            "Existing crystallized knowledge:\n"
            f"{crystal_section}\n"
            "\n"
            "Extract 3-5 concise insights from your recent activity that would be "
            "valuable to remember across sessions. Focus on:\n"
            "- Patterns you noticed in the codebase\n"
            "- Debugging techniques that worked\n"
            "- User preferences or communication style\n"
            "- Architectural decisions and their rationale\n"
            "- Mistakes to avoid\n"
            "\n"
            "Format each insight as a single paragraph. Do not repeat insights "
            "already in your crystallized knowledge."
        )

    async def _dreaming_llm_call(self, prompt: str) -> Optional[str]:
        """Make a background LLM call for dreaming.

        Uses the existing provider instance directly (stateless call)
        so it does NOT interfere with the user's active conversation.
        Returns the LLM response text, or None if unavailable.
        """
        try:
            llm_service = (
                self.event_bus.get_service("llm_service") if self.event_bus else None
            )
            if not llm_service:
                return None

            api_service = getattr(llm_service, "api_service", None)
            if not api_service:
                return None

            provider = getattr(api_service, "_provider", None)
            if not provider:
                return None

            # Make a simple one-shot call using the provider directly.
            # This bypasses APICommunicationService state (cancel flags,
            # token tracking, etc.) so it's safe to run concurrently.
            messages = [
                {"role": "user", "content": prompt},
            ]

            response = await provider.call(messages=messages)
            content = response.get_text_content()
            return content.strip() if content else None

        except Exception as e:
            logger.debug(f"dreaming: LLM call failed: {e}")
            return None

    async def _coordinator_cleanup(self) -> None:
        """Coordinator duty: clean up dead agents, reassign work."""
        assert self._presence is not None
        agents = await self._presence.discover_agents_async(include_self=True)
        # discover_agents_async already cleans dead presence files
        # Check if any assigned work needs reassignment
        if self._work_queue:
            for slot in self._work_queue.get_all():
                if slot.status == "assigned" and slot.assigned_to:
                    # Check if assigned agent still exists
                    alive = any(a.identity == slot.assigned_to for a in agents)
                    if not alive:
                        dead_agent = slot.assigned_to
                        slot.status = "pending"
                        slot.assigned_to = None
                        logger.info(
                            f"Reassigning work {slot.id}: "
                            f"agent {dead_agent} is dead"
                        )

    async def _try_assign_work(self) -> None:
        """Try to assign pending work to idle agents using capability matching."""
        if not self._work_queue:
            return
        pending = self._work_queue.get_pending()
        if not pending:
            return

        assert self._presence is not None
        agents = await self._presence.discover_agents_async()

        for slot in sorted(pending, key=lambda s: -s.priority):
            best = self._work_queue.find_best_agent(agents, slot)
            if not best:
                continue
            # Find the agent identity for socket path
            target = next(
                (a for a in agents if getattr(a, "identity", "") == best),
                None,
            )
            if not target:
                continue
            claimed = self._work_queue.claim_by_id(slot.id, best)
            if not claimed:
                continue
            # Send work assignment via socket
            msg = HubMessage(
                action="message",
                from_identity="hub",
                to=best,
                content=(
                    f"[work assignment from hub]\n"
                    f"task: {slot.task}\n"
                    f"priority: {slot.priority}\n"
                    f"context: {slot.context}"
                ),
                scope=MessageScope.DIRECT.value,
            )
            await AgentMessenger.send_to_agent(target.socket_path, msg)

    async def _announce_to_peers(self, peers: List[AgentRuntime]) -> None:
        """Announce this agent to all existing peers, triggering their LLM.

        This is what makes agents social - when a new agent joins,
        every existing agent gets a message injected into their
        conversation telling them who joined and what they're working on.
        The LLM naturally responds, often offering help.
        """
        if not self._identity:
            return

        # Build a summary of everyone on the hub for the announcement
        roster_lines = []
        for peer in peers:
            task_info = f" working on: {peer.current_task}" if peer.current_task else ""
            coord = " (coordinator)" if peer.is_coordinator else ""
            roster_lines.append(f"  - {peer.identity}{coord}: {peer.state}{task_info}")

        roster_summary = "\n".join(roster_lines) if roster_lines else "  (none)"

        for peer in peers:
            intro = HubMessage(
                action="message",
                from_agent=self._identity.agent_id,
                from_identity=self._identity.identity,
                to=peer.identity,
                content=(
                    f"agent '{self._identity.identity}' just came online "
                    f"in project {self._identity.project}.\n"
                    f"cwd: {os.getcwd()}\n"
                    f"current hub roster:\n{roster_summary}\n"
                    f"if you need help with anything, let them know.\n"
                    f"respond back using: "
                    f'<hub_msg to="{self._identity.identity}">your message</hub_msg>'
                ),
                scope=MessageScope.DIRECT.value,
            )
            await self._deliver_to_agent(peer, intro)
            logger.info(f"Announced to {peer.identity}")

    async def _on_message_received(self, message: HubMessage) -> None:
        """Handle an incoming message from another agent."""
        # Dedup check
        msg_id = getattr(message, "id", "") or ""
        if msg_id and msg_id in self._seen_messages:
            logger.debug(f"Dedup: skipping already-seen message {msg_id}")
            return
        if msg_id:
            self._seen_messages[msg_id] = None
            while len(self._seen_messages) > 1000:
                self._seen_messages.popitem(last=False)

        # Context control-plane traffic — dispatch without vault/display
        if message.action == "context_ledger_update":
            context_svc = self._get_context_service()
            bridge = (
                context_svc.get_hub_bridge()
                if context_svc and hasattr(context_svc, "get_hub_bridge")
                else None
            )
            if bridge is not None:
                try:
                    bridge.on_peer_broadcast(message.metadata or {})
                except Exception as e:
                    logger.debug("on_peer_broadcast failed: %s", e)
            return

        if message.action == "roster_update":
            try:
                parsed = json.loads(message.content)
                # Roster must be a list of dicts. Reject malformed payloads
                # so _inject_roster_context doesn't crash on .get() calls.
                if isinstance(parsed, list) and all(
                    isinstance(a, dict) for a in parsed
                ):
                    self._roster = parsed
                else:
                    logger.warning(
                        f"Ignoring malformed roster_update from "
                        f"{message.from_identity}: not a list of dicts"
                    )
            except Exception:
                pass
            return

        # NOTE: _exit_waiting_state() is NOT called here anymore.
        # It moves to the TRIGGER_LLM_CONTINUE decision block below,
        # so only messages that actually trigger the LLM will wake us.
        # Previously this fired for every message (including peer chatter),
        # causing re-trigger loops via _retry_continue.

        # Log to vault
        if self._vault:
            self._vault.append_stream(
                "received",
                message.content,
                from_agent=message.from_identity,
                to_agent=self._identity.identity if self._identity else "",
            )

        # Auto-create TaskCard for incoming task assignments.
        # Messages with task metadata or work-assignment patterns
        # get persisted to disk so they survive compaction.
        if (
            self._task_ledger
            and self._identity
            and message.to == self._identity.identity
            and message.from_identity not in ("task-cron", "hub-cron")
        ):
            content_lower = message.content.lower()
            # Don't auto-create tasks from forwarded task-cron content
            is_cron_echo = (
                "[task reminder" in content_lower
                or "qa needed]" in content_lower
                or "<task_complete>" in content_lower
                or "<task_checkpoint>" in content_lower
            )
            is_task = not is_cron_echo and (
                message.metadata.get("task_assignment")
                or "[work assignment" in content_lower
            )
            if is_task:
                # Extract directive from the message content
                directive = message.content[:500]
                card = self._task_ledger.create(
                    assigner=message.from_identity,
                    assignee=self._identity.identity,
                    directive=directive,
                    report_to=message.from_identity,
                )
                logger.info(
                    f"Auto-created task {card.id} from" f" {message.from_identity}"
                )

        # Track active thread so <hub_reply> can auto-fill thread_id/reply_to
        if getattr(message, "thread_id", "") and message.thread_id != message.id:
            # Only track if this is part of an existing thread (not the thread root)
            self._active_thread_id = message.thread_id
            self._active_thread_msg_id = message.id
        elif getattr(message, "thread_id", ""):
            # Thread root — still track it so first reply can continue it
            self._active_thread_id = message.thread_id
            self._active_thread_msg_id = message.id

        # Display the message visually in the UI
        self._display_hub_message(message)

        # Forward to bridge (skip system/cron noise)
        if (
            self._bridge
            and message.from_identity not in ("hub-cron", "task-cron")
            and message.action != "roster_update"
        ):
            my_name = self._identity.identity if self._identity else "?"
            from_name = message.from_identity
            is_arrival = "just came online" in message.content
            is_departure = "is going offline" in message.content
            if is_arrival:
                # Extract just the agent name from "agent 'X' just came online ..."
                await self._bridge_forward(f"[hub] {from_name} came online")
                try:
                    from kollabor_ai.notifications.producer import push_env

                    push_env(
                        self.event_bus,
                        "joined",
                        f"{from_name} joined",
                        kind="peer_online",
                        collapse_key=f"join:{from_name}",
                    )
                except Exception:
                    pass
            elif is_departure:
                await self._bridge_forward(f"[hub] {from_name} is going offline")
                try:
                    from kollabor_ai.notifications.producer import push_env

                    push_env(
                        self.event_bus,
                        "changed",
                        f"{from_name} left",
                        kind="peer_offline",
                        collapse_key=f"leave:{from_name}",
                    )
                except Exception:
                    pass
                # Release the departing agent's claims (including its
                # hub_identity:<name> reservation) so the coordinator can
                # respawn that identity without hitting "already reserved".
                # Without this, claims linger in the coordinator's
                # in-memory _claims dict until process restart.
                try:
                    if self._change_feed and from_name:
                        result = self._change_feed.release_all(from_name)
                        released = result.get("paths", [])
                        if released:
                            logger.info(
                                f"Released {len(released)} stale claim(s) from "
                                f"departing {from_name}: {released}"
                            )
                except Exception as e:
                    logger.debug(
                        f"Failed to release claims for departing {from_name}: {e}"
                    )
            elif message.to == my_name or message.to in (
                "*",
                "all",
                "everyone",
                "team",
                "project",
            ):
                await self._bridge_forward(
                    f"[{from_name} -> {my_name}] {message.content}"
                )
            else:
                # Observed message (not directed at us)
                await self._bridge_forward(
                    f"[{from_name} -> {message.to}] (observed) {message.content}"
                )

        # Inject into llm_service.conversation_history and trigger LLM
        llm_service = (
            self.event_bus.get_service("llm_service") if self.event_bus else None
        )
        if llm_service and hasattr(llm_service, "conversation_history"):
            my_name = self._identity.identity if self._identity else ""
            is_intended = message.to == my_name or message.to in (
                "*",
                "all",
                "everyone",
                "team",
                "project",
            )

            # Build the injected content
            source_agent = (
                message.metadata.get("source_agent", "")
                if hasattr(message, "metadata") and message.metadata
                else ""
            )
            is_human_elsewhere = (
                message.from_agent == "human"
                and source_agent
                and source_agent != my_name
            )
            thread_id = getattr(message, "thread_id", "")
            reply_to = getattr(message, "reply_to", "")
            thread_header = ""
            if thread_id and thread_id != message.id:
                # This message is a reply in an existing thread
                thread_header = f" [thread:{thread_id[:8]}]"
                if reply_to:
                    thread_header += f" [reply-to:{reply_to[:8]}]"
            formatted = (
                f"[hub channel: {message.from_identity} -> {message.to}{thread_header}]\n"
                f"{message.content}"
            )
            if is_human_elsewhere:
                formatted += (
                    f"\n(the human is typing in {source_agent}'s window. "
                    f"do NOT relay, repeat, or respond to {source_agent} "
                    f"about this message — they already see it.)"
                )
            elif not is_intended:
                formatted += (
                    f"\n(this message was sent to {message.to}. "
                    f"you do not need to respond unless this is relevant "
                    f"to your current task or you can add value to the discussion.)"
                )
            elif message.to == my_name:
                formatted += (
                    "\n\n[hub wake instruction]\n"
                    "This message is addressed to you. Treat it as the current "
                    "user request. If it asks you to work, investigate, report, "
                    "or continue, start now and use tools as needed. Do not "
                    "return <wait_for_user/> unless the message explicitly "
                    "tells you to stand by."
                )

            try:
                from kollabor_events.data_models import ConversationMessage

                is_departure = "is going offline" in message.content
                should_trigger_llm = (
                    is_intended
                    and not is_departure
                    and not is_human_elsewhere
                )

                # If a parked agent is being woken, inject the wake catch-up
                # before the hub message. The direct assignment must stay as
                # the final user message for the continuation turn.
                if (
                    should_trigger_llm
                    and self._identity
                    and self._identity.state == "waiting"
                ):
                    await self._exit_waiting_state()

                # Build metadata, include bridge info if present
                msg_metadata = {
                    "hub_message": True,
                    "hub_from": message.from_identity,
                    "hub_to": message.to,
                    "hub_is_intended": is_intended,
                    "hub_scope": getattr(message, "scope", "direct"),
                    "hub_message_id": message.id,
                    "hub_thread_id": getattr(message, "thread_id", ""),
                    "hub_reply_to": getattr(message, "reply_to", ""),
                }
                # Pass through bridge metadata for relay
                if hasattr(message, "metadata") and message.metadata:
                    if message.metadata.get("bridge_platform"):
                        msg_metadata["bridge_platform"] = message.metadata[
                            "bridge_platform"
                        ]

                async with self._history_lock:
                    llm_service.conversation_history.append(
                        ConversationMessage(
                            role="user",
                            content=formatted,
                            metadata=msg_metadata,
                        )
                    )
                    # Log to JSONL so injected hub messages appear in conversation log
                    if (
                        hasattr(llm_service, "conversation_logger")
                        and llm_service.conversation_logger
                    ):
                        try:
                            await llm_service.conversation_logger.log_system_message(
                                formatted,
                                parent_uuid=getattr(
                                    llm_service, "current_parent_uuid", None
                                ),
                                subtype="hub_incoming",
                            )
                        except Exception:
                            pass
                # Trigger LLM if this agent is the intended target (or broadcast).
                # Skip departures to avoid feedback loops.
                # Skip human-elsewhere (human typing in another agent's window).
                # Loop prevention system handles runaway triggers.
                if should_trigger_llm:
                    await self.event_bus.emit_with_hooks(
                        EventType.TRIGGER_LLM_CONTINUE,
                        {
                            "source": f"hub:{message.from_identity}",
                            "content": message.content,
                        },
                        "hub_plugin",
                    )
            except Exception as e:
                logger.error(f"Hub message trigger failed: {e}")

    # Fallback colors when identity isn't in the gem pool
    _FALLBACK_COLORS = [
        (120, 200, 255),
        (255, 180, 100),
        (150, 255, 150),
        (255, 140, 180),
        (200, 170, 255),
        (100, 240, 220),
    ]

    def _get_agent_color(self, identity: str) -> tuple:
        """Get the gem color for an identity, or hash-based fallback."""
        gem = POOL_BY_NAME.get(identity)
        if gem:
            return gem.color_rgb
        # Numbered variants (peridot-2) -- strip the suffix
        base = identity.rsplit("-", 1)[0] if "-" in identity else identity
        gem = POOL_BY_NAME.get(base)
        if gem:
            return gem.color_rgb
        # Fallback for non-gem identities
        idx = hash(identity) % len(self._FALLBACK_COLORS)
        return self._FALLBACK_COLORS[idx]

    def _render_hub_box(
        self, from_name: str, to_name: str, content: str, observing: bool = False
    ) -> None:
        """Render a hub message through the theme system's agent message type.

        Routes through display_message_sequence with type "agent" so the
        renderer's agent_message() method handles all TagBox rendering.

        Direct messages: bright, > tag
        Observed messages: dimmed tag color, ~ tag
        """
        renderer = self.event_bus.get_service("renderer") if self.event_bus else None
        if not renderer or not hasattr(renderer, "message_coordinator"):
            return

        color = self._get_agent_color(from_name)
        display_content = f"{from_name} -> {to_name}\n{content}"
        tag_char = " ~ " if observing else " > "

        try:
            renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "agent",
                        display_content,
                        {
                            "agent_color": color,
                            "tag_char": tag_char,
                            "observing": observing,
                        },
                    )
                ]
            )
        except Exception as e:
            logger.warning(f"Hub message display failed: {e}")

    def _display_hub_message(self, message: HubMessage) -> None:
        """Display an incoming hub message with agent-colored TagBox.

        Direct: bright box with > tag in sender's color
        Observed: dim box with ~ tag, shows actual target
        Human-elsewhere: dim box with ~ tag, shows source agent
        """
        my_name = self._identity.identity if self._identity else "?"
        is_intended = message.to == my_name or message.to in (
            "*",
            "all",
            "everyone",
            "team",
            "project",
        )

        # Human typing in another agent's window: show as observed
        # with the source agent as the target, not our name
        source_agent = (
            message.metadata.get("source_agent", "")
            if hasattr(message, "metadata") and message.metadata
            else ""
        )
        is_human_elsewhere = (
            message.from_agent == "human"
            and source_agent
            and source_agent != my_name
        )

        if is_human_elsewhere:
            # Show "user -> koordinator" (observed), not "user -> lapis"
            self._render_hub_box(
                message.from_identity,
                source_agent,
                message.content,
                observing=True,
            )
        elif is_intended:
            self._render_hub_box(message.from_identity, my_name, message.content)
        else:
            self._render_hub_box(
                message.from_identity,
                message.to,
                message.content,
                observing=True,
            )

    def _display_outgoing_message(self, to_name: str, content: str) -> None:
        """Display an outgoing hub message with agent-colored TagBox."""
        my_name = self._identity.identity if self._identity else "?"
        self._render_hub_box(my_name, to_name, content)

    async def _inject_roster_context(self, context, event=None):
        """Inject hub roster into conversation history before LLM calls.

        This is the SOCIAL LAYER. The LLM sees who else is working,
        what they're doing, and can proactively offer help.

        Injects roster as the first system message in conversation_history
        (the actual list the API call uses), updating it each turn.
        """
        if not self._identity or not self._roster:
            return context

        # Build roster block
        lines = []
        lines.append("--- hub context ---")
        lines.append(f'you are "{self._identity.identity}" on the kollabor hub.')
        if self._identity.is_coordinator:
            lines.append("you are the coordinator.")
        lines.append("")

        if self._roster:
            lines.append("active agents:")
            for agent in self._roster:
                if not isinstance(agent, dict):
                    continue
                status = agent.get("state", "unknown")
                task = agent.get("current_task", "")
                ident = agent.get("identity", "?")
                coord = " (coordinator)" if agent.get("is_coordinator") else ""

                if task:
                    lines.append(f"  {ident}{coord} - {status}: {task}")
                else:
                    lines.append(f"  {ident}{coord} - {status}")
        else:
            lines.append("no other agents online.")

        lines.append("")

        auto_help = True
        if self.config:
            auto_help = self.config.get("plugins.hub.auto_help", True)

        lines.append("to message an agent, ALWAYS use this exact format:")
        lines.append('<hub_msg to="identity">your message</hub_msg>')
        lines.append("")
        lines.append(
            "IMPORTANT: when asked to delegate, coordinate, or assign tasks "
            "to other agents, you MUST use <hub_msg> tags. without them, "
            "your message will NOT reach the other agent. never describe "
            "what you would say -- actually say it with the tag."
        )

        if auto_help and self._roster:
            lines.append(
                "if you can assist another agent with their current task, "
                "proactively offer help via hub_msg."
            )

        route_untagged = False
        if self.config:
            route_untagged = self.config.get(
                "plugins.hub.route_untagged_to_coordinator", False
            )
        if route_untagged and not self._identity.is_coordinator:
            lines.append(
                "note: your untagged responses are auto-routed to "
                "the coordinator. use <hub_msg> tags only when you "
                "need to message a specific non-coordinator agent."
            )

        lines.append("")
        lines.append("agent operations (XML tags parsed from your responses):")
        lines.append('  <hub_spawn name="lapis">task description</hub_spawn>')
        lines.append(
            '  <hub_spawn name="sapphire" type="research">'
            "research task</hub_spawn>"
        )
        lines.append(
            "  note: name is a hub identity. to choose an agent bundle, "
            'use type="coder" or type="research".'
        )
        lines.append("  <hub_work/>  -- view work queue")
        lines.append("  <hub_queue>task description</hub_queue>  -- add to queue")
        lines.append('  <hub_claim/>  or  <hub_claim id="slot-id"/>  -- claim work')
        lines.append('  <hub_vault name="identity"/>  -- read agent vault summary')
        lines.append("  <hub_vaults/>  -- list all vaults")
        lines.append(
            '  <hub_cron_add target="name" interval="5m">' "message</hub_cron_add>"
        )
        lines.append("  <hub_cron_list/>  -- list cron jobs")
        lines.append("  <hub_cron_delete>job-id</hub_cron_delete>")
        lines.append(
            '  <hub_capture name="agent-name" lines="50"/>' "  -- capture agent output"
        )

        lines.append("--- end hub context ---")

        roster_block = "\n".join(lines)

        # Inject active tasks (compaction-proof -- lives on disk, injected
        # into system prompt every LLM turn, survives any compaction)
        if self._identity and self._task_ledger:
            my_tasks = self._task_ledger.get_active_for(self._identity.identity)
            if my_tasks:
                task_lines = ["", "--- active tasks (DO NOT FORGET) ---"]
                for task in my_tasks:
                    task_lines.append(
                        f"task {task.id} from {task.assigner}"
                        f" (priority {task.priority}):"
                    )
                    task_lines.append(f"  directive: {task.directive}")
                    if task.deliverable:
                        task_lines.append(f"  deliverable: {task.deliverable}")
                    task_lines.append(f"  report to: {task.report_to}")
                    if task.last_checkpoint_note():
                        task_lines.append(
                            f"  last checkpoint:" f" {task.last_checkpoint_note()}"
                        )
                    task_lines.append(f"  elapsed: {task.elapsed_str()}")
                    if task.is_timed_out():
                        task_lines.append("  WARNING: exceeded timeout, complete NOW")
                    if task.status == "qa_review":
                        task_lines.append("  STATUS: awaiting QA review")
                    task_lines.append("")
                task_lines.append(
                    "when done, use:"
                    ' <task_complete id="TASK_ID">result</task_complete>'
                )
                task_lines.append(
                    "to checkpoint progress:"
                    ' <task_checkpoint id="TASK_ID">'
                    "progress note</task_checkpoint>"
                )
                task_lines.append(
                    "to request QA:"
                    ' <task_qa id="TASK_ID">'
                    "result for review</task_qa>"
                )
                task_lines.append("--- end tasks ---")
                roster_block += "\n" + "\n".join(task_lines)

        # Inject scratchpad (live notes, survives compaction)
        if self._scratchpad:
            pad = self._scratchpad.get()
            if pad:
                roster_block += (
                    "\n\n--- scratchpad ---\n" f"{pad}\n" "--- end scratchpad ---"
                )

        # Inject session state (working context from previous session)
        if self._vault:
            state_prompt = self._session_state_mgr.get_injection_prompt(
                self._vault._vault_dir
            )
            if state_prompt:
                roster_block += f"\n\n{state_prompt}"

        # Inject active lane claims for this agent
        if self._change_feed and self._identity:
            claims_result = self._change_feed.get_claims()
            all_claims = claims_result.get("claims", [])
            if all_claims:
                claim_lines = ["\n--- active lane claims ---"]
                for c in all_claims:
                    task = c.get("task", "")
                    task_str = f" (task: {task})" if task else ""
                    claim_lines.append(
                        f"  {c.get('identity', '?')} -> "
                        f"{c.get('path', '?')}{task_str}"
                    )
                claim_lines.append("--- end claims ---")
                roster_block += "\n".join(claim_lines)

        # Inject recent file changes for subscribed patterns
        if self._change_feed and self._identity:
            my_subs = self._change_feed.get_subscriptions(self._identity.identity)
            if my_subs:
                recent = self._change_feed.get_recent(20)
                matching = []
                for change in recent.get("entries", []):
                    for pattern in my_subs:
                        if pattern.match(change.get("path", "")):
                            matching.append(change)
                            break
                if matching:
                    change_lines = ["\n--- recent file changes (subscribed) ---"]
                    for c in matching[:10]:
                        change_lines.append(
                            f"  {c.get('identity', '?')} "
                            f"{c.get('action', '?')} "
                            f"{c.get('path', '?')}"
                        )
                    change_lines.append("--- end changes ---")
                    roster_block += "\n".join(change_lines)

        # Inject into actual conversation history that gets sent to the API.
        # Find the llm_service's conversation_history and prepend/update
        # a hub context system message.
        try:
            llm_service = (
                self.event_bus.get_service("llm_service") if self.event_bus else None
            )
            if llm_service and hasattr(llm_service, "conversation_history"):
                history = llm_service.conversation_history
                if history:
                    # Check if first message is system prompt - append to it
                    first = history[0]
                    if getattr(first, "role", None) == "system":
                        content = getattr(first, "content", "") or ""
                        # Remove old hub context if present
                        if "--- hub context ---" in content:
                            idx = content.index("--- hub context ---")
                            content = content[:idx].rstrip()
                        first.content = content + "\n\n" + roster_block
                    else:
                        # No system message - inject one
                        from kollabor_events.data_models import ConversationMessage

                        history.insert(
                            0,
                            ConversationMessage(role="system", content=roster_block),
                        )
        except Exception as e:
            logger.debug(f"Roster injection error: {e}")

        return context

    async def _set_working(self, context, event=None):
        """Mark agent as working when LLM request starts.

        Preserves WAITING state if the agent emitted <wait_for_user/>
        during this turn — the continuation LLM call must not overwrite
        the waiting state.
        """
        from .presence_states import PresenceState

        if self._identity:
            if self._identity.state != PresenceState.WAITING.value:
                self._identity.state = AgentState.WORKING.value
            self._last_activity_at = time.time()
            messages = context.get("messages", [])
            for msg in reversed(messages):
                role = (
                    msg.get("role", "")
                    if isinstance(msg, dict)
                    else getattr(msg, "role", "")
                )
                if role == "user":
                    content = (
                        msg.get("content", "")
                        if isinstance(msg, dict)
                        else getattr(msg, "content", "")
                    )
                    if not content.startswith("<sys_msg>") and not content.startswith(
                        "[message from"
                    ):
                        self._identity.current_task = content[:200]
                        if self._vault:
                            self._vault.append_stream("user_input", content[:500])
                        break
        return context

    async def _set_idle(self, context, event_context=None):
        """Mark agent as idle when LLM response completes.

        Preserves WAITING state if the agent emitted <wait_for_user/>
        during this response — the waiting state must survive until
        cooldown expires or the agent is woken.
        """
        from .presence_states import PresenceState

        if self._identity:
            if self._identity.state != PresenceState.WAITING.value:
                self._identity.state = AgentState.IDLE.value
            self._last_activity_at = time.time()
        return context

    async def _broadcast_user_input(self, data, event=None):
        """Broadcast user input to all peers so they see what the human said."""
        if not self._started or not self._identity or not self._presence:
            return data

        # User typing IS the event wait_for_user was waiting for.
        # Clear waiting state before anything else so the LLM_RESPONSE
        # handler won't suppress tool continuation on this turn.
        if self._identity.state == "waiting":
            await self._exit_waiting_state()

        user_content = (data.get("message") or "").strip()
        if not user_content:
            return data

        # Skip in attach mode - input goes directly to remote agent via proxy
        if self._cli_args and getattr(self._cli_args, "attach", None):
            return data

        # Skip slash commands - those are local
        if user_content.startswith("/"):
            return data

        # Skip system/hub messages that were injected
        if user_content.startswith("<sys_msg>") or user_content.startswith(
            "[message from"
        ):
            return data

        # Truncate for sanity
        user_content = user_content[:500]

        # Resolve display name for the human
        user_name = os.environ.get("USER", "user")
        if self.config:
            user_name = self.config.get("plugins.hub.user_name", user_name)

        peers = await self._presence.discover_agents_async()
        if not peers:
            return data

        # Include source agent identity so receivers know which window
        # the human typed in.  This prevents other agents from
        # auto-responding with messages directed at the source agent
        # (otherwise coordinator sees "user -> *", responds with
        # <hub_msg to="lapis">, and lapis gets a message "from
        # coordinator" even though the human was typing locally).
        source_agent = self._identity.identity if self._identity else ""
        for peer in peers:
            try:
                msg = HubMessage(
                    action="message",
                    from_agent="human",
                    from_identity=user_name,
                    to="*",
                    content=user_content,
                    scope=MessageScope.BROADCAST.value,
                    metadata={"source_agent": source_agent},
                )
                await self._deliver_to_agent(peer, msg)
            except Exception:
                pass

        return data

    async def _crystal_nudge_on_input(self, data, event=None):
        """Inject relevant crystallized memories when user input matches.

        Runs on USER_INPUT_POST at DISPLAY priority. Searches both the
        project and global crystal stores for entries whose keywords
        match the user's message, then injects matching summaries as a
        system message so the LLM has relevant context from past sessions.
        """
        if not self._started:
            return data
        if not self._crystal_store and not self._global_crystal_store:
            return data

        user_content = (data.get("message") or "").strip()
        if not user_content or len(user_content) < 10:
            return data

        # Skip slash commands and system messages
        if user_content.startswith("/") or user_content.startswith("<sys_msg>"):
            return data

        # Collect matches from both tiers, deduplicated by entry ID.
        # Project store is searched first; global fills remaining slots.
        matches = []
        seen_ids: set = set()
        try:
            if self._crystal_store:
                for entry in self._crystal_store.nudge(user_content, top_k=5):
                    if entry.id not in seen_ids:
                        matches.append(("project", entry))
                        seen_ids.add(entry.id)
        except Exception as e:
            logger.debug("project crystal nudge error: %s", e)
        try:
            if self._global_crystal_store:
                for entry in self._global_crystal_store.nudge(user_content, top_k=5):
                    if entry.id not in seen_ids:
                        matches.append(("global", entry))
                        seen_ids.add(entry.id)
        except Exception as e:
            logger.debug("global crystal nudge error: %s", e)

        if not matches:
            logger.info("crystal nudge: no matches for input")
            return data

        logger.info(
            "crystal nudge: %d matches (%d project, %d global) for '%s'",
            len(matches),
            sum(1 for tier, _ in matches if tier == "project"),
            sum(1 for tier, _ in matches if tier == "global"),
            user_content[:60],
        )

        # Build nudge injection (label global entries so agent knows scope)
        nudge_lines = ["[crystal nudge] relevant memories from past sessions:"]
        for tier, entry in matches:
            prefix = "[global] " if tier == "global" else ""
            nudge_lines.append(f"  {prefix}{entry.summary_line()}")
        nudge_lines.append(
            "use crystal_read to see full details of any entry."
        )
        nudge_text = "\n".join(nudge_lines)

        # Inject as system message into conversation history
        llm_service = (
            self.event_bus.get_service("llm_service")
            if self.event_bus
            else None
        )
        if not llm_service:
            logger.info("crystal nudge: no llm_service available")
            return data
        if not hasattr(llm_service, "inject_system_message"):
            logger.info("crystal nudge: llm_service has no inject_system_message")
            return data

        try:
            async with self._history_lock:
                await llm_service.inject_system_message(
                    f"<sys_msg>\n{nudge_text}\n</sys_msg>",
                    subtype="crystal_nudge",
                )
            logger.info("crystal nudge: injected %d entries", len(matches))
        except Exception as e:
            logger.warning("crystal nudge injection error: %s", e)

        return data

    async def _parse_hub_messages(self, data, event=None):
        """Parse remaining hub XML tags from LLM response.

        Pipeline handles: hub_msg, hub_broadcast, hub_stop, hub_status,
        scratchpad*, state_update, task_*, change feed tags.
        This hook handles: agent ops tags, nudge engine, vault logging,
        bridge forwarding.
        """
        # Use raw response_text for PARSING (finding tags)
        response = data.get("response_text", "") or data.get("clean_response", "")
        if not response:
            return data

        # Log every response to vault (truncated)
        if self._vault and response.strip():
            self._vault.append_stream("response", response[:1000])

        # All hub XML tags are now handled by the pipeline
        # (registered in _register_pipeline_tools). This hook handles:
        # vault logging, nudge engine, bridge forwarding, coordinator routing.

        # Start from clean_response for display (already stripped of tags by parser)
        # Fall back to response_text only if clean_response isn't available
        cleaned = data.get("clean_response", response)
        # All hub XML tags are now handled by the pipeline.
        # See _register_pipeline_tools for the full list of 33 tags.



        # --- Nudge engine: observe behavior and maybe remind ---
        # NOTE: `response` (from data["response_text"]) is the RAW pre-parser
        # LLM output. The queue_processor emits the raw response as
        # response_text and the parser-cleaned version as clean_response.
        # This means all string checks for tags below (e.g. "<hub_msg" in
        # response) work correctly because the raw text still contains all
        # original XML tags before the response_parser strips them.
        if self._identity and self._nudge_engine:
            identity = self._identity.identity

            # Detect real tool usage via unified tool pipeline.
            # all_tools = XML tools parsed by response_parser (terminal, file ops, MCP, plugin)
            # has_native_tools = native API tool_use blocks from the model
            xml_tools = data.get("all_tools", [])
            has_native = data.get("has_native_tools", False)
            real_tool_tags = bool(xml_tools) or has_native

            # Observe what tools were used
            self._nudge_engine.observe_response(
                identity=identity,
                response=response,
                used_scratchpad=("<scratchpad>" in response),
                used_state_update=("<state_update>" in response),
                used_checkpoint=("<task_checkpoint" in response),
                used_hub_msg=("<hub_msg" in response),
                used_real_tools=real_tool_tags,
                edited_files=[
                    m.strip()
                    for m in re.findall(
                        r"<file_changed>(.*?)</file_changed>",
                        response,
                        re.DOTALL,
                    )
                ],
                claimed_files=[
                    m.strip()
                    for m in re.findall(
                        r'<lane_claim(?:\s+task="[^"]*")?\s*>(.*?)</lane_claim>',
                        response,
                        re.DOTALL,
                    )
                ],
            )

            # Check if we should nudge. Nudges are passive: inject a system
            # message that rides along with the agent's NEXT natural turn.
            # They never spawn turns themselves (that caused wake-from-park
            # loops -- agent says "..", nudge fires force_continue, loop).
            peers_online = len(self._roster) if self._roster else 0
            nudge = self._nudge_engine.evaluate(identity, peers_online)
            if nudge:
                if "loop detected" in nudge.lower():
                    self._loop_metrics["loop_nudges_fired"] += 1
                try:
                    llm_svc = (
                        self.event_bus.get_service("llm_service")
                        if self.event_bus
                        else None
                    )
                    if llm_svc and hasattr(llm_svc, "inject_system_message"):
                        async with self._history_lock:
                            await llm_svc.inject_system_message(
                                f"[system: {nudge}]",
                                subtype="hub_nudge",
                            )
                except Exception as e:
                    logger.debug(f"Nudge injection error: {e}")

        # Apply cleaned response -- write to BOTH keys unconditionally
        # so the queue_processor always gets the stripped version
        # regardless of which keys were in the original data dict.
        _hub_tag_markers = (
            "<hub_msg", "<hub_broadcast", "<hub_stop", "<hub_status",
            "<scratchpad", "<state_update", "<task_checkpoint",
            "<task_complete", "<task_approve", "<task_reject",
            "<lane_claim", "<lane_release", "<file_changed",
            "<file_watch", "<file_unwatch", "<feed_recent", "<feed_file",
            "<claims", "<hub_spawn", "<hub_queue", "<hub_claim",
            "<hub_work", "<hub_vault", "<hub_vaults",
            "<hub_cron", "<hub_capture", "<wait_for_user",
        )
        had_tags = any(m in response for m in _hub_tag_markers) if response else False
        data["response_text"] = cleaned
        data["clean_response"] = cleaned
        if had_tags:
            logger.info(
                f"Hub tag strip: had_tags={had_tags}, "
                f"cleaned_len={len(cleaned)}, "
                f"cleaned_preview={cleaned[:80]!r}"
            )

        # If the entire response was tags (nothing left after stripping),
        # suppress the assistant message render entirely.
        # EXCEPTION: don't suppress when there are pending native API tool
        # calls.  When the LLM emits only tool_use blocks (no text), the
        # cleaned response is naturally empty — that's expected, not a
        # signal to hide the tool results.  The queue_processor handles
        # native tool display independently of this flag.
        if not cleaned:
            has_native_tools = False
            try:
                llm_svc = self.event_bus.get_service("llm_service") if self.event_bus else None
                api_svc = getattr(llm_svc, "api_service", None) if llm_svc else None
                if api_svc and hasattr(api_svc, "has_pending_tool_calls"):
                    has_native_tools = api_svc.has_pending_tool_calls()
            except Exception:
                pass
            if not has_native_tools:
                data["suppress_display"] = True

        # If agent entered waiting state this turn, suppress force_continue.
        # The wait_for_user handler already set the state; we just need to
        # make sure the pipeline doesn't auto-continue us.
        if (
            self._identity
            and self._identity.state == "waiting"
        ):
            data["force_continue"] = False
            data["turn_complete"] = True
            logger.info("force_continue suppressed + turn_complete set (waiting state active)")

        # Bridge relay: forward ALL LLM responses to the bridge.
        # Hub messages from this agent are already forwarded at send time,
        # so only forward the cleaned text that remains after tag stripping
        # (the "natural language" part of the response).
        if cleaned:
            await self._bridge_forward(cleaned)

        # Route untagged responses to coordinator if enabled
        if not had_tags and cleaned:
            await self._maybe_route_to_coordinator(cleaned)

        return data

    async def _route_message(
        self, message: HubMessage
    ) -> List[Tuple[str, str]]:
        """Route a message to all agents (open channel).

        Every agent sees every message - like a Slack channel.
        The intended recipient is marked so others know they don't
        have to respond unless the topic is relevant to them.

        Returns:
            A list of (recipient_identity, rejection_reason) tuples.
            Empty list means all recipients accepted.
        """
        assert self._presence is not None
        rejections: List[Tuple[str, str]] = []

        # Log outgoing message to vault (skip cron noise)
        if self._vault and message.from_identity not in ("hub-cron", "task-cron"):
            self._vault.append_stream(
                "sent",
                message.content,
                from_agent=self._identity.identity if self._identity else "",
                to_agent=message.to,
            )

        # Broadcast to ALL agents except self (open channel model)
        # Self already sees the message via _display_outgoing_message
        agents = await self._presence.discover_agents_async()
        my_id = self._identity.agent_id if self._identity else ""

        # Gatekeeper: check if sender is approved for mesh participation.
        # Unapproved agents (pending/rejected) can only receive messages,
        # not send to others. Coordinator and self always bypass.
        sender_approved = True
        if (
            self._dns_registry
            and self._identity
            and not self._identity.is_coordinator
        ):
            sender_approved = self._dns_registry.is_approved(
                self._identity.identity
            )
        if not sender_approved:
            record = self._dns_registry.resolve(self._identity.identity)
            approval_state = record.approval_state if record else "unknown"
            logger.warning(
                f"Message from {self._identity.identity} blocked: "
                f"not approved (state={approval_state})"
            )
            return [("mesh", "sender not approved for mesh participation")]

        for agent in agents:
            if agent.agent_id == my_id:
                continue
            delivered = await self._deliver_to_agent(agent, message)
            if not delivered:
                # Build a reason string for the rejection
                cooldown_remaining = (
                    agent.cooldown_until - time.time()
                    if agent.cooldown_until
                    else 0
                )
                if cooldown_remaining > 0:
                    reason = f"in cooldown for {int(cooldown_remaining)}s"
                else:
                    reason = "in waiting state"
                rejections.append((agent.identity, reason))

        return rejections

    # Control-plane actions that must bypass the WAITING cooldown gate.
    # These never trigger an LLM turn — they only update silent local state.
    _CONTROL_PLANE_ACTIONS = frozenset(
        {
            "context_ledger_update",
            "roster_update",
        }
    )

    async def _deliver_to_agent(
        self, agent: AgentRuntime, message: HubMessage
    ) -> bool:
        """Deliver message to agent via socket, fall back to filesystem.

        Checks cooldown state before delivery. Returns False if the
        message was rejected due to cooldown, True if delivered.
        Control-plane actions (context_ledger_update, roster_update) always
        bypass the gate — they update silent local state and never wake the LLM.
        """
        from .presence_states import PresenceState

        now = time.time()

        # Control-plane traffic bypasses the WAITING gate entirely.
        # These messages never trigger a LLM turn so there is no loop risk.
        if message.action in self._CONTROL_PLANE_ACTIONS:
            success = await AgentMessenger.send_to_agent(agent.socket_path, message)
            if not success:
                await AgentMessenger.send_to_file(agent.agent_id, message)
            return True

        # Check cooldown only if the target is in waiting state
        if agent.state == PresenceState.WAITING.value:
            cooldown_active = (
                agent.cooldown_until is not None
                and agent.cooldown_until > now
            )

            if cooldown_active:
                # Check breakthrough conditions
                sender_is_coordinator = self._sender_is_coordinator(message)
                force_flag = message.force

                # Coordinator auto-breakthrough can be disabled via config
                auto_breakthrough = True
                if self.config:
                    auto_breakthrough = self.config.get(
                        "plugins.hub.coordinator_auto_breakthrough", True
                    )

                if sender_is_coordinator and not auto_breakthrough:
                    # Coordinator auto-breakthrough disabled; still
                    # allow explicit force flag.
                    if not force_flag:
                        self._loop_metrics["cooldown_rejections"] += 1
                        logger.info(
                            f"Rejected hub message to {agent.identity}: "
                            f"coordinator auto-breakthrough disabled"
                        )
                        return False

                if not sender_is_coordinator and not force_flag:
                    self._loop_metrics["cooldown_rejections"] += 1
                    logger.info(
                        f"Rejected hub message to {agent.identity}: "
                        f"in cooldown for {int(agent.cooldown_until - now)}s"
                    )
                    return False

                # Metrics for breakthroughs
                if sender_is_coordinator:
                    self._loop_metrics["coordinator_breakthroughs"] += 1
                else:
                    self._loop_metrics["force_breakthroughs"] += 1

                logger.info(
                    f"Breakthrough to {agent.identity}: "
                    f"{'coordinator' if sender_is_coordinator else 'force'}"
                )

            # Either cooldown expired OR breakthrough accepted — wake if self
            if self._is_self(agent):
                await self._exit_waiting_state()

        success = await AgentMessenger.send_to_agent(agent.socket_path, message)
        if not success:
            await AgentMessenger.send_to_file(agent.agent_id, message)
        return True

    def _resolve_scope(self, target: str) -> str:
        """Determine message scope from target string."""
        if target in ("*", "all", "everyone"):
            return MessageScope.BROADCAST.value
        elif target in ("team", "project"):
            return MessageScope.PROJECT.value
        return MessageScope.DIRECT.value

    async def _maybe_route_to_coordinator(self, response: str) -> None:
        """Auto-route untagged responses to coordinator if enabled.

        INTENTIONALLY SILENT path. Do NOT add cmd_results.append or
        force_continue here. This is the escape hatch for an agent
        that has run out of tool calls and is emitting a final summary
        to the (nonexistent) user — in hub mode there's no user, so we
        route that summary to the coordinator so it isn't lost.

        If this path triggered force_continue, the agent would be
        re-invoked, produce another "i'm done" summary, which would
        route again, triggering another continue, infinite loop.
        Silence here is the whole point — the agent's turn MUST end.

        The tagged <hub_msg> path (line ~2317) is different and DOES
        append to cmd_results + force_continue, because that's the
        agent explicitly saying "send this AND keep going." The
        10-second dedup on that path (_recent_hub_msgs at line ~2344)
        prevents accidental loops if the agent retries the same
        message.

        If you're "fixing" this to return feedback, STOP and read the
        history: fix was considered on 2026-04-11 and deliberately
        rejected. See docs/architecture/tool-calling-architecture.md.
        """
        if not self._identity or not self._started:
            return

        # Don't auto-route if we're in waiting state
        from .presence_states import PresenceState

        if self._identity.state == PresenceState.WAITING.value:
            return

        # coordinator doesn't route to itself
        if self._identity.is_coordinator:
            return

        # check flag
        enabled = False
        if self.config:
            enabled = self.config.get(
                "plugins.hub.route_untagged_to_coordinator", False
            )
        if not enabled:
            return

        # find coordinator identity from election state
        coordinator = self._get_coordinator_identity()
        if not coordinator:
            return

        # skip empty/trivial responses
        clean = response.strip()
        if not clean or len(clean) < 5:
            return

        # skip responses containing ANY XML tags -- agent is using tools
        # only route pure conversational text (no < followed by a tag name)
        if re.search(r"<[a-zA-Z_]", clean):
            return

        msg = HubMessage(
            action="message",
            from_agent=self._identity.agent_id,
            from_identity=self._identity.identity,
            to=coordinator,
            content=clean,
            scope=MessageScope.DIRECT.value,
        )

        await self._route_message(msg)

    def _get_coordinator_identity(self) -> Optional[str]:
        """Get coordinator's designation name from election state."""
        if not self._election:
            return None
        state = self._election.get_current_coordinator()
        if not state:
            return None
        return state.get("coordinator_identity")

    def _sender_is_coordinator(self, msg: "HubMessage") -> bool:
        """Check if the sender of a message is the elected coordinator."""
        coord = self._get_coordinator_identity()
        if not coord:
            return False
        return msg.from_identity == coord

    def _is_self(self, agent: "AgentRuntime") -> bool:
        """Check if the given agent is this plugin's identity."""
        if not self._identity:
            return False
        return agent.agent_id == self._identity.agent_id

    def _register_commands(self) -> None:
        """Register /hub slash command."""
        cmd = CommandDefinition(
            name="hub",
            description="Agent mesh hub",
            category=CommandCategory.CUSTOM,
            plugin_name=self.name,
            aliases=["mesh"],
            handler=self._handle_hub_command,
            mode=CommandMode.INSTANT,
            subcommands=[
                SubcommandInfo("on", "", "Enable hub (persistent, requires restart)"),
                SubcommandInfo("off", "", "Disable hub (persistent, requires restart)"),
                SubcommandInfo(
                    "user", "[name]", "Show or set your display name on the mesh"
                ),
                SubcommandInfo("status", "", "Show hub status and agents"),
                SubcommandInfo("msg", "<agent> <message>", "Send message to agent"),
                SubcommandInfo("broadcast", "<message>", "Broadcast to all agents"),
                SubcommandInfo("work", "", "List pending work"),
                SubcommandInfo("queue", "<task>", "Queue work for next agent"),
                SubcommandInfo("claim", "[id]", "Claim a work slot"),
                SubcommandInfo("whoami", "", "Show your identity"),
                SubcommandInfo("vault", "[name]", "Show vault info for an agent"),
                SubcommandInfo("vaults", "", "List all agent vaults"),
                SubcommandInfo("org", "<name> [mission]", "Launch an organization"),
                SubcommandInfo("orgs", "", "List available organizations"),
                SubcommandInfo("feed", "", "Live dashboard of agent activity"),
                SubcommandInfo(
                    "console", "", "Agent management console (sidebar + feed)"
                ),
                SubcommandInfo("spawn", "<name> <task>", "Spawn a new agent"),
                SubcommandInfo("capture", "<name|all> [lines]", "Capture agent output"),
                SubcommandInfo("stop", "<identity|all>", "Stop agent(s) on the mesh"),
                SubcommandInfo("agents", "", "List all active agents"),
                SubcommandInfo(
                    "cron",
                    "add|list|delete|clear",
                    "Schedule recurring hub messages",
                ),
                SubcommandInfo(
                    "notify",
                    "enable|disable|channel|url|threshold|test|status",
                    "Manage notification settings",
                ),
                SubcommandInfo(
                    "tasks",
                    "list|mine|assign|cancel|status",
                    "Task management",
                ),
                SubcommandInfo(
                    "bridge",
                    "status|send|enable|disable",
                    "Messaging bridge (Telegram, etc)",
                ),
                SubcommandInfo(
                    "wake", "<identity>", "Manually wake a waiting agent"
                ),
                SubcommandInfo(
                    "approve", "<identity>", "Approve agent for mesh participation"
                ),
                SubcommandInfo(
                    "reject", "<identity> [reason]", "Reject agent from mesh"
                ),
                SubcommandInfo(
                    "pending", "", "List agents pending approval"
                ),
                SubcommandInfo(
                    "metrics", "", "Show loop prevention metrics"
                ),
                SubcommandInfo(
                    "dns",
                    "resolve|find|trust|leaderboard|endorse|keys [args]",
                    "Agent DNS: identity, trust, capabilities",
                ),
            ],
        )
        assert self.command_registry is not None
        self.command_registry.register_command(cmd)

    async def _handle_hub_command(self, command_or_args=None, **kwargs) -> str:
        """Handle /hub slash command.

        Accepts either a SlashCommand object (from executor) or a string (direct call).
        """
        # Extract args string from SlashCommand or use directly
        if command_or_args is None:
            args_str = ""
        elif isinstance(command_or_args, str):
            args_str = command_or_args
        elif hasattr(command_or_args, "args"):
            # SlashCommand object -- args is List[str]
            args_str = " ".join(command_or_args.args) if command_or_args.args else ""
        else:
            args_str = str(command_or_args)

        parts = args_str.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "on":
            if self.config:
                self.config.save_key("plugins.hub.enabled", True)
            return "hub enabled (takes effect next session)"
        elif subcmd == "off":
            if self.config:
                self.config.save_key("plugins.hub.enabled", False)
            return "hub disabled (takes effect next session)"
        elif subcmd == "user":
            name = rest.strip()
            if not name:
                current = os.environ.get("USER", "user")
                if self.config:
                    current = self.config.get("plugins.hub.user_name", current)
                return f"hub user: {current}\nset with: /hub user <name>"
            if self.config:
                self.config.save_key("plugins.hub.user_name", name)
            return f"hub user set to: {name}"
        elif subcmd == "status":
            # Phase 4.5 step 8: route through state_service so /hub status
            # works in attach mode (the daemon owns hub state; client's
            # _format_status would return "hub: not connected" otherwise).
            text = await self._read_hub_text_via_state_service("get_hub_status_text")
            if text is not None:
                return text
            return self._format_status()
        elif subcmd == "whoami":
            text = await self._read_hub_text_via_state_service("get_hub_whoami_text")
            if text is not None:
                return text
            return self._format_whoami()
        elif subcmd == "msg":
            text = await self._write_hub_via_state_service(
                "hub_send_msg", rest
            )
            if text is not None:
                return text
            return await self._handle_msg_command(rest)
        elif subcmd == "broadcast":
            text = await self._write_hub_via_state_service(
                "hub_broadcast", rest
            )
            if text is not None:
                return text
            return await self._handle_broadcast_command(rest)
        elif subcmd == "work":
            text = await self._read_hub_text_via_state_service("get_hub_work_text")
            if text is not None:
                return text
            return self._format_work()
        elif subcmd == "queue":
            return self._handle_queue_command(rest)
        elif subcmd == "claim":
            return await self._handle_claim_command(rest)
        elif subcmd == "vault":
            return self._format_vault(rest)
        elif subcmd == "vaults":
            return self._format_all_vaults()
        elif subcmd == "org":
            return await self._handle_org_command(rest)
        elif subcmd == "orgs":
            return self._format_orgs()
        elif subcmd == "feed":
            return await self._handle_feed_command()
        elif subcmd == "console":
            return await self._handle_console_command()
        elif subcmd == "spawn":
            return await self._handle_spawn_command(rest)
        elif subcmd == "capture":
            return await self._handle_capture_command(rest)
        elif subcmd == "stop":
            return await self._handle_stop_command(rest)
        elif subcmd == "agents":
            return await self._handle_agents_command()
        elif subcmd == "cron":
            return self._handle_cron_command(rest)
        elif subcmd == "notify":
            return await self._handle_notify_command(rest)
        elif subcmd == "tasks":
            return await self._handle_tasks_command(rest)
        elif subcmd == "bridge":
            return await self._handle_bridge_command(rest)
        elif subcmd == "wake":
            return await self._handle_wake_command(rest)
        elif subcmd == "approve":
            return self._handle_approve_command(rest)
        elif subcmd == "reject":
            return self._handle_reject_command(rest)
        elif subcmd == "pending":
            return self._handle_pending_command()
        elif subcmd == "metrics":
            return self._format_metrics()
        elif subcmd == "dns":
            return self._handle_dns_command(rest)
        else:
            return self._format_status()

    def _handle_dns_command(self, args: str) -> str:
        """Handle /hub dns subcommands: resolve, find, trust, leaderboard, endorse, keys."""
        if not _DNS_AVAILABLE:
            return "dns: PyNaCl not installed\nrun: pip install pynacl"
        if not self._dns_registry:
            return "dns: not initialized (hub not started as agent)"

        parts = args.strip().split(maxsplit=1) if args.strip() else []
        sub = parts[0] if parts else "leaderboard"
        rest = parts[1] if len(parts) > 1 else ""

        if sub == "resolve":
            target = rest.strip()
            if not target:
                lines = self._dns_registry.format_roster(include_trust=True)
                if not lines:
                    return "dns roster: empty"
                return "dns roster:\n" + "\n".join(f"  {ln}" for ln in lines)
            record = self._dns_registry.resolve(target)
            if not record:
                return f"dns: '{target}' not found"
            lines = [
                f"designation: {record.designation}",
                f"aid:         {record.aid}",
                f"runtime:     {record.runtime}",
                f"state:       {record.state}",
                f"trust:       {record.trust_score:.3f}",
                f"pid:         {record.pid}",
                f"project:     {record.project}",
                f"public_key:  {record.public_key[:16]}...",
            ]
            if record.capabilities:
                lines.append(f"capabilities: {', '.join(record.capabilities)}")
            if record.is_coordinator:
                lines.append("role: coordinator")
            return "\n".join(lines)

        elif sub == "find":
            if not rest.strip():
                return "usage: /hub dns find <capability>"
            cap = rest.strip()
            if not self._dns_capabilities:
                return "dns: capabilities index not available"
            matches = self._dns_capabilities.query(cap)
            if not matches:
                return f"dns find: no agents with capability '{cap}'"
            lines = [f"agents with '{cap}':"]
            for designation, entry in matches[:10]:
                lines.append(f"  {designation}  [{entry.evidence}]  confidence={entry.confidence:.2f}")
            return "\n".join(lines)

        elif sub == "trust":
            target = rest.strip()
            if not target:
                return "usage: /hub dns trust <designation>"
            if not self._dns_reputation:
                return "dns: reputation not available"
            score = self._dns_reputation.get_score(target)
            trust = self._dns_reputation.get_trust(target)
            lines = [
                f"trust score for {target}:",
                f"  composite:    {trust:.3f}",
                f"  tasks done:   {score.tasks_completed}",
                f"  tasks failed: {score.tasks_failed}",
                f"  sessions:     {score.uptime_sessions}",
                f"  endorsements: {len(score.endorsements)}",
                f"  avg response: {score.avg_response_time_ms:.0f}ms",
            ]
            return "\n".join(lines)

        elif sub == "leaderboard":
            if not self._dns_reputation:
                return "dns: reputation not available"
            limit = 10
            try:
                if rest.strip():
                    limit = int(rest.strip())
            except ValueError:
                pass
            board = self._dns_reputation.get_leaderboard(limit=limit)
            if not board:
                return "dns leaderboard: no data yet"
            lines = ["dns leaderboard:"]
            for rank, (designation, trust) in enumerate(board, 1):
                bar = "█" * int(trust * 10) + "░" * (10 - int(trust * 10))
                lines.append(f"  {rank:2}. {designation:<14} {bar} {trust:.3f}")
            return "\n".join(lines)

        elif sub == "endorse":
            ep = rest.strip().split(maxsplit=1)
            if len(ep) < 2:
                return "usage: /hub dns endorse <designation> <capability>"
            target, cap = ep[0], ep[1]
            if not self._dns_reputation or not self._designation:
                return "dns: not running as named agent"
            from .dns.models import Endorsement
            endorsement = Endorsement(
                from_designation=self._designation,
                to_designation=target,
                capability=cap,
                weight=1.0,
            )
            new_trust = self._dns_reputation.add_endorsement(endorsement)
            return f"endorsed {target} for '{cap}' (new trust: {new_trust:.3f})"

        elif sub == "keys":
            target = rest.strip() or self._designation
            if not target:
                return "dns: no designation (not running as agent)"
            record = self._dns_registry.resolve(target)
            if not record:
                return f"dns: '{target}' not in registry"
            lines = [
                f"keys for {target}:",
                f"  public_key: {record.public_key}",
                f"  aid:        {record.aid}",
            ]
            if record.attestation:
                att = record.attestation
                lines += [
                    f"  attested_by: {att.issuer}",
                    f"  issued_at:   {att.issued_at:.0f}",
                    f"  sig:         {att.signature[:16]}...",
                ]
            return "\n".join(lines)

        else:
            return (
                "dns subcommands:\n"
                "  resolve [name]          resolve agent or list all\n"
                "  find <capability>       find agents by capability\n"
                "  trust <name>            show trust score breakdown\n"
                "  leaderboard [N]         top N by trust (default 10)\n"
                "  endorse <name> <cap>    endorse an agent's capability\n"
                "  keys [name]             show public key + AID"
            )

    def _get_orchestrator(self):
        """Get the agent orchestrator plugin via service registry."""
        if not self.event_bus:
            return None
        return self.event_bus.get_service("agent_orchestrator")

    def _resolve_identity_to_agent_name(self, identity: str) -> Optional[str]:
        """Resolve a hub identity (gem name) to the orchestrator agent name.

        The hub presence system tracks agents by identity (e.g. "lapis"),
        while the orchestrator tracks spawned subprocesses by session name.
        This bridges the gap by matching on PID when the target peer was
        launched by the local agent_orchestrator.

        Args:
            identity: Hub identity name (gem name like "lapis")

        Returns:
            Orchestrator agent name if found, None otherwise.
        """
        orch = self._get_orchestrator()
        if not orch or not self._presence:
            return None

        peer = self._get_peer_by_identity(identity)
        if not peer:
            return None

        # Match PID to orchestrator agent session
        for name, session in orch.orchestrator.get_all_agents().items():
            if session.pid == peer.pid:
                return name

        return None

    def _get_peer_by_identity(self, identity: str) -> Optional[AgentRuntime]:
        """Return a live/stale peer record by hub identity without cleanup."""
        if not self._presence:
            return None

        try:
            peers = self._presence.scan_all_presence()
        except Exception:
            peers = []

        for peer in peers:
            if peer.identity == identity:
                return peer
        return None

    async def _capture_peer_output(self, peer: AgentRuntime, lines: int) -> str:
        """Capture output directly from a live hub peer via its socket."""
        if not peer.socket_path:
            self._presence.cleanup_agent(peer)
            return f"agent '{peer.identity}' has no socket path, cleaned up"

        output_lines = await AgentMessenger.request_output(
            peer.socket_path,
            lines=lines,
            timeout=5.0,
        )
        if not output_lines:
            return f"no recent output from '{peer.identity}'"

        header = f"[{peer.identity}]"
        return header + "\n" + "\n".join(output_lines)

    async def _handle_spawn_command(self, rest: Any) -> str:
        """Handle /hub spawn <name> <task>.

        Three spawn modes:
          1. By identity: name matches a pool gem (e.g. "lapis")
             → uses that gem's agent_type from pool.json
             → fails if that identity is already online
          2. By agent_type: name is an agent type (e.g. "coder")
             → picks next available gem from pool with matching agent_type
             → fails if no gems available with that type
          3. Explicit identity + type: name is identity, type attr overrides
             → e.g. name="lapis" type="research"
        """
        orch = self._get_orchestrator()
        if not orch:
            return "error: agent orchestrator not available"

        requested_identity = ""
        agent_type_override = ""
        if isinstance(rest, dict):
            agent_name = str(rest.get("name", "")).strip()
            task = str(rest.get("task", "")).strip()
            requested_identity = str(rest.get("identity", "")).strip()
            agent_type_override = (
                str(rest.get("agent_type_override", "") or "").strip()
            )
            if not agent_name or not task:
                return "usage: /hub spawn <name> [identity=X] [type=X] <task>"
        else:
            raw = str(rest or "").strip()
            # Parse key=value args (identity=X, type=X) from the string
            import shlex
            try:
                tokens = shlex.split(raw)
            except ValueError:
                tokens = raw.split()
            agent_name = ""
            task_parts: List[str] = []
            for token in tokens:
                if token.startswith("identity="):
                    requested_identity = token.split("=", 1)[1].strip().strip('"')
                elif token.startswith("type="):
                    agent_type_override = token.split("=", 1)[1].strip().strip('"')
                elif not agent_name:
                    agent_name = token
                else:
                    task_parts.append(token)
            task = " ".join(task_parts)
            if not agent_name or not task:
                return "usage: /hub spawn <name> [identity=X] [type=X] <task>"

        # --- Resolve what was requested ---
        # Is agent_name a pool identity? (e.g. "lapis")
        pool_match = POOL_BY_NAME.get(agent_name)

        if pool_match:
            # MODE 1 or 3: spawning by identity
            resolved_identity = agent_name
            # Type override wins, then pool's agent_type, then fallback to "coder"
            effective_agent_type = (
                agent_type_override
                or pool_match.agent_type
                or "coder"
            )
            effective_skills = list(pool_match.skills or [])
        else:
            # MODE 2: spawning by agent_type (e.g. "coder")
            # Find next available gem from pool with matching agent_type
            effective_agent_type = agent_name
            resolved_identity = ""
            effective_skills: List[str] = []

            if requested_identity:
                requested_pool = POOL_BY_NAME.get(requested_identity)
                resolved_identity = requested_identity
                if requested_pool:
                    effective_agent_type = (
                        agent_type_override
                        or requested_pool.agent_type
                        or effective_agent_type
                    )
                    effective_skills = list(requested_pool.skills or [])

            # Get online identities to exclude
            online_identities = set()
            if self._presence:
                for peer in self._presence.scan_all_presence():
                    online_identities.add(peer.identity)

            # Find first pool gem with matching agent_type that isn't online
            if not resolved_identity:
                for gem in POOL_IDENTITIES:
                    if gem.agent_type == agent_name and gem.name not in online_identities:
                        resolved_identity = gem.name
                        effective_skills = list(gem.skills or [])
                        break

            # If the requested name is a real agent bundle but the pool does
            # not bind any identity to that type (for example "research"),
            # use the next free identity and keep the requested bundle type.
            if not resolved_identity and self.event_bus:
                try:
                    agent_mgr = self.event_bus.get_service("agent_manager")
                    agent_def = agent_mgr.get_agent(agent_name) if agent_mgr else None
                except Exception:
                    agent_def = None
                if agent_def is not None:
                    for gem in POOL_IDENTITIES:
                        if gem.name not in online_identities:
                            resolved_identity = gem.name
                            effective_skills = list(gem.skills or [])
                            break
                    if not resolved_identity:
                        return (
                            f"all identities are busy for agent_type '{agent_name}'. "
                            f"use hub_msg to assign work to an existing one."
                        )

            if not resolved_identity:
                online_with_type = [
                    g.name for g in POOL_IDENTITIES
                    if g.agent_type == agent_name and g.name in online_identities
                ]
                if online_with_type:
                    return (
                        f"all {agent_name} agents are busy: "
                        f"{', '.join(online_with_type)}. "
                        f"use hub_msg to assign work to an existing one."
                    )
                return (
                    f"no pool gem found with agent_type '{agent_name}'. "
                    f"check pool.json configuration."
                )

        # --- Check if already online ---
        if self._presence:
            for peer in self._presence.scan_all_presence():
                if peer.identity == resolved_identity:
                    return (
                        f"'{resolved_identity}' is already online "
                        f"(agent type: {effective_agent_type}). "
                        f"use hub_msg to send work instead."
                    )

        # --- Reserve identity via change feed ---
        if resolved_identity and self._change_feed:
            claim_state = self._change_feed.get_claims(identity=resolved_identity)
            for claim in claim_state.get("claims", {}).values():
                claim_path = str(claim.get("path", ""))
                if claim_path == f"hub_identity:{resolved_identity}":
                    return (
                        f"identity '{resolved_identity}' is already reserved by "
                        f"{claim.get('identity', '?')}"
                    )
            reserve = self._change_feed.claim(
                resolved_identity,
                f"hub_identity:{resolved_identity}",
                f"spawn reservation for {effective_agent_type}",
            )
            if reserve.get("status") == "conflict":
                return (
                    f"identity '{resolved_identity}' is already reserved by "
                    f"{reserve.get('claimed_by', '?')}"
                )

        # --- Resolve profile ---
        # Priority: agent bundle's preferred profile > parent's active profile
        # Without this, agents with no profile in agent.json fall back to
        # the "default" profile (usually Anthropic) instead of the correct LLM.
        resolved_profile = ""
        try:
            agent_mgr = (
                self.event_bus.get_service("agent_manager") if self.event_bus else None
            )
            if agent_mgr:
                agent_def = agent_mgr.get_agent(effective_agent_type)
                if agent_def and agent_def.profile:
                    resolved_profile = agent_def.profile
        except Exception:
            pass

        if not resolved_profile and self._identity and self._identity.profile:
            resolved_profile = self._identity.profile

        # --- Spawn ---
        result = await orch.orchestrator.spawn(
            name=resolved_identity,
            task=task,
            files=[],
            wait=False,
            agent_type=effective_agent_type,
            skills=effective_skills,
            identity=resolved_identity,
            profile=resolved_profile,
        )
        if not result:
            return f"Failed to create agent (identity: {resolved_identity}, type: {effective_agent_type})"

        # --- Build response (identity is known upfront, no discovery needed) ---
        msg = f"Created agent '{resolved_identity}' (agent type: {effective_agent_type})"
        msg += f"\nTask: {task}"
        if effective_skills:
            msg += f"\nSkills: {', '.join(effective_skills)}"

        # Coordinator signs attestation for spawned agent (pre-approves it)
        if (
            _DNS_AVAILABLE
            and self._dns_identity
            and self._dns_registry
            and self._identity
            and self._identity.is_coordinator
        ):
            try:
                _, spawned_pub_hex = self._dns_identity.get_or_create_keypair(
                    resolved_identity
                )
                attestation = self._dns_identity.create_attestation(
                    subject=resolved_identity,
                    issuer=self._identity.identity,
                    subject_public_key_hex=spawned_pub_hex,
                )
                # Pre-register with attestation and auto-approve
                pre_record = AgentRecord(
                    designation=resolved_identity,
                    runtime="kollab",
                    authority=self.config.get("plugins.hub.authority", "kollabor.ai") if self.config else "kollabor.ai",
                    public_key=spawned_pub_hex,
                    attestation=attestation,
                    approval_state="auto_approved",
                )
                self._dns_registry.register(pre_record)
                msg += f"\nAttestation: signed by {self._identity.identity}"
            except Exception as e:
                logger.warning(f"Failed to sign attestation for spawn: {e}")

        return msg

    async def _handle_capture_command(self, rest: str) -> str:
        """Handle /hub capture <identity|name|all> [lines].

        Accepts hub identity names (e.g. "lapis"), orchestrator agent
        names, or "all". Hub identities capture directly over peer sockets.
        Orchestrator session names still route through agent_orchestrator.
        """
        orch = self._get_orchestrator()
        if not orch:
            return "error: agent orchestrator not available"
        args = rest.split() if rest.strip() else []
        if not args:
            return "usage: /hub capture <identity|name|all> [lines]"

        target = args[0]
        try:
            lines = int(args[1]) if len(args) > 1 else 50
        except (TypeError, ValueError):
            lines = 50

        # Resolve hub identity first. Not every live peer is an orchestrator-
        # owned subprocess in this process, so capture over the hub socket when
        # we have a presence record.
        if target.lower() != "all":
            own_identity = self._identity.identity if self._identity else ""
            if target.lower() == own_identity.lower():
                return (
                    f"self-capture not supported for '{own_identity}' "
                    "(coordinator runs in the main process, not a "
                    "spawned session). capture a spawned agent instead."
                )

            peer = self._get_peer_by_identity(target)
            if peer is not None:
                return await self._capture_peer_output(peer, lines)

            resolved = self._resolve_identity_to_agent_name(target)
            if resolved is not None:
                args[0] = resolved
            else:
                known_agent_names = []
                try:
                    known_agent_names = list(orch.orchestrator.get_all_agents().keys())
                except Exception:
                    known_agent_names = []

                if target not in known_agent_names:
                    return f"error: agent '{target}' not found"

        result = await orch._cmd_capture(args)
        return str(result.message)

    async def _handle_wake_command(self, rest: str) -> str:
        """Handle /hub wake <identity> — manually wake a waiting agent."""
        target = rest.strip()
        if not target:
            return "usage: /hub wake <identity>"

        if not self._presence:
            return "hub not active"

        # Find the target agent (include_self in case the operator wakes the
        # agent they're currently attached to)
        agents = self._presence.scan_all_presence(include_self=True)
        agent = next((a for a in agents if a.identity == target), None)

        # Also check self directly (self may not appear in presence scan)
        if not agent and self._identity and self._identity.identity == target:
            agent = self._identity

        if not agent:
            return f"agent {target} not found"

        if agent.state != "waiting":
            return f"agent {target} is not in waiting state (current: {agent.state})"

        # Send a synthetic wake message with force=True
        wake_msg = HubMessage(
            action="message",
            from_agent=self._identity.agent_id if self._identity else "",
            from_identity=self._identity.identity if self._identity else "user",
            to=target,
            content="[system: user wake] the user has manually woken you up. resume work.",
            scope=MessageScope.DIRECT.value,
            force=True,
        )
        await self._route_message(wake_msg)
        return f"woke {target}"

    def _handle_approve_command(self, args: str) -> str:
        """Handle /hub approve <identity> — approve an agent for mesh."""
        target = args.strip()
        if not target:
            return "usage: /hub approve <identity>"

        if not self._identity or not self._identity.is_coordinator:
            return "error: only coordinator can approve agents"

        if not _DNS_AVAILABLE or not self._dns_registry:
            return "error: DNS registry not available"

        record = self._dns_registry.resolve(target)
        if not record:
            return f"agent '{target}' not found in DNS registry"

        if record.is_approved:
            return f"agent '{target}' is already approved ({record.approval_state})"

        # Sign attestation if we have identity manager
        if self._dns_identity:
            try:
                _, pub_hex = self._dns_identity.get_or_create_keypair(target)
                attestation = self._dns_identity.create_attestation(
                    subject=target,
                    issuer=self._identity.identity,
                    subject_public_key_hex=pub_hex,
                )
                record.attestation = attestation
            except Exception as e:
                logger.warning(f"Failed to sign attestation for {target}: {e}")

        self._dns_registry.approve(target, approver=self._identity.identity)
        return f"approved '{target}' for mesh participation"

    def _handle_reject_command(self, args: str) -> str:
        """Handle /hub reject <identity> [reason] — reject an agent."""
        parts = args.strip().split(maxsplit=1)
        if not parts:
            return "usage: /hub reject <identity> [reason]"

        target = parts[0]
        reason = parts[1] if len(parts) > 1 else ""

        if not self._identity or not self._identity.is_coordinator:
            return "error: only coordinator can reject agents"

        if not _DNS_AVAILABLE or not self._dns_registry:
            return "error: DNS registry not available"

        record = self._dns_registry.resolve(target)
        if not record:
            return f"agent '{target}' not found in DNS registry"

        self._dns_registry.reject(target, reason=reason)
        return f"rejected '{target}' from mesh{f': {reason}' if reason else ''}"

    def _handle_pending_command(self) -> str:
        """Handle /hub pending — list agents pending approval."""
        if not _DNS_AVAILABLE or not self._dns_registry:
            return "DNS registry not available"

        pending = self._dns_registry.get_pending()
        if not pending:
            return "no agents pending approval"

        lines = [f"pending approval ({len(pending)} agent(s)):"]
        for record in pending:
            runtime = f"runtime={record.runtime}"
            trust = f"trust={record.trust_score:.2f}"
            since = f"registered={time.strftime('%H:%M:%S', time.localtime(record.registered_at))}"
            lines.append(f"  {record.designation} ({runtime}, {trust}, {since})")

        lines.append("\napprove: /hub approve <identity>")
        lines.append("reject: /hub reject <identity> [reason]")
        return "\n".join(lines)

    async def _handle_stop_command(self, rest: str) -> str:
        """Handle /hub stop <name|all>.

        Sends shutdown signals via hub sockets (works for org-launched
        agents and any other peer on the mesh, not just orchestrator-
        managed agents).
        """
        target = rest.strip()
        if not target:
            return "usage: /hub stop <identity|all>"

        if not self._presence or not self._identity:
            return "hub not active"

        if target.lower() == "all":
            # Use scan_all_presence so we see agents even if they're
            # mid-shutdown (discover_agents would clean them up first
            # and report "no peers" even though we just killed them).
            peers = self._presence.scan_all_presence()
            if not peers:
                return "no peers to stop"

            results = []
            stop_targets = [
                agent for agent in peers if agent.agent_id != self._identity.agent_id
            ]
            stop_results = await asyncio.gather(
                *[
                    self._stop_peer_agent(
                        agent,
                        reason=f"stopped by {self._identity.identity}",
                    )
                    for agent in stop_targets
                ]
            )
            for agent, result in zip(stop_targets, stop_results):
                results.append(f"  {agent.identity}: {result}")

            if not results:
                return "no peers to stop (only you on mesh)"
            return f"stopping {len(results)} agent(s):\n" + "\n".join(results)
        else:
            # Single agent
            if target == self._identity.identity:
                # Self-stop: schedule clean shutdown + watchdog exit.
                # The shutdown coroutine tries graceful teardown (save
                # vault, release socket, announce departure) and calls
                # os._exit(0) at the end. If it hangs on an await (e.g.
                # stale peer socket, signal_shutdown timeout), the
                # watchdog kills the process after 8s so self-stop is
                # always terminal — matches the operator's mental model of
                # "hub_stop means die."
                logger.info(f"{self._identity.identity} requested self-stop via hub_stop")
                self._self_stop_requested = True
                asyncio.ensure_future(self.shutdown())

                async def _self_stop_watchdog() -> None:
                    try:
                        await asyncio.sleep(8.0)
                    except asyncio.CancelledError:
                        return
                    logger.warning(
                        f"{self._identity.identity}: self-stop watchdog fired, "
                        f"forcing os._exit after graceful shutdown timeout"
                    )
                    os._exit(0)

                asyncio.ensure_future(_self_stop_watchdog())
                return f"{self._identity.identity} shutting down..."

            # Scan without cleanup so we can still find dying agents
            all_agents = self._presence.scan_all_presence()
            agent = None
            for a in all_agents:
                if a.identity == target:
                    agent = a
                    break

            if not agent:
                available = [a.identity for a in all_agents]
                return f"agent '{target}' not found. online: {', '.join(available) or 'none'}"

            if not agent.socket_path:
                self._presence.cleanup_agent(agent)
                return f"agent '{target}' has no socket path, cleaned up"

            result = await self._stop_peer_agent(
                agent,
                reason=f"stopped by {self._identity.identity}",
            )
            return f"'{target}' {result}"

    async def _stop_peer_agent(self, agent, reason: str) -> str:
        """Stop a peer and only report success after the pid exits.

        A socket shutdown ack means "request received", not "process is gone".
        Wait briefly for graceful exit, then fall back to SIGTERM and verify
        that exit too. This avoids the first-run lie where status still sees
        the agent online and a second stop is needed.
        """
        if not self._presence:
            return "hub not active"

        if not getattr(agent, "socket_path", ""):
            self._presence.cleanup_agent(agent)
            return "no socket, cleaned up"

        try:
            success = await AgentMessenger.signal_shutdown(
                agent.socket_path,
                reason=reason,
                timeout=3.0,
            )
        except Exception as e:
            success = False
            logger.debug(f"shutdown signal failed for {agent.identity}: {e}")

        if success:
            if await self._wait_for_agent_exit(agent, timeout=STOP_GRACE_SECONDS):
                self._presence.cleanup_agent(agent)
                return "stopped"

            killed = self._force_kill_agent(agent)
            if killed and await self._wait_for_agent_exit(agent, timeout=STOP_TERM_SECONDS):
                self._presence.cleanup_agent(agent)
                return "killed (SIGTERM after graceful timeout)"
            return "stop timed out (pid still alive)"

        killed = self._force_kill_agent(agent)
        if killed and await self._wait_for_agent_exit(agent, timeout=STOP_TERM_SECONDS):
            self._presence.cleanup_agent(agent)
            return "killed (SIGTERM fallback)"

        if not self._agent_pid_alive(getattr(agent, "pid", 0)):
            self._presence.cleanup_agent(agent)
            return "already dead, cleaned up"

        return "failed (pid still alive)"

    async def _wait_for_agent_exit(self, agent, timeout: float = 5.0) -> bool:
        """Wait until an agent pid is no longer alive."""
        pid = getattr(agent, "pid", 0)
        if not pid:
            return True

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._agent_pid_alive(pid):
                return True
            await asyncio.sleep(0.1)
        return not self._agent_pid_alive(pid)

    @staticmethod
    def _agent_pid_alive(pid: int) -> bool:
        """Return True when pid exists and can be signaled."""
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _force_kill_agent(self, agent) -> bool:
        """Send SIGTERM to an agent process as last resort.

        Returns True if the signal was sent, False if the process
        was already dead.
        """
        pid = getattr(agent, "pid", 0)
        if not pid:
            return False
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to {agent.identity} (pid {pid})")
            return True
        except ProcessLookupError:
            logger.debug(f"Process {pid} already dead")
            return False
        except PermissionError:
            logger.warning(f"No permission to kill pid {pid}")
            return False

    async def _handle_agents_command(self) -> str:
        """Handle /hub agents -- list all active agents via presence."""
        if not self._identity or not self._presence:
            return "error: hub not initialized"
        agents = self._presence.get_cached_agents()
        if not agents:
            return "No active agents."
        lines = ["Active Agents:"]
        for a in agents:
            role = " (coordinator)" if a.is_coordinator else ""
            me = " (you)" if a.agent_id == self._identity.agent_id else ""
            lines.append(f"  {a.identity}{role}{me}: {a.state}")
        return "\n".join(lines)

    # -- Cron commands --

    def _handle_cron_command(self, args: str) -> str:
        """Handle /hub cron subcommands."""
        parts = args.strip().split(maxsplit=1)
        action = parts[0] if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""

        if action == "add":
            return self._cron_add(rest)
        elif action == "list":
            return self._cron_list()
        elif action == "delete":
            return self._cron_delete(rest.strip())
        elif action == "clear":
            return self._cron_clear()
        else:
            return (
                "usage: /hub cron add|list|delete|clear\n"
                "  add <target> <interval> <message>\n"
                "  list\n"
                "  delete <id>\n"
                "  clear"
            )

    def _cron_add(self, args: str) -> str:
        """Add a new cron job: /hub cron add <target> <interval> <message>."""
        parts = args.strip().split(maxsplit=2)
        if len(parts) < 3:
            return "usage: /hub cron add <target> <interval> <message>"

        target, interval_str, message = parts

        try:
            interval_seconds = _parse_interval(interval_str)
        except ValueError as e:
            return f"bad interval: {e}"

        job_id = uuid.uuid4().hex[:8]
        job = HubCronJob(
            id=job_id,
            target=target,
            message=message,
            interval_seconds=interval_seconds,
            next_fire=time.time() + interval_seconds,
        )
        self._hub_cron_jobs.append(job)
        logger.info(f"hub cron job created: {job_id} every {interval_str} -> {target}")
        return f"cron job {job_id} created: every {interval_str} -> {target}"

    def _cron_list(self) -> str:
        """List all active cron jobs."""
        if not self._hub_cron_jobs:
            return "hub cron: no jobs"

        lines = [f"hub cron: {len(self._hub_cron_jobs)} job(s)"]
        now = time.time()
        for job in self._hub_cron_jobs:
            remaining = max(0, job.next_fire - now)
            mode = "recurring" if job.recurring else "one-shot"
            lines.append(
                f"  [{job.id}] -> {job.target} ({mode})"
                f" every {self._format_seconds(job.interval_seconds)}"
                f" | next in {self._format_seconds(remaining)}"
            )
            lines.append(f"    msg: {job.message[:80]}")
        return "\n".join(lines)

    def _cron_delete(self, job_id: str) -> str:
        """Delete a cron job by id."""
        if not job_id:
            return "usage: /hub cron delete <id>"

        before = len(self._hub_cron_jobs)
        self._hub_cron_jobs = [j for j in self._hub_cron_jobs if j.id != job_id]
        if len(self._hub_cron_jobs) < before:
            logger.info(f"hub cron job deleted: {job_id}")
            return f"cron job {job_id} deleted"
        return f"cron job {job_id} not found"

    def _cron_clear(self) -> str:
        """Remove all cron jobs."""
        count = len(self._hub_cron_jobs)
        self._hub_cron_jobs.clear()
        logger.info(f"hub cron cleared: {count} job(s) removed")
        return f"hub cron cleared: {count} job(s) removed"

    # -- Task commands --

    async def _handle_tasks_command(self, args: str) -> str:
        """Handle /hub tasks subcommands."""
        if not self._task_ledger:
            return "task ledger not initialized"

        parts = args.strip().split(maxsplit=1)
        action = parts[0] if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""

        if action == "list":
            return self._tasks_list()
        elif action == "mine":
            return self._tasks_mine()
        elif action == "assign":
            return self._tasks_assign(rest)
        elif action == "cancel":
            return self._tasks_cancel(rest.strip())
        elif action == "status":
            return self._tasks_status(rest.strip())
        else:
            return (
                "usage: /hub tasks list|mine|assign|cancel|status\n"
                "  list                        all tasks\n"
                "  mine                        my active tasks\n"
                "  assign <agent> <directive>  assign task\n"
                "  cancel <id>                 cancel a task\n"
                "  status <id>                 task details"
            )

    def _tasks_list(self) -> str:
        """List all tasks."""
        if self._task_ledger is None:
            return "task system not available"
        all_tasks = self._task_ledger.get_all()
        if not all_tasks:
            return "no tasks"

        lines = [f"tasks: {len(all_tasks)} total"]
        for card in sorted(all_tasks, key=lambda c: -c.priority):
            status_icon = {
                "active": ">>",
                "paused": "||",
                "done": "ok",
                "failed": "!!",
                "qa_review": "QA",
                "closed": "--",
            }.get(card.status, "??")
            lines.append(
                f"  [{status_icon}] {card.id}"
                f" {card.assigner}->{card.assignee}"
                f" p{card.priority}"
                f" {card.elapsed_str()}"
            )
            lines.append(f"       {card.directive[:70]}")
        return "\n".join(lines)

    def _tasks_mine(self) -> str:
        """Show my active tasks."""
        if not self._identity:
            return "not connected to hub"
        if not self._task_ledger:
            return "task ledger not available"
        my_tasks = self._task_ledger.get_active_for(self._identity.identity)
        if not my_tasks:
            return "no active tasks assigned to you"

        lines = [f"your tasks: {len(my_tasks)}"]
        for card in my_tasks:
            lines.append(
                f"  [{card.id}] p{card.priority}"
                f" from {card.assigner}"
                f" ({card.elapsed_str()})"
            )
            lines.append(f"    directive: {card.directive[:70]}")
            if card.deliverable:
                lines.append(f"    deliverable: {card.deliverable[:70]}")
            if card.last_checkpoint_note():
                lines.append(
                    f"    last checkpoint:" f" {card.last_checkpoint_note()[:60]}"
                )
            if card.status == "qa_review":
                lines.append("    status: awaiting QA review")
        return "\n".join(lines)

    def _tasks_assign(self, args: str) -> str:
        """Assign a task: /hub tasks assign <agent> <directive>."""
        parts = args.strip().split(maxsplit=1)
        if len(parts) < 2:
            return "usage: /hub tasks assign <agent> <directive>"

        assignee = parts[0]
        directive = parts[1]

        if not self._identity:
            return "not connected to hub"
        if not self._task_ledger:
            return "task ledger not available"

        card = self._task_ledger.create(
            assigner=self._identity.identity,
            assignee=assignee,
            directive=directive,
        )
        return f"task {card.id} assigned to {assignee}:" f" {directive[:60]}"

    def _tasks_cancel(self, task_id: str) -> str:
        """Cancel a task by id."""
        if not task_id:
            return "usage: /hub tasks cancel <id>"

        if self._task_ledger is None:
            return "task system not available"
        if self._task_ledger.cancel(task_id):
            return f"task {task_id} cancelled"
        return f"task {task_id} not found"

    def _tasks_status(self, task_id: str) -> str:
        """Show detailed task status."""
        if not task_id:
            return "usage: /hub tasks status <id>"

        if self._task_ledger is None:
            return "task system not available"
        card = self._task_ledger.get(task_id)
        if not card:
            return f"task {task_id} not found"

        lines = [
            f"task {card.id}:",
            f"  status:      {card.status}",
            f"  assigner:    {card.assigner}",
            f"  assignee:    {card.assignee}",
            f"  directive:   {card.directive}",
            f"  deliverable: {card.deliverable or '(none)'}",
            f"  report to:   {card.report_to}",
            f"  priority:    {card.priority}",
            f"  elapsed:     {card.elapsed_str()}",
            f"  cron:        {'active' if card.cron_active else 'off'}"
            f" ({int(card.cron_interval)}s interval)",
        ]
        if card.timeout_seconds:
            lines.append(
                f"  timeout:     {int(card.timeout_seconds)}s"
                f" ({'EXPIRED' if card.is_timed_out() else 'ok'})"
            )
        if card.checkpoints:
            lines.append(f"  checkpoints: {len(card.checkpoints)}")
            for cp in card.checkpoints[-3:]:
                lines.append(f"    - {cp.get('note', '')[:60]}")
        if card.result:
            lines.append(f"  result:      {card.result[:100]}")
        if card.error:
            lines.append(f"  error:       {card.error[:100]}")
        if card.qa_reviewer:
            passed = "PASSED" if card.qa_passed else "REJECTED"
            lines.append(f"  qa:          {passed} by {card.qa_reviewer}")
            if card.qa_notes:
                lines.append(f"  qa notes:    {card.qa_notes[:60]}")
        return "\n".join(lines)

    # -- Notify commands --

    async def _handle_notify_command(self, args: str) -> str:
        """Handle /hub notify subcommands."""
        parts = args.strip().split(maxsplit=1)
        action = parts[0] if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""

        if action == "enable":
            return self._notify_set_enabled(True)
        elif action == "disable":
            return self._notify_set_enabled(False)
        elif action == "channel":
            return self._notify_set_channel(rest.strip())
        elif action == "url":
            return self._notify_set_url(rest.strip())
        elif action == "threshold":
            return self._notify_set_threshold(rest.strip())
        elif action == "test":
            return await self._notify_test()
        elif action == "status":
            return self._notify_status()
        else:
            return (
                "usage: /hub notify enable|disable|channel|url|threshold|test|status\n"
                "  enable          enable notifications\n"
                "  disable         disable notifications\n"
                "  channel <name>  set channel (webhook, telegram)\n"
                "  url <url>       set webhook url\n"
                "  threshold <sec> set idle threshold in seconds\n"
                "  test            send a test notification\n"
                "  status          show current config"
            )

    def _notify_set_enabled(self, enabled: bool) -> str:
        """Enable or disable the notification loop at runtime."""
        if not self.config:
            return "config not available"

        self.config.setdefault("plugins", {}).setdefault("hub", {})
        self.config["plugins"]["hub"]["notify_enabled"] = enabled

        if enabled and not self._notify_task:
            # Start the loop
            if not self._notifier:
                self._notifier = HubNotifier(
                    config=self.config,
                    get_identity=lambda: (
                        self._identity.identity if self._identity else ""
                    ),
                    get_last_activity=lambda: self._last_activity_at,
                    get_state=lambda: (
                        self._identity.state if self._identity else "unknown"
                    ),
                )
            self._notify_task = asyncio.create_task(self._notifier.run())
            return "notifications enabled (loop started)"
        elif not enabled and self._notify_task:
            # Stop the loop
            self._notify_task.cancel()
            self._notify_task = None
            self._notifier = None
            return "notifications disabled (loop stopped)"

        return f"notifications {'enabled' if enabled else 'disabled'}"

    def _notify_set_channel(self, channel: str) -> str:
        """Set the notification channel."""
        if not channel:
            return "usage: /hub notify channel <webhook|telegram>"
        if channel not in ("webhook", "telegram"):
            return f"unknown channel '{channel}'. use: webhook, telegram"

        if not self.config:
            return "config not available"
        self.config.setdefault("plugins", {}).setdefault("hub", {})
        self.config["plugins"]["hub"]["notify_channel"] = channel

        # Rebuild backend if notifier exists
        if self._notifier:
            self._notifier._backend = self._notifier._build_backend()

        return f"notification channel set to {channel}"

    def _notify_set_url(self, url: str) -> str:
        """Set the webhook url."""
        if not url:
            return "usage: /hub notify url <https://...>"

        if not self.config:
            return "config not available"
        self.config.setdefault("plugins", {}).setdefault("hub", {})
        self.config["plugins"]["hub"]["notify_url"] = url

        # Rebuild backend if notifier exists
        if self._notifier:
            self._notifier._backend = self._notifier._build_backend()

        return "webhook url set"

    def _notify_set_threshold(self, value: str) -> str:
        """Set the idle notification threshold in seconds."""
        if not value:
            return "usage: /hub notify threshold <seconds>"
        try:
            seconds = int(value)
            if seconds < 30:
                return "threshold must be at least 30 seconds"
        except ValueError:
            return f"invalid threshold: {value}"

        if not self.config:
            return "config not available"
        self.config.setdefault("plugins", {}).setdefault("hub", {})
        self.config["plugins"]["hub"]["notify_idle_threshold"] = seconds

        return f"notify idle threshold set to {self._format_seconds(seconds)}"

    async def _notify_test(self) -> str:
        """Send a test notification through the configured backend."""
        if not self.config:
            return "config not available"

        hub_cfg = self.config.get("plugins", {}).get("hub", {})
        channel = hub_cfg.get("notify_channel", "webhook")

        # Build a temporary backend for testing
        backend: "WebhookNotifier | TelegramNotifier | None" = None
        if channel == "webhook":
            url = hub_cfg.get("notify_url", "")
            if not url:
                return "no webhook url configured. use: /hub notify url <url>"
            from .notifier import WebhookNotifier

            backend = WebhookNotifier(url)
        elif channel == "telegram":
            token = hub_cfg.get("notify_telegram_token", "")
            chat_id = hub_cfg.get("notify_telegram_chat_id", "")
            if not token or not chat_id:
                return "telegram token/chat_id not configured"
            from .notifier import TelegramNotifier

            backend = TelegramNotifier(token, chat_id)
        else:
            return f"unknown channel: {channel}"

        if not backend:
            return "no notification backend configured"

        identity = self._identity.identity if self._identity else "unknown"
        try:
            await backend.send(
                f"[test] agent '{identity}' notification test",
                {"test": True, "identity": identity},
            )
            return f"test notification sent via {channel}"
        except Exception as e:
            return f"test notification failed: {e}"

    def _notify_status(self) -> str:
        """Show current notification configuration."""
        if not self.config:
            return "config not available"

        hub_cfg = self.config.get("plugins", {}).get("hub", {})
        enabled = hub_cfg.get("notify_enabled", False)
        channel = hub_cfg.get("notify_channel", "webhook")
        url = hub_cfg.get("notify_url", "")
        threshold = hub_cfg.get("notify_idle_threshold", 300)
        cooldown = hub_cfg.get("notify_cooldown", 1800)

        loop_status = "running" if self._notify_task else "stopped"
        url_display = url[:50] + "..." if len(url) > 50 else (url or "(not set)")

        return (
            f"notifications: {'enabled' if enabled else 'disabled'}\n"
            f"  loop: {loop_status}\n"
            f"  channel: {channel}\n"
            f"  url: {url_display}\n"
            f"  idle threshold: {self._format_seconds(threshold)}\n"
            f"  cooldown: {self._format_seconds(cooldown)}"
        )

    @staticmethod
    def _format_seconds(s: float) -> str:
        """Format seconds as human-readable string."""
        s = int(s)
        if s < 60:
            return f"{s}s"
        elif s < 3600:
            m, sec = divmod(s, 60)
            return f"{m}m{sec}s" if sec else f"{m}m"
        else:
            h, remainder = divmod(s, 3600)
            m = remainder // 60
            return f"{h}h{m}m" if m else f"{h}h"

    # -- Bridge commands --

    async def _handle_bridge_command(self, args: str) -> str:
        """Handle /hub bridge subcommands."""
        parts = args.strip().split(maxsplit=1)
        action = parts[0] if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""

        if action == "status":
            return self._bridge_status()
        elif action == "send":
            if not rest.strip():
                return "usage: /hub bridge send <message>"
            return await self._bridge_send_command(rest.strip())
        elif action == "enable":
            return await self._bridge_set_enabled(True)
        elif action == "disable":
            return await self._bridge_set_enabled(False)
        elif action == "setup":
            return await self._bridge_setup()
        else:
            return (
                "usage: /hub bridge status|send|enable|disable|setup\n"
                "  status          show bridge connection state\n"
                "  send <message>  send message through bridge\n"
                "  enable          start the bridge loop\n"
                "  disable         stop the bridge loop\n"
                "  setup           auto-detect config and send test message"
            )

    def _bridge_status(self) -> str:
        """Show current bridge configuration and state."""
        if not self.config:
            return "config not available"

        enabled = self.config.get("plugins.hub.bridge_enabled", False)
        platform = self.config.get("plugins.hub.bridge_platform", "telegram")
        has_token = bool(self.config.get("plugins.hub.bridge_token", ""))
        has_chat = bool(self.config.get("plugins.hub.bridge_chat_id", ""))
        target = self.config.get("plugins.hub.bridge_target_agent", "") or "self"
        poll_interval = self.config.get("plugins.hub.bridge_poll_interval", 2)
        loop_status = "running" if self._bridge_task else "stopped"
        connected = "yes" if self._bridge else "no"

        return (
            f"messaging bridge: {'enabled' if enabled else 'disabled'}\n"
            f"  loop: {loop_status}\n"
            f"  connected: {connected}\n"
            f"  platform: {platform}\n"
            f"  token: {'set' if has_token else '(not set)'}\n"
            f"  chat_id: {'set' if has_chat else '(not set)'}\n"
            f"  target: {target}\n"
            f"  poll interval: {poll_interval}s"
        )

    async def _bridge_setup(self) -> str:
        """Auto-detect bridge config from env vars and send test message."""
        import os as _os

        lines = ["bridge setup:"]

        # Check env vars
        token = _os.environ.get("KOLLAB_HUB_BRIDGE_TOKEN", "")
        chat_id = _os.environ.get("KOLLAB_HUB_BRIDGE_CHAT_ID", "")

        if not token:
            token = (
                self.config.get("plugins.hub.bridge_token", "") if self.config else ""
            )
        if not chat_id:
            chat_id = (
                self.config.get("plugins.hub.bridge_chat_id", "") if self.config else ""
            )

        if not token:
            lines.append("  no bot token found.")
            lines.append("")
            lines.append("  quick start:")
            lines.append("  1. open telegram, talk to @BotFather")
            lines.append("  2. /newbot -> pick a name -> get the token")
            lines.append("  3. export KOLLAB_HUB_BRIDGE_TOKEN=your-token")
            lines.append("  4. talk to @userinfobot to get your chat ID")
            lines.append("  5. export KOLLAB_HUB_BRIDGE_CHAT_ID=your-chat-id")
            lines.append("  6. run /hub bridge setup again")
            return "\n".join(lines)

        if not chat_id:
            lines.append(f"  token: found ({token[:8]}...)")
            lines.append("  chat_id: missing")
            lines.append("  talk to @userinfobot on telegram to get your chat ID")
            lines.append("  then: export KOLLAB_HUB_BRIDGE_CHAT_ID=your-chat-id")
            return "\n".join(lines)

        lines.append(f"  token: {token[:8]}...")
        lines.append(f"  chat_id: {chat_id}")
        lines.append("  platform: telegram")

        # Save to config
        if self.config:
            self.config.save_key("plugins.hub.bridge_token", token)
            self.config.save_key("plugins.hub.bridge_chat_id", chat_id)
            self.config.save_key("plugins.hub.bridge_platform", "telegram")
            self.config.save_key("plugins.hub.bridge_enabled", True)

        # Try to connect and send test message
        try:
            from .messaging_bridge import TelegramBridge

            bridge = TelegramBridge(token=token, chat_id=chat_id)
            connected = await bridge.connect()
            if connected:
                identity = self._identity.identity if self._identity else "kollabor"
                ok = await bridge.send(
                    f"kollabor bridge setup complete. agent '{identity}' can now reach you here.",
                )
                await bridge.disconnect()
                if ok:
                    lines.append("  test message: sent (check your telegram)")
                    lines.append("")
                    lines.append(
                        "  bridge is configured. run /hub bridge enable to start."
                    )
                else:
                    lines.append("  test message: failed to send")
            else:
                lines.append("  connection failed. check your bot token.")
        except Exception as e:
            lines.append(f"  error: {e}")

        return "\n".join(lines)

    async def _bridge_send_command(self, text: str) -> str:
        """Send a message through the active bridge."""
        if not self._bridge:
            return "bridge not connected. enable it first: /hub bridge enable"
        success = await self.bridge_send(text)
        if success:
            return f"sent via {self._bridge.name}"
        else:
            return "bridge send failed (check logs)"

    async def _bridge_set_enabled(self, enabled: bool) -> str:
        """Enable or disable the messaging bridge at runtime."""
        if enabled and not self._bridge_task:
            # Validate config before starting
            if not self.config:
                return "config not available"
            token = self.config.get("plugins.hub.bridge_token", "")
            chat_id = self.config.get("plugins.hub.bridge_chat_id", "")
            if not token or not chat_id:
                return "cannot enable: bridge_token and bridge_chat_id required"

            if self.config:
                self.config.save_key("plugins.hub.bridge_enabled", True)
            self._bridge_task = asyncio.create_task(self._messaging_bridge_loop())
            return "messaging bridge enabled and starting"

        elif not enabled and self._bridge_task:
            if self.config:
                self.config.save_key("plugins.hub.bridge_enabled", False)
            self._bridge_task.cancel()
            try:
                await self._bridge_task
            except asyncio.CancelledError:
                pass
            self._bridge_task = None
            return "messaging bridge disabled"

        elif enabled:
            return "messaging bridge is already running"
        else:
            return "messaging bridge is already stopped"

    async def _read_hub_text_via_state_service(self, method_name: str) -> Optional[str]:
        """Try a state_service hub-read rpc; return None if unavailable.

        Phase 4.5 step 8: in attach mode this hits the daemon via
        RemoteStateService; in local mode it hits LocalStateService
        which just proxies to _format_* on this very plugin. Returns
        None if state_service isn't wired or the rpc returned an
        empty string (meaning hub plugin isn't loaded on the daemon
        either) -- caller falls back to the local _format_* method.
        """
        if not self.event_bus or not hasattr(self.event_bus, "get_service"):
            return None
        try:
            state_service = self.event_bus.get_service("state_service")
        except Exception:
            return None
        if state_service is None:
            return None
        fn = getattr(state_service, method_name, None)
        if fn is None:
            return None
        try:
            result = await fn()
        except Exception as e:
            logger.debug(f"state_service.{method_name} error: {e}")
            return None
        if not result:
            return None
        return str(result)

    async def _write_hub_via_state_service(
        self, method_name: str, args_str: str
    ) -> Optional[str]:
        """Try a state_service hub-write rpc; return None if unavailable.

        Phase 4.6: msg and broadcast are write operations that must run
        on the daemon (which owns the identity, sockets, and presence).
        In local mode this round-trips through LocalStateService back to
        this plugin's own handlers -- harmless identity pass-through.
        Returns None if state_service isn't wired so the caller falls
        back to the local handler.
        """
        if not self.event_bus or not hasattr(self.event_bus, "get_service"):
            return None
        try:
            state_service = self.event_bus.get_service("state_service")
        except Exception:
            return None
        if state_service is None:
            return None
        fn = getattr(state_service, method_name, None)
        if fn is None:
            return None
        try:
            if method_name == "hub_send_msg":
                parts = args_str.split(maxsplit=1)
                if len(parts) < 2:
                    return "usage: /hub msg <agent> <message>"
                result = await fn(parts[0], parts[1])
            elif method_name == "hub_broadcast":
                if not args_str.strip():
                    return "usage: /hub broadcast <message>"
                result = await fn(args_str.strip())
            else:
                return None
        except Exception as e:
            logger.debug(f"state_service.{method_name} error: {e}")
            return None
        if not result:
            return None
        return str(result)

    def _format_metrics(self) -> str:
        """Format loop prevention metrics for display."""
        m = self._loop_metrics
        lines = ["hub loop prevention metrics:"]
        lines.append(f"  loop nudges fired:         {m['loop_nudges_fired']}")
        lines.append(f"  coordinator breakthroughs: {m['coordinator_breakthroughs']}")
        lines.append(f"  force breakthroughs:       {m['force_breakthroughs']}")
        lines.append(f"  cooldown rejections:       {m['cooldown_rejections']}")
        lines.append(f"  waiting state entries:     {m['waiting_state_entries']}")
        lines.append(f"  waiting state exits:       {m['waiting_state_exits']}")
        if self._waiting_durations:
            avg = sum(self._waiting_durations) / len(self._waiting_durations)
            lines.append(f"  avg waiting duration:      {avg:.1f}s")
            lines.append(f"  max waiting duration:      {max(self._waiting_durations):.1f}s")
        else:
            lines.append("  avg waiting duration:      n/a")
        return "\n".join(lines)

    def _format_status(self) -> str:
        """Format hub status display."""
        if not self._identity:
            return "hub: not connected"

        assert self._presence is not None
        agents = self._presence.get_cached_agents()
        # Include self in the count
        if self._identity and not any(
            a.agent_id == self._identity.agent_id for a in agents
        ):
            agents = [self._identity] + agents
        lines = [f"hub: {len(agents)} agent(s) online"]
        lines.append("")
        for a in agents:
            role = " (coordinator)" if a.is_coordinator else ""
            me = " (you)" if a.agent_id == self._identity.agent_id else ""

            # Format state with cooldown countdown if waiting
            if a.state == "waiting":
                if a.cooldown_until:
                    remaining = int(a.cooldown_until - time.time())
                    if remaining > 0:
                        state_str = f"waiting (cooldown: {remaining}s remaining)"
                    else:
                        state_str = "waiting (cooldown expired)"
                else:
                    state_str = "waiting"
                if a.waiting_reason:
                    state_str += f" - {a.waiting_reason}"
            else:
                state_str = a.state

            task = f" - {a.current_task[:50]}" if a.current_task else ""
            proj = f" [{a.project}]" if a.project else ""
            lines.append(f"  {a.identity}{role}{me}: {state_str}{proj}{task}")

        pending = self._work_queue.get_pending() if self._work_queue else []
        if pending:
            lines.append(f"\nwork queue: {len(pending)} pending")
            for slot in pending[:5]:
                lines.append(f"  [{slot.id}] {slot.task[:60]}")

        return "\n".join(lines)

    def _format_whoami(self) -> str:
        if not self._identity:
            return "hub: not connected"
        role = "coordinator" if self._identity.is_coordinator else "agent"
        return (
            f"identity: {self._identity.identity}\n"
            f"role: {role}\n"
            f"agent_id: {self._identity.agent_id}\n"
            f"pid: {self._identity.pid}\n"
            f"project: {self._identity.project}"
        )

    async def _handle_msg_command(self, args: str) -> str:
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "usage: /hub msg <agent> <message>"
        target, content = parts
        msg = HubMessage(
            action="message",
            from_agent=self._identity.agent_id if self._identity else "",
            from_identity=self._identity.identity if self._identity else "",
            to=target,
            content=content,
            scope=self._resolve_scope(target),
        )
        rejections = await self._route_message(msg)
        if rejections:
            parts = [f"{ident}: {reason}" for ident, reason in rejections]
            return (
                f"rejected: {'; '.join(parts)}. "
                f"use force=\"true\" to break through."
            )
        return f"sent to {target}"

    async def _handle_broadcast_command(
        self, content: str, force: bool = False
    ) -> str:
        if not content:
            return "usage: /hub broadcast <message>"
        msg = HubMessage(
            action="message",
            from_agent=self._identity.agent_id if self._identity else "",
            from_identity=self._identity.identity if self._identity else "",
            to="*",
            content=content,
            scope=MessageScope.BROADCAST.value,
            force=force,
        )
        rejections = await self._route_message(msg)
        if not self._presence:
            return "broadcast sent (hub not fully initialized)"
        agents = self._presence.get_cached_agents()
        base = f"broadcast to {len(agents)} agent(s)"
        if rejections:
            parts = [f"{ident}: {reason}" for ident, reason in rejections]
            base += f" (rejected: {'; '.join(parts)})"
        return base

    def _format_work(self) -> str:
        if not self._work_queue:
            return "work queue: unavailable"
        slots = self._work_queue.get_all()
        if not slots:
            return "work queue: empty"
        lines = [f"work queue: {len(slots)} slot(s)"]
        for s in slots:
            assigned = f" -> {s.assigned_to}" if s.assigned_to else ""
            lines.append(f"  [{s.id}] {s.status}{assigned}: {s.task[:60]}")
        return "\n".join(lines)

    def _handle_queue_command(self, task: str) -> str:
        if not task:
            return "usage: /hub queue <task description>"
        if not self._work_queue:
            return "work queue: unavailable"
        slot = self._work_queue.add(
            task=task,
            project=self._identity.project if self._identity else "",
            queued_by=self._identity.identity if self._identity else "",
        )
        return f"queued: [{slot.id}] {task[:60]}"

    async def _handle_claim_command(self, slot_id: str) -> str:
        if not self._work_queue or not self._identity:
            return "work queue: unavailable"
        rest = slot_id.strip()
        if rest:
            slot = self._work_queue.claim_by_id(
                rest,
                self._identity.identity,
            )
            if slot:
                return f"claimed: [{slot.id}] {slot.task[:60]}"
            return f"slot '{rest}' not found or not pending"
        slot = self._work_queue.claim_next(
            self._identity.identity,
            self._identity.project,
        )
        if slot:
            return f"claimed: [{slot.id}] {slot.task[:60]}"
        return "no available work to claim"

    def _format_vault(self, args: str = "") -> str:
        """Show vault info or crystal entry details.

        Subcommands:
            /hub vault [identity]     - Show vault summary
            /hub vault read <id>      - Full crystal entry body
            /hub vault list [identity] - List all crystal entries
            /hub vault search <query> - Search crystal entries
            /hub vault stats          - Crystal store statistics
        """
        args = args.strip()
        parts = args.split(maxsplit=1)
        subcmd = parts[0] if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        # Crystal subcommands
        if subcmd == "read":
            return self._vault_read_entry(rest)
        elif subcmd == "list":
            return self._vault_list_entries(rest)
        elif subcmd == "search":
            return self._vault_search_entries(rest)
        elif subcmd == "stats":
            return self._vault_stats()

        # Default: show vault summary for identity
        name = args or (self._identity.identity if self._identity else "")
        if not name:
            return (
                "usage: /hub vault [identity]\n"
                "       /hub vault read <id>\n"
                "       /hub vault list [identity]\n"
                "       /hub vault search <query>\n"
                "       /hub vault stats"
            )
        vault = AgentVault(name)
        if not vault.exists():
            return f"no vault for '{name}'"
        info = vault.get_summary()
        project_count = CrystalStore(vault._vault_dir).count()
        global_count = CrystalStore(vault.global_vault_dir).count()
        crystal_line = (
            f"\n  crystals: {project_count} project, {global_count} global"
            if (project_count or global_count)
            else ""
        )
        return (
            f"vault: {info['identity']}\n"
            f"  sessions: {info['session_count']}\n"
            f"  stream entries: {info['stream_entries']}\n"
            f"  last active: {info['last_active']}\n"
            f"  has working memory: {info['has_working_memory']}\n"
            f"  has crystallized: {info['has_crystallized']}"
            f"{crystal_line}"
        )

    def _vault_read_entry(self, entry_id: str) -> str:
        """Read full body of a crystal entry by ID.

        Searches project store first, falls back to global store.
        """
        if not entry_id:
            return "usage: /hub vault read <id> (e.g. crys-001)"
        identity = (
            self._identity.identity if self._identity else "koordinator"
        )
        vault = AgentVault(identity)
        # Search project store first, then global
        project_store = CrystalStore(vault._vault_dir)
        entry = project_store.get_by_id(entry_id)
        tier = "project"
        if not entry:
            global_store = CrystalStore(vault.global_vault_dir)
            entry = global_store.get_by_id(entry_id)
            tier = "global"
        if not entry:
            return f"no crystal entry '{entry_id}' (searched project + global)"
        return (
            f"[{entry.id}] {entry.summary}  [{tier}]\n"
            f"date: {entry.date}\n"
            f"keywords: {', '.join(entry.keywords[:15])}\n"
            f"---\n"
            f"{entry.body}"
        )

    def _vault_list_entries(self, identity: str = "") -> str:
        """List all crystal entries for an agent (both tiers, labeled)."""
        identity = identity.strip() or (
            self._identity.identity if self._identity else ""
        )
        if not identity:
            return "usage: /hub vault list [identity]"
        vault = AgentVault(identity)
        project_entries = CrystalStore(vault._vault_dir).load()
        global_entries = CrystalStore(vault.global_vault_dir).load()
        if not project_entries and not global_entries:
            return f"no crystal entries for '{identity}'"
        lines = [
            f"crystal entries for {identity} "
            f"({len(project_entries)} project, {len(global_entries)} global):"
        ]
        if project_entries:
            lines.append("  project:")
            for entry in project_entries:
                lines.append(f"    {entry.summary_line()}")
        if global_entries:
            lines.append("  global:")
            for entry in global_entries:
                lines.append(f"    {entry.summary_line()}")
        return "\n".join(lines)

    def _vault_search_entries(self, query: str) -> str:
        """Search crystal entries by keyword across both tiers."""
        if not query:
            return "usage: /hub vault search <query>"
        identity = (
            self._identity.identity if self._identity else "koordinator"
        )
        vault = AgentVault(identity)
        seen_ids: set = set()
        results = []
        for tier, store in (
            ("project", CrystalStore(vault._vault_dir)),
            ("global", CrystalStore(vault.global_vault_dir)),
        ):
            for entry in store.nudge(query, top_k=10):
                if entry.id not in seen_ids:
                    results.append((tier, entry))
                    seen_ids.add(entry.id)
        if not results:
            return f"no matches for '{query}'"
        lines = [f"search results for '{query}' ({len(results)} matches):"]
        for tier, entry in results:
            lines.append(f"  [{tier}] {entry.summary_line()}")
        return "\n".join(lines)

    def _vault_stats(self) -> str:
        """Show crystal store statistics for both tiers."""
        identity = (
            self._identity.identity if self._identity else "koordinator"
        )
        vault = AgentVault(identity)
        lines = [f"crystal store: {identity}"]
        for tier_label, store in (
            ("project", CrystalStore(vault._vault_dir)),
            ("global", CrystalStore(vault.global_vault_dir)),
        ):
            entries = store.load()
            if not entries:
                lines.append(f"  {tier_label}: 0 entries")
                continue
            total_keywords = sum(len(e.keywords) for e in entries)
            dates = sorted(set(e.date for e in entries))
            avg_kw = total_keywords / len(entries)
            lines.append(
                f"  {tier_label}: {len(entries)} entries, "
                f"avg {avg_kw:.1f} keywords, "
                f"{dates[0]} to {dates[-1]}"
            )
        return "\n".join(lines)

    def _format_all_vaults(self) -> str:
        """List all agent vaults."""
        from .vault import get_vault_summaries

        summaries = get_vault_summaries()
        if not summaries:
            return "no vaults yet"
        lines = [f"vaults: {len(summaries)} agent(s)"]
        for s in summaries:
            lines.append(
                f"  {s['identity']:12} sessions={s['session_count']} "
                f"entries={s['stream_entries']} last={s['last_active']}"
            )
        return "\n".join(lines)

    async def _handle_org_command(self, args: str) -> str:
        """Launch an organization."""
        from .org_launcher import OrgLauncher, get_org_agents, load_organization

        parts = args.strip().split(maxsplit=1)
        if not parts:
            return "usage: /hub org <name> [mission]"

        org_name = parts[0]
        mission = parts[1] if len(parts) > 1 else ""

        org = load_organization(org_name)
        if not org:
            return f"organization '{org_name}' not found. try /hub orgs"

        agents = get_org_agents(org)

        launcher = OrgLauncher(self._identity.project if self._identity else "")
        count, launched = launcher.launch_org(org_name, mission)

        lines = [f"launched org '{org_name}': {count} agents"]
        for a in agents:
            level = a.get("level", "member")
            role = a.get("role", "")
            team = a.get("team", "")
            indent = (
                "  "
                if level == "director"
                else "    " if level == "manager" else "      "
            )
            team_str = f" [{team}]" if team else ""
            lines.append(f"{indent}{a['identity']}: {role}{team_str}")

        if mission:
            lines.append(f"\nmission: {mission}")

        return "\n".join(lines)

    def _format_orgs(self) -> str:
        """List available organizations."""
        from .org_launcher import list_organizations

        orgs = list_organizations()
        if not orgs:
            return "no organizations found"

        lines = [f"organizations: {len(orgs)} available"]
        for org in orgs:
            source = f" ({org['source']})" if org.get("source") == "user" else ""
            lines.append(f"  {org['name']}{source}: {org['description']}")
        lines.append("\nusage: /hub org <name> [mission]")
        lines.append("custom: create JSON in the user hub organizations directory")
        return "\n".join(lines)

    async def _handle_feed_command(self) -> str:
        """Open live hub feed dashboard via AltView stack."""
        if not self.event_bus:
            return "event bus not available"

        try:
            from plugins.altview.hub_feed_altview import HubFeedAltView

            # Get or create the altview stack manager
            stack_mgr = None
            try:
                stack_mgr = self.event_bus.get_service("altview_stack_manager")
            except Exception:
                pass

            if not stack_mgr:
                from kollabor_tui.altview.stack_manager import AltViewStackManager

                renderer = self.event_bus.get_service("renderer")
                stack_mgr = AltViewStackManager(self.event_bus, renderer)
                self.event_bus.register_service("altview_stack_manager", stack_mgr)

            altview = HubFeedAltView()
            await stack_mgr.push(altview, "hub-feed")

            return ""

        except Exception as e:
            return f"feed error: {e}"

    async def _handle_console_command(self) -> str:
        """Open hub console (sidebar + feed) via AltView stack."""
        if not self.event_bus:
            return "event bus not available"

        try:
            from plugins.altview.hub_console_altview import HubConsoleAltView

            stack_mgr = None
            try:
                stack_mgr = self.event_bus.get_service("altview_stack_manager")
            except Exception:
                pass

            if not stack_mgr:
                from kollabor_tui.altview.stack_manager import AltViewStackManager

                renderer = self.event_bus.get_service("renderer")
                stack_mgr = AltViewStackManager(self.event_bus, renderer)
                self.event_bus.register_service("altview_stack_manager", stack_mgr)

            altview = HubConsoleAltView()
            altview.set_event_bus(self.event_bus)
            await stack_mgr.push(altview, "hub-console")

            return ""

        except Exception as e:
            return f"console error: {e}"

    def _is_enabled(self) -> bool:
        if self.config:
            return bool(self.config.get("plugins.hub.enabled", True))
        return True

    def get_vault(self):
        """Public accessor for vault (used by trender tags and compaction plugin)."""
        return self._vault

    def get_crystal_store(self):
        """Return project-scoped crystal store (default write target)."""
        return self._crystal_store

    def get_global_crystal_store(self):
        """Return global crystal store (cross-project personality/skills)."""
        return self._global_crystal_store

    def get_status_line(self) -> Optional[str]:
        """Status bar widget showing hub state."""
        if self._identity and self._started:
            peers = len(self._roster)
            role = "hub" if self._identity.is_coordinator else "mesh"
            return f"{role}:{self._identity.identity} +{peers}"

        # Attach mode fallback: show remote agent's hub info
        if self.event_bus:
            try:
                info = self.event_bus.get_service("attach_hub_info")
                if info:
                    role = "hub" if info.get("is_coordinator") else "mesh"
                    return f"{role}:{info.get('identity', '?')}"
            except Exception:
                pass

        return None

    def _get_output_lines(self, limit: int = 50) -> List[str]:
        """Return recent rendered UI output as lines.

        Pulls from DisplayTap (the same stream attach clients see) so
        `kollab --hub capture` mirrors what's visible on the agent's
        screen -- tool boxes, messages, status, etc. Events are filtered
        to type=="output" and sliced to the most recent ``limit`` events.
        Each event's ``rendered`` payload is already multi-line, so
        callers join with "\\n" cleanly.
        """
        if not self._display_tap:
            return ["(no display tap)"]

        snapshot = self._display_tap.get_snapshot()
        if not snapshot:
            return ["(no recent output)"]

        rendered_events = [e for e in snapshot if e.get("type") == "output"]
        if not rendered_events:
            return ["(no recent output)"]

        recent = rendered_events[-limit:]
        return [str(e.get("rendered", "")) for e in recent]

    async def _inject_attacher_input(self, text: str) -> None:
        """Inject text from a remote attacher as if the user typed it.

        Routes through the event bus so all hooks (hub broadcast,
        working state, etc) fire identically to local input.
        """
        if not self.event_bus:
            return
        try:
            await self.event_bus.emit_with_hooks(
                EventType.USER_INPUT,
                {"message": text, "source": "attach"},
                "hub_plugin",
            )
        except Exception as e:
            logger.debug(f"Attacher input inject error: {e}")

    async def _on_remote_shutdown(self, reason: str = "") -> None:
        """Handle shutdown signal received via hub socket.

        Triggers the app's graceful shutdown so the terminal
        gets properly restored from raw mode.
        """
        logger.info(f"Remote shutdown received: {reason}")
        try:
            self._self_stop_requested = True

            async def _remote_stop_watchdog() -> None:
                try:
                    await asyncio.sleep(REMOTE_SHUTDOWN_WATCHDOG_SECONDS)
                except asyncio.CancelledError:
                    return
                logger.warning(
                    "%s: remote-stop watchdog fired, forcing os._exit",
                    self._identity.identity if self._identity else "agent",
                )
                os._exit(0)

            asyncio.ensure_future(_remote_stop_watchdog())

            app = self.event_bus.get_service("app") if self.event_bus else None
            if app and hasattr(app, "running"):
                app.running = False

            # Cancel the main tasks so the event loop unblocks.
            # This lets the finally: cleanup() chain run, which calls
            # plugin.shutdown() for vault save + presence removal.
            if app and hasattr(app, "_background_tasks"):
                for task in list(app._background_tasks):
                    if not task.done():
                        task.cancel()

            # Use the same terminal path as self-stop: run hub cleanup,
            # remove presence/socket, then os._exit at the end. Detached
            # agents may be blocked on stdin, so waiting for the app loop to
            # wander into cleanup makes stop feel hung.
            asyncio.ensure_future(self.shutdown())
        except Exception as e:
            logger.error(f"Failed to trigger shutdown: {e}")

    async def shutdown(self) -> None:
        """Clean shutdown - save vault, remove presence, close socket, release lock."""
        # Guard against re-entry (SIGINT can fire multiple times during shutdown)
        if getattr(self, "_shutdown_in_progress", False):
            return
        self._shutdown_in_progress = True

        # Save working memory before dying
        if self._vault and self._identity:
            try:
                recent = self._vault.get_recent_stream(50)
                peer_identities = (
                    [a.identity for a in self._presence.get_cached_agents()]
                    if self._presence
                    else []
                )
                self._vault.update_working_memory(
                    recent, self._identity.current_task, peer_identities
                )
                self._vault.append_stream(
                    "session_end",
                    f"agent {self._identity.identity} shutting down",
                    from_agent=self._identity.identity,
                )
                self._vault.touch()
            except Exception as e:
                logger.error(f"Vault save on shutdown failed: {e}")

        # Save session state (serialize working context for next session)
        if self._vault and self._identity and self._session_state_mgr:
            try:
                # Populate investigation_notes from scratchpad content
                investigation_notes = ""
                if self._scratchpad:
                    investigation_notes = self._scratchpad.get()

                # Populate claimed_lanes from change_feed
                claimed_lanes: list[str] = []
                if self._change_feed:
                    try:
                        claims_result = self._change_feed.get_claims(
                            self._identity.identity
                        )
                        claimed_lanes = list(claims_result.get("claims", {}).keys())
                    except Exception:
                        pass

                # TODO: populate open_files from editor/file tracking
                #   (not yet tracked — agents currently have no file-open registry)

                state = SessionState(
                    identity=self._identity.identity,
                    open_files=[],
                    investigation_notes=investigation_notes,
                    claimed_lanes=claimed_lanes,
                    pending_promises=[],
                    last_command="",
                    focus_file=getattr(self._identity, "current_task", ""),
                )
                self._session_state_mgr.save_state(self._vault._vault_dir, state)
            except Exception as e:
                logger.error(f"Session state save on shutdown failed: {e}")

        # Release all lane claims
        if self._change_feed and self._identity:
            try:
                self._change_feed.release_all(self._identity.identity)
            except Exception as e:
                logger.debug(f"Lane release on shutdown failed: {e}")
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._mailbox_task:
            self._mailbox_task.cancel()
            try:
                await self._mailbox_task
            except asyncio.CancelledError:
                pass

        if self._dreaming_task:
            self._dreaming_task.cancel()
            try:
                await self._dreaming_task
            except asyncio.CancelledError:
                pass

        if self._notify_task:
            self._notify_task.cancel()
            try:
                await self._notify_task
            except asyncio.CancelledError:
                pass

        if self._cron_task:
            self._cron_task.cancel()
            try:
                await self._cron_task
            except asyncio.CancelledError:
                pass

        if self._autosave_task:
            self._autosave_task.cancel()
            try:
                await self._autosave_task
            except asyncio.CancelledError:
                pass

        if self._bridge_task:
            self._bridge_task.cancel()
            try:
                await self._bridge_task
            except asyncio.CancelledError:
                pass

        # Announce departure to peers before removing presence.
        # Each peer gets a unique HubMessage (unique id) so that
        # the dedup in _on_message_received can catch socket+file
        # double-delivery without id collisions across peers.
        # Only announce departure if the hub fully started (has identity + presence)
        if (
            self._started
            and self._presence
            and self._identity
            and self._identity.identity
        ):
            # Forward own departure to bridge
            await self._bridge_forward(
                f"[hub] {self._identity.identity} is going offline"
            )
            try:
                assert self._presence is not None
                live_peers = self._presence.get_cached_agents()
                # Deduplicate by identity (stale presence files can cause duplicates)
                # Skip self — no point sending departure to ourselves
                my_ident = self._identity.identity
                seen_identities = set()
                for peer in live_peers:
                    ident = peer.identity
                    if ident in seen_identities or ident == my_ident:
                        continue
                    seen_identities.add(ident)
                    try:
                        departure = HubMessage(
                            action="message",
                            from_agent=self._identity.agent_id,
                            from_identity=self._identity.identity,
                            to=ident,
                            content=f"agent '{self._identity.identity}' is going offline.",
                            scope=MessageScope.DIRECT.value,
                        )
                        await self._deliver_to_agent(peer, departure)
                    except Exception:
                        pass
            except Exception:
                pass

        # Stop socket server FIRST so peers can't reach us after
        # presence is gone (avoids confusion window).
        if self._socket_server:
            await self._socket_server.stop()

        if self._presence:
            self._presence.remove()

        if self._election:
            self._election.release()

        # Clean up message directory
        if self._identity:
            msg_dir = get_messages_dir() / self._identity.agent_id
            if msg_dir.exists():
                try:
                    for f in msg_dir.glob("*"):
                        f.unlink()
                    msg_dir.rmdir()
                except Exception:
                    pass

        logger.info("Hub plugin shut down")

        # If this was a self-stop request, exit the process after clean teardown
        if getattr(self, "_self_stop_requested", False):
            logger.info(f"{self._identity.identity}: self-stop complete, exiting process")
            os._exit(0)
