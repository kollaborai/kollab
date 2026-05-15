"""LLM Coordinator for Kollab.

Central orchestrator that wires together all LLM subsystems:
kollabor-ai (conversation, API, context), kollabor-agent (tools, MCP, queue),
kollabor-tui (display, status), and kollabor-events (hooks).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, cast

from kollabor_agent import BackgroundTaskManager, NativeToolsHandler
from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.queue_processor import QueueProcessor
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_ai import (
    ConversationManager,
    KollaborConversationLogger,
    ResponseParser,
    SystemPromptBuilder,
)
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.context_injection import ContextService as ContextInjectionService
from kollabor_ai.context_service import ContextService
from kollabor_ai.providers.registry import ProviderRegistry, create_config_from_profile
from kollabor_ai.providers.transformers import ToolCallAccumulator
from kollabor_config import LLMTaskConfig
from kollabor_events import EventType, Hook, HookPriority
from kollabor_events.data_models import ConversationMessage
from kollabor_tui import MessageDisplayService

from .hook_system import LLMHookSystem
from .message_handler import MessageHandler
from .session_manager import SessionManager
from .status_service import StatusService
from .streaming_handler import StreamingHandler

logger = logging.getLogger(__name__)


class LLMService:
    """Core LLM service providing essential language model functionality.

    This service is initialized as a core component and cannot be disabled.
    It manages conversation history, model communication, and intelligent
    conversation logging with memory features.
    """

    def _add_conversation_message(
        self, message_or_role, content=None, parent_uuid=None
    ) -> str:
        """Add a message to both conversation manager and legacy history.

        This wrapper method ensures that messages are added to both the
        ConversationManager and the legacy conversation_history for compatibility.

        Args:
            message_or_role: Either a ConversationMessage object or a role string
            content: Message content (required if first arg is role string)
            parent_uuid: Optional parent UUID for message threading

        Returns:
            UUID of the added message
        """
        from kollabor_events.data_models import ConversationMessage

        # Handle both signatures: ConversationMessage object or separate role/content
        if isinstance(message_or_role, ConversationMessage):
            message = message_or_role
            role = message.role
            content = message.content
        else:
            role = message_or_role
            if content is None:
                raise TypeError("Content is required when role is provided as string")
            message = ConversationMessage(role=role, content=content)

        # Add to conversation manager if available
        if hasattr(self, "conversation_manager") and self.conversation_manager:
            message_uuid = self.conversation_manager.add_message(
                role=role, content=content, parent_uuid=parent_uuid
            )
        else:
            # Fallback - create a UUID if conversation manager not available
            import uuid

            message_uuid = str(uuid.uuid4())

        # conversation_history: primary list used by API calls
        # conversation_manager: adds persistence, UUID tracking, metadata
        # both systems stay synchronized
        self.conversation_history.append(message)

        return cast(str, message_uuid)

    async def inject_system_message(
        self, content: str, subtype: str = "injection"
    ) -> None:
        """Inject a system message into conversation history AND log it.

        Use this instead of raw conversation_history.append() so the
        message appears in the JSONL conversation log for debugging.

        Args:
            content: Message content to inject
            subtype: Message subtype for logging (e.g. 'hub_result', 'nudge')
        """
        from kollabor_events.data_models import ConversationMessage

        self.conversation_history.append(
            ConversationMessage(role="user", content=content)
        )

        if hasattr(self, "conversation_logger") and self.conversation_logger:
            await self.conversation_logger.log_system_message(
                content,
                parent_uuid=getattr(self, "current_parent_uuid", None),
                subtype=subtype,
            )

    async def inject_tool_grant(
        self,
        tool_name: str,
        reason: str = "",
    ) -> None:
        """Inject a tool grant notification for the current agent.

        Called by the plugin system or permission system when a new
        tool becomes available mid-session. Renders the tool's
        documentation from the registry, updates the bundle scope,
        and injects a user message with the [notification] prefix.

        Args:
            tool_name: The registry name of the newly available tool
                (e.g. 'file-read', 'hub-msg').
            reason: Optional explanation for why the tool is being
                granted. E.g. 'MCP server github connected'.
        """
        from kollabor_agent.tool_generators.markdown import render_tool_markdown
        from kollabor_agent.tool_registry import get_registry

        tool = get_registry().get(tool_name)
        if tool is None:
            logger.warning(
                f"inject_tool_grant: unknown tool '{tool_name}', skipping"
            )
            return

        # Update the bundle scope to include the new tool
        current_scope = self.tool_executor._bundle_tools
        if current_scope is not None:
            if tool_name not in current_scope:
                updated = list(current_scope) + [tool_name]
                self.tool_executor.set_bundle_scope(updated)

        await self._refresh_native_tools_after_scope_change()

        # Render docs and inject notification
        docs = render_tool_markdown(tool)
        reason_block = f" ({reason})" if reason else ""

        xml_tag = tool.xml_tag_name
        content = (
            f"[notification] new tool available{reason_block}\n\n"
            f"you now have access to the `{tool_name}` tool.\n\n"
            f"{docs}\n\n"
            f"start using `<{xml_tag}>` from your next turn onwards."
        )

        await self.inject_system_message(content, subtype="tool_grant")
        logger.info(f"Tool grant injected: {tool_name}{reason_block}")

        # Env-notification: tool grant (fire-and-forget)
        try:
            from kollabor_ai.notifications.producer import push_env

            push_env(
                self.event_bus,
                "capability",
                f"+tool:{tool_name}",
                kind="tool_grant",
            )
        except Exception:
            pass

    async def inject_tool_revoke(
        self,
        tool_name: str,
        reason: str = "",
    ) -> None:
        """Inject a tool revoke notification for the current agent.

        Called when a tool becomes unavailable mid-session (plugin
        shutdown, MCP server disconnect, user command). Updates the
        bundle scope and injects a notification.

        Args:
            tool_name: The registry name of the tool being revoked.
            reason: Optional explanation for why.
        """
        # Update the bundle scope to remove the tool
        current_scope = self.tool_executor._bundle_tools
        if current_scope is not None and tool_name in current_scope:
            updated = [t for t in current_scope if t != tool_name]
            self.tool_executor.set_bundle_scope(updated)

        await self._refresh_native_tools_after_scope_change()

        reason_block = f" ({reason})" if reason else ""

        # Find the xml tag for the tool
        xml_tag = tool_name
        try:
            from kollabor_agent.tool_registry import get_registry
            tool = get_registry().get(tool_name)
            if tool:
                xml_tag = tool.xml_tag_name
        except Exception:
            pass

        content = (
            f"[notification] tool revoked{reason_block}\n\n"
            f"you no longer have access to the `{tool_name}` tool. "
            "attempts to use it will return an error. the tool has "
            "been removed from your available tool list.\n\n"
            f"do not emit `<{xml_tag}>` tags in your responses."
        )

        await self.inject_system_message(content, subtype="tool_revoke")
        logger.info(f"Tool revoke injected: {tool_name}{reason_block}")

        # Env-notification: tool revoke (fire-and-forget)
        try:
            from kollabor_ai.notifications.producer import push_env

            push_env(
                self.event_bus,
                "capability",
                f"-tool:{tool_name}",
                kind="tool_revoke",
            )
        except Exception:
            pass

    async def _refresh_native_tools_after_scope_change(self) -> None:
        """Reload native tool schemas after dynamic tool scope changes."""
        native_tools = getattr(self, "_native_tools", None)
        load_tools = getattr(native_tools, "load_tools", None)
        if not callable(load_tools):
            return
        try:
            await load_tools()
        except Exception as e:
            logger.debug("Failed to refresh native tool schemas: %s", e)

    def __init__(
        self,
        config,
        event_bus,
        renderer,
        profile_manager=None,
        agent_manager=None,
        default_timeout: Optional[float] = None,
        enable_metrics: bool = False,
    ):
        """Initialize the core LLM service.

        Args:
            config: Configuration manager instance
            event_bus: Event bus for hook registration
            renderer: Terminal renderer for output
            profile_manager: Profile manager for LLM endpoint profiles
            agent_manager: Agent manager for agent/skill system
            default_timeout: Default timeout for background tasks in seconds
            enable_metrics: Whether to enable detailed task metrics tracking
        """
        # Initialize in logical phases
        self._init_config(
            config,
            default_timeout,
            enable_metrics,
            profile_manager,
            agent_manager,
            event_bus,
            renderer,
        )
        self._init_conversation_system(config)
        self._init_tools_and_parsers(config, event_bus, renderer)
        self._init_api_service(config)
        self._init_stats_and_metrics()
        self._init_components()
        self._init_hooks()

        logger.info("Core LLM Service initialized")

    def _init_config(
        self,
        config,
        default_timeout,
        enable_metrics,
        profile_manager,
        agent_manager,
        event_bus,
        renderer,
    ):
        """Initialize configuration and core dependencies."""
        self.config = config
        self.event_bus = event_bus
        self.renderer = renderer
        self.profile_manager = profile_manager
        self.agent_manager = agent_manager

        # True once the agent has completed at least one turn. Used by
        # auto_grant_mcp_tools to skip boot-time connects (tools are already
        # in the initial system prompt) and only fire per-tool grants for
        # MCP servers that connect mid-conversation.
        self._first_turn_complete = False

        # Timeout and metrics configuration
        self.default_timeout = default_timeout
        self.enable_metrics = enable_metrics

        # Load LLM configuration from kollabor.llm section (API details handled by API service)
        self.max_history = config.get("kollabor.llm.max_history", 999)

        # Load task management configuration using structured dataclass
        task_config_dict = config.get("kollabor.llm.task_management", {})
        self.task_config = LLMTaskConfig.from_dict(task_config_dict)

    def _init_conversation_system(self, config):
        """Initialize conversation state, logger, and manager."""
        from kollabor_config.config_utils import get_conversations_dir

        # Conversation state
        self.conversation_history: List[ConversationMessage] = []
        # Note: max_queue_size is now owned by QueueProcessor, accessed via property

        # Initialize conversation logger with intelligence features
        conversations_dir = get_conversations_dir()
        conversations_dir.mkdir(parents=True, exist_ok=True)

        # Initialize raw conversation logging directory (inside conversations/)
        self.raw_conversations_dir = conversations_dir / "raw"
        self.raw_conversations_dir.mkdir(parents=True, exist_ok=True)
        self.conversation_logger = KollaborConversationLogger(conversations_dir)

        # Set conversations_dir on config for ConversationManager (kollabor-ai needs it externally)
        self.config._conversations_dir = conversations_dir

        # Initialize conversation manager for advanced features
        self.conversation_manager = ConversationManager(
            config=self.config, conversation_logger=self.conversation_logger
        )

    def _init_tools_and_parsers(self, config, event_bus, renderer):
        """Initialize hook system, MCP integration, response parser, tool executor, and display/context services."""
        # Initialize hook system
        self.hook_system = LLMHookSystem(event_bus)

        # Initialize MCP integration and tool components
        self.mcp_integration = MCPIntegration(
            event_bus=event_bus,
            agent_manager=self.agent_manager,
        )
        self.response_parser = ResponseParser()
        self.tool_executor = ToolExecutor(
            mcp_integration=self.mcp_integration,
            event_bus=event_bus,
            terminal_timeout=config.get("kollabor.llm.terminal_timeout", 120),
            mcp_timeout=config.get("kollabor.llm.mcp_timeout", 120),
            renderer=renderer,  # Pass renderer for tool execution state
        )
        # Wire up cancellation callback so tool executor can check for user cancellation
        self.tool_executor.set_cancel_callback(lambda: self.cancel_processing)

        # Register as services so plugins can access them
        event_bus.register_service("response_parser", self.response_parser)
        event_bus.register_service("tool_executor", self.tool_executor)

        # Initialize message display service (KISS/DRY: eliminates duplicated display code)
        self.message_display_service = MessageDisplayService(renderer)

        # Old keyword-trigger context service (kept for backward compat)
        self.context_service = ContextInjectionService(
            config=self.config,
            conversation_manager=self.conversation_manager,
            event_bus=event_bus,
        )

        # New ledger-based context service — registered on event bus
        self._context_ledger = ContextService()
        self._context_ledger.set_event_bus(event_bus)
        event_bus.register_service("context_service", self._context_ledger)

    def _init_api_service(self, config):
        """Initialize API communication service with profile resolution."""
        # Get active profile for API service (fallback to minimal default if no profile manager)
        if self.profile_manager:
            api_profile = self.profile_manager.get_active_profile()
        else:
            # Fallback: create minimal default profile (profile_manager should always exist)
            from kollabor_ai import LLMProfile

            api_profile = LLMProfile(
                name="default",
                provider="custom",
                base_url="http://localhost:1234",
                model="default",
                temperature=0.7,
            )

        # Initialize API communication service (KISS: pure API communication separation)
        self.api_service = APICommunicationService(
            config, self.raw_conversations_dir, api_profile
        )

        # Link session ID for raw log correlation
        self.api_service.set_session_id(self.conversation_logger.session_id)

    def _init_components(self):
        """Initialize all extracted components (NativeToolsHandler, SystemPromptBuilder, QueueProcessor, etc)."""
        config = self.config

        # Native tools handler (owns MCP discovery, tool loading, tool execution)
        self._native_tools = NativeToolsHandler(
            mcp_integration=self.mcp_integration,
            profile_manager=self.profile_manager,
            api_service=self.api_service,
            config=config,
        )

        # Track current message threading
        self.current_parent_uuid = None

        # System prompt builder (owns prompt construction + plugin prompt additions)
        self._prompt_builder = SystemPromptBuilder(
            config=self.config,
            agent_manager=self.agent_manager,
            profile_manager=self.profile_manager,
            conversation_logger=self.conversation_logger,
        )

        # Question gate: pending tools queue
        # When agent uses <question> tag, tool calls are suspended here
        # and injected when user responds
        self.pending_tools: List[Dict[str, Any]] = []
        self.question_gate_active = False
        self.question_gate_enabled = config.get(
            "kollabor.llm.question_gate_enabled", True
        )
        self.wait_for_user_enabled = config.get(
            "plugins.hub.wait_for_user_enabled", True
        )

        # Provider system integration (wrapper pattern - LEGACY mode for backward compatibility)
        self._provider_registry = ProviderRegistry
        self._tool_accumulator = ToolCallAccumulator(legacy_mode=True)
        self._provider_lock = asyncio.Lock()
        self._current_provider = None

        # Queue overflow metrics counters (shared between task manager and queue processor)
        self._queue_metrics = {
            "drop_oldest_count": 0,
            "drop_newest_count": 0,
            "block_count": 0,
            "block_timeout_count": 0,
            "total_enqueue_attempts": 0,
            "total_enqueue_successes": 0,
        }

        # Streaming handler (owns streaming state, thinking display, LLM call orchestration)
        self._streaming = StreamingHandler(
            api_service=self.api_service,
            message_display_service=self.message_display_service,
            renderer=self.renderer,
        )

        # Background task manager (owns task tracking, circuit breaker, monitoring)
        self._task_manager = BackgroundTaskManager(
            task_config=self.task_config,
            queue_metrics=self._queue_metrics,
            enable_metrics=self.enable_metrics,
        )

        # Session manager (owns conversation init, restart, context setup)
        self._session = SessionManager(
            conversation_logger=self.conversation_logger,
            conversation_manager=self.conversation_manager,
            config=self.config,
            event_bus=self.event_bus,
            api_service=self.api_service,
            prompt_builder=self._prompt_builder,
        )

        # Queue processor (owns message queue, overflow strategies, LLM turns)
        self._queue_processor = QueueProcessor(
            conversation_history=self.conversation_history,
            session_stats=self.session_stats,
            stats=self.stats,
            pending_tools=self.pending_tools,
            queue_metrics=self._queue_metrics,
            task_config=self.task_config,
            api_service=self.api_service,
            tool_executor=self.tool_executor,
            response_parser=self.response_parser,
            message_display_service=self.message_display_service,
            renderer=self.renderer,
            config=self.config,
            event_bus=self.event_bus,
            conversation_logger=self.conversation_logger,
            streaming_handler=self._streaming,
            native_tools_handler=self._native_tools,
            add_message_fn=self._add_conversation_message,
            max_history=self.max_history,
            question_gate_enabled=self.question_gate_enabled,
            max_queue_size=self.task_config.queue.max_size,
            wait_for_user_enabled=self.wait_for_user_enabled,
        )

        # Message handler (owns event/message handling methods)
        self._message_handler = MessageHandler(coordinator=self)

        # Status service (owns status line generation and queue metrics)
        self._status_service = StatusService(coordinator=self)

    def _init_hooks(self):
        """Create hooks for LLM service (delegated to MessageHandler)."""
        self.hooks = [
            Hook(
                name="inject_context",
                plugin_name="llm_core",
                event_type=EventType.USER_INPUT,
                priority=HookPriority.PREPROCESSING.value,
                callback=self._handle_context_injection,
            ),
            Hook(
                name="process_user_input",
                plugin_name="llm_core",
                event_type=EventType.USER_INPUT,
                priority=HookPriority.LLM.value,
                callback=self._handle_user_input,
            ),
            Hook(
                name="cancel_request",
                plugin_name="llm_core",
                event_type=EventType.CANCEL_REQUEST,
                priority=HookPriority.SYSTEM.value,
                callback=self._handle_cancel_request,
            ),
            Hook(
                name="add_message_handler",
                plugin_name="llm_core",
                event_type=EventType.ADD_MESSAGE,
                priority=HookPriority.LLM.value,
                callback=self._handle_add_message,
            ),
            Hook(
                name="trigger_llm_continue",
                plugin_name="llm_core",
                event_type=EventType.TRIGGER_LLM_CONTINUE,
                priority=HookPriority.LLM.value,
                callback=self._handle_llm_continue,
            ),
        ]

    def _init_stats_and_metrics(self):
        """Initialize session statistics and processing state tracking."""
        # Session statistics
        self.stats = {
            "total_messages": 0,
            "total_thinking_time": 0.0,
            "sessions_count": 0,
            "last_session": None,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }

        self.session_stats = {
            "input_tokens": 0,  # Last request input tokens (context size)
            "output_tokens": 0,  # Last request output tokens
            "total_input_tokens": 0,  # Cumulative session input
            "total_output_tokens": 0,  # Cumulative session output
            "messages": 0,
        }

        # Current processing state
        self.current_processing_tokens = 0
        self.processing_start_time = None

    # Forwarding properties for QueueProcessor state (owned by QueueProcessor)
    @property
    def processing_queue(self):
        return self._queue_processor.processing_queue

    @property
    def max_queue_size(self) -> int:
        return cast(int, self._queue_processor.max_queue_size)

    @property
    def dropped_messages(self) -> int:
        return cast(int, self._queue_processor.dropped_messages)

    @dropped_messages.setter
    def dropped_messages(self, value: int):
        self._queue_processor.dropped_messages = value

    @property
    def is_processing(self) -> bool:
        return cast(bool, self._queue_processor.is_processing)

    @is_processing.setter
    def is_processing(self, value: bool):
        self._queue_processor.is_processing = value

    @property
    def turn_completed(self) -> bool:
        return cast(bool, self._queue_processor.turn_completed)

    @turn_completed.setter
    def turn_completed(self, value: bool):
        self._queue_processor.turn_completed = value
        # One-way latch: once a turn has completed, never unset. Consumed
        # by auto_grant_mcp_tools to distinguish boot-time MCP connects
        # (skip grants — tools are in initial prompt) from mid-session
        # connects (fire grants per tool).
        if value:
            self._first_turn_complete = True

    @property
    def cancel_processing(self) -> bool:
        return cast(bool, self._queue_processor.cancel_processing)

    @cancel_processing.setter
    def cancel_processing(self, value: bool):
        self._queue_processor.cancel_processing = value

    @property
    def cancellation_message_shown(self) -> bool:
        return cast(bool, self._queue_processor.cancellation_message_shown)

    @cancellation_message_shown.setter
    def cancellation_message_shown(self, value: bool):
        self._queue_processor.cancellation_message_shown = value

    @property
    def current_processing_tokens(self) -> int:
        return cast(int, self._queue_processor.current_processing_tokens)

    @current_processing_tokens.setter
    def current_processing_tokens(self, value: int):
        if hasattr(self, "_queue_processor"):
            self._queue_processor.current_processing_tokens = value

    @property
    def processing_start_time(self):
        return self._queue_processor.processing_start_time

    @processing_start_time.setter
    def processing_start_time(self, value):
        if hasattr(self, "_queue_processor"):
            self._queue_processor.processing_start_time = value

    @property
    def question_gate_active(self) -> bool:
        return cast(bool, self._queue_processor.question_gate_active)

    @question_gate_active.setter
    def question_gate_active(self, value: bool):
        if hasattr(self, "_queue_processor"):
            self._queue_processor.question_gate_active = value
        # During init, no-op (will be set in QueueProcessor.__init__)

    async def initialize(self) -> bool:
        """Initialize the LLM service components."""
        # Initialize API communication service (KISS refactoring)
        await self.api_service.initialize()

        # Initialize provider system (wrapper pattern - transparent integration)
        await self._initialize_provider()

        # Register hooks
        await self.hook_system.register_hooks()

        # Discover MCP servers in background (non-blocking startup)
        # This allows the UI to start immediately while MCP servers connect
        try:
            self.create_background_task(
                self._background_mcp_discovery(), name="mcp_discovery"
            )
        except Exception as e:
            # Log but don't fail startup - MCP discovery is non-critical
            logger.warning(f"Failed to start background MCP discovery: {e}")

        # Initialize conversation with context
        await self._initialize_conversation()

        # Set conversation context before logging start
        self._set_conversation_context()

        # Log conversation start
        await self.conversation_logger.log_conversation_start()

        # Start task monitoring
        if self.task_config.background_tasks.enable_monitoring:
            await self.start_task_monitor()

        logger.info("Core LLM Service initialized and ready")
        return True

    # --- SessionManager forwarding methods ---

    async def _initialize_conversation(self):
        """Initialize conversation with project context."""
        if self.conversation_logger and hasattr(self.conversation_logger, "session_id"):
            self._prompt_builder.set_session_id(self.conversation_logger.session_id)
        parent_uuid = await self._session.initialize_conversation(
            conversation_history=self.conversation_history,
            add_message_fn=self._add_conversation_message,
        )
        if parent_uuid is not None:
            self.current_parent_uuid = parent_uuid

    async def restart_session(self) -> dict:
        """Restart the conversation session - save current, start fresh."""
        self.current_parent_uuid = None
        return cast(
            dict,
            await self._session.restart_session(
                conversation_history=self.conversation_history,
                add_message_fn=self._add_conversation_message,
            ),
        )

    async def _initialize_provider(self) -> None:
        """Initialize provider from active profile.

        Wrapper pattern integration: Uses provider system transparently.
        Falls back to legacy HTTP system if provider fails.
        """
        try:
            if self.profile_manager:
                profile = self.profile_manager.get_active_profile()
                provider_config = create_config_from_profile(profile.to_dict())

                async with self._provider_lock:
                    self._current_provider = await self._provider_registry.get_provider(
                        provider_config
                    )

                logger.info(
                    f"Provider initialized: {self._current_provider.provider_name} "
                    f"(model={self._current_provider.model})"
                )
        except Exception as e:
            logger.warning(f"Provider initialization failed, using legacy system: {e}")
            self._current_provider = None

    async def switch_profile(self, profile_name: str) -> bool:
        """Switch to a different profile with thread-safe provider reinitialization.

        Wrapper pattern: Updates provider system transparently while maintaining
        backward compatibility with legacy HTTP system.

        Args:
            profile_name: Name of the profile to switch to

        Returns:
            True if switch successful, False otherwise
        """
        async with self._provider_lock:
            try:
                if not self.profile_manager:
                    logger.error("Cannot switch profile: no profile manager")
                    return False

                # Get the profile
                profile = self.profile_manager.get_profile(profile_name)
                if not profile:
                    logger.error(f"Profile not found: {profile_name}")
                    return False

                # Auto-refresh OAuth tokens and resolve model before switching
                if profile.auth_type == "oauth":
                    try:
                        from kollabor_ai.oauth import OAuthTokenStorage

                        storage = OAuthTokenStorage()
                        tokens = await storage.load_tokens("openai", auto_refresh=True)
                        if tokens:
                            if tokens.access_token != profile.api_key:
                                profile.api_key = tokens.access_token
                                logger.info(
                                    "OAuth token refreshed during profile switch"
                                )
                            if tokens.account_id:
                                if not profile.extra_headers:
                                    profile.extra_headers = {}
                                profile.extra_headers["ChatGPT-Account-Id"] = (
                                    tokens.account_id
                                )
                            # Resolve generic model name
                            if profile.model in ("codex", ""):
                                from kollabor_ai.oauth.openai_oauth import (
                                    pick_best_model,
                                    query_codex_models,
                                )

                                models = await query_codex_models(
                                    tokens.access_token, tokens.account_id
                                )
                                resolved = pick_best_model(models)
                                if resolved != "codex":
                                    profile.model = resolved
                                    logger.info(f"Resolved model to: {resolved}")
                    except Exception as e:
                        logger.warning(f"OAuth refresh during switch failed: {e}")
                        self.renderer.message_coordinator.display_message_sequence(
                            [
                                (
                                    "system",
                                    f"oauth refresh failed: {e} — using cached token",
                                    {"display_type": "warning"},
                                )
                            ]
                        )

                # Update API service with new profile
                self.api_service.update_from_profile(profile)
                self.conversation_logger.set_provider(profile.provider)

                # Reinitialize provider with new profile
                provider_config = create_config_from_profile(profile.to_dict())
                self._current_provider = await self._provider_registry.get_provider(
                    provider_config
                )

                logger.info(
                    f"Switched to profile '{profile_name}' "
                    f"(provider={self._current_provider.provider_name}, "
                    f"model={self._current_provider.model})"
                )
                return True

            except Exception as e:
                logger.error(f"Failed to switch profile to '{profile_name}': {e}")
                return False

    def _set_conversation_context(self):
        """Set dynamic context on conversation logger before logging start."""
        self._session.set_conversation_context()

    # --- QueueProcessor forwarding methods ---

    async def _enqueue_with_overflow_strategy(self, message: str) -> None:
        """Enqueue message with overflow strategy. Delegates to QueueProcessor."""
        await self._queue_processor.enqueue(message)

    async def _process_queue(self):
        """Process queued messages. Delegates to QueueProcessor."""
        await self._queue_processor.process_queue(
            task_manager=self._task_manager,
            process_message_batch_fn=self._process_message_batch,
            continue_conversation_fn=self._continue_conversation,
        )

    async def _process_message_batch(self, messages: List[str]):
        """Process a batch of messages. Delegates to QueueProcessor."""
        self.current_parent_uuid = await self._queue_processor.process_message_batch(
            messages=messages,
            current_parent_uuid=self.current_parent_uuid,
        )

    async def _continue_conversation(self):
        """Continue an ongoing conversation. Delegates to QueueProcessor."""
        self.current_parent_uuid = await self._queue_processor.continue_conversation(
            current_parent_uuid=self.current_parent_uuid,
        )

    # -- Forwarding methods to BackgroundTaskManager --

    def create_background_task(self, coro, name: str | None = None) -> asyncio.Task:
        """Create and track a background task. Delegates to BackgroundTaskManager."""
        return cast(asyncio.Task, self._task_manager.create_background_task(coro, name))

    async def start_task_monitor(self):
        """Start background task monitoring. Delegates to BackgroundTaskManager."""
        await self._task_manager.start_task_monitor()

    async def get_task_status(self):
        """Get status of all background tasks. Delegates to BackgroundTaskManager."""
        return await self._task_manager.get_task_status()

    async def cancel_all_tasks(self):
        """Cancel all background tasks. Delegates to BackgroundTaskManager."""
        await self._task_manager.cancel_all_tasks()

    async def wait_for_tasks(self, timeout: float = 30.0):
        """Wait for all background tasks. Delegates to BackgroundTaskManager."""
        await self._task_manager.wait_for_tasks(timeout)

    # --- SystemPromptBuilder forwarding methods ---

    def set_plugin_instances(self, plugin_instances: Dict[str, Any]) -> None:
        """Set plugin instances reference for system prompt additions."""
        self._prompt_builder.set_plugin_instances(plugin_instances)
        # Sync session ID in case it was set before plugins were loaded
        if self.conversation_logger and hasattr(self.conversation_logger, "session_id"):
            self._prompt_builder.set_session_id(self.conversation_logger.session_id)

    def _build_system_prompt(self) -> str:
        """Build system prompt from file or agent."""
        return cast(str, self._prompt_builder.build())

    def rebuild_system_prompt(self) -> bool:
        """Rebuild the system prompt and update conversation history."""
        return cast(bool, self._prompt_builder.rebuild(self.conversation_history))

    # --- NativeToolsHandler forwarding properties and methods ---

    @property
    def native_tools(self):
        """Native tool definitions for API function calling."""
        return self._native_tools.tools

    @native_tools.setter
    def native_tools(self, value):
        """Set native tool definitions."""
        self._native_tools.tools = value

    @property
    def native_tool_calling_enabled(self):
        """Whether native tool calling is enabled."""
        return self._native_tools.tool_calling_enabled

    @native_tool_calling_enabled.setter
    def native_tool_calling_enabled(self, value):
        """Set native tool calling enabled flag."""
        self._native_tools.tool_calling_enabled = value

    @property
    def mcp_discovery_complete(self):
        """Event signaling MCP discovery is complete."""
        return self._native_tools.discovery_complete

    async def _background_mcp_discovery(self) -> None:
        """Discover MCP servers in background."""
        await self._native_tools.background_discovery()

    async def _load_native_tools(self) -> None:
        """Load MCP tools for native API function calling."""
        await self._native_tools.load_tools()

    async def _execute_native_tool_calls(self) -> List[Any]:
        """Execute tool calls from native API response."""
        return cast(
            List[Any], await self._native_tools.execute_tool_calls(self.tool_executor)
        )

    async def process_user_input(self, message: str) -> Dict[str, Any]:
        """Process user input through the LLM.

        This is the main entry point for user messages.

        Args:
            message: User's input message

        Returns:
            Status information about processing
        """
        # Display user message using MessageDisplayService (DRY refactoring)
        logger.debug(
            f"DISPLAY DEBUG: About to display user message: '{message[:100]}...' ({len(message)} chars)"
        )
        self.message_display_service.display_user_message(message)

        # Question gate: if enabled and there are pending tools, execute them now
        # and inject results into conversation before processing user message
        tool_injection_results = None
        if (
            self.question_gate_enabled
            and self.question_gate_active
            and self.pending_tools
        ):
            # Snapshot before any await — another coroutine could mutate
            # pending_tools while we're suspended inside execute_all_tools.
            pending_snapshot = list(self.pending_tools)
            self.pending_tools.clear()
            self.question_gate_active = False

            logger.info(
                f"Question gate: executing {len(pending_snapshot)} suspended tool(s)"
            )

            # Show tool execution indicator (prevents UI freeze appearance)
            tool_count = len(pending_snapshot)
            tool_desc = (
                pending_snapshot[0].get("type", "tool")
                if tool_count == 1
                else f"{tool_count} tools"
            )
            self.renderer.update_thinking(True, f"Executing {tool_desc}...")

            tool_injection_results = await self.tool_executor.execute_all_tools(
                pending_snapshot
            )

            # Stop tool execution indicator
            self.renderer.update_thinking(False)

            # Display and log tool results
            if tool_injection_results:
                self.message_display_service.display_complete_response(
                    thinking_duration=0,
                    response="",
                    tool_results=tool_injection_results,
                    original_tools=pending_snapshot,
                )

                # Add tool results to conversation history
                batched_tool_results = []
                for result in tool_injection_results:
                    await self.conversation_logger.log_system_message(
                        (
                            f"Executed {result.tool_type} ({result.tool_id}): "
                            f"{result.output if result.success else result.error}"
                        ),
                        parent_uuid=self.current_parent_uuid,
                        subtype="tool_result",
                        tool_use_id=result.tool_id,
                    )

                    # Collect tool results for batching
                    tool_context = self.tool_executor.format_result_for_conversation(
                        result
                    )
                    batched_tool_results.append(f"Tool result: {tool_context}")

                # Add all tool results as single conversation message
                if batched_tool_results:
                    self._add_conversation_message(
                        ConversationMessage(
                            role="user", content="\n".join(batched_tool_results)
                        )
                    )

            # Clear question gate state
            self.pending_tools.clear()
            self.question_gate_active = False
            logger.info("Question gate: cleared after tool execution")

        # Reset turn_completed flag
        self.turn_completed = False
        self.cancel_processing = False
        self.cancellation_message_shown = False

        # Log user message
        self.current_parent_uuid = await self.conversation_logger.log_user_message(
            message, parent_uuid=self.current_parent_uuid
        )

        # Add to processing queue with overflow handling
        await self._enqueue_with_overflow_strategy(message)

        # Start processing if not already running
        if not self.is_processing:
            self.create_background_task(self._process_queue(), name="process_queue")

        return {
            "status": "queued",
            "tools_injected": (
                len(tool_injection_results) if tool_injection_results else 0
            ),
        }

    # --- MessageHandler delegation methods ---

    async def _handle_context_injection(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle context injection before user input processing (delegated)."""
        return cast(
            Dict[str, Any],
            await self._message_handler.handle_context_injection(data, event),
        )

    async def _handle_user_input(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle user input hook callback (delegated)."""
        return cast(
            Dict[str, Any], await self._message_handler.handle_user_input(data, event)
        )

    async def _handle_cancel_request(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle cancel request hook callback (delegated)."""
        return cast(
            Dict[str, Any],
            await self._message_handler.handle_cancel_request(data, event),
        )

    async def _handle_add_message(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle ADD_MESSAGE event - inject messages into conversation (delegated)."""
        return cast(
            Dict[str, Any], await self._message_handler.handle_add_message(data, event)
        )

    async def _handle_llm_continue(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle TRIGGER_LLM_CONTINUE event - trigger LLM to process injected messages (delegated)."""
        return cast(
            Dict[str, Any], await self._message_handler.handle_llm_continue(data, event)
        )

    async def register_hooks(self) -> None:
        """Register LLM service hooks with the event bus."""
        for hook in self.hooks:
            await self.event_bus.register_hook(hook)
        logger.info(f"Registered {len(self.hooks)} hooks for LLM core service")

    async def register_cancel_hook(self) -> None:
        """Register only the cancel hook (for attach-mode clients).

        In attach mode the client skips full hook registration to avoid
        competing with the remote daemon for USER_INPUT / TRIGGER_LLM_CONTINUE.
        The cancel hook is still needed so ESC can forward the request to the
        daemon via RPC.
        """
        cancel_hook = next(
            (h for h in self.hooks if h.name == "cancel_request"), None
        )
        if cancel_hook:
            await self.event_bus.register_hook(cancel_hook)
            logger.info("Registered cancel hook for attach-mode client")

    def cancel_current_request(self):
        """Cancel the current processing request."""
        if self.is_processing:
            self.cancel_processing = True
            # Cancel API request through API service (KISS refactoring)
            self.api_service.cancel_current_request()
            logger.info("Processing cancellation requested")

    # --- StreamingHandler forwarding methods ---

    async def _call_llm(self) -> str:
        """Make API call to LLM using StreamingHandler."""
        return cast(
            str,
            await self._streaming.call_llm(
                conversation_history=self.conversation_history,
                max_history=self.max_history,
                native_tools=self.native_tools,
                mcp_discovery_complete=self.mcp_discovery_complete,
                is_cancelled_fn=lambda: self.cancel_processing,
            ),
        )

    async def _handle_streaming_chunk(self, chunk: str) -> None:
        """Handle streaming content chunk from API."""
        await self._streaming.handle_chunk(chunk)

    def _cleanup_streaming_state(self) -> None:
        """Clean up streaming state after request completion or failure."""
        self._streaming.cleanup()

    def reload_config(self) -> None:
        """Reload configuration values from config service (hot reload support).

        Called when configuration changes via /config modal or file watcher.
        Re-reads all cached config values to apply changes without restart.
        """
        logger.info("Hot reloading LLM configuration...")

        # Reload LLM settings
        self.max_history = self.config.get("kollabor.llm.max_history", 999)

        # Reload tool executor timeouts
        self.tool_executor.terminal_timeout = self.config.get(
            "kollabor.llm.terminal_timeout", 120
        )
        self.tool_executor.mcp_timeout = self.config.get(
            "kollabor.llm.mcp_timeout", 120
        )

        # Reload streaming setting
        self.api_service.enable_streaming = self.config.get(
            "kollabor.llm.enable_streaming", False
        )

        # Question gate + wait-for-user: cached at init; file save + in-memory
        # config update these keys, but QueueProcessor uses cached copies until
        # reload_config runs (triggered by ConfigAltView after Ctrl+S save).
        self.question_gate_enabled = self.config.get(
            "kollabor.llm.question_gate_enabled", True
        )
        self.wait_for_user_enabled = self.config.get(
            "plugins.hub.wait_for_user_enabled", True
        )
        if getattr(self, "_queue_processor", None) is not None:
            self._queue_processor.question_gate_enabled = self.question_gate_enabled
            self._queue_processor.wait_for_user_enabled = (
                self.wait_for_user_enabled
            )

        # Note: processing_delay and thinking_delay are already read dynamically each call

        logger.info(
            f"Config reloaded: max_history={self.max_history}, "
            f"terminal_timeout={self.tool_executor.terminal_timeout}, "
            f"mcp_timeout={self.tool_executor.mcp_timeout}, "
            f"streaming={self.api_service.enable_streaming}, "
            f"question_gate={self.question_gate_enabled}, "
            f"wait_for_user={self.wait_for_user_enabled}"
        )

    # --- StatusService delegation methods ---

    def get_status_line(self) -> Dict[str, List[str]]:
        """Get status information for display (delegated)."""
        return cast(Dict[str, List[str]], self._status_service.get_status_line())

    def get_queue_metrics(self) -> dict:
        """Get comprehensive queue metrics for monitoring (delegated)."""
        return cast(dict, self._status_service.get_queue_metrics())

    def reset_queue_metrics(self):
        """Reset queue metrics (for testing or maintenance) (delegated)."""
        self._status_service.reset_queue_metrics()

    async def shutdown(self):
        """Shutdown the LLM service."""
        # Log conversation end
        await self.conversation_logger.log_conversation_end()

        # Cancel all background tasks
        await self.cancel_all_tasks()

        # Stop task monitoring
        monitoring_task = self._task_manager._monitoring_task
        if monitoring_task and not monitoring_task.done():
            monitoring_task.cancel()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass

        # Shutdown API communication service (KISS refactoring)
        await self.api_service.shutdown()

        # Shutdown provider system (wrapper pattern cleanup)
        try:
            await self._provider_registry.shutdown_all()
            logger.info("Provider system shutdown complete")
        except Exception as e:
            logger.warning(f"Provider shutdown error: {e}")

        # Shutdown MCP integration
        try:
            await self.mcp_integration.shutdown()
            logger.info("MCP integration shutdown complete")
        except Exception as e:
            logger.warning(f"MCP shutdown error: {e}")

        logger.info("Core LLM Service shutdown complete")
