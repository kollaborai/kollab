"""Agent Orchestrator Plugin for spawning and managing parallel kollab sub-agents.

This plugin enables the LLM to spawn sub-agents via XML commands in its responses.
Sub-agents run as subprocesses and are monitored for completion via MD5 hashing.
"""

import argparse
import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from kollabor_config import ConfigSchemaBuilder, PluginConfigSchema
from kollabor_events.models import CommandResult, EventType, Hook, HookPriority
from kollabor_plugins import BasePlugin
from kollabor_tui.message_renderer import DisplayFilterRegistry, MessageType

from .activity_monitor import ActivityMonitor
from .message_injector import MessageInjector
from .models import AgentTask
from .orchestrator import AgentOrchestrator
from .xml_parser import XMLCommandParser

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for performance (used in display filter)
_ORCH_QUICK_CHECK = re.compile(
    r"</?(?:agent|status|capture|stop|message|clone|team|broadcast|sys_msg|context_inject)|<task>",
    re.IGNORECASE,
)
# Orphaned closing tags (LLM outputs </agent> without opening <agent>)
_STRIP_ORPHAN_CLOSING = re.compile(
    r"</(?:agent|status|capture|stop|message|clone|team|broadcast)>\s*",
    re.IGNORECASE,
)
_STRIP_AGENT = re.compile(r"<agent>.*?</agent>\s*", re.DOTALL)
_STRIP_STATUS = re.compile(r"<status>\s*</status>\s*")
_STRIP_CAPTURE = re.compile(r"<capture>.*?</capture>\s*", re.DOTALL)
_STRIP_STOP = re.compile(r"<stop>.*?</stop>\s*", re.DOTALL)
_STRIP_MESSAGE = re.compile(
    r'<message\s+to=["\'][^"\']+["\']>.*?</message>\s*', re.DOTALL
)
_STRIP_CLONE = re.compile(r"<clone>.*?</clone>\s*", re.DOTALL)
_STRIP_TEAM = re.compile(r"<team\s+[^>]*>.*?</team>\s*", re.DOTALL)
_STRIP_BROADCAST = re.compile(r"<broadcast\s+[^>]*>.*?</broadcast>\s*", re.DOTALL)
_STRIP_SYS_MSG = re.compile(r"<sys_msg>.*?</sys_msg>\s*", re.DOTALL)
_STRIP_CONTEXT_INJECT = re.compile(
    r"<context_inject[^>]*>.*?</context_inject>\s*", re.DOTALL
)
# Catch task assignment blocks wrapped in any tag (e.g. <sapphire>...<task>...</task>...</sapphire>)
# LLMs sometimes use identity names instead of <agent> tags
_STRIP_TASK_BLOCK = re.compile(
    r"<([a-z][\w-]*)>(?:(?!</\1>).)*<task>.*?</task>.*?</\1>\s*",
    re.DOTALL | re.IGNORECASE,
)

# Agent orchestration instructions moved to ContextService
# See core/llm/context_service.py:AGENT_ORCHESTRATION_CONTEXT
# This is now auto-injected via ContextService when trigger keywords are detected
AGENT_INSTRUCTIONS = None  # Deprecated: Use ContextService instead

# Keywords that trigger instruction injection
TRIGGER_KEYWORDS = [
    # spawn variations
    "spawn agent",
    "spawn agents",
    "spawn an agent",
    "spawn one agent",
    "spawn multiple",
    # parallel variations
    "parallel agent",
    "parallel agents",
    "in parallel",
    "run in parallel",
    "do this in parallel",
    "work in parallel",
    # sub-agent variations (all formats)
    "sub-agent",
    "sub-agents",
    "subagent",
    "subagents",
    "sub agent",
    "sub agents",
    # launch/start/create variations
    "launch agent",
    "launch an agent",
    "start an agent",
    "start agent",
    "create agent",
    "create an agent",
    # run/execute variations
    "run an agent",
    "run agent",
    "execute agent",
    "execute an agent",
    # spin up / kick off
    "spin up agent",
    "spin up an agent",
    "kick off agent",
    "kick off an agent",
    # delegate/assign/offload
    "delegate to agent",
    "delegate this to",
    "assign to agent",
    "assign an agent",
    "offload to agent",
    "offload this",
    # background/concurrent
    "background agent",
    "run in background",
    "in the background",
    "concurrent agent",
    "concurrently",
    "simultaneously",
    # agent XML
    "<agent>",
    "agent orchestrat",
    # worker variations
    "worker agent",
    "use an agent",
    "use agent",
    "have an agent",
    "let an agent",
    # fork/split
    "fork an agent",
    "fork this",
    "split into agent",
    # other natural phrases
    "another agent",
    "separate agent",
    "new agent",
]


class AgentOrchestratorPlugin(BasePlugin):
    """Plugin for spawning and managing parallel kollab sub-agents."""

    def __init__(
        self,
        name: str = "agent_orchestrator",
        event_bus=None,
        renderer=None,
        config=None,
    ):
        """Initialize the agent orchestrator plugin.

        Args:
            name: Plugin name.
            event_bus: Event bus for hook registration.
            renderer: Terminal renderer.
            config: Configuration manager.
        """
        self.name = name
        self.version = "1.0.0"
        self.description = "Spawn and manage parallel kollab sub-agents"
        self.enabled = True

        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self.command_registry = None
        self.conversation_manager = None
        self.context_service = None

        # Components (initialized in initialize())
        self.xml_parser = XMLCommandParser()
        self.orchestrator: Optional[AgentOrchestrator] = None
        self.activity_monitor: Optional[ActivityMonitor] = None
        self.message_injector: Optional[MessageInjector] = None

        self._monitor_task: Optional[asyncio.Task] = None
        self._args: Optional[argparse.Namespace] = None

        # Tracking for keyword trigger mode (legacy, for startup mode)
        self._last_injection_time: float = 0.0
        self._message_count_since_injection: int = 0
        self._context_triggers_registered: bool = False

        # New: proactive user-facing offer tracking
        self._offer_shown_in_conversation: bool = False
        self._last_offer_time: float = 0.0

        self.logger = logger

    # -------------------------------------------------------------------------
    # CLI Args (static, called before app init)
    # -------------------------------------------------------------------------

    @staticmethod
    def register_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register CLI arguments for agent management."""
        group = parser.add_argument_group("Agent Orchestrator")
        group.add_argument(
            "--session",
            type=str,
            metavar="NAME",
            help="Agent session name to interact with",
        )
        group.add_argument(
            "--capture",
            type=int,
            metavar="LINES",
            default=None,
            help="Capture N lines from session (requires --session)",
        )
        group.add_argument(
            "--list-agents",
            action="store_true",
            help="List all active agents and exit",
        )

    @staticmethod
    def handle_early_args(  # type: ignore[override]
        args: argparse.Namespace,
    ) -> tuple[bool, str | None]:
        """Handle args that exit before app starts.

        Returns:
            Tuple of (should_exit, output_message).
            If should_exit is True, output_message contains the text to display.
        """
        project_name = Path.cwd().name

        if getattr(args, "list_agents", False):
            orchestrator = AgentOrchestrator(project_name=project_name)
            agents = orchestrator.list_agents()
            if agents:
                lines = ["[agents]"]
                for agent in agents:
                    lines.append(
                        f"  {agent.name:<20} {agent.status:<10} {agent.duration}"
                    )
                return (True, "\n".join(lines))
            else:
                return (True, "No active agents")

        session = getattr(args, "session", None)
        capture = getattr(args, "capture", None)

        if session and capture:
            orchestrator = AgentOrchestrator(project_name=project_name)
            output = orchestrator.capture_output(session, capture)
            return (True, output)

        return (False, None)  # continue normal startup

    # -------------------------------------------------------------------------
    # Plugin Lifecycle
    # -------------------------------------------------------------------------

    async def initialize(  # type: ignore[override]
        self, args: Optional[argparse.Namespace] = None, **kwargs
    ) -> None:
        """Initialize plugin components.

        Args:
            args: Parsed CLI arguments.
            **kwargs: Additional initialization parameters including:
                - event_bus: Event bus for hook registration
                - config: Configuration manager
                - command_registry: Command registry for slash commands
                - conversation_manager: Conversation manager instance
        """
        self._args = args

        # Get dependencies from kwargs
        if "event_bus" in kwargs:
            self.event_bus = kwargs["event_bus"]
        if "config" in kwargs:
            self.config = kwargs["config"]
        if "command_registry" in kwargs:
            self.command_registry = kwargs["command_registry"]
        if "conversation_manager" in kwargs:
            self.conversation_manager = kwargs["conversation_manager"]
        if "context_service" in kwargs:
            self.context_service = kwargs["context_service"]

        # Get config values with defaults
        def cfg(key, default):
            return (
                self.config.get(f"plugins.agent_orchestrator.{key}", default)
                if self.config
                else default
            )

        # Activity monitor config
        poll_interval = cfg("poll_interval", 2)
        idle_threshold = cfg("idle_threshold", 3)
        capture_lines = cfg("capture_lines", 500)

        # Orchestrator timing config
        session_init_delay = cfg("session_init_delay", 3.0)
        kollab_init_delay = cfg("kollab_init_delay", 4.0)
        message_delay = cfg("message_delay", 2.0)

        # Ready detection config
        ready_timeout = cfg("ready_timeout", 30.0)
        ready_poll_interval = cfg("ready_poll_interval", 0.5)
        ready_stable_threshold = cfg("ready_stable_threshold", 2)
        ready_initial_wait = cfg("ready_initial_wait", 1.0)

        # Initialize orchestrator with config
        self.orchestrator = AgentOrchestrator(
            project_name=Path.cwd().name,
            session_init_delay=session_init_delay,
            kollab_init_delay=kollab_init_delay,
            message_delay=message_delay,
            ready_timeout=ready_timeout,
            ready_poll_interval=ready_poll_interval,
            ready_stable_threshold=ready_stable_threshold,
            ready_initial_wait=ready_initial_wait,
        )

        # Initialize message injector if we have conversation manager
        if self.conversation_manager and self.event_bus:
            self.message_injector = MessageInjector(
                event_bus=self.event_bus,
                conversation_manager=self.conversation_manager,
            )

        # Initialize activity monitor
        self.activity_monitor = ActivityMonitor(
            orchestrator=self.orchestrator,
            on_agent_complete=self._on_agent_complete,
            poll_interval=poll_interval,
            idle_threshold=idle_threshold,
            capture_lines=capture_lines,
        )

        # Register display filter to strip orchestrator XML from displayed messages
        # Applies to both ASSISTANT (XML commands) and USER (<sys_msg> tags)
        DisplayFilterRegistry.register(
            name="agent_orchestrator",
            filter_fn=self._strip_orchestrator_xml,
            message_types=[MessageType.ASSISTANT, MessageType.USER],
            priority=100,
        )

        # Register slash commands if command registry is available
        if self.command_registry:
            self._register_commands()

        # Phase 3: register orchestrator XML tags with the unified tool pipeline
        self._register_pipeline_tools()

        # Reset proactive offer state when starting a new conversation
        self._offer_shown_in_conversation = False
        self._last_offer_time = 0.0

        logger.info("Agent orchestrator plugin initialized")

    async def start_monitor(self) -> None:
        """Start the activity monitor background task."""
        if self.activity_monitor and not self._monitor_task:
            self._monitor_task = asyncio.create_task(self.activity_monitor.start())
            logger.info("Activity monitor started")

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                logger.debug("Activity monitor task cancelled")
            except Exception as e:
                logger.warning(f"Error cancelling monitor task: {e}")

        # Cancel background tasks in orchestrator
        if self.orchestrator:
            await self.orchestrator.shutdown()

        # Unregister display filter
        DisplayFilterRegistry.unregister("agent_orchestrator")

        logger.info("Agent orchestrator plugin shutdown")

    # -------------------------------------------------------------------------
    # Command Registration
    # -------------------------------------------------------------------------

    def _register_commands(self):
        """Register subagent commands with the command registry."""
        from kollabor_events.models import (
            CommandCategory,
            CommandDefinition,
            CommandMode,
            SubcommandInfo,
        )

        # /sub - manage agent sessions
        subagent_cmd = CommandDefinition(
            name="sub",
            description="Manage agent sessions (list/status/capture/stop/message)",
            handler=self._handle_sub_command,
            plugin_name=self.name,
            category=CommandCategory.CUSTOM,
            mode=CommandMode.INSTANT,
            aliases=["subagent", "sa"],
            icon="[🤖]",
            subcommands=[
                SubcommandInfo("list", "", "List all active agents"),
                SubcommandInfo(
                    "status", "[name]", "Get status of agent(s) (all if no name)"
                ),
                SubcommandInfo("create", "<name> <task>", "Create new agent with task"),
                SubcommandInfo(
                    "capture", "<name|all> [lines]", "Capture output from agent(s)"
                ),
                SubcommandInfo("stop", "<name|all>", "Stop agent(s)"),
                SubcommandInfo("message", "<name> <msg>", "Send message to agent"),
            ],
            cli_hidden=False,  # Allow CLI invocation: kollab --sub list
        )
        self.command_registry.register_command(subagent_cmd)

        self.logger.info("Subagent commands registered")

    # ------------------------------------------------------------------
    # Phase 3: pipeline tool registration
    # ------------------------------------------------------------------
    # Orchestrator XML tags are registered with response_parser so they
    # get extracted and stripped from display automatically.  Matching
    # handlers are registered with tool_executor so the pipeline routes
    # execution to us.  _on_llm_response still handles the display
    # filter (sys_msg, context_inject on USER messages) and orphan tags.
    # ------------------------------------------------------------------

    def _register_pipeline_tools(self) -> None:
        """Register orchestrator XML tags with response_parser and tool_executor.

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

        # --- <status></status> ---
        status_pat = _re.compile(
            r"<status>\s*</status>",
            _re.IGNORECASE,
        )

        def _extract_status(m):
            return {}

        response_parser.register_plugin_tag(
            "status", status_pat, "orch_status", _extract_status,
        )
        tool_executor.register_plugin_handler("orch_status", self._handle_status_tool)

        # --- <capture>name [lines]</capture> ---
        capture_pat = _re.compile(
            r"<capture>(.*?)</capture>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_capture(m):
            content = m.group(1).strip()
            parts = content.split()
            target = parts[0] if parts else ""
            lines = 50
            if len(parts) > 1:
                try:
                    lines = int(parts[-1])
                    target = " ".join(parts[:-1]).replace(",", "").strip()
                except ValueError:
                    target = content
            return {"target": target, "lines": lines}

        response_parser.register_plugin_tag(
            "capture", capture_pat, "orch_capture", _extract_capture,
        )
        tool_executor.register_plugin_handler("orch_capture", self._handle_capture_tool)

        # --- <stop>name1, name2</stop> ---
        stop_pat = _re.compile(
            r"<stop>(.*?)</stop>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_stop(m):
            content = m.group(1).strip()
            targets = [t.strip() for t in _re.split(r"[,\s]+", content) if t.strip()]
            return {"targets": targets}

        response_parser.register_plugin_tag(
            "stop", stop_pat, "orch_stop", _extract_stop,
        )
        tool_executor.register_plugin_handler("orch_stop", self._handle_stop_tool)

        # --- <message to="name">content</message> ---
        message_pat = _re.compile(
            r'<message\s+to=["\']([^"\']+)["\']>(.*?)</message>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_message(m):
            return {"target": m.group(1), "content": m.group(2).strip()}

        response_parser.register_plugin_tag(
            "message", message_pat, "orch_message", _extract_message,
        )
        tool_executor.register_plugin_handler(
            "orch_message", self._handle_message_tool,
        )

        # --- <broadcast to="pattern">content</broadcast> ---
        broadcast_pat = _re.compile(
            r'<broadcast\s+to=["\']([^"\']+)["\']>(.*?)</broadcast>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_broadcast(m):
            return {"pattern": m.group(1), "content": m.group(2).strip()}

        response_parser.register_plugin_tag(
            "broadcast", broadcast_pat, "orch_broadcast", _extract_broadcast,
        )
        tool_executor.register_plugin_handler(
            "orch_broadcast", self._handle_broadcast_tool,
        )

        # --- <clone>...</clone> ---
        clone_pat = _re.compile(
            r"<clone>(.*?)</clone>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_clone(m):
            # Parse agent definitions from the clone block
            agents = self._parse_agent_defs_from_text(m.group(1))
            agent = agents[0] if agents else None
            return {
                "agent_name": agent.name if agent else "",
                "task": agent.task if agent else "",
                "files": agent.files if agent else [],
                "agent_type": agent.agent_type if agent else "",
                "skills": agent.skills if agent else [],
            }

        response_parser.register_plugin_tag(
            "clone", clone_pat, "orch_clone", _extract_clone,
        )
        tool_executor.register_plugin_handler("orch_clone", self._handle_clone_tool)

        # --- <team lead="name" workers="N">...</team> ---
        team_pat = _re.compile(
            r'<team\s+lead=["\']([^"\']+)["\']\s+workers=["\'](\d+)["\']>(.*?)</team>',
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_team(m):
            lead = m.group(1)
            workers = int(m.group(2))
            agents = self._parse_agent_defs_from_text(
                f"<{lead}>{m.group(3)}</{lead}>"
            )
            return {
                "lead": lead,
                "workers": workers,
                "agents": [
                    {
                        "name": a.name,
                        "task": a.task,
                        "files": a.files,
                        "agent_type": a.agent_type,
                        "skills": a.skills,
                    }
                    for a in agents
                ],
            }

        response_parser.register_plugin_tag(
            "team", team_pat, "orch_team", _extract_team,
        )
        tool_executor.register_plugin_handler("orch_team", self._handle_team_tool)

        # --- <agent>...</agent> ---
        agent_pat = _re.compile(
            r"<agent>(.*?)</agent>",
            _re.DOTALL | _re.IGNORECASE,
        )

        def _extract_agent(m):
            agents = self._parse_agent_defs_from_text(m.group(1))
            return {
                "agents": [
                    {
                        "name": a.name,
                        "task": a.task,
                        "files": a.files,
                        "agent_type": a.agent_type,
                        "skills": a.skills,
                    }
                    for a in agents
                ],
            }

        response_parser.register_plugin_tag(
            "agent", agent_pat, "orch_agent", _extract_agent,
        )
        tool_executor.register_plugin_handler("orch_agent", self._handle_agent_tool)

        logger.info("Registered 8 orchestrator pipeline tags (status, capture, stop, message, broadcast, clone, team, agent)")

    # ------------------------------------------------------------------
    # Pipeline tool handlers
    # ------------------------------------------------------------------

    def _parse_agent_defs_from_text(self, block: str) -> list:
        """Parse agent definitions from a text block using xml_parser logic.

        Shared by extract_fns for agent, clone, and team tags.
        """
        from .models import AgentTask
        pattern = (
            r"<((?!task|files|file|todo|goal|n\d|agent-type|skill)\w[\w-]*)>(.*?)</\1>"
        )
        agents = []
        for match in re.finditer(pattern, block, re.DOTALL):
            name = match.group(1)
            content = match.group(2)
            agent_type_match = re.search(
                r"<agent-type>(.*?)</agent-type>", content, re.DOTALL
            )
            agent_type = agent_type_match.group(1).strip() if agent_type_match else ""
            skills = [
                s.strip()
                for s in re.findall(r"<skill>(.*?)</skill>", content, re.DOTALL)
            ]
            task_match = re.search(r"<task>(.*?)</task>", content, re.DOTALL)
            task = task_match.group(1).strip() if task_match else ""
            files = []
            files_match = re.search(r"<files>(.*?)</files>", content, re.DOTALL)
            if files_match:
                files = [
                    f.strip()
                    for f in re.findall(r"<file>(.*?)</file>", files_match.group(1))
                ]
            agents.append(AgentTask(
                name=name, task=task, files=files,
                agent_type=agent_type, skills=skills,
            ))
        return agents

    async def _handle_status_tool(self, tool_data: dict):
        """Execute a status tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        result_text = await self._get_status()
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_status",
            success=True,
            output=result_text,
        )

    async def _handle_capture_tool(self, tool_data: dict):
        """Execute a capture tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        target = tool_data.get("target", "")
        lines = tool_data.get("lines", 50)
        if not target:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="orch_capture",
                success=False,
                error="no target specified",
            )
        result_text = await self._capture_output(target, lines)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_capture",
            success=True,
            output=result_text,
        )

    async def _handle_stop_tool(self, tool_data: dict):
        """Execute a stop tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        targets = tool_data.get("targets", [])
        if not targets:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="orch_stop",
                success=False,
                error="no targets specified",
            )
        result_text = await self._stop_agents(targets)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_stop",
            success=True,
            output=result_text,
        )

    async def _handle_message_tool(self, tool_data: dict):
        """Execute a message tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        target = tool_data.get("target", "")
        content = tool_data.get("content", "")
        if not target:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="orch_message",
                success=False,
                error="no target specified",
            )
        result_text = await self._message_agent(target, content)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_message",
            success=True,
            output=result_text,
        )

    async def _handle_broadcast_tool(self, tool_data: dict):
        """Execute a broadcast tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        pattern = tool_data.get("pattern", "")
        content = tool_data.get("content", "")
        if not pattern:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="orch_broadcast",
                success=False,
                error="no pattern specified",
            )
        result_text = await self._broadcast(pattern, content)
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_broadcast",
            success=True,
            output=result_text,
        )

    async def _handle_clone_tool(self, tool_data: dict):
        """Execute a clone tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult
        from .models import AgentTask

        name = tool_data.get("agent_name", "")
        if not name:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="orch_clone",
                success=False,
                error="no agent name specified",
            )
        agent = AgentTask(
            name=name,
            task=tool_data.get("task", ""),
            files=tool_data.get("files", []),
            agent_type=tool_data.get("agent_type", ""),
            skills=tool_data.get("skills", []),
        )
        result_text = await self._clone_agent(agent, wait=False)
        success = result_text.startswith("[cloned:")
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_clone",
            success=success,
            output=result_text,
        )

    async def _handle_team_tool(self, tool_data: dict):
        """Execute a team tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult
        from .models import AgentTask

        lead = tool_data.get("lead", "")
        workers = tool_data.get("workers", 3)
        raw_agents = tool_data.get("agents", [])
        agents = [
            AgentTask(
                name=a["name"], task=a.get("task", ""), files=a.get("files", []),
                agent_type=a.get("agent_type", ""), skills=a.get("skills", []),
            )
            for a in raw_agents
        ]
        result_text = await self._spawn_team(lead, workers, agents, wait=False)
        success = result_text.startswith("[team spawned:")
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_team",
            success=success,
            output=result_text,
        )

    async def _handle_agent_tool(self, tool_data: dict):
        """Execute an agent spawn tool."""
        from kollabor_agent.tool_executor import ToolExecutionResult
        from .models import AgentTask

        raw_agents = tool_data.get("agents", [])
        if not raw_agents:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="orch_agent",
                success=False,
                error="no agents defined in <agent> block",
            )
        agents = [
            AgentTask(
                name=a["name"], task=a.get("task", ""), files=a.get("files", []),
                agent_type=a.get("agent_type", ""), skills=a.get("skills", []),
            )
            for a in raw_agents
        ]
        result_text = await self._spawn_agents(agents, wait=False)
        success = result_text.startswith("[spawned:")
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="orch_agent",
            success=success,
            output=result_text,
        )

    async def _handle_sub_command(self, command) -> "CommandResult":
        """Handle /sub command with subcommands.

        Usage:
            /sub list                    List all active agents
            /sub status <name>           Get status of specific agent
            /sub create <name> <task>    Create new agent with task
            /sub capture <name|all> [lines]  Capture output from agent(s)
            /sub stop <name>             Stop an agent
            /sub message <name> <msg>    Send message to agent
        """
        from kollabor_events.models import CommandResult

        args = command.args if command.args else []

        if not args:
            # No args - show list by default
            result = await self._cmd_list([])
            return result

        subcommand = args[0].lower()

        if subcommand == "list":
            result = await self._cmd_list(args[1:])
        elif subcommand == "status":
            result = await self._cmd_status(args[1:])
        elif subcommand == "create":
            result = await self._cmd_create(args[1:])
        elif subcommand == "capture":
            result = await self._cmd_capture(args[1:])
        elif subcommand == "stop":
            result = await self._cmd_stop(args[1:])
        elif subcommand == "message":
            result = await self._cmd_message(args[1:])
        elif subcommand in ("help", "--help", "-h"):
            return CommandResult(
                success=True, message=self._get_help_text(), display_type="info"
            )
        else:
            return CommandResult(
                success=False,
                message=f"Unknown subcommand: {subcommand}\n\n{self._get_help_text()}",
                display_type="error",
            )

        return result

    def _get_help_text(self) -> str:
        """Get help text for subagent command."""
        return """Agent Session Manager

Manage parallel kollab sub-agents spawned via XML commands.

Usage:
  /sub list                    List all active agents
  /sub status [name]           Get status of agent(s) (all if no name provided)
  /sub create <name> <task>    Create new agent with task
  /sub capture <name|all> [lines]  Capture output from agent(s) (default: 50)
  /sub stop <name|all>         Stop agent(s)
  /sub message <name> <msg>    Send message to agent

Examples:
  /sub list
  /sub status                  Show status of all agents
  /sub status researcher-1     Show status of specific agent
  /sub create bug-fix "review the authentication code"
  /sub capture dev-agent 100
  /sub capture all
  /sub stop researcher-1
  /sub stop all                Stop all agents
  /sub message dev-agent "continue with task 2"

Aliases: /sub, /sa"""

    async def _cmd_create(self, args: list) -> "CommandResult":
        """Create a new agent with a task.

        Args:
            args: Agent name and task description.

        Returns:
            CommandResult with creation confirmation.
        """
        from kollabor_events.models import CommandResult

        if not self.orchestrator:
            return CommandResult(
                success=False,
                message="Agent orchestrator not initialized",
                display_type="error",
            )

        if len(args) < 2:
            return CommandResult(
                success=False,
                message="Usage: /sub create <name> <task>\nExample: /sub create bug-fix 'review assignment'",
                display_type="error",
            )

        agent_name = args[0]
        task = " ".join(args[1:])  # Everything after name is the task

        # Spawn the agent (non-blocking)
        import asyncio

        result = await self.orchestrator.spawn(
            name=agent_name, task=task, files=[], wait=False  # Non-blocking spawn
        )

        if not result:
            return CommandResult(
                success=False,
                message=f"Failed to create agent '{agent_name}'",
                display_type="error",
            )

        # Send system message indicating user created this agent
        user_sys_msg = f"<sys_msg>User has created you as an agent. User requested: {task}</sys_msg>"
        # Use orchestrator's configured init delay
        init_delay = self.orchestrator.kollab_init_delay if self.orchestrator else 2
        await asyncio.sleep(init_delay)
        send_result = await self.orchestrator.message(agent_name, user_sys_msg)

        # Reset activity state so monitor waits for new activity
        if self.activity_monitor:
            self.activity_monitor.reset_agent_state(agent_name)

        return CommandResult(
            success=True,
            message=f"Created agent '{agent_name}'\nTask: {task}\nSystem message sent: {send_result}",
            display_type="success",
        )

    async def _cmd_list(self, args: list) -> "CommandResult":
        """List all active agents.

        Args:
            args: Additional arguments (unused for list).

        Returns:
            CommandResult with agent list.
        """
        from kollabor_events.models import CommandResult

        if not self.orchestrator:
            return CommandResult(
                success=False,
                message="Agent orchestrator not initialized",
                display_type="error",
            )

        agents = self.orchestrator.list_agents()

        if not agents:
            msg = (
                "No active agents. Use XML commands to spawn agents:\n"
                "  <agent><name><task>...</task></name></agent>"
            )
            return CommandResult(success=True, message=msg, display_type="info")

        lines = ["Active Agents:"]
        for agent in agents:
            lines.append(f"  {agent.name:<25} {agent.status:<12} {agent.duration}")

        return CommandResult(
            success=True, message="\n".join(lines), display_type="info"
        )

    async def _cmd_status(self, args: list) -> "CommandResult":
        """Get status of agent(s).

        Args:
            args: Optional agent name argument. If not provided, shows all agents.

        Returns:
            CommandResult with agent status.
        """
        import datetime

        from kollabor_events.models import CommandResult

        if not self.orchestrator:
            return CommandResult(
                success=False,
                message="Agent orchestrator not initialized",
                display_type="error",
            )

        # If no args provided, show detailed status for all agents
        if not args:
            agents = self.orchestrator.list_agents()

            if not agents:
                msg = (
                    "No active agents. Use XML commands to spawn agents:\n"
                    "  <agent><name><task>...</task></name></agent>"
                )
                return CommandResult(success=True, message=msg, display_type="info")

            # Show detailed status for each agent
            lines = ["Agent Status:"]
            for _agent in agents:
                created_at = datetime.datetime.fromtimestamp(
                    _agent.start_time
                ).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"\n  Name: {_agent.name}")
                lines.append(f"  Status: {_agent.status}")
                lines.append(f"  Duration: {_agent.duration}")
                lines.append(f"  Started: {created_at}")

            return CommandResult(
                success=True, message="\n".join(lines), display_type="info"
            )

        # Single agent status
        agent_name = args[0]
        agent = self.orchestrator.get_agent(agent_name)

        if not agent:
            return CommandResult(
                success=False,
                message=f"Agent '{agent_name}' not found",
                display_type="error",
            )

        # Get status info
        created_at = datetime.datetime.fromtimestamp(agent.start_time).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        lines = [
            f"Agent: {agent.name}",
            f"Status: {agent.status}",
            f"Duration: {agent.duration}",
            f"Started: {created_at}",
        ]

        return CommandResult(
            success=True, message="\n".join(lines), display_type="info"
        )

    async def _cmd_capture(self, args: list) -> "CommandResult":
        """Capture output from agent(s).

        Args:
            args: Agent name (or "all") and optional lines count.

        Returns:
            CommandResult with captured output.
        """
        from kollabor_events.models import CommandResult

        if not self.orchestrator:
            return CommandResult(
                success=False,
                message="Agent orchestrator not initialized",
                display_type="error",
            )

        if not args:
            return CommandResult(
                success=False,
                message="Usage: /sub capture <agent_name|all> [lines]",
                display_type="error",
            )

        agent_name = args[0]
        lines = 50  # default
        if len(args) > 1:
            try:
                lines = int(args[1])
            except ValueError:
                return CommandResult(
                    success=False,
                    message=f"Invalid line count: {args[1]}",
                    display_type="error",
                )

        # Handle "all" - capture from all agents
        if agent_name.lower() == "all":
            agents = self.orchestrator.get_all_agents()
            if not agents:
                return CommandResult(
                    success=True, message="No active agents", display_type="info"
                )

            results = []
            for _name, _agent in agents.items():
                output = self.orchestrator.capture_output(_name, lines)
                output_lines = output.strip().split("\n")
                preview = "\n".join(output_lines[-lines:])
                results.append(
                    f"[{_name} @ {_agent.duration}, {len(output_lines)} lines]\n{preview}"
                )

            separator = "\n" + "=" * 60 + "\n"
            return CommandResult(
                success=True, message=separator.join(results), display_type="info"
            )

        # Single agent capture
        output = self.orchestrator.capture_output(agent_name, lines)
        agent = self.orchestrator.get_agent(agent_name)

        if not agent:
            return CommandResult(
                success=False,
                message=f"Agent '{agent_name}' not found",
                display_type="error",
            )

        # Format output
        output_lines = output.strip().split("\n")
        preview = "\n".join(output_lines[-lines:])

        result = f"[capture: {agent_name} @ {agent.duration}, {len(output_lines)} lines]\n{preview}"

        return CommandResult(success=True, message=result, display_type="info")

    async def _cmd_stop(self, args: list) -> "CommandResult":
        """Stop agent(s).

        Args:
            args: Agent name argument or "all" to stop all agents.

        Returns:
            CommandResult with stop confirmation.
        """
        from kollabor_events.models import CommandResult

        if not self.orchestrator:
            return CommandResult(
                success=False,
                message="Agent orchestrator not initialized",
                display_type="error",
            )

        if not args:
            return CommandResult(
                success=False,
                message="Usage: /sub stop <agent_name|all>",
                display_type="error",
            )

        agent_name = args[0]

        # Handle "all" - stop all agents
        if agent_name.lower() == "all":
            agents = self.orchestrator.get_all_agents()
            if not agents:
                return CommandResult(
                    success=True,
                    message="No active agents to stop",
                    display_type="info",
                )

            stopped_count = 0
            failed_count = 0
            results = []
            errors = []

            for name, agent in agents.items():
                try:
                    # Untrack from activity monitor
                    if self.activity_monitor:
                        self.activity_monitor.untrack(name)

                    # Stop agent
                    output, duration = await self.orchestrator.stop(name)

                    # Emit plugin-specific event
                    if self.event_bus:
                        await self.event_bus.emit_with_hooks(
                            "agent_stopped",
                            {"name": name, "duration": duration, "output": output},
                            "agent_orchestrator",
                        )

                    # Truncate output for display
                    output_lines = output.strip().split("\n")
                    if len(output_lines) > 5:
                        output_preview = "\n".join(output_lines[-5:])
                    else:
                        output_preview = output.strip()

                    results.append(f"[stopped: {name} @ {duration}]\n{output_preview}")
                    stopped_count += 1
                except Exception as e:
                    logger.error(f"Error stopping agent {name}: {e}")
                    errors.append(f"{name}: {str(e)}")
                    failed_count += 1

            separator = "\n" + "=" * 60 + "\n"
            result_parts = []
            result_parts.append(f"Stopped {stopped_count} agent(s)")

            if errors:
                result_parts.append(f"\nFailed to stop {failed_count} agent(s):")
                result_parts.extend(errors)

            if results:
                result_parts.append(f"\n{separator.join(results)}")

            return CommandResult(
                success=(failed_count == 0),
                message="\n".join(result_parts),
                display_type="success" if failed_count == 0 else "error",
            )

        # Single agent stop
        # Untrack from activity monitor
        if self.activity_monitor:
            self.activity_monitor.untrack(agent_name)

        # Stop agent
        output, duration = await self.orchestrator.stop(agent_name)

        # Emit plugin-specific event
        if self.event_bus:
            await self.event_bus.emit_with_hooks(
                "agent_stopped",
                {"name": agent_name, "duration": duration, "output": output},
                "agent_orchestrator",
            )

        # Truncate output for display
        output_lines = output.strip().split("\n")
        if len(output_lines) > 10:
            output_preview = "\n".join(output_lines[-10:])
        else:
            output_preview = output.strip()

        result = f"[stopped: {agent_name} @ {duration}]\n{output_preview}"

        return CommandResult(success=True, message=result, display_type="success")

    async def _cmd_message(self, args: list) -> "CommandResult":
        """Send message to agent.

        Args:
            args: Agent name and message content.

        Returns:
            CommandResult with message confirmation.
        """
        from kollabor_events.models import CommandResult

        if not self.orchestrator:
            return CommandResult(
                success=False,
                message="Agent orchestrator not initialized",
                display_type="error",
            )

        if len(args) < 2:
            return CommandResult(
                success=False,
                message="Usage: /subagent message <agent_name> <message>",
                display_type="error",
            )

        agent_name = args[0]
        content = " ".join(args[1:])

        success = await self.orchestrator.message(agent_name, content)

        # Reset activity state so monitor waits for new activity
        if success and self.activity_monitor:
            self.activity_monitor.reset_agent_state(agent_name)

        if success:
            return CommandResult(
                success=True,
                message=f"Message sent to '{agent_name}'",
                display_type="success",
            )
        else:
            return CommandResult(
                success=False,
                message=f"Agent '{agent_name}' not found",
                display_type="error",
            )

    # -------------------------------------------------------------------------
    # Display Filter
    # -------------------------------------------------------------------------

    def _strip_orchestrator_xml(self, content: str, message_type: MessageType) -> str:
        """Strip orchestrator XML commands from content before display.

        These commands are displayed separately as tool indicators,
        so we strip them from the prose to avoid duplication.

        For USER messages, this strips <sys_msg> tags that contain agent
        orchestration instructions injected by the plugin.

        Args:
            content: Message content to filter.
            message_type: Type of message (ASSISTANT and USER messages filtered).

        Returns:
            Content with orchestrator XML stripped.
        """
        # Early exit - skip expensive regex if no orchestrator tags present
        if not _ORCH_QUICK_CHECK.search(content):
            return content

        # For user messages, strip <sys_msg> and <context_inject> tags (plugin instruction injection)
        if message_type == MessageType.USER:
            content = _STRIP_SYS_MSG.sub("", content)
            content = _STRIP_CONTEXT_INJECT.sub("", content)

        # For assistant messages, strip orchestrator XML commands
        content = _STRIP_AGENT.sub("", content)
        content = _STRIP_STATUS.sub("", content)
        content = _STRIP_CAPTURE.sub("", content)
        content = _STRIP_STOP.sub("", content)
        content = _STRIP_MESSAGE.sub("", content)
        content = _STRIP_CLONE.sub("", content)
        content = _STRIP_TEAM.sub("", content)
        content = _STRIP_BROADCAST.sub("", content)

        # Catch task assignment blocks wrapped in non-standard tags
        # (e.g. <sapphire><task>...</task></sapphire> instead of <agent>)
        content = _STRIP_TASK_BLOCK.sub("", content)

        # Catch orphaned closing tags (e.g. </agent> without <agent>)
        content = _STRIP_ORPHAN_CLOSING.sub("", content)

        return content

    # -------------------------------------------------------------------------
    # Hook Registration
    # -------------------------------------------------------------------------

    async def register_hooks(self) -> None:
        """Register hooks for LLM response processing and keyword triggers."""
        if not self.event_bus:
            logger.warning("No event bus available for hook registration")
            return

        # Hook for processing XML commands in LLM responses
        response_hook = Hook(
            name="agent_orchestrator_response",
            plugin_name=self.name,
            event_type=EventType.LLM_RESPONSE_POST,
            callback=self._on_llm_response,
            priority=HookPriority.POSTPROCESSING.value,
        )
        await self.event_bus.register_hook(response_hook)

        # Hook for ContextService ready - register our triggers
        context_ready_hook = Hook(
            name="agent_orchestrator_context_ready",
            plugin_name=self.name,
            event_type=EventType.CONTEXT_SERVICE_READY,
            callback=self._on_context_service_ready,
            priority=HookPriority.PREPROCESSING.value,
        )
        await self.event_bus.register_hook(context_ready_hook)

        # Hook for keyword-triggered instruction injection (legacy, for startup mode)
        # This is only used if enable_mode is "startup" - otherwise ContextService handles it
        keyword_hook = Hook(
            name="agent_orchestrator_keyword_trigger",
            plugin_name=self.name,
            event_type=EventType.USER_INPUT_PRE,
            callback=self._on_user_input,
            priority=HookPriority.PREPROCESSING.value,
        )
        await self.event_bus.register_hook(keyword_hook)

        # Register as a service so other plugins (hub) can access us
        self.event_bus.register_service("agent_orchestrator", self)

        logger.info("Registered agent orchestration hooks")

    # -------------------------------------------------------------------------
    # Default Config
    # -------------------------------------------------------------------------

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Get default configuration for this plugin."""
        return {
            "plugins": {
                "agent_orchestrator": {
                    "enabled": True,
                    "enabled_desc": "Enable/disable the agent orchestrator plugin",
                    # Activity monitor settings
                    "poll_interval": 2,
                    "poll_interval_desc": "Seconds between activity monitor polls to detect agent completion",
                    "idle_threshold": 3,
                    "idle_threshold_desc": "Consecutive unchanged polls required to consider agent complete",
                    "capture_lines": 500,
                    "capture_lines_desc": "Number of lines to capture from agent pane on completion",
                    "max_concurrent": 10,
                    "max_concurrent_desc": "Maximum number of concurrent agents allowed",
                    # Instruction injection mode
                    "enable_mode": "keyword",
                    "enable_mode_desc": (
                        "How to inject agent instructions: 'keyword' (on trigger words), "
                        "'startup' (in system prompt), 'disabled'"
                    ),
                    "trigger_delay": "0",
                    "trigger_delay_desc": (
                        "Re-injection delay: '0' (once only), '60s' (every 60 sec), "
                        "'5m' (every 5 messages)"
                    ),
                    # Timing configuration for spawning agents
                    "session_init_delay": 3.0,
                    "session_init_delay_desc": "Seconds to wait after creating subprocess before sending commands",
                    "kollab_init_delay": 4.0,
                    "kollab_init_delay_desc": "Fallback delay if ready detection fails or times out",
                    "message_delay": 2.0,
                    "message_delay_desc": "Seconds to wait after sending a message to agent",
                    # Ready detection settings
                    "ready_timeout": 30.0,
                    "ready_timeout_desc": "Max seconds to wait for kollab to be ready before using fallback delay",
                    "ready_poll_interval": 0.5,
                    "ready_poll_interval_desc": "Seconds between polls when detecting if kollab is ready",
                    "ready_stable_threshold": 2,
                    "ready_stable_threshold_desc": (
                        "Consecutive stable polls (unchanged output) required "
                        "to consider ready"
                    ),
                    "ready_initial_wait": 1.0,
                    "ready_initial_wait_desc": "Seconds to wait before starting ready detection polling",
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "Agent Orchestrator",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.agent_orchestrator.enabled",
                    "help": "Enable/disable the agent orchestrator plugin",
                },
                {
                    "type": "slider",
                    "label": "Max Concurrent",
                    "config_path": "plugins.agent_orchestrator.max_concurrent",
                    "min_value": 1,
                    "max_value": 30,
                    "step": 1,
                    "help": "Maximum number of concurrent agents allowed",
                },
                {
                    "type": "slider",
                    "label": "Poll Interval",
                    "config_path": "plugins.agent_orchestrator.poll_interval",
                    "min_value": 1,
                    "max_value": 10,
                    "step": 1,
                    "help": "Seconds between activity monitor polls",
                },
                {
                    "type": "slider",
                    "label": "Idle Threshold",
                    "config_path": "plugins.agent_orchestrator.idle_threshold",
                    "min_value": 1,
                    "max_value": 10,
                    "step": 1,
                    "help": "Consecutive unchanged polls to consider agent complete",
                },
                {
                    "type": "slider",
                    "label": "Capture Lines",
                    "config_path": "plugins.agent_orchestrator.capture_lines",
                    "min_value": 50,
                    "max_value": 2000,
                    "step": 50,
                    "help": "Lines to capture from agent pane on completion",
                },
                {
                    "type": "dropdown",
                    "label": "Enable Mode",
                    "config_path": "plugins.agent_orchestrator.enable_mode",
                    "options": ["keyword", "startup", "disabled"],
                    "help": "How to inject agent instructions into context",
                },
                {
                    "type": "slider",
                    "label": "Session Init Delay",
                    "config_path": "plugins.agent_orchestrator.session_init_delay",
                    "min_value": 0.5,
                    "max_value": 10.0,
                    "step": 0.5,
                    "help": "Seconds to wait after creating agent session",
                },
                {
                    "type": "slider",
                    "label": "Kollab Init Delay",
                    "config_path": "plugins.agent_orchestrator.kollab_init_delay",
                    "min_value": 1.0,
                    "max_value": 15.0,
                    "step": 0.5,
                    "help": "Fallback delay if ready detection fails",
                },
                {
                    "type": "slider",
                    "label": "Message Delay",
                    "config_path": "plugins.agent_orchestrator.message_delay",
                    "min_value": 0.5,
                    "max_value": 10.0,
                    "step": 0.5,
                    "help": "Seconds to wait after sending message to agent",
                },
                {
                    "type": "slider",
                    "label": "Ready Timeout",
                    "config_path": "plugins.agent_orchestrator.ready_timeout",
                    "min_value": 5.0,
                    "max_value": 60.0,
                    "step": 5.0,
                    "help": "Max seconds to wait for kollab ready detection",
                },
            ],
        }

    @staticmethod
    def get_config_schema() -> PluginConfigSchema:
        """Get configuration schema for UI widget generation.

        Returns:
            PluginConfigSchema with all configuration fields defined.
        """
        builder = ConfigSchemaBuilder(
            plugin_name="agent_orchestrator",
            description="Spawn and manage parallel kollab sub-agents via XML commands",
        )

        # General settings
        builder.add_checkbox(
            key="enabled",
            label="Enabled",
            default=True,
            help_text="Enable or disable the agent orchestrator plugin",
            category="General",
        )

        builder.add_slider(
            key="max_concurrent",
            label="Max Concurrent Agents",
            default=10,
            min_value=1,
            max_value=50,
            step=1,
            help_text="Maximum number of concurrent agents allowed",
            category="General",
        )

        # Instruction injection
        builder.add_dropdown(
            key="enable_mode",
            label="Instruction Mode",
            options=["keyword", "startup", "disabled"],
            default="keyword",
            help_text=(
                "How to inject agent instructions: 'keyword' (on trigger words), "
                "'startup' (in system prompt), 'disabled'"
            ),
            category="Instruction Injection",
        )

        builder.add_text_input(
            key="trigger_delay",
            label="Trigger Delay",
            default="0",
            placeholder="0, 60s, or 5m",
            help_text="Re-injection delay: '0' (once only), '60s' (every 60 sec), '5m' (every 5 messages)",
            category="Instruction Injection",
        )

        # Activity monitor settings
        builder.add_slider(
            key="poll_interval",
            label="Poll Interval",
            default=2,
            min_value=1,
            max_value=10,
            step=1,
            help_text="Seconds between activity monitor polls to detect agent completion",
            category="Activity Monitor",
        )

        builder.add_slider(
            key="idle_threshold",
            label="Idle Threshold",
            default=3,
            min_value=1,
            max_value=10,
            step=1,
            help_text="Consecutive unchanged polls required to consider agent complete",
            category="Activity Monitor",
        )

        builder.add_slider(
            key="capture_lines",
            label="Capture Lines",
            default=500,
            min_value=50,
            max_value=2000,
            step=50,
            help_text="Number of lines to capture from agent pane on completion",
            category="Activity Monitor",
        )

        # Timing configuration
        builder.add_slider(
            key="session_init_delay",
            label="Session Init Delay",
            default=3.0,
            min_value=0.5,
            max_value=10.0,
            step=0.5,
            help_text="Seconds to wait after creating agent session before sending commands",
            category="Timing",
            advanced=True,
        )

        builder.add_slider(
            key="kollab_init_delay",
            label="Kollab Init Delay (Fallback)",
            default=4.0,
            min_value=1.0,
            max_value=15.0,
            step=0.5,
            help_text="Fallback delay if ready detection fails or times out",
            category="Timing",
            advanced=True,
        )

        builder.add_slider(
            key="message_delay",
            label="Message Delay",
            default=2.0,
            min_value=0.5,
            max_value=5.0,
            step=0.5,
            help_text="Seconds to wait after sending a message to agent",
            category="Timing",
            advanced=True,
        )

        # Ready detection settings
        builder.add_slider(
            key="ready_timeout",
            label="Ready Timeout",
            default=30.0,
            min_value=5.0,
            max_value=120.0,
            step=5.0,
            help_text="Max seconds to wait for kollab to be ready before using fallback delay",
            category="Ready Detection",
            advanced=True,
        )

        builder.add_slider(
            key="ready_poll_interval",
            label="Ready Poll Interval",
            default=0.5,
            min_value=0.1,
            max_value=2.0,
            step=0.1,
            help_text="Seconds between polls when detecting if kollab is ready",
            category="Ready Detection",
            advanced=True,
        )

        builder.add_slider(
            key="ready_stable_threshold",
            label="Ready Stable Threshold",
            default=2,
            min_value=1,
            max_value=5,
            step=1,
            help_text="Consecutive stable polls (unchanged output) required to consider ready",
            category="Ready Detection",
            advanced=True,
        )

        builder.add_slider(
            key="ready_initial_wait",
            label="Ready Initial Wait",
            default=1.0,
            min_value=0.5,
            max_value=5.0,
            step=0.5,
            help_text="Seconds to wait before starting ready detection polling",
            category="Ready Detection",
            advanced=True,
        )

        return builder.build()

    def get_system_prompt_addition(self) -> Optional[str]:
        """Get system prompt addition if enable_mode is 'startup'.

        Returns:
            Agent instructions string, or None if not startup mode.
        """
        if not self.config:
            return None

        enable_mode = self.config.get(
            "plugins.agent_orchestrator.enable_mode", "keyword"
        )
        if enable_mode == "startup":
            # Import from kollabor_ai package since we extracted the context service
            from kollabor_ai import AGENT_ORCHESTRATION_CONTEXT

            return AGENT_ORCHESTRATION_CONTEXT
        return None

    def _parse_trigger_delay(self, delay_str: str) -> tuple[str, int]:
        """Parse trigger delay string.

        Args:
            delay_str: Delay string like "0", "60s", "5m"

        Returns:
            Tuple of (delay_type, delay_value) where:
            - ("once", 0) for "0"
            - ("seconds", N) for "Ns"
            - ("messages", N) for "Nm"
        """
        delay_str = delay_str.strip().lower()

        if delay_str == "0":
            return ("once", 0)

        if delay_str.endswith("s"):
            try:
                return ("seconds", int(delay_str[:-1]))
            except ValueError:
                return ("once", 0)

        if delay_str.endswith("m"):
            try:
                return ("messages", int(delay_str[:-1]))
            except ValueError:
                return ("once", 0)

        return ("once", 0)

    def _should_inject(self) -> bool:
        """Check if we should inject instructions based on trigger_delay.

        Returns:
            True if injection should happen.
        """
        import time

        trigger_delay = (
            self.config.get("plugins.agent_orchestrator.trigger_delay", "0")
            if self.config
            else "0"
        )
        delay_type, delay_value = self._parse_trigger_delay(trigger_delay)

        if delay_type == "once":
            # Only inject if never injected before
            return self._last_injection_time == 0.0

        elif delay_type == "seconds":
            # Inject if enough time has passed
            elapsed = time.time() - self._last_injection_time
            return elapsed >= delay_value

        elif delay_type == "messages":
            # Inject if enough messages have passed
            return self._message_count_since_injection >= delay_value

        return False

    async def _on_context_service_ready(self, data: dict, event) -> dict:
        """Register agent orchestration triggers with ContextService.

        Called when ContextService is ready for plugin registrations.

        Args:
            data: Event data with ContextService instance.
            event: Event object.

        Returns:
            Unmodified data.
        """
        context_service = data.get("service")
        if not context_service:
            logger.warning("ContextService not found in event data")
            return data

        if self._context_triggers_registered:
            logger.debug("Agent orchestration context triggers already registered")
            return data

        logger.info("Registering agent orchestration context triggers")

        # Register all trigger keywords from TRIGGER_KEYWORDS
        # Each maps to the built-in agent orchestration context
        for keyword in TRIGGER_KEYWORDS:
            # Normalize keyword (lowercase, strip)
            normalized = keyword.lower().strip()
            # Map to built-in context
            context_service.register_trigger(
                keyword=normalized, context_id="builtin:agent-orchestration"
            )

        self._context_triggers_registered = True
        logger.info(f"Registered {len(TRIGGER_KEYWORDS)} agent orchestration triggers")

        return data

    async def _on_user_input(self, data: dict, event) -> dict:
        """Check for trigger keywords and inject instructions (legacy).

        NOTE: This is now handled by ContextService for keyword mode.
        This hook only remains for "startup" mode compatibility.

        Args:
            data: Event data with user input.
            event: Event object.

        Returns:
            Modified data with injected instructions.
        """
        logger.info(
            f"[AGENT_ORCH] _on_user_input called with data keys: {list(data.keys())}"
        )
        context = data  # Alias for compatibility

        if not self.config:
            logger.info("[AGENT_ORCH] No config, returning early")
            return context

        enable_mode = self.config.get(
            "plugins.agent_orchestrator.enable_mode", "keyword"
        )

        # If keyword mode, ContextService handles injection via CONTEXT_SERVICE_READY
        # This hook only handles "startup" mode (for system prompt injection)
        if enable_mode == "keyword":
            # ContextService handles LLM context. We still run our proactive user offer logic.
            await self._maybe_offer_to_spawn_agent(
                context.get("message", context.get("input", ""))
            )
            return context

        if enable_mode != "startup":
            return context

        # Startup mode: inject on every message (legacy behavior)
        # The instructions should already be in system prompt via get_system_prompt_addition()
        # But we also inject here to ensure it's present
        original_input = context.get("message", context.get("input", ""))
        injected = f"""<sys_msg>
## Agent Orchestration

You can spawn parallel sub-agents to work on tasks concurrently. Each agent runs as a separate subprocess.

### Spawn Agents

```xml
<agent>
  <agent-name>
    <agent-type>coder</agent-type>
    <skill>debugging</skill>
    <task>
    objective: What to accomplish
    context:
    - Relevant constraints
    success: How to verify completion
    </task>
    <files>
      <file>path/to/file.py</file>
    </files>
  </agent-name>
</agent>
```

### Other Commands
```xml
<message to="agent-name">Send instruction</message>
<capture>agent-name 200</capture>
<stop>agent-name</stop>
<status></status>
```
</sys_msg>

{original_input}"""
        context["message"] = injected
        if "input" in context:
            context["input"] = injected

        logger.info("Injected agent orchestration instructions (startup mode)")

        return context

    # -------------------------------------------------------------------------
    # Core Logic
    # -------------------------------------------------------------------------

    async def _on_llm_response(self, data: dict, event) -> dict:
        """Process LLM response for agent XML commands.

        Phase 3: All 8 orchestrator XML tags (agent, message, stop,
        status, capture, clone, team, broadcast) are now registered
        with the unified tool pipeline via _register_pipeline_tools().

        The pipeline handles tag extraction, execution, display, and
        result injection.  This hook is retained as a passthrough for
        any future orchestration logic that doesn't fit the pipeline
        model (e.g. pipe-mode wait handling, cross-agent coordination).

        The display filter (_strip_orchestrator_xml) is still active
        for USER messages (sys_msg, context_inject stripping) and
        orphan tag cleanup.
        """
        return data

    def _display_tool_indicator(self, cmd) -> None:
        """Display tool call indicator using proper TagBox format like real tool calls.

        Args:
            cmd: Parsed command object.
        """
        if not self.renderer:
            return

        if cmd.type == "agent":
            # Show spawn progress in the transient thinking/status area
            names = [a.name for a in cmd.agents] if cmd.agents else []
            label = ", ".join(names) if names else "agent"
            if self.renderer:
                self.renderer.update_thinking(True, f"Spawning {label}...")
        elif cmd.type == "status":
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": "status",
                            "tool_args": "",
                            "tool_status": "running",
                        },
                    )
                ]
            )
        elif cmd.type == "capture":
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": "capture",
                            "tool_args": f"{cmd.target}, {cmd.lines}",
                            "tool_status": "running",
                        },
                    )
                ]
            )
        elif cmd.type == "stop":
            targets = ", ".join(cmd.targets) if cmd.targets else ""
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": "stop",
                            "tool_args": targets,
                            "tool_status": "running",
                        },
                    )
                ]
            )
        elif cmd.type == "message":
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": "message",
                            "tool_args": cmd.target,
                            "tool_status": "running",
                        },
                    )
                ]
            )
        elif cmd.type == "clone":
            names = [a.name for a in cmd.agents] if cmd.agents else []
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": "clone",
                            "tool_args": names[0] if names else "",
                            "tool_status": "running",
                        },
                    )
                ]
            )
        elif cmd.type == "team":
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": "spawn_team",
                            "tool_args": cmd.lead,
                            "tool_status": "running",
                        },
                    )
                ]
            )
        elif cmd.type == "broadcast":
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": "broadcast",
                            "tool_args": cmd.pattern,
                            "tool_status": "running",
                        },
                    )
                ]
            )
        else:
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "tool",
                        "",
                        {
                            "tool_name": cmd.type,
                            "tool_args": "",
                            "tool_status": "running",
                        },
                    )
                ]
            )

    def _display_tool_result(self, cmd, result: str, is_error: bool = False) -> None:
        """Display tool execution result using proper TagBox format like real tool calls.

        Args:
            cmd: Parsed command object.
            result: Result string from execution.
            is_error: Whether this is an error result.
        """
        if not self.renderer:
            return

        # Determine tool status
        tool_status = "error" if is_error else "success"

        # Extract tool name and args based on command type
        if cmd.type == "agent":
            # Clear the transient "Spawning..." from the thinking area
            self.renderer.update_thinking(False)
            names = [a.name for a in cmd.agents] if cmd.agents else []
            args = ", ".join(names) if names else ""
            tool_name = "run_subagent"
            tool_args = args
        elif cmd.type == "status":
            tool_name = "status"
            tool_args = ""
            # Status returns multi-line, show summary
            line_count = result.count("\n") + 1
            result = f"Retrieved status ({line_count} lines)"
        elif cmd.type == "capture":
            tool_name = "capture"
            tool_args = f"{cmd.target}, {cmd.lines}"
        elif cmd.type == "stop":
            targets = ", ".join(cmd.targets) if cmd.targets else ""
            tool_name = "stop"
            tool_args = targets
        elif cmd.type == "message":
            tool_name = "message"
            tool_args = cmd.target
        elif cmd.type == "clone":
            names = [a.name for a in cmd.agents] if cmd.agents else []
            tool_name = "clone"
            tool_args = names[0] if names else ""
        elif cmd.type == "team":
            tool_name = "spawn_team"
            tool_args = cmd.lead
        elif cmd.type == "broadcast":
            tool_name = "broadcast"
            tool_args = cmd.pattern
        else:
            tool_name = cmd.type
            tool_args = ""

        # For capture commands, include full output as content (not in summary)
        content = "" if cmd.type != "capture" else result
        # For capture, summary is just the header line, not full output
        result_summary = result
        if cmd.type == "capture":
            result_summary = result.split("\n")[0] if result else ""

        self.renderer.message_coordinator.display_message_sequence(
            [
                (
                    "tool",
                    content,
                    {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_status": tool_status,
                        "result_summary": result_summary,
                    },
                )
            ]
        )

    async def _execute_command(self, cmd, wait: bool = False) -> str:
        """Execute a parsed agent command.

        Args:
            cmd: ParsedCommand instance.
            wait: For spawn commands, wait for initialization to complete.

        Returns:
            Result string.
        """
        if cmd.type == "agent":
            return await self._spawn_agents(cmd.agents, wait=wait)
        elif cmd.type == "message":
            return await self._message_agent(cmd.target, cmd.content)
        elif cmd.type == "stop":
            return await self._stop_agents(cmd.targets)
        elif cmd.type == "status":
            return await self._get_status()
        elif cmd.type == "capture":
            return await self._capture_output(cmd.target, cmd.lines)
        elif cmd.type == "clone":
            if not cmd.agents:
                return "[error: clone requires an agent name]"
            return await self._clone_agent(cmd.agents[0], wait=wait)
        elif cmd.type == "team":
            return await self._spawn_team(cmd.lead, cmd.workers, cmd.agents, wait=wait)
        elif cmd.type == "broadcast":
            return await self._broadcast(cmd.pattern, cmd.content)
        else:
            return f"[error: unknown command type '{cmd.type}']"

    async def _spawn_agents(self, agents: list, wait: bool = False) -> str:
        """Spawn multiple agents in parallel.

        Args:
            agents: List of AgentTask instances.
            wait: If True, wait for initialization to complete.

        Returns:
            Result string.
        """
        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        # Spawn all agents in parallel for better performance
        tasks = [
            self.orchestrator.spawn(
                name=agent.name,
                task=agent.task,
                files=agent.files,
                wait=wait,
                agent_type=agent.agent_type,
                skills=agent.skills,
            )
            for agent in agents
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        spawned = []
        for agent, result in zip(agents, results):
            # Debug logging
            logger.debug(
                f"Agent spawn result for {agent.name}: {result} (type: {type(result).__name__})"
            )
            # Check for success (True) and not an exception
            if result is True:
                spawned.append(agent.name)
                if self.activity_monitor:
                    self.activity_monitor.track(agent.name)

                # Emit plugin-specific event (string literal, not in core EventType)
                if self.event_bus:
                    await self.event_bus.emit_with_hooks(
                        "agent_spawned",
                        {"name": agent.name, "task": agent.task},
                        "agent_orchestrator",
                    )
            elif isinstance(result, Exception):
                logger.error(f"Failed to spawn agent {agent.name}: {result}")

        if spawned:
            return f"[spawned: {', '.join(spawned)}]"
        return "[error: no agents spawned]"

    async def _message_agent(self, target: str, content: str) -> str:
        """Send message to agent.

        Args:
            target: Agent name.
            content: Message content.

        Returns:
            Result string.
        """
        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        success = await self.orchestrator.message(target, content)

        # Reset activity state so monitor waits for new activity
        if success and self.activity_monitor:
            self.activity_monitor.reset_agent_state(target)

        if success:
            return f"[message sent: {target}]"
        return f"[error: agent {target} not found]"

    async def _stop_agents(self, targets: list) -> str:
        """Stop agents and capture final output.

        Args:
            targets: List of agent names to stop.

        Returns:
            Result string.
        """
        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        results = []

        for target in targets:
            if self.activity_monitor:
                self.activity_monitor.untrack(target)

            output, duration = await self.orchestrator.stop(target)

            # Emit plugin-specific event (string literal, not in core EventType)
            if self.event_bus:
                await self.event_bus.emit_with_hooks(
                    "agent_stopped",
                    {"name": target, "duration": duration, "output": output},
                    "agent_orchestrator",
                )

            # Truncate output for display
            output_lines = output.strip().split("\n")
            if len(output_lines) > 10:
                output_preview = "\n".join(output_lines[-10:])
            else:
                output_preview = output.strip()

            results.append(f"[stopped: {target} @ {duration}]\n{output_preview}")

        return "\n".join(results)

    async def _get_status(self) -> str:
        """Get status of all agents.

        Returns:
            Status string.
        """
        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        agents = self.orchestrator.list_agents()

        if not agents:
            return "[agents]\n  (none active)"

        lines = ["[agents]"]
        for agent in agents:
            lines.append(f"  {agent.name:<20} {agent.status:<10} {agent.duration}")

        return "\n".join(lines)

    async def _capture_output(self, target: str, lines: int) -> str:
        """Capture output from agent.

        Args:
            target: Agent name.
            lines: Number of lines to capture.

        Returns:
            Captured output string.
        """
        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        output = self.orchestrator.capture_output(target, lines)
        agent = self.orchestrator.get_agent(target)
        duration = agent.duration if agent else "?"

        # Extract last 10 lines for preview
        output_lines = output.strip().split("\n")
        preview_lines = output_lines[-10:] if len(output_lines) > 10 else output_lines
        preview = "\n".join(preview_lines)

        return f"[capture: {target} @ {duration}, {lines} lines]\n{preview}"

    async def _clone_agent(self, agent: AgentTask, wait: bool = False) -> str:
        """Clone agent with conversation context.

        Args:
            agent: AgentTask to clone.
            wait: If True, wait for initialization to complete.

        Returns:
            Result string.
        """
        if not agent:
            return "[error: no agent specified for clone]"

        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        # Export conversation
        conv_file = await self._export_conversation()
        if not conv_file:
            return "[error: could not export conversation for clone]"

        success = await self.orchestrator.spawn_clone(
            name=agent.name,
            task=agent.task,
            files=agent.files,
            conversation_file=conv_file,
            wait=wait,
        )

        if success:
            if self.activity_monitor:
                self.activity_monitor.track(agent.name)
            return f"[cloned: {agent.name} with conversation context]"
        return f"[error: failed to clone {agent.name}]"

    async def _spawn_team(
        self, lead: str, workers: int, agents: list, wait: bool = False
    ) -> str:
        """Spawn team lead agent.

        Args:
            lead: Lead agent name.
            workers: Max number of workers.
            agents: List of agent tasks (should have one for the lead).
            wait: If True, wait for initialization to complete.

        Returns:
            Result string.
        """
        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        task = agents[0] if agents else AgentTask(name=lead, task="", files=[])

        success = await self.orchestrator.spawn_team_lead(
            lead_name=lead, max_workers=workers, task=task, wait=wait
        )

        if success:
            if self.activity_monitor:
                self.activity_monitor.track(lead)
            return f"[team spawned: {lead} (max {workers} workers)]"
        return f"[error: failed to spawn team {lead}]"

    async def _broadcast(self, pattern: str, content: str) -> str:
        """Broadcast message to agents matching pattern.

        Args:
            pattern: Glob pattern for matching agents.
            content: Message content.

        Returns:
            Result string.
        """
        if not self.orchestrator:
            return "[error: orchestrator not initialized]"

        targets = self.orchestrator.find_agents(pattern)
        count = 0

        for target in targets:
            if await self.orchestrator.message(target, content):
                count += 1
                # Reset activity state
                if self.activity_monitor:
                    self.activity_monitor.reset_agent_state(target)

        return f"[broadcast: sent to {count} agents matching '{pattern}']"

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    async def _on_agent_complete(self, name: str, duration: str, output: str) -> None:
        """Called when activity monitor detects agent completion.

        Args:
            name: Agent name.
            duration: Duration string.
            output: Captured output.
        """
        logger.info(f"Agent completed: {name} @ {duration}")

        # Emit plugin-specific event (string literal, not in core EventType)
        if self.event_bus:
            await self.event_bus.emit_with_hooks(
                "agent_completed",
                {"name": name, "duration": duration, "output": output},
                "agent_orchestrator",
            )

        # Inject completion message
        if self.message_injector:
            # Truncate output for summary
            output_lines = output.strip().split("\n")
            if len(output_lines) > 20:
                summary = "\n".join(output_lines[-20:])
            else:
                summary = output.strip()

            await self.message_injector.inject(
                source=name,
                content=f"[done: {name} @ {duration}]\n{summary}",
                trigger_llm=True,  # auto-continue conversation
            )

    async def _export_conversation(self) -> Optional[str]:
        """Export current conversation to temp file.

        Returns:
            Path to temp file, or None on error.
        """
        if not self.conversation_manager:
            return None

        try:
            messages = self.conversation_manager.get_messages()

            fd, path = tempfile.mkstemp(suffix=".json", prefix="conv-")
            with open(path, "w") as f:
                json.dump(messages, f)

            return path
        except Exception as e:
            logger.error(f"Failed to export conversation: {e}")
            return None

    async def _maybe_offer_to_spawn_agent(self, user_input: str) -> None:
        """Proactively offer to spawn a sub-agent when user mentions relevant keywords.

        This provides the clean natural UX the user was testing for.
        Smart + non-annoying: only triggers once per conversation by default.
        """
        if not self.message_injector or not user_input:
            return

        import time

        current_time = time.time()

        # Cooldown to prevent spam
        if current_time - self._last_offer_time < 30.0:
            return

        # Normalize input
        normalized = user_input.lower()

        # Check if any trigger keyword is present
        triggered = any(kw.lower() in normalized for kw in TRIGGER_KEYWORDS)

        if not triggered:
            return

        # Don't spam the same conversation
        if self._offer_shown_in_conversation:
            return

        offer_text = """I saw you say "subagent".

You actually want me to spawn one or are you just testing me?

Name + task now. Or /sub create <name> <task>."""

        success = await self.message_injector.inject(
            source="agent_orchestrator",
            content=offer_text,
            trigger_llm=False,  # don't force LLM response immediately
            metadata={"type": "agent_offer", "proactive": True},
        )

        if success:
            self._offer_shown_in_conversation = True
            self._last_offer_time = current_time
            logger.info("Proactively offered to spawn sub-agent to user")
