"""LLM Coordinator for Kollab.

Central orchestrator that wires together all LLM subsystems:
kollabor-ai (conversation, API, context), kollabor-agent (tools, MCP, queue),
kollabor-tui (display, status), and kollabor-events (hooks).
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, cast

from kollabor.user_input_source import UserInputSource
from kollabor_agent import BackgroundTaskManager, NativeToolsHandler
from kollabor_agent.execution_context import remote_task_id
from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.queue_processor import CancellationOrigin, QueuedInput, QueueProcessor
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
from kollabor_ai.message_content import (
    EphemeralImageStore,
    MessageContent,
    MessageContentError,
    combine_message_contents,
    contains_image_content,
    content_to_text,
    normalize_message_content,
)
from kollabor_ai.providers.registry import ProviderRegistry, create_config_from_profile
from kollabor_ai.providers.transformers import ToolCallAccumulator
from kollabor_config import LLMTaskConfig
from kollabor_events import EventType, Hook, HookPriority
from kollabor_events.data_models import ConversationMessage
from kollabor_tui import MessageDisplayService

from .agent_hud import (
    AgentHudEntry,
    format_agent_hud,
    merge_agent_hud_with_user_message,
    normalize_hud_label,
)
from .hook_system import LLMHookSystem
from .message_handler import MessageHandler
from .session_manager import SessionManager
from .status_service import StatusService
from .streaming_handler import StreamingHandler

logger = logging.getLogger(__name__)

_ACTIVE_OPERATION_PHASES = frozenset(
    {
        "provider_request",
        "provider_streaming",
        "provider_retry_wait",
        "provider_response",
        "provider_failed",
        "provider_cancelled",
        "tool_permission",
        "tool_execution",
        "tool_complete",
        "cancellation_cleanup",
    }
)
_ACTIVE_OPERATION_CLEANUP_STATES = frozenset(
    {"requested", "pending", "complete", "failed", "superseded"}
)


class LLMService:
    """Core LLM service providing essential language model functionality.

    This service is initialized as a core component and cannot be disabled.
    It manages conversation history, model communication, and intelligent
    conversation logging with memory features.
    """

    def _add_conversation_message(
        self, message_or_role, content=None, parent_uuid=None, metadata=None
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
            message = ConversationMessage(
                role=role, content=content, metadata=dict(metadata or {})
            )

        # Add to conversation manager if available
        if hasattr(self, "conversation_manager") and self.conversation_manager:
            message_uuid = self.conversation_manager.add_message(
                role=role,
                # ConversationManager has legacy text-search/reporting helpers;
                # the canonical structured message remains in conversation_history.
                content=content_to_text(content),
                parent_uuid=parent_uuid,
                metadata=dict(getattr(message, "metadata", None) or metadata or {}),
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
        """Queue an agent HUD entry and log it.

        Use this for non-human runtime context. It must not create a
        standalone model turn; queued HUD diffs are merged into the next
        real user or actionable hub turn.

        Args:
            content: Message content to inject
            subtype: Message subtype for logging (e.g. 'hub_result', 'nudge')
        """
        normalized_subtype = normalize_hud_label(subtype, fallback="injection")
        entry = self.queue_agent_hud(
            section="system",
            label=normalized_subtype,
            content=content,
        )
        formatted_content = format_agent_hud([entry]) if entry else ""

        if (
            formatted_content
            and hasattr(self, "conversation_logger")
            and self.conversation_logger
        ):
            await self.conversation_logger.log_system_message(
                formatted_content,
                parent_uuid=getattr(self, "current_parent_uuid", None),
                subtype=normalized_subtype,
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
            logger.warning(f"inject_tool_grant: unknown tool '{tool_name}', skipping")
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
        # Cancellation cleanup tasks are keyed to their generation. Relay
        # admission waits for all in-flight cleanup so an older background
        # cancel cannot reach a later remote turn.
        self._cancellation_cleanup_tasks: dict[int, asyncio.Task | None] = {}

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
        self._initialize_active_operation_tracking()

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

    def _initialize_active_operation_tracking(self) -> None:
        self._active_operation_generation = 0
        self._active_operation: dict[str, Any] = {
            "task_id": None,
            "generation": 0,
            "phase": "idle",
            "provider": None,
            "request_id": None,
            "tool_name": None,
            "tool_call_id": None,
            "tool_call_id_generated": False,
            "cancel_generation": None,
            "cleanup_state": "none",
        }

    def _init_conversation_system(self, config):
        """Initialize conversation state, logger, and manager."""
        from kollabor_config.config_utils import get_conversations_dir

        # Conversation state
        self.conversation_history: List[ConversationMessage] = []
        self._image_store = EphemeralImageStore()
        self._pending_agent_hud: List[AgentHudEntry] = []
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
        self.tool_executor.set_operation_observer(self._observe_tool_operation)

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
        self.api_service.set_media_resolver(self.resolve_media)
        self.api_service.set_operation_observer(self._observe_provider_operation)

    @staticmethod
    def _safe_operation_task_id(task_id: Any) -> str | None:
        if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
            return None
        return task_id

    @staticmethod
    def _safe_operation_provider(provider: Any) -> str | None:
        from kollabor_ai.providers.models import ProviderType

        provider_id = getattr(provider, "value", provider)
        if not isinstance(provider_id, str):
            return None
        allowed = {entry.value for entry in ProviderType}
        return provider_id if provider_id in allowed else None

    @staticmethod
    def _safe_operation_tool_name(tool_data: Any) -> str | None:
        if not isinstance(tool_data, dict):
            return None
        candidates = []
        tool_type = tool_data.get("type")
        if isinstance(tool_type, str):
            candidates.append(tool_type.replace("-", "_"))
        if isinstance(tool_type, str) and tool_type in {"mcp_tool", "mcp-tool"}:
            name = tool_data.get("name")
            if isinstance(name, str):
                candidates.append(name)

        try:
            from kollabor_agent.tool_registry import get_registry

            registry = get_registry()
            for candidate in candidates:
                definition = registry.get_by_native_name(candidate)
                if definition is None:
                    definition = registry.get(candidate.replace("_", "-"))
                if definition is None:
                    definition = registry.get_by_xml_tag(candidate)
                if definition is not None:
                    native_name = definition.native_name
                    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", native_name):
                        return native_name
        except Exception:
            pass
        return None

    @staticmethod
    def _safe_operation_id(value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 128:
            return None
        return (
            value
            if re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value
            )
            else None
        )

    def _operation_context_task_id(self) -> str | None:
        return self._safe_operation_task_id(remote_task_id.get())

    def _begin_active_operation(
        self,
        phase: str,
        *,
        task_id: str | None = None,
        provider: Any = None,
        request_id: Any = None,
        tool_name: str | None = None,
        tool_call_id: Any = None,
        tool_call_id_generated: bool = False,
    ) -> int:
        if phase not in _ACTIVE_OPERATION_PHASES:
            return self._active_operation_generation
        current_task_id = self._safe_operation_task_id(task_id)
        if current_task_id is None:
            current_task_id = self._operation_context_task_id()

        # A remote operation that has entered cancellation cleanup cannot
        # become active again under stale work from the same task.
        if (
            current_task_id is not None
            and self._active_operation.get("task_id") == current_task_id
            and self._active_operation.get("cleanup_state")
            in {"requested", "pending", "failed", "superseded"}
        ):
            return int(self._active_operation.get("generation", 0))

        self._active_operation_generation += 1
        generation = self._active_operation_generation
        safe_tool_name = (
            tool_name
            if isinstance(tool_name, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool_name)
            else None
        )
        self._active_operation = {
            "task_id": current_task_id,
            "generation": generation,
            "phase": phase,
            "provider": self._safe_operation_provider(provider),
            "request_id": self._safe_operation_id(request_id),
            "tool_name": safe_tool_name,
            "tool_call_id": self._safe_operation_id(tool_call_id),
            "tool_call_id_generated": bool(tool_call_id_generated),
            "cancel_generation": None,
            "cleanup_state": "none",
        }
        return generation

    def _update_active_operation(
        self,
        phase: str,
        *,
        task_id: str | None = None,
        operation_generation: int | None = None,
        provider: Any = None,
        request_id: Any = None,
        tool_name: str | None = None,
        tool_call_id: Any = None,
        tool_call_id_generated: bool | None = None,
    ) -> bool:
        if phase not in _ACTIVE_OPERATION_PHASES:
            return False
        current_task_id = self._safe_operation_task_id(task_id)
        if current_task_id is None:
            current_task_id = self._operation_context_task_id()
        current = self._active_operation
        if (
            isinstance(operation_generation, bool)
            or not isinstance(operation_generation, int)
            or current.get("generation") != operation_generation
            or current.get("task_id") != current_task_id
        ):
            return False
        if current.get("cleanup_state") != "none":
            return False

        current["phase"] = phase
        if provider is not None:
            current["provider"] = self._safe_operation_provider(provider)
        if request_id is not None:
            current["request_id"] = self._safe_operation_id(request_id)
        if tool_name is not None:
            current["tool_name"] = (
                tool_name
                if isinstance(tool_name, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool_name)
                else None
            )
        if tool_call_id is not None:
            current["tool_call_id"] = self._safe_operation_id(tool_call_id)
        if tool_call_id_generated is not None:
            current["tool_call_id_generated"] = bool(tool_call_id_generated)
        return True

    def _observe_provider_operation(
        self,
        *,
        phase: str,
        provider: Any,
        request_id: Any,
        operation_generation: int | None,
        begin: bool,
    ) -> int | None:
        if begin:
            return self._begin_active_operation(
                phase,
                provider=provider,
                request_id=request_id,
            )
        self._update_active_operation(
            phase,
            operation_generation=operation_generation,
            provider=provider,
            request_id=request_id,
        )
        return operation_generation

    def _observe_tool_operation(
        self,
        *,
        phase: str,
        tool_data: Any,
        tool_call_id: Any,
        tool_call_id_generated: bool,
        operation_generation: int | None,
        begin: bool,
    ) -> int | None:
        tool_name = self._safe_operation_tool_name(tool_data)
        if begin:
            return self._begin_active_operation(
                phase,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_call_id_generated=tool_call_id_generated,
            )
        self._update_active_operation(
            phase,
            operation_generation=operation_generation,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_call_id_generated=tool_call_id_generated,
        )
        return operation_generation

    def get_active_operation_snapshot(self) -> dict[str, Any]:
        """Return a detached, allowlisted owner-visible activity snapshot."""
        return dict(self._active_operation)

    def _set_operation_cleanup_state(
        self, task_id: str, cancel_generation: int, cleanup_state: str
    ) -> bool:
        safe_task_id = self._safe_operation_task_id(task_id)
        current = self._active_operation
        if (
            safe_task_id is None
            or current.get("task_id") != safe_task_id
            or current.get("cancel_generation") != cancel_generation
        ):
            return False
        if cleanup_state not in _ACTIVE_OPERATION_CLEANUP_STATES:
            return False
        current["phase"] = "cancellation_cleanup"
        current["cleanup_state"] = cleanup_state
        return True

    def _record_operation_cancellation(
        self, task_id: str, cancel_generation: int
    ) -> None:
        safe_task_id = self._safe_operation_task_id(task_id)
        if safe_task_id is None:
            return
        current = self._active_operation
        if current.get("task_id") != safe_task_id:
            self._begin_active_operation(
                "cancellation_cleanup", task_id=safe_task_id
            )
            current = self._active_operation
        current["phase"] = "cancellation_cleanup"
        current["cancel_generation"] = cancel_generation
        current["cleanup_state"] = "requested"

    def _mark_current_operation_superseded(self, cancel_generation: int) -> None:
        current = self._active_operation
        task_id = current.get("task_id")
        if task_id is not None:
            self._set_operation_cleanup_state(
                task_id, cancel_generation, "superseded"
            )

    def resolve_media(self, media_id: str) -> Optional[str]:
        """Resolve one live pasted image for a provider request."""
        store = getattr(self, "_image_store", None)
        return store.resolve(media_id) if store is not None else None

    def supports_image_input(self) -> bool:
        """Return whether the active provider model accepts image input."""
        return bool(
            getattr(
                getattr(self, "api_service", None),
                "supports_image_input",
                lambda: False,
            )()
        )

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
            mcp_integration=self.mcp_integration,
        )

        # Question gate: pending tools queue
        # When agent uses <question> tag, tool calls are suspended here
        # and injected when user responds
        self.pending_tools: List[Dict[str, Any]] = []
        self.question_gate_active = False
        self.question_gate_enabled = config.get(
            "kollabor.llm.question_gate_enabled", True
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
            session_stats=self.session_stats,
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
        )

        # Message handler (owns event/message handling methods)
        self._message_handler = MessageHandler(coordinator=self)

        # Progress watchdog: recovers a session that goes silent while alive
        # (wedged is_processing / orphaned queue). Started in initialize().
        # Config-gated so it can be disabled if it ever misbehaves.
        self._turn_watchdog = None
        if self.config.get("kollabor.llm.watchdog_enabled", True):
            from kollabor.llm.turn_watchdog import TurnWatchdog

            self._turn_watchdog = TurnWatchdog(
                queue_processor=self._queue_processor,
                api_service=self.api_service,
                message_handler=self._message_handler,
                restart_queue=self._watchdog_restart_queue,
                stuck_threshold_s=float(
                    self.config.get("kollabor.llm.watchdog_stuck_threshold_s", 600)
                ),
                check_interval_s=float(
                    self.config.get("kollabor.llm.watchdog_check_interval_s", 60)
                ),
            )

        # Status service (owns status line generation and queue metrics)
        self._status_service = StatusService(coordinator=self)

    def _watchdog_restart_queue(self) -> None:
        """Re-kick the queue drain on behalf of the watchdog.

        Fire-and-forget: launches _process_queue as a background task and
        returns None so the watchdog's heal step does not block on the whole
        drain completing.
        """
        self.create_background_task(
            lambda: self._process_queue(), name="watchdog_requeue"
        )

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
            "input_tokens_estimated": False,  # True when provider omitted usage
            "output_tokens": 0,  # Last request output tokens
            "total_input_tokens": 0,  # Cumulative session input
            "total_output_tokens": 0,  # Cumulative session output
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_creation_tokens": 0,
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

        # Register registry tool XML tags with the response parser
        # This bridges ToolDefinitions (web, workspace, on_demand, etc.)
        # into the XML tag parser so they're recognized in agent responses
        self._register_registry_tool_tags()

        # Discover MCP servers in background (non-blocking startup)
        # This allows the UI to start immediately while MCP servers connect
        try:
            self.create_background_task(
                lambda: self._background_mcp_discovery(), name="mcp_discovery"
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

        # Start the progress watchdog (recovers wedged/silent sessions).
        if self._turn_watchdog is not None:
            try:
                self.create_background_task(
                    lambda: self._turn_watchdog.run(), name="turn_watchdog"
                )
            except Exception as e:
                logger.warning(f"Failed to start turn watchdog: {e}")

        logger.info("Core LLM Service initialized and ready")
        return True

    def _register_registry_tool_tags(self) -> None:
        """Register all registry ToolDefinitions as XML tags with the response parser.

        The response parser uses hardcoded regex patterns for legacy tools
        (terminal, file ops, etc.) and a plugin-tag system for dynamically
        registered tags. Tools that are defined in the unified ToolRegistry
        but NOT hardcoded in the parser (web, workspace, on_demand, mcp, etc.)
        need to be registered here so their XML tags are recognized in agent
        responses.

        This bridges the gap between ToolDefinition.get_xml_regex() and
        ResponseParser.register_plugin_tag().
        """
        import re

        try:
            from kollabor_agent.tool_generators.xml_regex import build_regex_for_tool
            from kollabor_agent.tool_registry import get_registry

            registry = get_registry()

            # Tags already hardcoded in response_parser — don't double-register
            hardcoded_tags = {
                "terminal",
                "terminal-status",
                "terminal-output",
                "terminal-kill",
                "tool",
                "tool_call",
                "read",
                "edit",
                "create",
                "create-overwrite",
                "delete",
                "move",
                "copy",
                "copy-overwrite",
                "append",
                "insert-after",
                "insert-before",
                "grep",
                "mkdir",
                "rmdir",
            }

            # Also skip tags registered by plugins (hub, agent_orchestrator, etc.)
            # We check by seeing if the tag is already in _plugin_tags
            existing_plugin_tags = {
                t["tool_type"] for t in self.response_parser._plugin_tags
            }
            existing_plugin_tags.update(
                t["tool_type"].replace("_", "-")
                for t in self.response_parser._plugin_tags
            )

            count = 0
            for tool in registry.list():
                tag_name = tool.xml_tag_name

                # Skip hardcoded tags
                if tag_name in hardcoded_tags:
                    continue

                # Skip tags already registered by plugins
                tool_type_hyphen = tool.name  # e.g. "web-search"
                tool_type_underscore = tool_type_hyphen.replace(
                    "-", "_"
                )  # e.g. "web_search"

                if tool_type_underscore in existing_plugin_tags:
                    continue
                if tool_type_hyphen in existing_plugin_tags:
                    continue

                # Build regex pattern from the ToolDefinition
                pattern_str = build_regex_for_tool(tool)
                pattern = re.compile(pattern_str, re.DOTALL | re.IGNORECASE)

                # Create extract function based on xml_form
                if tool.xml_form == "nested":

                    def _make_extract(tl):
                        def _extract(m):
                            raw = m.group(1)
                            params = {}
                            for param in tl.parameters:
                                param_pattern = re.compile(
                                    rf"<{re.escape(param.name)}>(.*?)</{re.escape(param.name)}>",
                                    re.DOTALL | re.IGNORECASE,
                                )
                                pm = param_pattern.search(raw)
                                if pm:
                                    params[param.name] = pm.group(1).strip()
                            return params

                        return _extract

                    extract_fn = _make_extract(tool)
                elif tool.xml_form == "body":

                    def _make_body_extract(tl):
                        body_param = (
                            tl.xml_body_param or tl.parameters[0].name
                            if tl.parameters
                            else "command"
                        )

                        def _extract(m):
                            return {body_param: m.group(1).strip()}

                        return _extract

                    extract_fn = _make_body_extract(tool)
                else:
                    # attributes or mixed — extract all groups as positional
                    def _make_attr_extract(tl):
                        def _extract(m):
                            params = {}
                            for i, param in enumerate(tl.parameters):
                                if i + 1 <= m.lastindex:
                                    val = m.group(i + 1)
                                    if val is not None:
                                        params[param.name] = val.strip()
                            return params

                        return _extract

                    extract_fn = _make_attr_extract(tool)

                self.response_parser.register_plugin_tag(
                    tag_name,
                    pattern,
                    tool_type_underscore,
                    extract_fn,
                )
                count += 1

            if count > 0:
                logger.info(
                    f"Registered {count} registry tool XML tags with response parser (web, workspace, on_demand, etc.)"
                )
        except Exception as e:
            logger.warning(f"Failed to register registry tool tags: {e}", exc_info=True)

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
                    set_resolver = getattr(
                        self._current_provider, "set_media_resolver", None
                    )
                    if callable(set_resolver):
                        set_resolver(self.resolve_media)

                logger.info(
                    f"Provider initialized: {self._current_provider.provider_name} "
                    f"(model={self._current_provider.model})"
                )
        except Exception as e:
            logger.warning(f"Provider initialization failed, using legacy system: {e}")
            self._current_provider = None

    async def _announce_hub_model_switch(
        self, profile: Any, previous_model: Optional[str] = None
    ) -> None:
        """Best-effort lifecycle notice after a successful profile switch."""
        if not self.event_bus or profile is None:
            return
        try:
            hub = self.event_bus.get_service("hub_plugin")
            announce = getattr(hub, "announce_model_switch", None)
            if not callable(announce):
                return
            await announce(
                profile_name=profile.name,
                provider=profile.get_provider(),
                model=profile.get_model(),
                previous_model=previous_model,
            )
        except Exception:
            logger.debug("hub model-switch announcement failed", exc_info=True)

    async def switch_profile(self, profile_name: str, persist: bool = True) -> bool:
        """Switch to a different profile with thread-safe provider reinitialization.

        Wrapper pattern: Updates provider system transparently while maintaining
        backward compatibility with legacy HTTP system.

        Also syncs ``profile_manager`` active state so the runtime provider,
        the status bar, ``/model``, and ``/loadout`` all agree. With
        ``persist=True`` (default) the choice is written to config so it
        survives restart and becomes the startup default; callers that only
        want a session-scoped switch (e.g. ``/login`` when the user declines
        making it the default) pass ``persist=False``.

        Args:
            profile_name: Name of the profile to switch to
            persist: If True, persist as the active/default profile in config

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

                active_profile = self.profile_manager.get_active_profile()
                previous_model = (
                    active_profile.get_model() if active_profile is not None else None
                )

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

                # Reinitialize the provider used by the request path.  The
                # coordinator also keeps a provider reference for its wrapper
                # integrations, but StreamingHandler calls
                # ``api_service.call_llm``.  Updating only the coordinator
                # reference leaves APICommunicationService holding the old
                # provider (and, after a failed startup, its old
                # ``_provider_error``), so the next turn can still report the
                # previous profile's error.
                provider_config = create_config_from_profile(profile.to_dict())
                api_reinitialized = await self.api_service.reinitialize_provider(
                    profile
                )
                if not api_reinitialized:
                    logger.warning(
                        "API service could not reinitialize for profile '%s'",
                        profile_name,
                    )
                    return False

                self.conversation_logger.set_provider(profile.provider)

                # Keep the coordinator's provider reference in sync with the
                # API service. ProviderRegistry caches matching configurations,
                # so this normally returns the same instance.
                self._current_provider = await self._provider_registry.get_provider(
                    provider_config
                )

                # Keep profile_manager's active state (and optionally the
                # persisted config) in sync with the runtime provider. Without
                # this the provider switches but active_profile never updates --
                # e.g. after `/login openai` the status bar and next launch
                # would still show the old profile.
                try:
                    self.profile_manager.set_active_profile(
                        profile_name, persist=persist
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not sync active profile '{profile_name}': {e}"
                    )

                # Keep this explicit class call compatible with the focused
                # unbound-method test stand-in used for switch_profile.
                await LLMService._announce_hub_model_switch(
                    self, profile, previous_model
                )

                logger.info(
                    f"Switched to profile '{profile_name}' "
                    f"(provider={self._current_provider.provider_name}, "
                    f"model={self._current_provider.model}, persist={persist})"
                )
                return True

            except Exception as e:
                logger.error(f"Failed to switch profile to '{profile_name}': {e}")
                return False

    def _set_conversation_context(self):
        """Set dynamic context on conversation logger before logging start."""
        self._session.set_conversation_context()

    # --- QueueProcessor forwarding methods ---

    async def _enqueue_with_overflow_strategy(self, message: MessageContent) -> None:
        """Enqueue message with overflow strategy. Delegates to QueueProcessor."""
        await self._queue_processor.enqueue(message)

    async def _process_queue(self):
        """Process queued messages. Delegates to QueueProcessor."""
        await self._queue_processor.process_queue(
            task_manager=self._task_manager,
            process_message_batch_fn=self._process_message_batch,
            continue_conversation_fn=self._continue_conversation,
        )

    async def _process_message_batch(self, messages: List[MessageContent]):
        """Process a batch of messages. Delegates to QueueProcessor."""
        if self._pending_agent_hud:
            voice_inputs = [m for m in messages if isinstance(m, QueuedInput)]
            combined = combine_message_contents(
                [m.content if isinstance(m, QueuedInput) else m for m in messages]
            )
            combined = self.merge_pending_agent_hud(combined)
            messages = [
                QueuedInput(combined, voice_inputs[-1].voice) if voice_inputs else combined
            ]
        self.current_parent_uuid = await self._queue_processor.process_message_batch(
            messages=messages,
            current_parent_uuid=self.current_parent_uuid,
        )

    async def _continue_conversation(self):
        """Continue an ongoing conversation. Delegates to QueueProcessor.

        Note: HUD injection here is gated on turn_completed because this
        method is called from both process_queue (where turn_completed=True
        means the previous turn finished and HUD should be shown) and
        _hub_continue (where turn_completed should be False so HUD is NOT
        injected mid-continuation). See FIX in message_handler.py
        _hub_continue for the session-dying bug this coupling caused.
        """
        if self._pending_agent_hud and getattr(
            self._queue_processor, "turn_completed", False
        ):
            hud_content = self.drain_pending_agent_hud()
            if hud_content:
                from kollabor_events.data_models import ConversationMessage

                self.current_parent_uuid = self._add_conversation_message(
                    ConversationMessage(
                        role="user",
                        content=hud_content,
                        metadata={
                            "agent_hud": True,
                            "agent_hud_sources": ["pending"],
                        },
                    ),
                    parent_uuid=self.current_parent_uuid,
                )
        self.current_parent_uuid = await self._queue_processor.continue_conversation(
            current_parent_uuid=self.current_parent_uuid,
        )

    def queue_agent_hud(
        self,
        section: str,
        label: str,
        content: str,
    ) -> AgentHudEntry | None:
        """Queue one changed HUD item for the next model-visible turn."""
        body = (content or "").strip()
        if not body:
            return None
        if not hasattr(self, "_pending_agent_hud"):
            self._pending_agent_hud = []
        entry = AgentHudEntry(
            section=normalize_hud_label(section, fallback="state"),
            label=normalize_hud_label(label, fallback="info"),
            content=body,
        )
        self._pending_agent_hud.append(entry)
        return entry

    def pop_pending_agent_hud(self) -> List[AgentHudEntry]:
        """Drain queued HUD diffs waiting for the next turn."""
        if not hasattr(self, "_pending_agent_hud"):
            self._pending_agent_hud = []
        pending = list(self._pending_agent_hud)
        self._pending_agent_hud.clear()
        return pending

    def drain_pending_agent_hud(self) -> str:
        """Return only pending HUD diffs, without creating a turn by itself."""
        return format_agent_hud(self.pop_pending_agent_hud())

    def merge_pending_agent_hud(self, user_message: MessageContent) -> MessageContent:
        """Return one user payload containing queued HUD diffs + message."""
        return merge_agent_hud_with_user_message(
            self.pop_pending_agent_hud(),
            user_message,
        )

    # -- Forwarding methods to BackgroundTaskManager --

    def create_background_task(
        self, coro_or_factory, name: str | None = None
    ) -> asyncio.Task:
        """Create and track a background task. Delegates to BackgroundTaskManager."""
        return cast(
            asyncio.Task,
            self._task_manager.create_background_task(coro_or_factory, name),
        )

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

    def refresh_mcp_tools(self) -> None:
        """Refresh MCP integration reference in prompt builder.

        Called after MCP server discovery completes so the prompt builder
        can generate up-to-date MCP tool summaries on the next build.
        """
        self._prompt_builder.set_mcp_integration(self.mcp_integration)

    def _build_system_prompt(self) -> str:
        """Build system prompt from file or agent."""
        return cast(str, self._prompt_builder.build())

    def rebuild_system_prompt(self) -> bool:
        """Rebuild the system prompt and update conversation history."""
        return cast(bool, self._prompt_builder.rebuild(self.conversation_history))

    def build_volatile_context(self) -> str:
        """Render per-turn volatile context for the injection rail.

        Empty string when stable_prefix is off or nothing renders. Drained by
        the queue processor and prepended to the user turn (like the [env] block)
        so the stable system prefix stays byte-identical across sessions.
        """
        try:
            return cast(str, self._prompt_builder.build_volatile_context())
        except Exception:
            return ""

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
        # Refresh MCP tools in prompt builder for lazy injection
        self.refresh_mcp_tools()

    async def _load_native_tools(self) -> None:
        """Load MCP tools for native API function calling."""
        await self._native_tools.load_tools()

    async def process_user_input(
        self, message: MessageContent, pre_displayed: bool = False, voice: dict | None = None
    ) -> Dict[str, Any]:
        """Process user input through the LLM.

        This is the main entry point for user messages.

        Args:
            message: User's input message
            pre_displayed: Whether the user message was already echoed to the UI
                before startup gating released.

        Returns:
            Status information about processing
        """
        image_store = getattr(self, "_image_store", None)
        if image_store is None:
            image_store = EphemeralImageStore()
            self._image_store = image_store
        if contains_image_content(message) and not self.supports_image_input():
            return {
                "status": "rejected",
                "reason": f"model '{self.api_service.model}' does not accept image input",
            }
        try:
            normalized_message = normalize_message_content(message, image_store)
        except MessageContentError as exc:
            logger.warning("Rejected message content: %s", exc)
            return {"status": "rejected", "reason": str(exc)}

        display_text = content_to_text(normalized_message)
        if not display_text.strip():
            return {"status": "rejected", "reason": "empty message"}
        if (
            contains_image_content(normalized_message)
            and not self.supports_image_input()
        ):
            return {
                "status": "rejected",
                "reason": f"model '{self.api_service.model}' does not accept image input",
            }

        if voice and self._queue_processor.processing_queue.full():
            return {"status": "rejected", "reason": "Agent input queue is full"}

        # Display user message using MessageDisplayService (DRY refactoring)
        if not pre_displayed:
            logger.debug(
                f"DISPLAY DEBUG: About to display user message: '{display_text[:100]}...' ({len(display_text)} chars)"
            )
            self.message_display_service.display_user_message(display_text)

        prompt_builder = getattr(self, "_prompt_builder", None)
        if prompt_builder and prompt_builder.ensure_shell_aliases_loaded():
            self.rebuild_system_prompt()

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
            display_text, parent_uuid=self.current_parent_uuid
        )

        # Add to processing queue with overflow handling
        await self._enqueue_with_overflow_strategy(
            QueuedInput(normalized_message, voice) if voice else normalized_message
        )

        # Start processing if not already running
        if not self.is_processing:
            self.create_background_task(
                lambda: self._process_queue(), name="process_queue"
            )

        return {
            "status": "queued",
            "tools_injected": (
                len(tool_injection_results) if tool_injection_results else 0
            ),
        }

    async def submit_human_input(
        self,
        message: MessageContent,
        *,
        source: UserInputSource,
        pre_displayed: bool = False,
    ) -> Dict[str, Any]:
        """Submit trusted human input through the canonical hook pipeline.

        RPC, initial CLI and pipe producers use this method so they receive the
        same pre/main/post phases exactly once. Internal/model-origin callers
        cannot select an arbitrary source string here.
        """
        if not isinstance(source, UserInputSource):
            return {"status": "rejected", "reason": "invalid_input_source"}

        emit = getattr(self.event_bus, "emit_with_hooks", None)
        if not callable(emit):
            return {"status": "rejected", "reason": "input_pipeline_unavailable"}

        try:
            outcome = await emit(
                EventType.USER_INPUT,
                {
                    "message": message,
                    "message_pre_displayed": pre_displayed,
                },
                source.value,
            )
        except Exception as exc:
            # Do not include user content or exception text in logs/results.
            logger.error("Human input event dispatch failed (%s)", type(exc).__name__)
            return {"status": "rejected", "reason": "input_dispatch_failed"}

        if not isinstance(outcome, dict):
            return {"status": "rejected", "reason": "invalid_event_result"}

        pre_result = outcome.get("pre") or {}
        if pre_result.get("cancelled"):
            return {"status": "cancelled", "phase": "pre_user_input"}

        main_result = outcome.get("main") or {}
        if main_result.get("cancelled"):
            return {"status": "cancelled", "phase": "user_input"}

        for hook_result in main_result.get("hook_results", []):
            if hook_result.get("hook_key") != "llm_core.process_user_input":
                continue
            if not hook_result.get("success"):
                return {
                    "status": "rejected",
                    "reason": "input_processing_failed",
                }
            result = hook_result.get("result")
            if isinstance(result, dict):
                return result
            return {"status": "rejected", "reason": "invalid_input_result"}

        return {"status": "rejected", "reason": "input_handler_unavailable"}

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
        cancel_hook = next((h for h in self.hooks if h.name == "cancel_request"), None)
        if cancel_hook:
            await self.event_bus.register_hook(cancel_hook)
            logger.info("Registered cancel hook for attach-mode client")

    def cancel_current_request(
        self,
        *,
        origin: CancellationOrigin = "human",
        task_id: str | None = None,
    ) -> int | None:
        """Cancel the current turn and return its cancellation generation.

        Human callers keep the default origin. Relay cancellation is scoped to
        one remote turn and can be cleared after that turn is idle, but only if
        no later human or external cancellation superseded its generation.
        """
        if origin not in {"human", "remote_task", "external"}:
            raise ValueError("invalid cancellation origin")

        queue_processor = self._queue_processor
        if not self.is_processing:
            # A human ESC can arrive after the provider stopped but while the
            # bridge is still holding its cancellation latch. Keep that pause
            # and supersede the bridge token. An idle ESC with no pending
            # cancellation preserves the existing no-op behavior.
            if origin == "human" and queue_processor.cancel_processing:
                previous_generation = queue_processor.cancel_generation
                if queue_processor.cancel_origin == "remote_task":
                    self._mark_current_operation_superseded(previous_generation)
                return queue_processor.request_cancellation(origin="human")
            return None

        if queue_processor.cancel_processing:
            if queue_processor.cancel_origin != origin:
                # Never let a relay stop replace an existing human/external
                # cancellation. Human input, however, intentionally supersedes
                # a relay cancellation below.
                if origin != "human":
                    return None
                if queue_processor.cancel_origin == "remote_task":
                    self._mark_current_operation_superseded(
                        queue_processor.cancel_generation
                    )
            elif origin == "remote_task":
                # Repeated bridge stop requests for the same turn are
                # idempotent and retain the token that owns the latch.
                return queue_processor.cancel_generation

        generation = queue_processor.request_cancellation(origin=origin)
        cancellation_task_id = self._safe_operation_task_id(task_id)
        if cancellation_task_id is None:
            cancellation_task_id = self._operation_context_task_id()
        if cancellation_task_id is not None:
            self._record_operation_cancellation(cancellation_task_id, generation)
            self._set_operation_cleanup_state(
                cancellation_task_id, generation, "pending"
            )
        if self.is_processing:
            # Cancel API request through API service (KISS refactoring)
            self.api_service.cancel_current_request()
            # Cancel any running shell subprocess so ESC interrupts
            # long-running terminal commands, not just API streaming.
            try:
                coord = self  # alias for clarity in the closure
                if coord.tool_executor is not None:
                    cleanup_task = coord.create_background_task(
                        lambda: coord.tool_executor.cancel_running_tool(),
                        name=f"esc_cancel_tool_{generation}",
                    )
                    self._prune_cancellation_cleanup_tasks()
                    self._cancellation_cleanup_tasks[generation] = cleanup_task
                    cleanup_task.add_done_callback(
                        lambda _task: self._prune_cancellation_cleanup_tasks()
                    )
            except Exception as e:
                # A rejected cleanup task must fail closed. Do not let a relay
                # turn release its token while a subprocess or MCP call may
                # still be running.
                if origin == "remote_task":
                    self._prune_cancellation_cleanup_tasks()
                    self._cancellation_cleanup_tasks[generation] = None
                    if cancellation_task_id is not None:
                        self._set_operation_cleanup_state(
                            cancellation_task_id, generation, "failed"
                        )
                logger.debug(f"Could not cancel running tool: {e}")
            logger.info("Processing cancellation requested")
        return generation

    def clear_remote_task_cancellation(
        self, generation: int, *, task_id: str | None = None
    ) -> bool:
        """Release an idle relay cancellation only when its token still owns it."""
        if not self.remote_task_cancellation_ready(generation, task_id=task_id):
            return False
        if task_id is not None:
            if self._active_operation.get("cleanup_state") == "superseded":
                return False
        cleared = self._queue_processor.clear_remote_task_cancellation(generation)
        if task_id is not None:
            if cleared:
                self._set_operation_cleanup_state(task_id, generation, "complete")
            elif (
                self._queue_processor.cancel_generation != generation
                or self._queue_processor.cancel_origin != "remote_task"
            ):
                self._set_operation_cleanup_state(task_id, generation, "superseded")
        return cleared

    def remote_task_cancellation_ready(
        self, generation: int, *, task_id: str | None = None
    ) -> bool:
        """Whether cancellation cleanup for this token has completed safely."""
        if isinstance(generation, bool) or not isinstance(generation, int):
            return False
        if task_id is not None:
            safe_task_id = self._safe_operation_task_id(task_id)
            operation = self._active_operation
            if (
                safe_task_id is None
                or operation.get("task_id") != safe_task_id
                or operation.get("cancel_generation") != generation
                or operation.get("cleanup_state") == "failed"
            ):
                return False
        return self.cancellation_cleanup_ready()

    def cancellation_cleanup_ready(self) -> bool:
        """Whether any owned cancellation cleanup can still affect a later turn."""
        self._prune_cancellation_cleanup_tasks()
        return not self._cancellation_cleanup_tasks

    def _prune_cancellation_cleanup_tasks(self) -> None:
        """Drop terminal cleanup records once they cannot own a remote latch."""
        queue_processor = self._queue_processor
        current_remote_generation = (
            queue_processor.cancel_generation
            if queue_processor.cancel_processing
            and queue_processor.cancel_origin == "remote_task"
            else None
        )
        for generation, task in tuple(self._cancellation_cleanup_tasks.items()):
            if task is not None and not task.done():
                continue
            failed = task is None or task.cancelled()
            if task is not None and not failed:
                try:
                    failed = task.exception() is not None
                except Exception:
                    failed = True
            if failed and generation == current_remote_generation:
                # The current remote cancellation has no verified cleanup.
                # Keep its marker and block release until ownership changes.
                operation = self._active_operation
                task_id = operation.get("task_id")
                if (
                    isinstance(task_id, str)
                    and operation.get("cancel_generation") == generation
                ):
                    self._set_operation_cleanup_state(
                        task_id, generation, "failed"
                    )
                continue
            operation = self._active_operation
            operation_task_id = operation.get("task_id")
            if (
                not failed
                and not self.is_processing
                and current_remote_generation != generation
                and isinstance(operation_task_id, str)
                and operation.get("cancel_generation") == generation
                and operation.get("cleanup_state") not in {"failed", "superseded"}
            ):
                self._set_operation_cleanup_state(
                    operation_task_id, generation, "complete"
                )
            self._cancellation_cleanup_tasks.pop(generation, None)

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
                native_tools_provider=lambda: self.native_tools,
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

        # Question gate: cached at init; file save + in-memory config update
        # this key, but QueueProcessor uses cached copies until reload_config
        # runs (triggered by ConfigAltView after Ctrl+S save).
        self.question_gate_enabled = self.config.get(
            "kollabor.llm.question_gate_enabled", True
        )
        if getattr(self, "_queue_processor", None) is not None:
            self._queue_processor.question_gate_enabled = self.question_gate_enabled

        # Note: processing_delay and thinking_delay are already read dynamically each call

        logger.info(
            f"Config reloaded: max_history={self.max_history}, "
            f"terminal_timeout={self.tool_executor.terminal_timeout}, "
            f"mcp_timeout={self.tool_executor.mcp_timeout}, "
            f"streaming={self.api_service.enable_streaming}, "
            f"question_gate={self.question_gate_enabled}"
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

        image_store = getattr(self, "_image_store", None)
        if image_store is not None:
            image_store.clear()

        # Shutdown MCP integration
        try:
            await self.mcp_integration.shutdown()
            logger.info("MCP integration shutdown complete")
        except Exception as e:
            logger.warning(f"MCP shutdown error: {e}")

        logger.info("Core LLM Service shutdown complete")
