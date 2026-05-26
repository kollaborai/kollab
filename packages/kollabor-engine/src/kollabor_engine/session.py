"""EngineSession - owns all AI services for one conversation context."""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.permissions.manager import PermissionManager
from kollabor_agent.permissions.risk_assessor import RiskAssessor
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_ai import (
    APICommunicationService,
    LLMProfile,
)
from kollabor_config.config_utils import get_config_directory
from kollabor_events.bus import EventBus
from kollabor_events.permissions_models import (
    ApprovalMode,
    PermissionDecision,
    RiskAssessmentRules,
    ToolRiskLevel,
)

from . import sse

logger = logging.getLogger(__name__)

_APPROVAL_MODE_MAP = {
    "confirm_all": ApprovalMode.CONFIRM_ALL,
    "default": ApprovalMode.DEFAULT,
    "auto_approve_edits": ApprovalMode.AUTO_APPROVE_EDITS,
    "trust_all": ApprovalMode.TRUST_ALL,
}

_DATA_DIR = get_config_directory()
_PERMISSION_CONFIRMATION_HOOK_TIMEOUT_SECONDS = 300


def _permission_input_payload(tool_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the user-visible permission input payload."""
    explicit_input = tool_data.get("input")
    if isinstance(explicit_input, dict) and explicit_input:
        return explicit_input

    arguments = tool_data.get("arguments")
    if isinstance(arguments, dict) and arguments:
        return arguments

    if tool_data.get("type") == "terminal":
        payload: Dict[str, Any] = {}
        for key in (
            "command",
            "cwd",
            "background",
            "timeout",
            "session_name",
            "lines",
        ):
            value = tool_data.get(key)
            if value not in (None, ""):
                payload[key] = value
        return payload

    return {}


class EngineSession:
    """
    One conversation session. Owns all AI services.
    No terminal renderer dependency - emits SSE events instead.
    """

    def __init__(
        self,
        session_id: str,
        profile: LLMProfile,
        approval_mode: str = "confirm_all",
        workspace: Optional[str] = None,
        system_prompt: Optional[str] = None,
        mcp_server_names: Optional[List[str]] = None,
        user_token: Optional[str] = None,
    ):
        self.session_id = session_id
        self.user_token = user_token
        self.workspace_path = self._resolve_workspace(workspace)
        self.workspace = str(self.workspace_path) if workspace else None
        self.system_prompt = system_prompt or ""
        self.created_at = datetime.utcnow()
        self.profile = profile

        # MCP servers to auto-connect on initialization
        self.mcp_server_names = mcp_server_names or []

        # Conversation history (list of dicts: {role, content})
        self.history: List[Dict[str, Any]] = []

        # System prompt injected into history as first message if set
        if self.system_prompt:
            self.history.append({"role": "system", "content": self.system_prompt})

        # Engine config dict (lightweight - no ConfigService needed)
        raw_dir = _DATA_DIR / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        self._config = {
            "kollabor.llm.enable_streaming": True,
            "kollabor.llm.use_explicit_tool_accumulation": True,
            "kollabor.permissions.approval_mode": approval_mode,
            "terminal.interactive_shell": False,
        }

        # Event bus (per-session, no shared state)
        self.event_bus = EventBus(config=self._config)

        # API communication service
        self.api_service = APICommunicationService(
            config=self._SimpleConfig(self._config),
            raw_conversations_dir=str(raw_dir),
            profile=profile,
        )

        # MCP integration
        self.mcp_integration = MCPIntegration(
            event_bus=self.event_bus,
            workspace=self.workspace_path,
            user_token=self.user_token,
            session_id=session_id,
        )

        # Tool executor (no renderer - headless)
        self.tool_executor = ToolExecutor(
            mcp_integration=self.mcp_integration,
            event_bus=self.event_bus,
            config=self._SimpleConfig(self._config),
            renderer=None,
            workspace=self.workspace_path,
        )

        # Risk assessor + permission manager
        risk_rules = RiskAssessmentRules()
        risk_assessor = RiskAssessor(rules=risk_rules, config=self._config)
        self.permission_manager = PermissionManager(
            config=self._config,
            risk_assessor=risk_assessor,
            event_bus=self.event_bus,
        )
        self.permission_manager.set_confirmation_callback(self._permission_callback)

        # Permission hook registered in initialize() (requires await)

        # Approval mode
        mode = _APPROVAL_MODE_MAP.get(approval_mode, ApprovalMode.CONFIRM_ALL)
        self.permission_manager.set_approval_mode(mode, persist=False)

        # SSE queue for active turn (set by TurnRunner)
        self._sse_queue: Optional[asyncio.Queue] = None

        # Pending permission events: tool_id -> asyncio.Event
        self._pending_permissions: Dict[str, asyncio.Event] = {}
        # Permission results: tool_id -> PermissionDecision
        self._permission_results: Dict[str, PermissionDecision] = {}
        # Scope of approved permissions: tool_id -> str
        self._permission_scopes: Dict[str, str] = {}
        # Risk levels: tool_id -> ToolRiskLevel
        self._permission_risk_levels: Dict[str, ToolRiskLevel] = {}

        # Turn stats
        self.total_turns = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        # Active turn task (for cancellation)
        self._active_turn_task: Optional[asyncio.Task] = None

        logger.info(
            f"Session {session_id} created "
            f"(profile={profile.name}, approval={approval_mode})"
        )

    def _resolve_workspace(self, workspace: Optional[str]) -> Path:
        """Resolve and validate the workspace for this session."""
        if not workspace:
            return Path.cwd().resolve()

        path = Path(workspace).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Workspace does not exist: {workspace}")
        if not path.is_dir():
            raise ValueError(f"Workspace is not a directory: {workspace}")
        return path

    async def _register_permission_hook(self) -> None:
        """Register the permission manager as a TOOL_CALL_PRE hook."""
        from kollabor_events.models import EventType, Hook, HookPriority

        pm = self.permission_manager

        async def _handle_tool_pre(data: Dict[str, Any], event: Any) -> Dict[str, Any]:
            tool_data = data.get("tool_data", {})
            if not tool_data:
                return data
            decision = await pm.check_permission(tool_data)
            data["permission_decision"] = {
                "allowed": decision.allowed,
                "reason": decision.reason,
            }
            if not decision.allowed:
                event.cancelled = True
                event.cancel_reason = decision.reason
            return data

        hook = Hook(
            plugin_name="engine_permission_system",
            name="permission_check",
            event_type=EventType.TOOL_CALL_PRE,
            callback=_handle_tool_pre,
            priority=HookPriority.SECURITY.value,
            enabled=True,
            timeout=_PERMISSION_CONFIRMATION_HOOK_TIMEOUT_SECONDS,
            retry_attempts=0,
            error_action="stop",
        )
        await self.event_bus.register_hook(hook)
        logger.debug(f"Session {self.session_id}: permission hook registered")

    async def initialize(self) -> bool:
        """Initialize API service, permission hook, and MCP connections."""
        await self._register_permission_hook()
        ok = await self.api_service.initialize()
        if not ok:
            logger.warning(f"Session {self.session_id}: API service init failed")

        # Auto-connect MCP servers if specified
        if self.mcp_server_names:
            await self._connect_mcp_servers()

        return ok

    async def _connect_mcp_servers(self) -> None:
        """Auto-connect MCP servers specified in mcp_server_names."""
        for server_name in self.mcp_server_names:
            if server_name not in self.mcp_integration.mcp_servers:
                logger.warning(
                    f"Session {self.session_id}: MCP server '{server_name}' "
                    f"not found in configuration"
                )
                continue

            server_config = self.mcp_integration.mcp_servers[server_name]
            if not server_config.get("enabled", True):
                logger.info(
                    f"Session {self.session_id}: skipping disabled MCP server '{server_name}'"
                )
                continue

            logger.info(
                f"Session {self.session_id}: connecting to MCP server '{server_name}'"
            )
            try:
                command = server_config.get("command")
                if command:
                    await self.mcp_integration._connect_and_list_tools(
                        server_name, command
                    )
                    tool_count = sum(
                        1
                        for t in self.mcp_integration.tool_registry.values()
                        if t.get("server") == server_name
                    )
                    logger.info(
                        f"Session {self.session_id}: MCP server '{server_name}' "
                        f"connected with {tool_count} tools"
                    )
            except Exception as e:
                logger.error(
                    f"Session {self.session_id}: failed to connect MCP server '{server_name}': {e}"
                )

    async def shutdown(self):
        """Clean up session resources."""
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()

        # Clear pending permissions to prevent memory leaks
        self._pending_permissions.clear()
        self._permission_results.clear()
        self._permission_scopes.clear()
        self._permission_risk_levels.clear()

        await self.api_service.shutdown()
        await self.mcp_integration.shutdown()
        logger.info(f"Session {self.session_id} shut down")

    def cancel_turn(self):
        """Cancel the active turn."""
        self.api_service.cancel_current_request()
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()

    async def get_tools(self) -> Optional[List[Dict]]:
        """Get combined tool list (native + MCP) formatted for API."""
        return self.mcp_integration.get_tool_definitions_for_api()

    def resolve_permission(
        self,
        tool_id: str,
        decision: str,
        scope: str = "once",
    ) -> bool:
        """
        Resolve a pending permission request from the HTTP endpoint.
        Returns True if there was a pending request to resolve.
        """
        event = self._pending_permissions.get(tool_id)
        if not event:
            return False

        risk_level = self._permission_risk_levels.get(tool_id, ToolRiskLevel.MEDIUM)
        result = (
            PermissionDecision(
                allowed=True, reason="User approved", risk_level=risk_level
            )
            if decision == "approve"
            else PermissionDecision(
                allowed=False, reason="User denied", risk_level=risk_level
            )
        )
        self._permission_results[tool_id] = result
        self._permission_scopes[tool_id] = scope
        event.set()
        return True

    async def _permission_callback(
        self, tool_data: Dict[str, Any]
    ) -> PermissionDecision:
        """
        Called by PermissionManager when user confirmation is needed.
        Emits permission_request to SSE stream and waits for HTTP response.
        """
        tool_id = tool_data.get("id", str(uuid.uuid4()))
        tool_name = tool_data.get("name", tool_data.get("type", "unknown"))
        tool_type = tool_data.get("type", "unknown")
        risk_level = tool_data.get("risk_level", "medium")
        risk_reason = tool_data.get("risk_reason", "")
        # Store risk level for resolve_permission to use
        risk_enum = (
            ToolRiskLevel[risk_level.upper()]
            if isinstance(risk_level, str)
            else risk_level
        )
        self._permission_risk_levels[tool_id] = risk_enum

        # Emit to SSE stream if active
        if self._sse_queue:
            await self._sse_queue.put(
                sse.permission_request(
                    session_id=self.session_id,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    tool_type=tool_type,
                    input=_permission_input_payload(tool_data),
                    risk_level=str(risk_level).lower(),
                    risk_reason=risk_reason,
                )
            )

        # Create event and wait for HTTP response (5 min timeout)
        event = asyncio.Event()
        self._pending_permissions[tool_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=300)
            result = self._permission_results.get(tool_id)
            scope = self._permission_scopes.get(tool_id, "once")
            if (
                result
                and result.allowed
                and scope in ("session", "trust_tool")
                and risk_enum not in (ToolRiskLevel.HIGH, ToolRiskLevel.UNKNOWN)
            ):
                self.permission_manager._record_approval(
                    tool_type, tool_name, tool_data, "session"
                )

            # Emit result back to SSE stream
            if self._sse_queue:
                if result and result.allowed:
                    await self._sse_queue.put(
                        sse.permission_granted(
                            session_id=self.session_id,
                            tool_id=tool_id,
                            scope=scope,
                        )
                    )
                else:
                    await self._sse_queue.put(
                        sse.permission_denied(
                            session_id=self.session_id,
                            tool_id=tool_id,
                        )
                    )

            return result or PermissionDecision(
                allowed=False,
                reason="No response",
                risk_level=risk_enum,
            )
        except asyncio.TimeoutError:
            if self._sse_queue:
                await self._sse_queue.put(
                    sse.permission_denied(
                        session_id=self.session_id,
                        tool_id=tool_id,
                    )
                )
            return PermissionDecision(
                allowed=False,
                reason="Permission request timed out",
                risk_level=risk_enum,
            )
        finally:
            self._pending_permissions.pop(tool_id, None)
            self._permission_results.pop(tool_id, None)
            self._permission_scopes.pop(tool_id, None)
            self._permission_risk_levels.pop(tool_id, None)

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "profile": self.profile.name,
            "workspace": self.workspace,
            "approval_mode": self.permission_manager.approval_mode.value,
            "created_at": self.created_at.isoformat(),
            "total_turns": self.total_turns,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "history_length": len(self.history),
            "active": self._active_turn_task is not None
            and not self._active_turn_task.done(),
            "mcp_servers": self.mcp_server_names,
            "mcp_connected": list(self.mcp_integration.server_connections.keys()),
        }

    class _SimpleConfig:
        """Adapter so APICommunicationService can call config.get()."""

        def __init__(self, data: Dict):
            self._data = data

        def get(self, key: str, default=None):
            return self._data.get(key, default)
