"""Main application orchestrator for Kollab."""

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from kollabor_agent import AgentManager
from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.runtime import get_agent_tool_scope
from kollabor_ai import KollaborConversationLogger, ProfileManager
from kollabor_config import ConfigService
from kollabor_events import EventBus
from kollabor_events.dict_utils import deep_merge
from kollabor_events.models import CommandResult, EventType
from kollabor_events.ready_message import ReadyMessageCollector
from kollabor_plugins import KollaborPluginSDK, PluginRegistry
from kollabor_tui import EventDrivenRenderLoop
from kollabor_tui.input_handler import InputHandler
from kollabor_tui.status import StatusNavigationManager
from kollabor_tui.terminal_renderer import TerminalRenderer
from kollabor_tui.visual_effects import VisualEffects

from .llm import LLMService
from .llm.permissions.attach_bridge import AttachPermissionBridge
from .logging import setup_from_config
from .state.widget_state import WidgetState
from .updates import VersionCheckService
from .version import __version__

logger = logging.getLogger(__name__)


def merge_widget_state_snapshot(
    current: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    """Merge a DisplayTap state_snapshot into existing widget state."""
    current = current or {}
    current_source = str(current.get("_source") or "")
    base = WidgetState.from_flat_dict(current, source="existing")
    update = WidgetState.from_flat_dict(event or {}, source="display_tap")
    merged = base.update_from(update)
    preserved = {
        key: value
        for key, value in current.items()
        if key not in WidgetState.state_fields()
        and key not in {"type", "_source", "_updated_at", "_stale", "_degraded"}
    }
    result = {**preserved, **merged.to_dict()}
    if current_source in {"state_service", "state_refresher"}:
        for key in ("_source", "_updated_at", "_stale", "_degraded"):
            if key in current:
                result[key] = current[key]
    return result


class TerminalLLMChat:
    """Main Kollab application.

    Orchestrates all components including rendering, input handling,
    event processing, and plugin management.
    """

    def __init__(
        self,
        args=None,
        system_prompt_file: str | None = None,
        agent_name: str | None = None,
        profile_name: str | None = None,
        save_profile: bool = False,
        save_local: bool = False,
        make_default_profile: bool = False,
        skill_names: list[str] | None = None,
        plugin_registry=None,
        attach_to: str | None = None,
        context_name: str | None = None,
    ) -> None:
        """Initialize the chat application.

        Args:
            args: Parsed CLI arguments namespace (includes plugin args).
            system_prompt_file: Optional path to a custom system prompt file
                               (overrides all other system prompt sources)
            agent_name: Optional agent name to use (e.g., "lint-editor")
            profile_name: Optional LLM profile name to use (e.g., "claude")
            save_profile: If True, save auto-created profile to config
            save_local: If True with save_profile, save to local project config
            make_default_profile: If True with --profile, set it as startup default
            skill_names: Optional list of skill names to load for the agent
            plugin_registry: Pre-initialized plugin registry (for startup optimization)
        """
        # Store CLI args for plugins to access
        self.args = args

        # Get configuration directory using standard resolution
        from kollabor_config.config_utils import (
            ensure_config_directory,
            get_conversations_dir,
            get_existing_global_config_path,
            get_project_data_dir,
            initialize_config,
            initialize_system_prompt,
            initialize_user_directories,
            resolve_global_path,
            set_cli_system_prompt_file,
        )

        # Set CLI system prompt override if provided
        if system_prompt_file:
            set_cli_system_prompt_file(system_prompt_file)

        # Check if this is first install BEFORE creating directories
        global_config_path = get_existing_global_config_path()
        self._is_first_install = not global_config_path.exists()

        self.config_dir = ensure_config_directory()
        logger.info(f"Using config directory: {self.config_dir}")

        # Set ROOT socket name for nested agents to inherit
        # All agents (user and spawned) register on this socket for visibility in /t view
        os.environ["KOLLAB_ROOT_SOCKET"] = Path.cwd().name
        logger.debug(f"Set KOLLAB_ROOT_SOCKET={os.environ['KOLLAB_ROOT_SOCKET']}")

        # Initialize config.json (creates global with profiles, copies to local)
        initialize_config()

        # Initialize system prompt (copies default.md to config directories)
        initialize_system_prompt()

        # Initialize user-customizable directories (themes, status-widgets, mcp, plugins)
        initialize_user_directories()

        # Flag to indicate if we're in pipe mode (for plugins to check)
        self.pipe_mode = False

        # Apply hub project-scope env var from config BEFORE anything reads
        # presence/sockets. Plugin init normally does this, but attach mode
        # checks presence at __init__ time — before plugins boot.
        self._apply_hub_project_scope_from_config()

        # Attach mode: proxy to a remote agent instead of local LLM
        self._attach_to = attach_to
        self._attach_socket: str | None = None
        self._attach_permission_bridge = AttachPermissionBridge()
        if attach_to:
            self._attach_socket = self._resolve_attach_socket(attach_to)
            if not self._attach_socket:
                # List available agents
                available = []
                from plugins.hub.presence import get_presence_dir

                presence_dir = get_presence_dir()
                if presence_dir.exists():
                    import json as _j

                    for f in presence_dir.glob("*.json"):
                        try:
                            d = _j.loads(f.read_text())
                            pid = d.get("pid", 0)
                            try:
                                os.kill(pid, 0)
                                available.append(d.get("identity", "?"))
                            except (OSError, ProcessLookupError):
                                pass
                        except Exception:
                            pass
                msg = f"agent '{attach_to}' not found"
                if available:
                    msg += f"\nonline: {', '.join(sorted(available))}"
                else:
                    msg += "\nno agents online. start one with: kollab --detached"
                raise SystemExit(msg)

        # Flag to indicate if we're in simple mode (no fancy UI)
        self.simple_mode = getattr(args, "simple", False) if args else False

        # Initialize plugin registry (use pre-initialized if provided for startup optimization)
        if plugin_registry is not None:
            self.plugin_registry = plugin_registry
            logger.debug("Using pre-initialized plugin registry from CLI")
        else:
            # Try package installation directory first (for pip install), then cwd (for development)
            package_dir = Path(
                __file__
            ).parent.parent  # Go up from core/ to package root
            plugins_dir = package_dir / "plugins"
            if not plugins_dir.exists():
                plugins_dir = Path.cwd() / "plugins"  # Fallback for development mode
                logger.info(f"Using development plugins directory: {plugins_dir}")
            else:
                logger.info(f"Using installed package plugins directory: {plugins_dir}")

            self.plugin_registry = PluginRegistry(plugins_dir)
            self.plugin_registry.load_all_plugins()

        # Initialize configuration service with plugin registry
        # Always use fast_mode to skip expensive system prompt rendering in __init__
        # (LLM service builds its own system prompt independently in _build_system_prompt)
        self.config = ConfigService(
            self.config_dir / "config.json", self.plugin_registry, fast_mode=True
        )

        # Note: plugin configs are already merged in _initialize_config() via
        # load_complete_config(). No need to call update_from_plugins() again -
        # it would re-read all configs, re-discover plugins, and write to disk
        # which triggers the file watcher causing redundant reloads.

        # Phase 4.5: in attach mode, DAEMON_OWNED launch flags (profile,
        # agent, skill, system_prompt, save, local) are NOT applied to the
        # client's shadow state. They're stashed here and drained as RPC
        # calls after RemoteStateService is wired up in start(). This
        # prevents the bug where --profile X would update the client's
        # profile_manager while the daemon never hears about it.
        #
        # Note: system_prompt_file was ALREADY installed above via
        # set_cli_system_prompt_file(). In attach mode the daemon is the
        # authoritative owner of the prompt -- we still want the client
        # to have the reference for display purposes (/config shows it)
        # but we also enqueue an RPC set_system_prompt call to push the
        # content to the daemon. Reading the file here is fine because
        # the client's cwd is what the user typed the flag from.
        self._attach_pending_flags: dict[str, Any] = {}
        if attach_to:
            self._attach_pending_flags = {
                "profile": profile_name,
                "save_profile": save_profile,
                "save_local": save_local,
                "make_default_profile": make_default_profile,
                "agent": agent_name,
                "skills": list(skill_names or []),
                "system_prompt_file": system_prompt_file,
                "context": context_name,
            }
            # Suppress local application of these flags -- the local
            # profile_manager/agent_manager/skill loader will initialize
            # to defaults. The daemon still holds the real state.
            profile_name = None
            agent_name = None
            skill_names = None
            save_profile = False
            save_local = False
            make_default_profile = False
            logger.info(
                "attach mode: stashed launch flags for post-connect RPC: %s",
                {k: v for k, v in self._attach_pending_flags.items() if v},
            )

        # Initialize profile manager (for LLM endpoint profiles)
        # Pass cli_profile so auto-detection is skipped when --profile is used
        self.profile_manager = ProfileManager(self.config, cli_profile=profile_name)

        # Log auto-detection result
        if self.profile_manager.is_auto_detected:
            logger.info(
                f"Provider auto-detected from {self.profile_manager.auto_detected_source}: "
                f"using profile '{self.profile_manager.active_profile_name}'"
            )

        if profile_name:
            # CLI --profile is a one-time override, don't persist active selection
            if not self.profile_manager.set_active_profile(profile_name, persist=False):
                logger.warning(f"Profile '{profile_name}' not found, using default")
            elif save_profile or make_default_profile:
                # Save profile values to config if --save/--default was used
                profile = self.profile_manager.get_profile(profile_name)
                if profile:
                    self.profile_manager.save_profile_values_to_config(profile)
                    logger.info(f"Saved profile '{profile_name}' to config")

                if make_default_profile:
                    from kollabor_config.config_utils import set_default_profile

                    level = "project" if save_local else "global"
                    if set_default_profile(profile_name, level):
                        logger.info(
                            f"Set default profile '{profile_name}' at {level} level"
                        )
                    else:
                        logger.warning(
                            f"Failed to set default profile '{profile_name}' at {level} level"
                        )

        # Initialize agent manager (for agent/skill system)
        self.agent_manager = AgentManager(self.config)
        # Load default agent using priority system (CLI > project > global > fallback)
        if not self.agent_manager.load_default_agent(agent_name):
            logger.warning(
                "Failed to load any agent, system may not function correctly"
            )

        # If agent has a preferred profile, use it (unless profile was explicitly set)
        # Don't persist agent's profile - it's automatic based on agent selection
        if not profile_name:
            agent_profile = self.agent_manager.get_preferred_profile()
            if agent_profile:
                if self.profile_manager.set_active_profile(
                    agent_profile, persist=False
                ):
                    logger.info(f"Using agent's preferred profile: {agent_profile}")

        # Load skills if specified (requires an active agent)
        # Store for later injection into conversation after llm_service is initialized
        self._pending_skill_names = skill_names or []
        if skill_names:
            if self.agent_manager.get_active_agent():
                for skill_name in skill_names:
                    if self.agent_manager.load_skill(skill_name):
                        logger.info(f"Loaded skill: {skill_name}")
                    else:
                        logger.warning(f"Skill '{skill_name}' not found")
            else:
                logger.warning("Cannot load skills without an active agent")

        # Reconfigure logging now that config system is available
        # Skip for help mode to avoid creating log files
        if not getattr(self.args, "_help_pending", False):
            setup_from_config(self.config.config_manager.config)

        # Initialize version check service
        self.version_check_service = VersionCheckService(
            config=self.config, current_version=__version__
        )

        # Initialize core components
        self.event_bus = EventBus(config=self.config.config_manager.config)

        # Register services created before event_bus for lookup via service registry
        self.event_bus.register_service("profile_manager", self.profile_manager)
        self.event_bus.register_service("agent_manager", self.agent_manager)

        # Wire event_bus into agent_manager for hub trender tag rendering
        self.agent_manager.event_bus = self.event_bus

        # Initialize widget-based status system
        from kollabor_tui.status import (
            ScriptWidgetManager,
            StatusLayoutManager,
            StatusLayoutRenderer,
            StatusWidgetRegistry,
            register_core_widgets,
            register_script_widgets,
        )

        self.widget_registry = StatusWidgetRegistry()
        register_core_widgets(self.widget_registry)

        # Register script-based widgets from ~/.kollab/status-widgets/
        self.script_widget_manager = ScriptWidgetManager()
        script_count = register_script_widgets(
            self.widget_registry, script_manager=self.script_widget_manager
        )
        if script_count > 0:
            logger.info(f"Registered {script_count} script widget(s)")

        self.layout_manager = StatusLayoutManager(self.config)
        self.layout_renderer = StatusLayoutRenderer(
            widget_registry=self.widget_registry,
            layout_manager=self.layout_manager,
        )
        # Propagate simple_mode to layout_renderer if enabled
        if self.simple_mode:
            self.layout_renderer.simple_mode = True
        logger.info("Widget-based status system initialized")

        # Initialize renderer with config (registered as service for command handlers)
        self.renderer = TerminalRenderer(self.event_bus, self.config)  # type: ignore[arg-type]
        self.event_bus.register_service("renderer", self.renderer)

        # Propagate simple_mode to renderer and message_renderer if enabled
        if self.simple_mode:
            self.renderer.simple_mode = True
            if hasattr(self.renderer, "message_renderer"):
                self.renderer.message_renderer.simple_mode = True

        # Select message renderer based on config (or simple_mode flag)
        from kollabor_tui.renderer_protocol import get_renderer as get_message_renderer

        if self.simple_mode:
            renderer_name = "simple"
        else:
            renderer_name = self.config.get("kollabor.ui.renderer", "clean")
        self.renderer.message_coordinator.renderer = get_message_renderer(renderer_name)
        logger.info(f"Message renderer: {renderer_name}")

        # Connect widget renderer to terminal renderer
        # Wire Application's layout_renderer (StatusLayoutRenderer) into TerminalRenderer
        # so the terminal can render status widgets. This is NOT self-referencing -
        # self.layout_renderer (Application) -> self.renderer.layout_renderer (TerminalRenderer)
        self.renderer.layout_renderer = self.layout_renderer  # type: ignore[assignment]

        # Initialize shell command service (for ! prefix commands)
        from kollabor_agent.shell_command_service import ShellCommandService

        self.shell_command_service = ShellCommandService(
            event_bus=self.event_bus, config=self.config, renderer=self.renderer
        )
        logger.info("Shell command service initialized")

        self.input_handler = InputHandler(
            self.event_bus,
            self.renderer,
            self.config,
            shell_command_service=self.shell_command_service,
        )

        # Give terminal renderer access to input handler for modal state checking
        self.renderer.input_handler = self.input_handler

        # Initialize status navigation manager for Tab navigation in status area
        self.navigation_manager = StatusNavigationManager(
            renderer=self.renderer,
            coordinator=self.renderer.message_coordinator,
            event_bus=self.event_bus,
            config=self.config,
        )
        self.navigation_manager.set_layout(self.layout_manager.get_layout())
        self.navigation_manager.set_widget_registry(self.widget_registry)
        self.navigation_manager.set_layout_manager(
            self.layout_manager
        )  # For layout editing
        self.navigation_manager.app = self  # Wire app for command execution
        self.layout_renderer.set_navigation_manager(self.navigation_manager)
        self.input_handler.navigation_manager = self.navigation_manager
        logger.info("Status navigation manager initialized")

        # Initialize visual effects system
        self.visual_effects = VisualEffects()

        # Initialize slash command system
        logger.info("About to initialize slash command system")
        self._initialize_slash_commands()

        # Initialize fullscreen plugin commands
        self._initialize_fullscreen_commands()

        # Initialize altview plugin commands (coexists with fullscreen during migration)
        self._initialize_altview_commands()
        logger.info("Slash command system initialization completed")

        # Initialize LLM core service components
        self.project_data_dir = get_project_data_dir()
        conversations_dir = get_conversations_dir()
        conversations_dir.mkdir(parents=True, exist_ok=True)
        self.conversation_logger = KollaborConversationLogger(conversations_dir)
        self.mcp_integration = MCPIntegration(event_bus=self.event_bus)
        self.plugin_sdk = KollaborPluginSDK()

        # Initialize permission system
        from kollabor_agent.permissions import (
            PERMISSION_CONFIG_DEFAULTS,
            PermissionManager,
            RiskAssessmentRules,
            RiskAssessor,
        )

        from .llm.permissions import PermissionHook

        # Merge permission config defaults
        self.config.config_manager.config = deep_merge(
            PERMISSION_CONFIG_DEFAULTS, self.config.config_manager.config
        )

        # Log permission config for debugging
        perm_config = self.config.config_manager.config.get("kollabor", {}).get(
            "permissions", {}
        )
        enabled = perm_config.get("enabled")
        mode = perm_config.get("approval_mode")
        logger.info(f"Permission config loaded: enabled={enabled}, mode={mode}")

        # Initialize risk assessor
        risk_rules = RiskAssessmentRules()
        self.risk_assessor = RiskAssessor(risk_rules, self.config.config_manager.config)

        # Initialize permission manager
        self.permission_manager = PermissionManager(
            config=self.config.config_manager.config,
            risk_assessor=self.risk_assessor,
            event_bus=self.event_bus,
            config_service=self.config,  # Pass ConfigService for persistence
        )

        logger.info(
            f"Permission manager initialized with mode: "
            f"{self.permission_manager.approval_mode}"
        )

        # Register permission_manager for service lookup
        self.event_bus.register_service("permission_manager", self.permission_manager)

        # Wire permission manager to layout manager for UI
        # Use self.renderer.layout_manager (LayoutManager from core/io/layout.py)
        # which has the show_permission_prompt method
        if hasattr(self.renderer, "layout_manager") and hasattr(
            self.renderer.layout_manager, "show_permission_prompt"
        ):
            # Import response handler
            from kollabor_agent.permissions import handle_confirmation_response

            # Create wrapper that converts ConfirmationResponse to PermissionDecision
            async def confirmation_callback_wrapper(details):
                # Show prompt and get user response. In daemon/attach mode,
                # the visible TUI lives in a separate client process, so route
                # the prompt across the attach socket instead of waiting on the
                # daemon's hidden stdin.
                response = await self._try_attach_permission_prompt(details)
                if response is None:
                    response = (
                        await self.renderer.layout_manager.show_permission_prompt(
                            details
                        )
                    )
                # Convert to PermissionDecision using response handler
                return await handle_confirmation_response(
                    self.permission_manager, details, response
                )

            self.permission_manager.set_confirmation_callback(
                confirmation_callback_wrapper
            )
            logger.info("Permission confirmation callback wired to layout manager")
        else:
            logger.warning(
                "Layout manager not available - permission prompts will be denied"
            )

        # Create permission hook (will be registered during async initialization)
        self.permission_hook = PermissionHook(self.permission_manager)

        logger.info(
            "Permission system initialized (hook will be registered during startup)"
        )

        # Initialize LLM service
        self.llm_service = LLMService(
            config=self.config,
            event_bus=self.event_bus,
            renderer=self.renderer,
            profile_manager=self.profile_manager,
            agent_manager=self.agent_manager,
        )

        # Register llm_service for service lookup
        self.event_bus.register_service("llm_service", self.llm_service)

        # === StateService wiring (phase 2 of daemon transparency refactor) ===
        # LocalStateService wraps in-process services. In attach mode, the
        # attach-client branch (see _initialize_attach_proxy) registers a
        # RemoteStateService AFTER rpc_client is created. We SKIP local
        # registration here in attach mode (phase 4.5 step 9 thin-client
        # optimization) so state_service is never pointed at the client's
        # empty shadow managers -- even briefly.
        #
        # In daemon mode (kollab --detached), the daemon runs the local
        # implementation and the hub plugin registers state RPC handlers
        # against it from plugins/hub/plugin.py once its RpcServer exists.
        #
        # At this point in startup the hub plugin has NOT been instantiated
        # yet (plugins are loaded in _deferred_startup), so the "register
        # handlers against hub plugin's rpc server" path here will always
        # miss. That's expected - hub plugin's own startup path looks up
        # state_service on the event bus and registers handlers from there.
        self._local_state_service = None
        if not self._attach_to:
            try:
                from kollabor.state import LocalStateService

                self._local_state_service = LocalStateService(
                    llm_service=self.llm_service,
                    profile_manager=self.profile_manager,
                    permission_manager=self.permission_manager,
                    agent_manager=self.agent_manager,
                    event_bus=self.event_bus,
                )
                self.event_bus.register_service(
                    "state_service", self._local_state_service
                )
                logger.info(
                    "LocalStateService initialized and registered as event bus service"
                )
            except Exception as e:
                logger.error(
                    f"failed to initialize LocalStateService: {e}", exc_info=True
                )
                self._local_state_service = None
        else:
            logger.info(
                "attach mode: skipping LocalStateService registration "
                "(RemoteStateService will be registered after RPC handshake)"
            )

        # Wire tool executor and permission sync (requires llm_service)
        self.permission_manager.set_tool_executor(self.llm_service.tool_executor)
        self.permission_manager._sync_file_access_mode()

        # Wire bundle scope sync callback into agent_manager
        # so scope is set on startup, resume, and agent changes
        def _sync_bundle_scope_to_executor(agent):
            te = self.llm_service.tool_executor
            tools = get_agent_tool_scope(agent)
            if tools:
                te.set_bundle_scope(tools)
            else:
                te.clear_bundle_scope()

        self.agent_manager._on_agent_changed = _sync_bundle_scope_to_executor
        # Sync the already-loaded default agent (loaded before llm_service existed)
        active = self.agent_manager.get_active_agent()
        if active:
            _sync_bundle_scope_to_executor(active)

        # Check for MCP configuration and provide user feedback
        mcp_config = resolve_global_path("mcp", "mcp_settings.json")
        if not mcp_config.exists():
            logger.info("No MCP configuration found")
            # Display one-time setup message (not added to history to avoid clutter)
            try:
                setup_message = (
                    "[MCP Setup]\n"
                    "No MCP servers configured. External tools disabled.\n"
                    "Run '/mcp' to configure Model Context Protocol servers."
                )
                # Use display_thinking to show in thinking area without adding to history
                if hasattr(self.renderer, "display_thinking"):
                    self.renderer.display_thinking(setup_message, add_to_history=False)
                else:
                    logger.info(setup_message)
            except Exception as e:
                logger.warning(f"Failed to display MCP setup message: {e}")
        else:
            logger.info(f"MCP configuration found at {mcp_config}")

        # Set up widget context now that all services are available
        from kollabor_tui.status import WidgetContext

        widget_context = WidgetContext(
            llm_service=self.llm_service,
            profile_manager=self.profile_manager,
            agent_manager=self.agent_manager,
            config=self.config,
            layout_manager=self.layout_manager,
            event_bus=self.event_bus,
        )
        if self._attach_to:
            widget_context.runtime_mode = "attach"
            widget_context.is_attach_mode = True
        elif getattr(self.args, "detached", False):
            widget_context.runtime_mode = "daemon"
        else:
            widget_context.runtime_mode = "local"
        self.layout_renderer.set_context(widget_context)
        # Store reference for plugin widget access
        self._widget_context = widget_context

        # Create widget API for plugins
        from kollabor_tui.status import StatusWidgetAPI

        self._widget_api = StatusWidgetAPI(self.widget_registry)
        self.event_bus.register_service("widget_api", self._widget_api)
        self.event_bus.register_service("layout_manager", self.layout_manager)

        # Register hot reload callbacks for config changes
        self.config.register_reload_callback(self._on_config_reload)

        # Inject active skills as user messages (after LLM service is ready).
        # Phase 4.5 step 9: skip in attach mode -- the llm_service here is a
        # client-side shadow; skill injection must go through state_service
        # to reach the daemon's real conversation_history. The --skill launch
        # flag is handled by _drain_attach_pending_flags_on_ready after RPC.
        if not self._attach_to:
            active_agent = self.agent_manager.get_active_agent()
            if active_agent and active_agent.active_skills:
                for skill_name in active_agent.active_skills:
                    skill = active_agent.get_skill(skill_name)
                    if skill:
                        skill_message = f"## Skill: {skill_name}\n\n{skill.content}"
                        self.llm_service._add_conversation_message(
                            "user", skill_message
                        )
                        logger.debug(f"Injected skill as user message: {skill_name}")

        # Configure renderer and UI settings (shared with hot reload)
        self._apply_renderer_config()
        self._apply_ui_theme()

        # Dynamically instantiate all discovered plugins
        self.plugin_instances = self.plugin_registry.instantiate_plugins(
            self.event_bus, self.renderer, self.config
        )

        # Task tracking for race condition prevention
        self.running = False
        self._startup_complete = False
        self._startup_ready = asyncio.Event()
        self._background_tasks: list[asyncio.Task] = []
        self._pending_skill_display: list[tuple[str, str, dict[str, Any]]] = []
        self._task_lock = asyncio.Lock()

        logger.info("Kollab initialized")

    @staticmethod
    def _apply_hub_project_scope_from_config() -> None:
        """Translate plugins.hub.project_scoped config into env var.

        Runs before any hub path helper is called so get_hub_dir() routes
        correctly when the flag is on. No-op if env var already set.
        """
        if os.environ.get("KOLLAB_HUB_PROJECT_SCOPED"):
            return
        try:
            import json as _json

            from kollabor_config.config_utils import get_existing_global_config_path

            cfg_path = get_existing_global_config_path()
            if not cfg_path.exists():
                os.environ["KOLLAB_HUB_PROJECT_SCOPED"] = "1"
                return
            cfg = _json.loads(cfg_path.read_text())
            scoped = cfg.get("plugins", {}).get("hub", {}).get("project_scoped", True)
            os.environ["KOLLAB_HUB_PROJECT_SCOPED"] = "1" if scoped else "0"
        except Exception:
            pass

    def _resolve_attach_socket(self, identity: str) -> str | None:
        """Resolve a hub identity to its live unix socket path.

        Presence records are treated as hints only. We verify the recorded pid
        is still alive before trusting the socket path so stale presence files
        do not attach clients to dead agents.
        """
        import json as _json

        from plugins.hub.presence import get_presence_dir

        presence_dir = get_presence_dir()
        if not presence_dir.exists():
            return None
        for f in presence_dir.glob("*.json"):
            try:
                data = _json.loads(f.read_text())
                if data.get("identity") == identity:
                    pid = data.get("pid", 0)
                    try:
                        os.kill(pid, 0)
                    except (OSError, ProcessLookupError):
                        continue
                    return str(data.get("socket_path", ""))
            except Exception:
                continue
        return None

    async def start(self, initial_message: str | None = None) -> None:
        """Start the chat application with guaranteed cleanup.

        Args:
            initial_message: Optional message to send after startup completes.
                           If provided, sends this message as the first user input
                           but stays in interactive mode for continued conversation.
        """
        logger.info("Application starting")

        render_task = None
        input_task = None
        self._needs_terminal_cleanup = (
            False  # Track if we need terminal restore on exit
        )

        # Handle early exits before try/finally (no cleanup needed)
        # Fast path for -h/--help: show help without banner
        if getattr(self.args, "_help_pending", False):
            logger.info("Help mode - skipping banner and LLM initialization")
            await self._initialize_plugins_for_commands()
            from .cli import print_full_help

            start_time = getattr(self.args, "_start_time", None)
            print_full_help(
                self.args._parser, self.input_handler.command_registry, start_time
            )
            logging.shutdown()  # Close log file handlers
            return

        # Fast path for unknown args: validate before banner/LLM initialization
        unknown_args = getattr(self.args, "_unknown_args", [])
        if unknown_args:
            logger.info("Validating unknown args before banner")
            await self._initialize_plugins_for_commands()
            from .cli import process_cli_command

            try:
                cli_command = process_cli_command(
                    self.args, self.input_handler.command_registry
                )
                # Valid command - store and continue to full initialization
                self.args.cli_command = cli_command
                self.args._unknown_args = []  # Clear to prevent re-processing
            except ValueError as e:
                # Invalid command - print error and return (caller handles exit code)
                self._print_error(str(e))
                logger.error(f"Invalid CLI command: {e}")
                return

        try:
            # Display banner for interactive/command mode
            await self._display_banner()
            self._needs_terminal_cleanup = True  # Now we need cleanup on exit

            # Register startup_ready event for service lookup (gates user input)
            self.event_bus.register_service("startup_ready", self._startup_ready)

            # Create event-driven render loop EARLY (before heavy init)
            self.render_loop = EventDrivenRenderLoop(
                render_callback=self._render_callback,
                input_callback=self._input_callback,
                target_fps=20.0,  # Periodic renders for animations
                input_poll_rate=100.0,  # 100Hz input polling
                name="MainRenderLoop",
            )
            logger.info("Event-driven render loop created")

            # Wire render loop to renderer for script widget refresh triggers
            self.renderer.render_loop = self.render_loop
            if hasattr(self.renderer, "layout_manager") and hasattr(
                self.renderer.layout_manager, "set_render_loop"
            ):
                self.renderer.layout_manager.set_render_loop(self.render_loop)

            # Register render loop as event bus service so AltViewStackManager
            # can resolve it lazily for hibernate/thaw
            self.event_bus.register_service("main_render_loop", self.render_loop)

            # Wire render loop to message coordinator for immediate input box render
            if hasattr(self.renderer, "message_coordinator"):
                self.renderer.message_coordinator.set_render_loop(self.render_loop)
                logger.info("Render loop wired to message coordinator")

            # Start main loops (input bar appears NOW - fast startup)
            self.running = True

            # CRITICAL: Set input_handler.running=True BEFORE starting render loop
            # to prevent race condition where render loop exits immediately
            self.input_handler.running = True

            render_task = self.create_background_task(
                self.render_loop.run(), "event_driven_render_loop"
            )
            input_task = self.create_background_task(
                self.input_handler.start(), "input_handler"
            )

            # Register LLM hooks early so USER_INPUT events are captured
            # (the handler gates on _startup_ready before doing real work)
            # Skip in attach mode - input goes to remote agent, not local LLM
            if not self._attach_to:
                await self.llm_service.register_hooks()

            # Launch deferred startup as background task
            self.create_background_task(
                self._deferred_startup(initial_message=initial_message),
                "deferred_startup",
            )

            # Wait for completion. CLI commands can intentionally clean up and
            # exit during deferred startup, which cancels the foreground loops.
            try:
                await asyncio.gather(render_task, input_task)
            except asyncio.CancelledError:
                if self.running:
                    raise

        except KeyboardInterrupt:
            print("\r\n")
            # print("\r\nInterrupted by user")
            logger.info("Application interrupted by user")
        except Exception as e:
            logger.error(f"Application error during startup: {e}")
            raise
        finally:
            # Guaranteed cleanup - always runs regardless of how we exit
            logger.info("Executing guaranteed cleanup")
            await self.cleanup()

    async def _deferred_startup(self, initial_message: str | None = None) -> None:
        """Run heavy initialization in background after render loop starts.

        This allows the input bar to appear immediately while LLM, plugins,
        and version check initialize in the background. User input is gated
        by _startup_ready event so messages typed during init are held, not lost.
        """
        try:
            # Fire-and-forget: version check (network, 5s timeout)
            self.create_background_task(self._check_for_updates(), "version_check")

            # Parent watchdog -- self-terminate if parent dies
            parent_pid_str = os.environ.get("KOLLAB_PARENT_PID", "")
            if parent_pid_str:
                parent_pid = int(parent_pid_str)

                async def _parent_watchdog():
                    while self.running:
                        await asyncio.sleep(10)
                        try:
                            os.kill(parent_pid, 0)
                        except (OSError, ProcessLookupError):
                            logger.warning(
                                f"Parent process {parent_pid} died, shutting down"
                            )
                            self.running = False
                            break

                self.create_background_task(_parent_watchdog(), "parent_watchdog")
                logger.info(f"Parent watchdog started (pid {parent_pid})")

            # Load config-based hooks EARLY (before LLM/MCP init so connect events fire)
            await self._load_config_hooks()

            # ATTACH MODE: skip LLM init, connect to remote agent instead
            if self._attach_to and self._attach_socket:
                await self._initialize_attach_proxy()
                # Register cancel hook so ESC forwards to daemon via RPC.
                # Full register_hooks() is skipped in attach mode to avoid
                # competing with the daemon for USER_INPUT events.
                await self.llm_service.register_cancel_hook()
            else:
                # Initialize LLM core service (system prompt runs in thread)
                await self._initialize_llm_core()
                await asyncio.sleep(0)  # Yield to let render loop process frames

                # Check if provider has configuration error and display warning
                await self._display_provider_warning()

            # Initialize all plugins dynamically (always - attach mode needs /hub etc)
            await self._initialize_plugins()
            await asyncio.sleep(0)  # Yield after plugin init

            # Process CLI command if provided (after plugins so all commands are registered)
            cli_command = await self._process_cli_command()
            if cli_command:
                exit_code = await self._execute_cli_command(cli_command)

                # Wait for any modal triggered by the CLI command to finish
                # (modal commands like /resume search emit MODAL_TRIGGER and return
                # immediately - we must wait for user interaction to complete)
                if hasattr(self, "input_handler") and hasattr(
                    self.input_handler, "_modal_controller"
                ):
                    mc = self.input_handler._modal_controller
                    from kollabor_events.models import CommandMode

                    while mc.command_mode != CommandMode.NORMAL:
                        await asyncio.sleep(0.1)

                # Determine if we should stay in interactive mode
                should_stay = getattr(self.args, "stay", False)

                # Some commands default to --stay when not in pipe mode
                if not should_stay and not self.pipe_mode:
                    cmd_name = cli_command.get("name", "")
                    cmd_def = cli_command.get("command_def")
                    stay_commands = {"permissions", "resume", "branch", "fork"}
                    if cmd_name in stay_commands:
                        should_stay = True
                        logger.info(f"{cmd_name} command defaults to interactive mode")
                    elif (
                        cmd_def
                        and getattr(cmd_def, "plugin_name", "")
                        == "fullscreen_integrator"
                    ):
                        should_stay = True
                        logger.info(
                            "Fullscreen plugin command defaults to interactive mode"
                        )

                if not should_stay:
                    logger.info(f"CLI command complete, exiting with code {exit_code}")
                    await self.cleanup()
                    sys.exit(exit_code)

                logger.info("CLI command complete, continuing to interactive mode")

            # Display any CLI-loaded skills in the UI
            if hasattr(self, "_pending_skill_display") and self._pending_skill_display:
                if hasattr(self.renderer, "message_coordinator"):
                    self.renderer.message_coordinator.display_message_sequence(
                        self._pending_skill_display
                    )
                    logger.info(
                        f"Displayed {len(self._pending_skill_display)} skill messages"
                    )
                self._pending_skill_display = []

            # Display ready message with all stats collected
            await self._display_ready_message()

            # Wire script widget refresh scheduler
            await self.script_widget_manager.initialize_refresh_scheduler(
                self.event_bus
            )
            if hasattr(self.script_widget_manager, "_scheduler"):
                self.script_widget_manager._scheduler.set_render_loop(self.render_loop)  # type: ignore[union-attr]
                logger.info("Render loop wired to refresh scheduler")
                self.event_bus.register_service(
                    "refresh_scheduler", self.script_widget_manager._scheduler
                )

            # Wire display controller render loop
            if hasattr(self.input_handler, "display_controller"):
                self.input_handler.display_controller.set_render_loop(self.render_loop)
                logger.info("Render loop wired to display controller")

            # Wire altview stack manager into display controller for render gating
            if hasattr(self.input_handler, "display_controller"):
                self.input_handler.display_controller.set_event_bus(self.event_bus)
                logger.info("Event bus wired to display controller for altview lookup")

            # Emit SYSTEM_STARTUP event (hooks + plugins can observe)
            await self.event_bus.emit_with_hooks(
                EventType.SYSTEM_STARTUP,
                {"source": "application", "version": __version__},
                "application",
            )

            # === WidgetStateRefresher (phase 5 of daemon transparency refactor) ===
            # Start the background refresh loop that keeps ctx.remote_state
            # fresh from state_service. Works in both local and attach mode:
            # in local mode the refresher pulls from LocalStateService
            # (direct in-process reads), in attach mode it pulls from
            # RemoteStateService (RPC over the hub socket). Widgets stay
            # sync and just read the dict; no widget code changes required.
            #
            # Runs at 2s intervals. If state_service isn't available for
            # any reason, the refresher is skipped and widgets fall back
            # to the legacy hub plugin state_snapshot DisplayTap path
            # (which still runs as a parallel source of truth -- phase 5
            # leaves it in place rather than deleting the belt-and-suspenders
            # setup).
            self._widget_state_refresher = None
            try:
                state_service = (
                    self.event_bus.get_service("state_service")
                    if self.event_bus
                    else None
                )
                if state_service is not None and hasattr(self, "_widget_context"):
                    from kollabor.state import WidgetStateRefresher

                    self._widget_state_refresher = WidgetStateRefresher(
                        widget_context=self._widget_context,
                        state_service=state_service,
                        request_render=(
                            self.render_loop.request_render
                            if hasattr(self, "render_loop") and self.render_loop
                            else None
                        ),
                    )
                    self._widget_state_refresher.start()
                    logger.info(
                        "WidgetStateRefresher started (2s interval, %s mode)",
                        (
                            "attach"
                            if self.event_bus.get_service("rpc_client")
                            else "local"
                        ),
                    )
                else:
                    logger.debug(
                        "WidgetStateRefresher skipped: state_service=%s widget_context=%s",
                        state_service is not None,
                        hasattr(self, "_widget_context"),
                    )
            except Exception as e:
                logger.warning(f"failed to start WidgetStateRefresher: {e}")

            # Mark startup as complete
            self._startup_complete = True
            self._startup_ready.set()
            logger.info("Application startup complete (deferred)")

            # Check for first-run wizard after startup is ready
            await self._check_first_run_wizard()

            # Send initial message if provided
            if initial_message:
                logger.info(f"Sending initial message: {initial_message[:50]}...")
                await asyncio.sleep(0.1)
                await self.llm_service.process_user_input(initial_message)

        except asyncio.CancelledError:
            logger.info("Deferred startup cancelled")
            raise
        except Exception as e:
            logger.error(f"Error during deferred startup: {e}")
            import traceback

            traceback.print_exc()
        finally:
            # Always set startup_ready to prevent deadlocks on waiting input
            if not self._startup_ready.is_set():
                self._startup_ready.set()
                logger.warning(
                    "Startup ready set in finally (startup may be incomplete)"
                )

    async def start_pipe_mode(self, piped_input: str, timeout: int = 120) -> None:
        """Start in pipe mode: process input and exit after response.

        Args:
            piped_input: Input text from stdin/pipe
            timeout: Maximum time to wait for processing in seconds (default: 120)
        """
        # Set a flag to indicate we're in pipe mode (plugins can check this)
        self.pipe_mode = True
        self.renderer.pipe_mode = True  # Also set on renderer for llm_service access
        # Propagate pipe_mode to message renderer
        if hasattr(self.renderer, "message_renderer"):
            self.renderer.message_renderer.pipe_mode = True

        try:
            # Initialize LLM core service
            await self._initialize_llm_core()

            # Register LLM hooks (in interactive mode this is done early in start())
            await self.llm_service.register_hooks()

            # Check if provider has configuration error - exit early in pipe mode
            api_service = self.llm_service.api_service
            if not api_service.is_provider_available():
                error_msg = api_service.get_provider_error()
                if error_msg:
                    print(
                        "Error: LLM provider not available due to configuration error:"
                    )
                    print(f"{error_msg}")
                    print()
                    print("Use /profile to fix the configuration.")
                    # Clean up before exiting
                    await self.cleanup()
                    return

            # Initialize plugins (they should check self.pipe_mode if needed)
            await self._initialize_plugins()

            # Mark startup as complete
            self._startup_complete = True
            self.running = True
            logger.info("Pipe mode initialized with plugins")

            # Send input to LLM and wait for response
            # The LLM service will handle the response display
            await self.llm_service.process_user_input(piped_input)

            # Wait for processing to start (max 10 seconds)
            start_timeout = 10
            start_wait: float = 0
            while not self.llm_service.is_processing and start_wait < start_timeout:
                await asyncio.sleep(0.1)
                start_wait += 0.1

            # Wait for processing to complete (including all tool calls and continuations)
            max_wait = timeout
            wait_time: float = 0
            while (
                self.llm_service.is_processing
                and not self.llm_service.cancel_processing
                and wait_time < max_wait
            ):
                await asyncio.sleep(0.1)
                wait_time += 0.1

            # Check if processing is still active after timeout
            timed_out = (
                self.llm_service.is_processing
                and not self.llm_service.cancel_processing
            )

            # Give a tiny bit of extra time for final display rendering
            await asyncio.sleep(0.2)

            if timed_out:
                # Timeout expired before processing completed
                import sys

                logger.warning(
                    f"Pipe mode timeout reached after {timeout}s - processing incomplete"
                )
                print(
                    f"\nWarning: Timeout reached after {timeout}s - response may be incomplete",
                    file=sys.stderr,
                )
                # Use exit code 124 (GNU timeout convention)
                sys.exit(124)
            else:
                logger.info("Pipe mode processing complete")

        except KeyboardInterrupt:
            logger.info("Pipe mode interrupted by user")
        except Exception as e:
            logger.error(f"Pipe mode error: {e}")
            import traceback

            traceback.print_exc()
            raise
        finally:
            # Cleanup
            self.running = False
            # Keep pipe_mode=True during cleanup so cancellation messages can be suppressed
            await self.cleanup()
            # DON'T reset pipe_mode here - let main.py's finally block check it to avoid double cleanup

    async def _display_banner(self) -> None:
        """Display Kollab banner (version check deferred to background)."""
        if self.simple_mode:
            print(f"Kollab v{__version__}")
            print()
        else:
            # Gather context for rich banner
            agent = self.agent_manager.get_active_agent()
            agent_name = agent.name if agent else "none"
            skills_count = len(agent.skills) if agent else 0
            profile = ""
            model = ""
            if self.profile_manager:
                profile = getattr(
                    self.profile_manager, "_active_profile_name", "default"
                )
                p = self.profile_manager.get_active_profile()
                if p:
                    model = getattr(p, "model", "") or ""

            banner_context = {
                "version": f"v{__version__}",
                "agent": agent_name,
                "model": model,
                "profile": profile,
                "skills": skills_count,
                "directory": str(Path.cwd()),
            }
            kollabor_banner = self.renderer.create_kollabor_banner(
                f"v{__version__}", context=banner_context
            )
            print(kollabor_banner)

    async def _display_ready_message(self) -> None:
        """Display ready message with stats from core and plugins."""
        # In simple mode, display minimal ready message
        if self.simple_mode:
            self.renderer.message_coordinator.display_message_sequence(
                [("system", "Ready. Type your message and press Enter.", {})]
            )
            return

        # Collect ready message stats from core and plugins
        ready_collector = ReadyMessageCollector()

        await self._add_core_ready_stats(ready_collector)

        # Emit SYSTEM_READY event for plugins to contribute
        await self.event_bus.emit_with_hooks(
            event_type=EventType.SYSTEM_READY,
            data={"collector": ready_collector},
            source="application",
        )

        # Format ready stats
        stats_message = ready_collector.format_for_display(max_items=6)
        logger.debug(f"Ready stats formatted message: '{stats_message}'")
        logger.debug(f"Ready stats item count: {ready_collector.get_count()}")

        # Compact ready line; keep startup quiet and leave the screen to work.
        ready_msg = "ready"
        if stats_message:
            compact_stats = stats_message.replace(", ", " · ")
            ready_msg += f" · {compact_stats}"

        # Get global width for proper text wrapping
        from kollabor_tui.terminal_state import get_global_terminal_state

        ts = get_global_terminal_state()
        width = ts.get_global_width() if ts else 80

        # Wrap the stats part to fit within width
        import textwrap

        from kollabor_tui.design_system import T, solid_fg

        wrapped_lines = textwrap.wrap(ready_msg.strip(), width=width)

        # Build pre-formatted ready text
        output_parts = []
        for line in wrapped_lines:
            output_parts.append(solid_fg(line, T().text_dim))

        ready_text = "\n" + "\n".join(output_parts) + "\n"
        self.renderer.message_coordinator.display_raw_text(ready_text)

    async def _check_for_updates(self) -> None:
        """Check for updates and display notification if newer version available."""
        try:
            # Initialize version check service
            await self.version_check_service.initialize()

            # Check for updates (uses cache if valid)
            release_info = await self.version_check_service.check_for_updates()

            # Display notification if newer version available
            if release_info:
                update_msg = (
                    f"\033[1;33mUpdate available:\033[0m "
                    f"v{release_info.version} is now available "
                    f"(current: v{__version__})\n"
                    f"\033[2;36mDownload:\033[0m {release_info.url}"
                )
                self.renderer.message_coordinator.display_raw_text(update_msg)
                logger.info(f"Update available: {release_info.version}")

        except Exception as e:
            # Graceful degradation - log but don't crash startup
            logger.warning(f"Failed to check for updates: {e}")

    async def _add_core_ready_stats(self, collector: ReadyMessageCollector) -> None:
        """Add core component statistics to ready message collector.

        Args:
            collector: ReadyMessageCollector to add stats to
        """
        logger.debug("Collecting core ready stats...")
        try:
            # System prompt modules count
            from kollabor_ai import PromptRenderer
            from kollabor_config.config_utils import (
                get_system_prompt_content,
                get_system_prompt_path,
            )

            raw_prompt = get_system_prompt_content()
            prompt_path = get_system_prompt_path()
            renderer = PromptRenderer(base_path=prompt_path.parent)
            includes = renderer.get_all_includes(raw_prompt)
            count = len(includes) if includes else 0
            logger.debug(f"Ready stats - System prompt modules count: {count}")
            if includes:
                collector.add(
                    category="system prompt",
                    count=len(includes),
                    label="modules",
                    priority=1000,
                    source="core",
                )

            # Hook count (all registered hooks across all event types)
            # Use hook_registry.hooks dict directly or get stats
            hook_stats = self.event_bus.hook_registry.get_registry_stats()
            hook_count = hook_stats.get("total_hooks", 0)
            logger.debug(f"Ready stats - Hook count: {hook_count}")
            if hook_count > 0:
                collector.add(
                    category="hooks",
                    count=hook_count,
                    label="active",
                    priority=900,
                    source="core",
                )

            # Plugin count (use instantiated plugins, not just discovered classes)
            plugin_count = (
                len(self.plugin_instances) if hasattr(self, "plugin_instances") else 0
            )
            logger.debug(f"Ready stats - Plugin count: {plugin_count}")
            if plugin_count > 0:
                collector.add(
                    category="plugins",
                    count=plugin_count,
                    label="active",
                    priority=800,
                    source="core",
                )

            # Status views count (old API - may not exist)
            status_view_count = 0
            if (
                hasattr(self.renderer, "status_renderer")
                and self.renderer.status_renderer
            ):
                if hasattr(self.renderer.status_renderer, "view_registry"):
                    status_view_count = len(
                        getattr(
                            self.renderer.status_renderer.view_registry, "views", []
                        )
                    )
            logger.debug(f"Ready stats - Status view count: {status_view_count}")
            if status_view_count > 0:
                collector.add(
                    category="status views",
                    count=status_view_count,
                    label="available",
                    priority=700,
                    source="core",
                )

        except Exception as e:
            logger.warning(f"Failed to collect core ready stats: {e}")

    async def _initialize_attach_proxy(self) -> None:
        """Initialize attach proxy mode: connect to remote agent, mirror display.

        In this mode the local process still owns the TUI, input loop, and
        status bar. The remote agent remains the source of truth for messages,
        thinking state, and command execution.
        """
        import json as _json

        identity = self._attach_to
        socket_path = self._attach_socket

        # Display attach status
        self.renderer.message_coordinator.display_message_sequence(
            [
                ("system", f"attaching to {identity}...", {"display_type": "info"}),
            ]
        )

        # Connect to agent socket.
        # Use kollabor_rpc's helper for a 16MB StreamReader buffer -- the
        # default 64KB limit can't hold real state payloads (e.g. conversation
        # history with a large system prompt).
        try:
            try:
                from kollabor_rpc import open_unix_connection_with_large_buffer

                reader, writer = await open_unix_connection_with_large_buffer(
                    socket_path
                )
            except ImportError:
                # Fallback if kollabor_rpc not available (shouldn't happen
                # post-phase-1).
                reader, writer = await asyncio.open_unix_connection(socket_path)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "system",
                        f"cannot connect to {identity}: {e}",
                        {"display_type": "error"},
                    ),
                ]
            )
            self._startup_ready.set()
            return

        # Send attach request (interactive - we want to send input)
        req = (
            _json.dumps(
                {
                    "action": "attach",
                    "mode": "interactive",
                    "client_id": f"attach-{os.getpid()}",
                }
            )
            + "\n"
        )
        writer.write(req.encode())
        await writer.drain()

        # Read ack
        try:
            ack_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            ack = _json.loads(ack_line.decode().strip())
        except Exception as e:
            self.renderer.message_coordinator.display_message_sequence(
                [
                    ("system", f"attach failed: {e}", {"display_type": "error"}),
                ]
            )
            self._startup_ready.set()
            return

        if ack.get("type") != "attach_ack":
            self.renderer.message_coordinator.display_message_sequence(
                [
                    (
                        "system",
                        f"attach failed: {ack.get('msg', 'unexpected response')}",
                        {"display_type": "error"},
                    ),
                ]
            )
            self._startup_ready.set()
            return

        uptime = ack.get("uptime", 0)
        uptime_str = f"{uptime // 60}m{uptime % 60}s" if uptime else "0s"

        # Extract hub info for status bar
        hub_info = ack.get("hub", {})

        attach_msg = (
            f"attached to {identity} (interactive)"
            f" | uptime {uptime_str} | Ctrl+Z to detach"
        )
        self.renderer.message_coordinator.display_message_sequence(
            [
                ("system", attach_msg, {"display_type": "success"}),
            ]
        )

        # Register hub info as event bus service so hub plugin's
        # get_status_line() can show the remote identity on the status bar
        if self.event_bus and hub_info:
            self.event_bus.register_service("attach_hub_info", hub_info)
        if self.event_bus:
            attach_runtime_state = {
                "identity": identity,
                "socket_path": str(self._attach_socket),
                "connected_at": time.time(),
                "last_heartbeat_at": 0.0,
            }
            self.event_bus.register_service(
                "attach_runtime_state", attach_runtime_state
            )

        # === RPC client (phase 1 of daemon transparency refactor) ===
        # Local import: kollabor_rpc is only needed in the attach branch, and
        # top-level imports can fail in local-only code paths if the package
        # isn't installed. Gate it here.
        try:
            from kollabor_rpc import RpcClient

            self._rpc_client = RpcClient(writer)
            if self.event_bus:
                self.event_bus.register_service("rpc_client", self._rpc_client)
            logger.info("rpc client initialized, registered as event bus service")
        except ImportError as e:
            logger.warning(f"kollabor_rpc not available, rpc client disabled: {e}")
            self._rpc_client = None

        # === RemoteStateService wiring (phase 2 of daemon transparency refactor) ===
        # In attach mode, StateService reads and writes go to the daemon via
        # RPC. RemoteStateService wraps the RpcClient and presents the same
        # interface as LocalStateService, so commands and widgets are unaware
        # of which implementation they're holding. Phase 4.5 step 9: __init__
        # skips LocalStateService registration in attach mode, so this call
        # is the FIRST state_service registration in the attach client (no
        # earlier local registration to overwrite).
        self._remote_state_service = None
        if self._rpc_client is not None:
            try:
                from kollabor.state import RemoteStateService

                self._remote_state_service = RemoteStateService(
                    rpc_client=self._rpc_client
                )
                if self.event_bus:
                    self.event_bus.register_service(
                        "state_service", self._remote_state_service
                    )
                logger.info(
                    "RemoteStateService initialized and registered (attach mode)"
                )
            except Exception as e:
                logger.error(
                    f"failed to initialize RemoteStateService: {e}", exc_info=True
                )
                self._remote_state_service = None

        # Store writer for input forwarding
        self._attach_writer = writer

        # Override the user input handler to send to remote agent instead of local LLM
        from kollabor_events import EventType, Hook, HookPriority

        async def _proxy_user_input(data, event=None):
            """Intercept user input and forward to remote agent via socket."""
            message = data.get("message", "")
            if not message:
                return data

            # Send to remote agent
            try:
                msg = _json.dumps({"type": "input", "text": message}) + "\n"
                writer.write(msg.encode())
                await writer.drain()
            except (ConnectionError, OSError):
                self.renderer.message_coordinator.display_message_sequence(
                    [
                        (
                            "system",
                            f"connection to {identity} lost",
                            {"display_type": "error"},
                        ),
                    ]
                )

            # Suppress default LLM processing (input goes to remote agent instead).
            # Thinking spinner will be triggered by remote "thinking" event.
            data["_suppress"] = True
            return data

        await self.event_bus.register_hook(
            Hook(
                name="attach_proxy_input",
                plugin_name="attach_proxy",
                event_type=EventType.USER_INPUT,
                callback=_proxy_user_input,
                priority=HookPriority.SYSTEM.value,  # Run before anything else
            )
        )

        # Ctrl+Z detach: disconnect from agent without killing it
        async def _detach_on_ctrl_z(data, event=None):
            key = data.get("key", "")
            if key != "Ctrl+Z":
                return data

            # Send detach message so server cleans up subscriber
            try:
                detach_msg = _json.dumps({"type": "detach"}) + "\n"
                writer.write(detach_msg.encode())
                await writer.drain()
            except Exception:
                pass

            try:
                writer.close()
            except Exception:
                pass

            self.renderer.message_coordinator.display_message_sequence(
                [
                    ("system", f"detached from {identity}", {"display_type": "info"}),
                    (
                        "system",
                        f"reattach: kollab --attach {identity}",
                        {"display_type": "info"},
                    ),
                ]
            )

            # Suppress the "disconnected" message from _read_remote_events
            self._attach_detaching = True

            # Clear daemon PID so the owner doesn't kill it on exit.
            # Ctrl+Z = detach (daemon survives). Ctrl+C = kill (daemon dies).
            os.environ.pop("KOLLAB_DAEMON_PID", None)

            # Give the message a moment to render, then shut down
            await asyncio.sleep(0.3)
            self.running = False
            await self.input_handler.stop()

            return {"prevent_default": True}

        await self.event_bus.register_hook(
            Hook(
                name="attach_detach_ctrl_z",
                plugin_name="attach_proxy",
                event_type=EventType.KEY_PRESS,
                callback=_detach_on_ctrl_z,
                priority=HookPriority.SYSTEM.value,
            )
        )

        # Start reading semantic events from remote agent.
        # The daemon streams high-level UI events, not raw terminal bytes,
        # so the attached client can render them with its own local TUI state.
        async def _read_remote_events():
            """Read semantic events from remote agent and render locally.

            Events are fed through the same display methods the local
            LLM pipeline uses. The local renderer handles all formatting.
            """
            coordinator = self.renderer.message_coordinator
            while self.running:
                try:
                    line = await reader.readline()
                except (ConnectionError, OSError):
                    break
                if not line:
                    break

                try:
                    event = _json.loads(line.decode().strip())
                except (ValueError, UnicodeDecodeError):
                    continue

                # RPC replies are routed to the client (phase 1 of daemon
                # transparency refactor). These use the "action" field rather
                # than "type" to match the messenger's rpc_request/rpc_reply
                # wire protocol.
                if event.get("action") == "rpc_reply":
                    if hasattr(self, "_rpc_client") and self._rpc_client is not None:
                        self._rpc_client.on_reply(event)
                    continue

                etype = event.get("type", "")

                if etype == "message":
                    # Semantic message - render through local display pipeline
                    msg_type = event.get("message_type", "system")
                    content = event.get("content", "")
                    kwargs = event.get("kwargs", {})
                    # Tool messages may have empty content but still need rendering
                    # (the tool box comes from kwargs["tool_name"], not content)
                    if content or kwargs.get("tool_name"):
                        coordinator.display_message_sequence(
                            [
                                (msg_type, content, kwargs),
                            ]
                        )

                elif etype == "thinking":
                    # Thinking state change - drive local spinner
                    active = event.get("active", False)
                    message = event.get("message", "")
                    self.renderer.update_thinking(active, message)
                    if hasattr(self, "render_loop") and self.render_loop:
                        self.render_loop.request_render()

                elif etype == "stream_chunk":
                    chunk = event.get("chunk", "")
                    if chunk:
                        coordinator.write_streaming_chunk(chunk)

                elif etype == "state_snapshot":
                    # Update widget context with daemon state
                    if hasattr(self, "_widget_context"):
                        self._widget_context.remote_state = merge_widget_state_snapshot(
                            getattr(self._widget_context, "remote_state", {}) or {},
                            event,
                        )
                        if hasattr(self, "render_loop") and self.render_loop:
                            self.render_loop.request_render()

                elif etype == "permission_request":
                    if self._rpc_client is not None:
                        await self._attach_permission_bridge.handle_client_event(
                            rpc_client=self._rpc_client,
                            layout_manager=self.renderer.layout_manager,
                            event=event,
                            wait_for_rpc_reply=False,
                        )
                    else:
                        coordinator.display_message_sequence(
                            [
                                (
                                    "error",
                                    "permission prompt received before rpc client was ready",
                                    {},
                                ),
                            ]
                        )

                elif etype == "heartbeat":
                    runtime = self.event_bus.get_service("attach_runtime_state")
                    if isinstance(runtime, dict):
                        runtime["last_heartbeat_at"] = time.time()

            # Connection ended (skip message if we detached intentionally)
            daemon_gone = self.running and not getattr(self, "_attach_detaching", False)
            if daemon_gone:
                coordinator.display_message_sequence(
                    [
                        (
                            "system",
                            f"{identity} disconnected — daemon exited",
                            {"display_type": "warning"},
                        ),
                    ]
                )

            # Cancel any pending RPC futures so callers don't hang on a dead
            # connection.
            if hasattr(self, "_rpc_client") and self._rpc_client is not None:
                try:
                    self._rpc_client.close()
                except Exception as e:
                    logger.debug(f"error closing rpc client: {e}")

            # If the daemon went away while we were attached, the attach
            # client has nothing to render and no peer to talk to. Exit
            # the app instead of sitting forever in a dead TUI. This is
            # the attach-side mirror of the daemon's self-stop watchdog:
            # daemon.os._exit(0) + client.shutdown() = clean end-to-end.
            if daemon_gone:
                logger.info(
                    f"Attach client: daemon {identity} exited, shutting down client"
                )
                self.running = False
                try:
                    asyncio.ensure_future(self.shutdown())
                except Exception as e:
                    logger.debug(f"attach client shutdown schedule failed: {e}")

                # Watchdog: if graceful shutdown hangs (input handler
                # won't release raw mode, render loop stuck mid-frame),
                # force-exit so the user isn't staring at a frozen TUI.
                async def _attach_exit_watchdog() -> None:
                    try:
                        await asyncio.sleep(5.0)
                    except asyncio.CancelledError:
                        return
                    logger.warning(
                        "Attach client: exit watchdog fired, forcing os._exit"
                    )
                    try:
                        # Best-effort terminal restore before force-exit.
                        import termios

                        if hasattr(self, "_saved_termios") and self._saved_termios:
                            termios.tcsetattr(
                                sys.stdin.fileno(),
                                termios.TCSADRAIN,
                                self._saved_termios,
                            )
                    except Exception:
                        pass
                    os._exit(0)

                try:
                    asyncio.ensure_future(_attach_exit_watchdog())
                except Exception:
                    pass

        self.create_background_task(_read_remote_events(), "attach_event_reader")

        # === Phase 4.5: drain pending launch flags via RPC ===
        #
        # In attach mode, DAEMON_OWNED launch flags (--profile, --agent,
        # --skill, --system-prompt, --save, --local) were stashed on
        # self._attach_pending_flags in __init__ instead of being applied
        # to the client's shadow state. Now that both RemoteStateService
        # AND the _read_remote_events task are running, drain the queue
        # as RPC calls in a deterministic order.
        #
        # IMPORTANT: this MUST come after create_background_task above,
        # because the RPC reply frames arrive via _read_remote_events.
        # If we drain before the reader is scheduled, the replies sit
        # in the socket buffer unread and every RPC hangs until timeout.
        #
        # Order: profile -> agent -> skills -> system_prompt
        #  1. profile sets the provider first so subsequent rpcs see
        #     the right model.
        #  2. agent may apply its preferred profile internally and
        #     loads default skills.
        #  3. additional skills get activated.
        #  4. system prompt override is applied last (most authoritative).
        #
        # Each call runs in a try/except: failures produce a visible
        # message in the attach client but do NOT crash the session.
        # A typo'd --agent should produce a visible error and leave
        # the daemon as-is, not prevent the user from sending messages.
        if self._remote_state_service is not None and getattr(
            self, "_attach_pending_flags", None
        ):
            # Run the drain as a background task so _initialize_attach_proxy
            # returns promptly. The drain itself waits on RPC replies,
            # which are now being consumed by the event reader we just
            # scheduled. Running it inline would be fine in theory but
            # backgrounding makes the startup path predictable and
            # non-blocking for the rest of init.
            self.create_background_task(
                self._drain_attach_pending_flags(),
                "attach_drain_pending_flags",
            )

        # Signal startup complete only after input forwarding hooks and the
        # remote event reader are live. Setting this earlier opens a race where
        # fast user input can bypass attach_proxy_input or where RPC replies
        # have no reader yet.
        self._startup_complete = True
        self._startup_ready.set()

    async def _try_attach_permission_prompt(self, details: Dict[str, Any]):
        """Route daemon permission prompts to the visible attach client."""
        if not self.event_bus:
            return None

        display_tap = self.event_bus.get_service("display_tap")
        rpc_server = self.event_bus.get_service("rpc_server")
        if not display_tap or not rpc_server:
            return None

        if not await self._attach_permission_bridge.wait_for_visible_attach_client(
            display_tap
        ):
            return None

        return await self._attach_permission_bridge.request_confirmation(
            display_tap=display_tap,
            rpc_server=rpc_server,
            details=dict(details),
            timeout=300,
        )

    async def _drain_attach_pending_flags(self) -> None:
        """Apply launch flags to the daemon via RPC in attach mode.

        Phase 4.5 fix for the "launch flags don't cross the process
        boundary" bug. In attach mode, --profile / --agent / --skill /
        --system-prompt were stashed on self._attach_pending_flags in
        __init__ instead of being applied to the client's shadow state.
        This method drains that queue via RPC calls on the newly-wired
        RemoteStateService.

        Order matters:
          1. profile (with optional --save/--local persist)
          2. agent (may apply its preferred profile internally)
          3. skills (each activated against the active agent)
          4. system_prompt (most authoritative, applied last)

        Each call runs in a try/except; failures surface as display
        messages but do NOT crash the attach session. A typo'd --agent
        should produce a visible error and leave the daemon as-is, not
        prevent the user from sending messages.
        """
        flags = getattr(self, "_attach_pending_flags", None) or {}
        if not flags:
            return
        logger.info(
            "attach drain: processing %d launch flags: %s",
            sum(1 for v in flags.values() if v),
            {k: v for k, v in flags.items() if v},
        )

        state = self._remote_state_service
        if state is None:
            logger.warning(
                "drain_attach_pending_flags: no remote state service, skipping"
            )
            return

        # Helper to surface errors to the user via the message coordinator
        # so they see exactly what failed. In simple/detached modes the
        # renderer may not have a coordinator -- in that case we log only.
        def _display(role: str, text: str, meta: dict | None = None) -> None:
            try:
                coord = getattr(self.renderer, "message_coordinator", None)
                if coord is not None and hasattr(coord, "display_message_sequence"):
                    coord.display_message_sequence([(role, text, meta or {})])
                    return
            except Exception as e:
                logger.debug(f"_display failed: {e}")
            # Log fallback
            logger.info(f"[{role}] {text}")

        # --- 0. Context (must come first so subsequent flags land on
        #        the chosen context, not whatever was live before) ---
        context_name = flags.get("context")
        if context_name:
            try:
                # Try to attach first. If the context doesn't exist,
                # create it and then attach.
                try:
                    snap = await state.attach_to_context(context_name)
                except ValueError as not_found_exc:
                    if "not found" not in str(not_found_exc):
                        raise
                    logger.info(
                        f"attach drain: context {context_name!r} not found, "
                        f"creating it"
                    )
                    await state.create_context(context_name)
                    snap = await state.attach_to_context(context_name)
                _display(
                    "system",
                    f"attached to context: {snap.name}"
                    + (
                        f" ({snap.message_count} messages)"
                        if snap.message_count
                        else ""
                    ),
                    {"display_type": "info"},
                )
                logger.info(
                    f"attach drain: context -> {snap.name} "
                    f"({snap.message_count} messages)"
                )
            except Exception as e:
                _display(
                    "error",
                    f"--context {context_name!r} failed on daemon: {e}",
                    {"display_type": "error"},
                )
                logger.warning(
                    f"attach drain: attach_to_context failed: "
                    f"{type(e).__name__}: {e}"
                )

        # --- 1. Profile ---
        profile_name = flags.get("profile")
        if profile_name:
            make_default_profile = bool(flags.get("make_default_profile", False))
            persist = bool(flags.get("save_profile", False)) or make_default_profile
            persist_local = bool(flags.get("save_local", False))
            try:
                snap = await state.set_active_profile(
                    profile_name, persist=persist, persist_local=persist_local
                )
                save_hint = (
                    " (saved"
                    + (" and set default" if make_default_profile else "")
                    + " to "
                    + ("local" if persist_local else "global")
                    + " config)"
                    if persist
                    else ""
                )
                _display(
                    "system",
                    f"switched to profile: {snap.name} ({snap.model}){save_hint}",
                    {"display_type": "info"},
                )
                logger.info(
                    f"attach drain: profile -> {snap.name} (model={snap.model})"
                )
            except Exception as e:
                _display(
                    "error",
                    f"--profile {profile_name!r} failed on daemon: {e}",
                    {"display_type": "error"},
                )
                logger.warning(
                    f"attach drain: set_active_profile failed: "
                    f"{type(e).__name__}: {e}"
                )

        # --- 2. Agent ---
        agent_name = flags.get("agent")
        if agent_name:
            try:
                snap = await state.set_agent(agent_name)
                _display(
                    "system",
                    f"switched to agent: {snap.name}"
                    + (f" (profile: {snap.profile})" if snap.profile else ""),
                    {"display_type": "info"},
                )
                logger.info(f"attach drain: agent -> {snap.name}")
            except Exception as e:
                _display(
                    "error",
                    f"--agent {agent_name!r} failed on daemon: {e}",
                    {"display_type": "error"},
                )
                logger.warning(f"attach drain: set_agent failed: {e}")

        # --- 3. Skills (activate each, tolerate partial failures) ---
        for skill_name in flags.get("skills") or []:
            if not skill_name:
                continue
            try:
                await state.activate_skill(skill_name)
                _display(
                    "system",
                    f"activated skill: {skill_name}",
                    {"display_type": "info"},
                )
                logger.info(f"attach drain: skill -> {skill_name}")
            except Exception as e:
                _display(
                    "error",
                    f"--skill {skill_name!r} failed on daemon: {e}",
                    {"display_type": "error"},
                )
                logger.warning(
                    f"attach drain: activate_skill {skill_name!r} failed: {e}"
                )

        # --- 4. System prompt ---
        # Read the file content on the CLIENT side because the daemon's
        # cwd may be different. We send the bytes over RPC, not the path.
        prompt_file = flags.get("system_prompt_file")
        if prompt_file:
            try:
                prompt_path = Path(prompt_file).expanduser().resolve()
                if not prompt_path.exists():
                    raise FileNotFoundError(
                        f"system prompt file not found: {prompt_path}"
                    )
                content = prompt_path.read_text(encoding="utf-8")
                snap = await state.set_system_prompt(
                    content, source="file", path=str(prompt_path)
                )
                _display(
                    "system",
                    f"installed system prompt from {prompt_path.name} "
                    f"({snap.size_chars} chars)",
                    {"display_type": "info"},
                )
                logger.info(
                    f"attach drain: system_prompt -> {prompt_path} "
                    f"({snap.size_chars} chars)"
                )
            except Exception as e:
                _display(
                    "error",
                    f"--system-prompt {prompt_file!r} failed on daemon: {e}",
                    {"display_type": "error"},
                )
                logger.warning(f"attach drain: set_system_prompt failed: {e}")

        # Clear the queue so a subsequent reattach doesn't redo it.
        self._attach_pending_flags = {}

    async def _initialize_llm_core(self) -> None:
        """Initialize LLM core service components."""
        from kollabor_ai.pricing_registry import PricingRegistry

        PricingRegistry().load_defaults()

        # Initialize LLM service
        await self.llm_service.initialize()
        logger.info("LLM core service initialized")

        # Inject CLI-loaded skills into conversation now that llm_service is ready
        # Also store for UI display after renderer is ready
        self._pending_skill_display = []
        if hasattr(self, "_pending_skill_names") and self._pending_skill_names:
            agent = self.agent_manager.get_active_agent()
            if agent:
                for skill_name in self._pending_skill_names:
                    skill = agent.get_skill(skill_name)
                    if skill and skill.content:
                        skill_message = f"## Skill: {skill_name}\n\n{skill.content}"
                        self.llm_service._add_conversation_message(
                            "user", skill_message
                        )
                        # Store for UI display
                        self._pending_skill_display.append(("user", skill_message, {}))
                        self._pending_skill_display.append(
                            ("system", f"[ok] Loaded skill: {skill_name}", {})
                        )
                        logger.info(
                            f"Injected skill content into conversation: {skill_name}"
                        )

        # Note: system_commands.llm_service uses dynamic lookup via event_bus.get_service()
        # so no manual wiring is needed here

        # Initialize conversation logger
        await self.conversation_logger.initialize()
        logger.info("Conversation logger initialized")

        # Note: MCP server discovery is handled in background by llm_service.initialize()
        # to avoid blocking startup (see llm_service._background_mcp_discovery)

        # Note: LLM service hooks are registered early in start() for fast input bar,
        # so we skip register_hooks() here. The handler gates on _startup_ready.

        # Register permission hook if available
        if hasattr(self, "permission_hook"):
            await self.permission_hook.register(self.event_bus)
            logger.info("Permission hook registered")

    async def _display_provider_warning(self) -> None:
        """Display warning if LLM provider has configuration error."""
        api_service = self.llm_service.api_service
        if not api_service.is_provider_available():
            error_msg = api_service.get_provider_error()
            if error_msg:
                # Truncate long error messages
                if len(error_msg) > 200:
                    error_msg = error_msg[:200] + "..."
                warning_text = (
                    f"\033[33m{'=' * 60}\033[0m\n"
                    f"\033[1;33m[!] LLM Provider Configuration Error\033[0m\n\n"
                    f"\033[33m{error_msg}\033[0m\n\n"
                    f"\033[1;37mUse /profile to fix the configuration.\033[0m\n"
                    f"\033[33m{'=' * 60}\033[0m"
                )
                self.renderer.message_coordinator.display_raw_text(warning_text)

    async def _initialize_plugins_for_commands(self) -> None:
        """Initialize plugins minimally for command registration only.

        Used by -h/--help to get plugin commands without starting
        background tasks or full LLM initialization.
        """
        initialized_instances = set()

        for plugin_name, plugin_instance in self.plugin_instances.items():
            instance_id = id(plugin_instance)
            if instance_id in initialized_instances:
                continue
            initialized_instances.add(instance_id)

            if hasattr(plugin_instance, "initialize"):
                # Minimal kwargs - no llm_service since it's not initialized
                init_kwargs = {
                    "args": self.args,
                    "event_bus": self.event_bus,
                    "config": self.config,
                    "command_registry": getattr(
                        self.input_handler, "command_registry", None
                    ),
                    "input_handler": self.input_handler,
                    "renderer": self.renderer,
                    "llm_service": None,  # Not available in list-commands mode
                    "conversation_logger": None,
                    "conversation_manager": None,
                }

                try:
                    import inspect

                    sig = inspect.signature(plugin_instance.initialize)
                    if len(sig.parameters) > 0:
                        await plugin_instance.initialize(**init_kwargs)
                    else:
                        await plugin_instance.initialize()
                    logger.debug(f"Initialized plugin for commands: {plugin_name}")
                except Exception as e:
                    # Log but don't fail - plugin might need llm_service
                    logger.debug(f"Plugin {plugin_name} init skipped: {e}")

        logger.info("Plugins initialized for command listing")

    async def _initialize_plugins(self) -> None:
        """Initialize all discovered plugins."""
        # Deduplicate plugin instances by ID (same instance may be stored under multiple keys)
        initialized_instances = set()

        for plugin_name, plugin_instance in self.plugin_instances.items():
            instance_id = id(plugin_instance)

            # Skip if we've already initialized this instance
            if instance_id in initialized_instances:
                continue

            initialized_instances.add(instance_id)

            if hasattr(plugin_instance, "initialize"):
                # Pass command registry, input handler, llm_service, renderer, and args to plugins
                init_kwargs = {
                    "args": self.args,  # Parsed CLI arguments including plugin args
                    "event_bus": self.event_bus,
                    "config": self.config,
                    "command_registry": getattr(
                        self.input_handler, "command_registry", None
                    ),
                    "input_handler": self.input_handler,
                    "renderer": self.renderer,
                    "llm_service": self.llm_service,
                    # Use llm_service's conversation_logger (the one actively logging)
                    "conversation_logger": getattr(
                        self.llm_service,
                        "conversation_logger",
                        self.conversation_logger,
                    ),
                    "conversation_manager": getattr(
                        self.llm_service, "conversation_manager", None
                    ),
                }

                # Check if initialize method accepts keyword arguments
                import inspect

                sig = inspect.signature(plugin_instance.initialize)
                if len(sig.parameters) > 0:
                    await plugin_instance.initialize(**init_kwargs)
                else:
                    await plugin_instance.initialize()
                logger.debug(f"Initialized plugin: {plugin_name}")

            if hasattr(plugin_instance, "register_hooks"):
                await plugin_instance.register_hooks()
                logger.debug(f"Registered hooks for plugin: {plugin_name}")

            # Yield between plugin inits so render loop stays responsive
            await asyncio.sleep(0)

        # Register system commands hooks (for modal command handling)
        if hasattr(self, "system_commands") and self.system_commands:
            await self.system_commands.register_hooks()
            logger.debug("Registered hooks for system commands")

        # Register application-level hooks (help overlay, etc.)
        await self._register_application_hooks()
        logger.debug("Registered application-level hooks")

        # Set plugin instances on LLM service for system prompt additions
        if hasattr(self, "llm_service") and self.llm_service:
            self.llm_service.set_plugin_instances(self.plugin_instances)

            # Inject TmuxPlugin (subprocess-based terminal manager) into ToolExecutor
            tmux_plugin = None
            for plugin_name, plugin_instance in self.plugin_instances.items():
                if plugin_instance.__class__.__name__ == "TmuxPlugin":
                    tmux_plugin = plugin_instance
                    logger.info(
                        "Found TmuxPlugin instance for ToolExecutor integration"
                    )
                    break

            if tmux_plugin and hasattr(self.llm_service, "tool_executor"):
                self.llm_service.tool_executor.tmux_plugin = tmux_plugin
                logger.info(
                    "Injected TmuxPlugin into ToolExecutor for subprocess terminal execution"
                )
            elif not tmux_plugin:
                logger.debug(
                    "TmuxPlugin not available - ToolExecutor will use fallback ShellExecutor"
                )

            # Check if any plugin wants to add to system prompt and rebuild if needed
            additions = (
                self.llm_service._prompt_builder._get_plugin_system_prompt_additions()
            )
            if additions:
                self.llm_service.rebuild_system_prompt()
                logger.info(
                    f"System prompt rebuilt with {len(additions)} plugin additions"
                )

    async def _load_config_hooks(self) -> None:
        """Load JSON-based hooks from hooks.json (Claude Code compatible)."""
        try:
            from kollabor.config_hooks import ConfigHookLoader

            loader = ConfigHookLoader(self.event_bus, self.config)
            count = await loader.load_and_register()
            self._config_hook_loader = loader
            if count > 0:
                logger.info(f"Loaded {count} config hooks")
        except Exception as e:
            logger.warning(f"Config hooks load failed: {e}")

    def _initialize_slash_commands(self) -> None:
        """Initialize the slash command system with core commands."""
        logger.info("Starting slash command system initialization...")
        try:
            from kollabor.commands.system_commands import SystemCommandsPlugin

            logger.info("SystemCommandsPlugin imported successfully")

            # Create and register system commands
            # Note: llm_service is passed if available, but may be None at this point
            self.system_commands = SystemCommandsPlugin(
                command_registry=self.input_handler.command_registry,
                event_bus=self.event_bus,
                config_manager=self.config,
                llm_service=getattr(self, "llm_service", None),
                profile_manager=getattr(self, "profile_manager", None),
                agent_manager=getattr(self, "agent_manager", None),
            )
            logger.info("SystemCommandsPlugin instance created")

            # Register all system commands
            # Note: system_commands accesses app services via event_bus.get_service()
            self.system_commands.register_commands()
            logger.info("System commands registration completed")

            # Register MCP commands before AltView discovery so the /mcp
            # manager can take bare /mcp while delegating subcommands.
            from kollabor.commands.mcp_command import register_mcp_commands

            try:
                llm_service = getattr(self, "llm_service", None)
                mcp_integration = (
                    getattr(llm_service, "mcp_integration", None)
                    if llm_service
                    else getattr(self, "mcp_integration", None)
                )
                self.mcp_commands = register_mcp_commands(
                    command_registry=self.input_handler.command_registry,
                    mcp_integration=mcp_integration,
                    renderer=self.renderer,
                    app=self,
                )
                logger.info("MCP commands registered successfully")
            except Exception as e:
                logger.warning(f"Failed to register MCP commands: {e}")

            stats = self.input_handler.command_registry.get_registry_stats()
            logger.info("Slash command system initialized with system commands")
            logger.info(f"[INFO] {stats['total_commands']} commands registered")

        except Exception as e:
            logger.error(f"Failed to initialize slash command system: {e}")
            import traceback

            logger.error(f"[INFO] Traceback: {traceback.format_exc()}")

    async def _check_first_run_wizard(self) -> None:
        """Check if this is first run and launch setup wizard if needed."""
        try:
            # Only show wizard on first install (when global config didn't exist before)
            if not self._is_first_install:
                logger.info("Not a first install, skipping wizard")
                return

            # Double-check the config flag in case wizard was already run
            setup_completed = self.config.get("application.setup_completed", False)
            if setup_completed:
                logger.info("Setup already completed, skipping wizard")
                return

            # Check if we have the fullscreen integrator
            if (
                not hasattr(self, "fullscreen_integrator")
                or not self.fullscreen_integrator
            ):
                logger.warning("Fullscreen integrator not available, skipping wizard")
                return

            # Check if setup plugin is registered
            if "setup" not in self.fullscreen_integrator.registered_plugins:
                logger.info("Setup wizard plugin not found, skipping")
                return

            logger.info("First run detected - launching setup wizard")

            # Get the setup plugin instance and pass managers
            plugin_class = self.fullscreen_integrator.registered_plugins["setup"]
            from kollabor_tui.fullscreen.plugin import PluginMetadata

            plugin_instance = plugin_class(
                PluginMetadata(name="setup", version="1.0.0")
            )
            plugin_instance.set_managers(self.config, self.profile_manager)  # type: ignore[attr-defined]

            # Ensure fullscreen manager is initialized (it's lazily created in command handlers)
            if not self.fullscreen_integrator._fullscreen_manager:
                from kollabor_tui.fullscreen import FullScreenManager

                self.fullscreen_integrator._fullscreen_manager = FullScreenManager(  # type: ignore[assignment]
                    self.fullscreen_integrator.event_bus,
                    self.fullscreen_integrator.terminal_renderer,
                )

            # Register and launch
            self.fullscreen_integrator._fullscreen_manager.register_plugin(plugin_instance)  # type: ignore[attr-defined]
            await self.fullscreen_integrator._fullscreen_manager.launch_plugin("setup")  # type: ignore[attr-defined]

            # Check wizard completion status and mark setup as completed
            if plugin_instance.completed:  # type: ignore[attr-defined]
                logger.info("Setup wizard completed successfully")
            elif plugin_instance.skipped:  # type: ignore[attr-defined]
                logger.info("Setup wizard skipped by user")
            else:
                logger.info("Setup wizard exited")

            # Mark setup as completed to avoid showing wizard on next startup
            self.config.save_key("application.setup_completed", True)

        except Exception as e:
            logger.error(f"Error launching setup wizard: {e}")
            import traceback

            logger.error(f"Setup wizard traceback: {traceback.format_exc()}")
            # Don't fail startup if wizard fails
            # Mark as completed so we don't retry
            self.config.save_key("application.setup_completed", True)

    async def _show_first_run_help_modal(self) -> None:
        """Show first-run help modal for interactive status widgets."""
        try:
            from kollabor_events.models import UIConfig
            from kollabor_tui.status.help_system import show_first_run_help

            logger.info("Checking if first-run help modal should be shown...")

            # Create a simple callback that shows modal via modal_controller
            async def show_modal_callback(ui_config: UIConfig) -> None:
                logger.info("Showing first-run help modal...")
                if hasattr(self.input_handler, "_modal_controller"):
                    await self.input_handler._modal_controller._enter_modal_mode(
                        ui_config
                    )
                    logger.info("First-run help modal displayed successfully")
                else:
                    logger.warning(
                        "modal_controller not available, skipping first-run help modal"
                    )

            # Show first-run help (checks internal config flag)
            result = await show_first_run_help(
                show_modal_callback=show_modal_callback,
                config=self.config,
            )

            logger.info(f"First-run help result: {result}")

        except Exception as e:
            logger.error(f"Error showing first-run help modal: {e}")
            import traceback

            logger.error(f"First-run help traceback: {traceback.format_exc()}")

    def _initialize_fullscreen_commands(self) -> None:
        """Initialize dynamic fullscreen plugin commands."""
        try:
            from kollabor.fullscreen.command_integration import (
                FullScreenCommandIntegrator,
            )

            # Create the integrator with managers for plugins that need them
            self.fullscreen_integrator = FullScreenCommandIntegrator(
                command_registry=self.input_handler.command_registry,
                event_bus=self.event_bus,
                config=self.config,
                profile_manager=self.profile_manager,
                terminal_renderer=self.renderer,
                app=self,  # Pass app reference for plugins that need full access
            )

            # Discover and register all fullscreen plugins
            # Use same plugin directory resolution as main plugin registry
            package_dir = Path(__file__).parent.parent
            plugins_dir = package_dir / "plugins"
            if not plugins_dir.exists():
                plugins_dir = Path.cwd() / "plugins"
            registered_count = self.fullscreen_integrator.discover_and_register_plugins(
                plugins_dir
            )

            logger.info(
                f"Fullscreen plugin commands initialized: "
                f"{registered_count} plugins registered"
            )

        except Exception as e:
            logger.error(f"Failed to initialize fullscreen commands: {e}")
            import traceback

            logger.error(f"Fullscreen commands traceback: {traceback.format_exc()}")

    def _initialize_altview_commands(self) -> None:
        """Initialize AltView plugin commands.

        Coexists with FullScreenCommandIntegrator during the migration
        period. Discovers plugins from plugins/altview/ and registers
        them as slash commands with CommandMode.ALTVIEW.
        """
        try:
            from kollabor.altview.command_integration import (
                AltViewCommandIntegrator,
            )

            self.altview_integrator = AltViewCommandIntegrator(
                command_registry=self.input_handler.command_registry,
                event_bus=self.event_bus,
                terminal_renderer=self.renderer,
                config=self.config,
                profile_manager=self.profile_manager,
                app=self,
            )

            # Discover and register all altview plugins
            # Same plugin directory resolution as fullscreen integrator
            package_dir = Path(__file__).parent.parent
            plugins_dir = package_dir / "plugins"
            if not plugins_dir.exists():
                plugins_dir = Path.cwd() / "plugins"
            registered_count = self.altview_integrator.discover_and_register_plugins(
                plugins_dir
            )

            logger.info(
                f"AltView plugin commands initialized: "
                f"{registered_count} plugins registered"
            )

        except Exception as e:
            logger.error(f"Failed to initialize altview commands: {e}")
            import traceback

            logger.error(f"AltView commands traceback: {traceback.format_exc()}")

    async def _render_callback(self, delta_time: float, trigger) -> bool:
        """Render callback for event-driven loop.

        Args:
            delta_time: Time since last frame
            trigger: RenderTrigger (INPUT, TIMER, FORCED, INITIAL)

        Returns:
            True to continue, False to exit loop
        """
        # Check if we need to stop
        if not self.running:
            return False

        # Render active area
        try:
            await self.renderer.render_active_area()
        except Exception as e:
            logger.error(f"Render callback error: {e}", exc_info=True)
            # Continue loop despite render error

        # Continue loop
        return True

    async def _input_callback(self):
        """Input polling callback for event-driven loop.

        Returns:
            (input_processed, should_exit)
            - (True, False): Input processed, don't exit
            - (True, True): Input processed and should exit
            - (False, False): No input
        """
        # Check if input handler has processed input since last poll
        if hasattr(self.input_handler, "_input_loop_manager"):
            processed = self.input_handler._input_loop_manager.has_pending_input()

            # Check if we should exit
            should_exit = not self.running or not self.input_handler.running

            return (processed, should_exit)

        return (False, not self.running)

    def create_background_task(self, coro, name: str = "unnamed"):
        """Create and track a background task with automatic cleanup.

        Args:
            coro: Coroutine to run as background task
            name: Human-readable name for the task

        Returns:
            The created asyncio.Task
        """
        task = asyncio.create_task(coro)
        task.set_name(name)
        self._background_tasks.append(task)
        logger.debug(f"Created background task: {name}")

        # Add callback to remove task from tracking when done
        def remove_task(t):
            try:
                self._background_tasks.remove(t)
                logger.debug(f"Background task completed: {name}")
            except ValueError:
                pass  # Task already removed

        task.add_done_callback(remove_task)
        return task

    # =========================================================================
    # CLI Slash Command Support
    # =========================================================================

    async def _process_cli_command(self) -> Optional[dict]:
        """Process CLI command from args once command registry is available.

        Returns:
            CLI command dict if valid command found, None otherwise.
        """
        from .cli import process_cli_command

        try:
            cli_command = process_cli_command(
                self.args, self.input_handler.command_registry
            )
            return cli_command
        except ValueError as e:
            self._print_error(str(e))
            logger.error(f"CLI command parse error (post-init): {e}")
            # Return None instead of sys.exit() - terminal is in raw mode,
            # sys.exit here would leave it corrupted. Caller falls through
            # to interactive mode which is safer than a broken terminal.
            return None

    async def _execute_cli_command(self, cli_command: dict) -> int:
        """Execute a CLI slash command with security and permissions.

        Args:
            cli_command: Dict with 'name', 'args', 'raw', 'requires_interactive', 'command_def'

        Returns:
            Exit code: 0 = success, 1 = failure, 2 = error

        State Management:
            When using --stay flag, the application enters interactive mode after
            the CLI command completes. Any state changes made by the command
            (permissions, config, etc.) persist into the interactive session.

            Example: `kollab --permissions trust --stay` leaves trust mode active.

            Commands that should not persist state should set `cli_hidden=True`
            and document interactive-only usage.
        """
        try:
            # Check if command requires interactive mode
            if cli_command.get("requires_interactive") and not getattr(
                self.args, "stay", False
            ):
                self._print_error(
                    f"Command /{cli_command['name']} requires interactive mode.\n"
                    f"Start kollabor: kollab\n"
                    f"Then run: /{cli_command['name']} {' '.join(cli_command['args'])}"
                )
                return 2

            # Parse the command through slash parser
            command = self.input_handler.slash_parser.parse_command(cli_command["raw"])
            if not command:
                self._print_error(f"Failed to parse command: {cli_command['raw']}")
                return 2

            # Execute through command executor (includes permission checks!)
            result = await self.input_handler.command_executor.execute_command(
                command, self.event_bus
            )

            # Display result with sanitized output (skip when modal is the display)
            if (
                result
                and result.message
                and not (result.ui_config and result.ui_config.type == "modal")
            ):
                self._print_command_result(result)

            # Determine exit code
            if not result:
                return 1
            if not result.success:
                return 1
            return 0

        except KeyboardInterrupt:
            logger.info("CLI command interrupted by user")
            return 130  # Standard SIGINT exit code
        except Exception as e:
            logger.error(f"CLI command execution failed: {e}", exc_info=True)
            self._print_error(f"Error: {e}")
            return 1

    def _print_command_result(self, result: "CommandResult") -> None:
        """Print command result with sanitized output.

        Strips ANSI escape sequences if not a TTY.
        """
        message = result.message

        # Strip ANSI codes if output is redirected
        if not sys.stdout.isatty():
            # Remove all ANSI escape sequences
            ansi_escape = re.compile(r"\x1b\[[0-9;]*[mKH]|\x1b\][^\x07]*\x07")
            message = ansi_escape.sub("", message)

        # Print with appropriate color if TTY
        if sys.stdout.isatty():
            if result.display_type == "error":
                print(f"\033[31m{message}\033[0m\n")
            elif result.display_type == "warning":
                print(f"\033[38;5;208m{message}\033[0m\n")
            elif result.display_type == "success":
                print(f"\033[32m{message}\033[0m\n")
            else:
                print(message)
        else:
            print(message)

    def _print_error(self, message: str) -> None:
        """Print error message to stderr."""
        if sys.stderr.isatty():
            print(f"\033[31mError:\033[0m {message}", file=sys.stderr)
        else:
            print(f"Error: {message}", file=sys.stderr)

    async def cleanup(self) -> None:
        """Clean up all resources and cancel background tasks.

        This method is guaranteed to run on all exit paths via finally block.
        Ensures no orphaned tasks or resources remain.
        """
        logger.info("Starting application cleanup...")
        self.running = False
        if hasattr(self, "input_handler"):
            self.input_handler.running = False

        # Stop the WidgetStateRefresher background task if running
        # (phase 5 of daemon transparency refactor)
        refresher = getattr(self, "_widget_state_refresher", None)
        if refresher is not None:
            try:
                await refresher.stop()
            except Exception as e:
                logger.debug(f"widget state refresher stop error: {e}")
            self._widget_state_refresher = None

        # Close attach proxy socket if active
        if hasattr(self, "_attach_writer") and self._attach_writer:
            try:
                self._attach_writer.close()
            except Exception:
                pass
            self._attach_writer = None  # type: ignore[assignment]

        # Cancel all tracked background tasks
        if self._background_tasks:
            logger.info(f"Cancelling {len(self._background_tasks)} background tasks")
            for task in self._background_tasks[
                :
            ]:  # Copy list to avoid modification during iteration
                if not task.done():
                    task.cancel()

            # Wait for cancelled tasks individually (asyncio.gather wrapping
            # cancelled tasks creates nested future chains that cause recursive
            # cancel() calls → RecursionError on deep task trees)
            pending = [t for t in self._background_tasks if not t.done()]
            if pending:
                try:
                    done, still_pending = await asyncio.wait(pending, timeout=5.0)
                    if still_pending:
                        logger.warning(
                            f"{len(still_pending)} tasks did not complete within timeout"
                        )
                except Exception as e:
                    logger.error(f"Error during task cleanup: {e}")

        # Clear task list
        self._background_tasks.clear()

        # Mark startup as incomplete
        self._startup_complete = False

        # Call full shutdown to cleanup other resources
        await self.shutdown()

        logger.info("Application cleanup complete")

    def _apply_renderer_config(self) -> None:
        """Apply thinking effect and shimmer config to renderer.

        Called from __init__ and _on_config_reload to avoid duplication.
        """
        if not hasattr(self, "renderer"):
            return

        thinking_effect = self.config.get("terminal.thinking_effect", "shimmer")
        shimmer_speed = self.config.get("terminal.shimmer_speed", 3)
        shimmer_wave_width = self.config.get("terminal.shimmer_wave_width", 4)
        thinking_limit = self.config.get("terminal.thinking_message_limit", 2)

        self.renderer.set_thinking_effect(thinking_effect)
        self.renderer.configure_shimmer(shimmer_speed, shimmer_wave_width)
        self.renderer.configure_thinking_limit(thinking_limit)

    def _apply_ui_theme(self) -> None:
        """Apply UI theme and border style from config.

        Called from __init__ and _on_config_reload to avoid duplication.
        """
        from kollabor_tui.design_system import (
            BORDER_STYLES,
            THEMES,
            set_border_style,
            set_theme,
        )

        ui_theme = self.config.get("kollabor.ui.theme", "dark")
        try:
            set_theme(ui_theme)
            logger.debug(f"UI theme set to: {ui_theme}")
        except ValueError:
            available = list(THEMES.keys())
            logger.warning(
                f"Unknown theme '{ui_theme}', using 'lime'. Available: {available}"
            )

        border_style = self.config.get("kollabor.ui.border_style", "half_blocks")
        if border_style in BORDER_STYLES:
            set_border_style(border_style)
            logger.debug(f"Border style set to: {border_style}")
        else:
            available = list(BORDER_STYLES.keys())
            logger.warning(
                f"Unknown border style '{border_style}', using 'half_blocks'. "
                f"Available: {available}"
            )

    def _on_config_reload(self) -> None:
        """Handle configuration reload (hot reload support).

        Called when configuration changes via /config modal or file watcher.
        Updates all services with new configuration values.
        """
        logger.info("Hot reloading application configuration...")

        # Reload LLM service settings
        if hasattr(self, "llm_service"):
            self.llm_service.reload_config()

        # Reload logging level
        from .logging import set_level

        new_level = self.config.get("logging.level", "INFO")
        set_level(new_level)

        # Reload renderer and UI settings (shared with init)
        self._apply_renderer_config()
        self._apply_ui_theme()

        # Invalidate render cache so new theme colors take effect
        if hasattr(self, "renderer") and self.renderer:
            self.renderer.invalidate_render_cache()

        # Reload terminal width configuration (invalidate cache)
        from kollabor_tui.terminal_state import reload_width_config

        reload_width_config()
        logger.debug("Terminal width configuration reloaded")

        logger.info("Hot reload complete")

    def get_widget_api(self):
        """Get the status widget API for plugin widget registration.

        Plugins can use this API to register their own status widgets
        that users can add to the status area.

        Returns:
            StatusWidgetAPI instance or None if not initialized

        Example usage in a plugin:
            widget_api = self.app.get_widget_api()
            if widget_api:
                widget_api.register_widget(
                    id="my-widget",
                    name="My Widget",
                    description="Shows custom info",
                    render_fn=self._render_widget,
                )
        """
        return getattr(self, "_widget_api", None)

    async def _register_application_hooks(self) -> None:
        """Register application-level event hooks.

        Registers hooks for:
        - SHOW_HELP_OVERLAY: F1 or ? key to show keyboard shortcuts
        - SHOW_FIRST_RUN_HELP: First Tab into navigation mode shows welcome help
        """
        from kollabor_events.models import EventType, Hook, HookPriority

        # Register help overlay handler
        help_overlay_hook = Hook(
            plugin_name="application",
            name="show_help_overlay",
            event_type=EventType.SHOW_HELP_OVERLAY,
            callback=self._handle_show_help_overlay,
            priority=HookPriority.DISPLAY.value,
        )
        await self.event_bus.register_hook(help_overlay_hook)
        logger.debug("Registered SHOW_HELP_OVERLAY hook")

        # Register first-run help handler
        first_run_help_hook = Hook(
            plugin_name="application",
            name="show_first_run_help",
            event_type=EventType.SHOW_FIRST_RUN_HELP,
            callback=self._handle_first_run_help_event,
            priority=HookPriority.DISPLAY.value,
        )
        await self.event_bus.register_hook(first_run_help_hook)
        logger.debug("Registered SHOW_FIRST_RUN_HELP hook")

    async def _handle_show_help_overlay(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle SHOW_HELP_OVERLAY event (triggered by F1 or ? key).

        Shows a modal with keyboard shortcuts for navigation and interaction.
        Detects current mode (INPUT, NAVIGATION, INTERACTION) and displays it.

        Args:
            event_data: Event data (contains 'source' key).
            context: Hook execution context.

        Returns:
            Result dict with success status.
        """
        try:
            from kollabor_events.models import UIConfig
            from kollabor_tui.status import show_help_overlay

            # Determine current mode
            mode = "INPUT"  # Default mode

            if hasattr(self, "navigation_manager") and self.navigation_manager:
                state = self.navigation_manager.state
                if state.interaction_active:
                    mode = "INTERACTION"
                elif state.is_active():
                    mode = "NAVIGATION"

            logger.info(f"Showing help overlay (current mode: {mode})")

            # Create modal callback for showing the help overlay
            async def show_modal_callback(ui_config: UIConfig) -> Dict[str, Any]:
                """Callback to show modal via ModalController."""
                if hasattr(self.input_handler, "_modal_controller"):
                    modal_controller = self.input_handler._modal_controller
                    # Use the modal controller's trigger handler
                    return await modal_controller._handle_modal_trigger(
                        {"ui_config": ui_config}, "help_overlay"
                    )
                return {"success": False, "error": "Modal controller not available"}

            # Show help overlay via the modal system
            result = await show_help_overlay(
                show_modal_callback=show_modal_callback,
                current_mode=mode,
            )

            logger.info(f"Help overlay result: {result}")
            return result

        except Exception as e:
            logger.error(f"Error handling SHOW_HELP_OVERLAY event: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _handle_first_run_help_event(
        self, event_data: Dict[str, Any], context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle SHOW_FIRST_RUN_HELP event (triggered on first Tab into navigation).

        Shows a welcome modal explaining the new interactive status widgets feature.

        Args:
            event_data: Event data (contains 'ui_config' key with modal configuration).
            context: Hook execution context.

        Returns:
            Result dict with success status.
        """
        try:
            ui_config = event_data.get("ui_config")
            if not ui_config:
                return {"success": False, "error": "No ui_config provided"}

            logger.info("Showing first-run help modal via event handler")

            # Show modal via modal controller
            if hasattr(self.input_handler, "_modal_controller"):
                await self.input_handler._modal_controller._enter_modal_mode(ui_config)
                logger.info("First-run help modal displayed successfully")
                return {"success": True}
            else:
                logger.warning("modal_controller not available for first-run help")
                return {"success": False, "error": "Modal controller not available"}

        except Exception as e:
            logger.error(
                f"Error handling SHOW_FIRST_RUN_HELP event: {e}", exc_info=True
            )
            return {"success": False, "error": str(e)}

    async def shutdown(self) -> None:
        """Shutdown the application gracefully."""
        logger.info("Application shutting down")
        self.running = False

        # Emit SYSTEM_SHUTDOWN event for config hooks
        try:
            await self.event_bus.emit_with_hooks(
                EventType.SYSTEM_SHUTDOWN,
                {"source": "application", "reason": "user_exit"},
                "application",
            )
        except Exception as e:
            logger.debug(f"SYSTEM_SHUTDOWN emit failed: {e}")

        # Drain pending async config hook tasks
        if hasattr(self, "_config_hook_loader"):
            try:
                await self._config_hook_loader.shutdown()
            except Exception:
                pass

        # Stop script widget refresh scheduler
        await self.script_widget_manager.shutdown_refresh_scheduler()

        # Stop input handler
        await self.input_handler.stop()

        # Shutdown LLM core service
        await self.llm_service.shutdown()
        await self.conversation_logger.shutdown()
        await self.mcp_integration.shutdown()
        logger.info("LLM core service shutdown complete")

        # Shutdown version check service
        if hasattr(self, "version_check_service"):
            await self.version_check_service.shutdown()
            logger.debug("Version check service shutdown complete")

        # Shutdown all plugins dynamically
        for plugin_name, plugin_instance in self.plugin_instances.items():
            if hasattr(plugin_instance, "shutdown"):
                try:
                    await plugin_instance.shutdown()
                    logger.debug(f"Shutdown plugin: {plugin_name}")
                except Exception as e:
                    logger.warning(f"Error shutting down plugin {plugin_name}: {e}")

        # Only do terminal cleanup if we entered interactive mode
        if getattr(self, "_needs_terminal_cleanup", False):
            # Clear active area (input box) before restoring terminal
            if not self.pipe_mode:
                self.renderer.clear_active_area(force=True)

            # Restore terminal
            self.renderer.exit_raw_mode()
            # Only show cursor if not in pipe mode
            if not self.pipe_mode:
                print("\033[?25h")  # Show cursor

        logger.info("Application shutdown complete")
