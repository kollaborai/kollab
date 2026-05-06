"""Permission manager for tool execution."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from kollabor_agent.file_operations_executor import PathAccessMode
from kollabor_events.dict_utils import safe_get
from kollabor_events.models import EventType
from kollabor_events.permissions_models import (
    ApprovalMode,
    ApprovalRecord,
    PermissionDecision,
    ToolRiskLevel,
)

from .risk_assessor import RiskAssessor

logger = logging.getLogger(__name__)

# Project approvals file name
PROJECT_APPROVALS_FILE = "permission_approvals.json"


class PermissionManager:
    """
    Central manager for tool execution permissions.

    Integrates with event bus to intercept tool execution requests
    and enforce permission policies.
    """

    def __init__(
        self,
        config: dict,
        risk_assessor: RiskAssessor,
        event_bus: Any,
        config_service=None,
    ):
        """Initialize permission manager.

        Args:
            config: Application configuration
            risk_assessor: Risk assessor instance
            event_bus: Event bus instance
            config_service: Optional ConfigService for persisting changes
        """
        self._config = config
        self._risk_assessor = risk_assessor
        self._event_bus = event_bus
        self._config_service = config_service  # For persisting mode changes

        # Session-scoped approvals (reset each session)
        self._session_approvals: Dict[str, ApprovalRecord] = {}

        # Project-scoped approvals (persistent, loaded from project data dir)
        self._project_approvals: Set[str] = set()
        self._load_project_approvals()

        # Approval mode (can change during session)
        self._approval_mode = self._load_approval_mode()
        self._sync_file_access_mode()

        # Pending confirmations (tool_id -> asyncio.Event)
        self._pending_confirmations: Dict[str, asyncio.Event] = {}
        self._confirmation_results: Dict[str, PermissionDecision] = {}

        # Callback for showing confirmation UI
        self._confirmation_callback: Optional[
            Callable[[Dict[str, Any]], Awaitable[PermissionDecision]]
        ] = None

        # Statistics
        self._stats = {
            "total_checks": 0,
            "auto_approved": 0,
            "user_approved": 0,
            "denied": 0,
            "blocked": 0,
        }

        logger.info(
            f"Permission manager initialized (project approvals: {len(self._project_approvals)})"
        )

    def _load_approval_mode(self) -> ApprovalMode:
        """Load approval mode from config."""
        # Use config_service (dot-notation aware) if available, fall back to raw dict
        if self._config_service:
            mode_str = self._config_service.get(
                "kollabor.permissions.approval_mode", "default"
            )
        else:
            mode_str = safe_get(
                self._config, "kollabor.permissions.approval_mode", "default"
            )
        mode_map = {
            "default": ApprovalMode.DEFAULT,
            "confirm_all": ApprovalMode.CONFIRM_ALL,
            "auto_approve_edits": ApprovalMode.AUTO_APPROVE_EDITS,
            "trust_all": ApprovalMode.TRUST_ALL,
        }
        return mode_map.get(mode_str, ApprovalMode.DEFAULT)

    @property
    def approval_mode(self) -> ApprovalMode:
        """Get current approval mode."""
        return self._approval_mode

    def set_tool_executor(self, tool_executor) -> None:
        """Set the tool executor for file access mode sync."""
        self._tool_executor = tool_executor

    def _sync_file_access_mode(self, tool_executor=None) -> None:
        """Sync file path access mode with current approval mode."""
        try:
            if tool_executor is None:
                tool_executor = getattr(self, "_tool_executor", None)
            file_ops = getattr(tool_executor, "file_ops_executor", None)
            if not file_ops:
                return

            if self._approval_mode == ApprovalMode.TRUST_ALL:
                file_ops.set_path_access_mode(PathAccessMode.ANYWHERE)
            else:
                file_ops.set_path_access_mode(PathAccessMode.PROJECT_ONLY)
        except Exception as e:
            logger.debug(f"Failed to sync file access mode: {e}")

    def set_approval_mode(self, mode: ApprovalMode, persist: bool = True) -> None:
        """
        Set approval mode for the session.

        Args:
            mode: New approval mode
            persist: If True, persist mode to config file
        """
        old_mode = self._approval_mode
        logger.info(f"Approval mode changed: {old_mode} -> {mode}")
        self._approval_mode = mode
        self._sync_file_access_mode()

        # Env-notification: capability change (fire-and-forget)
        try:
            from kollabor_ai.notifications.producer import push_env

            old_name = getattr(old_mode, "value", str(old_mode))
            new_name = getattr(mode, "value", str(mode))
            push_env(
                self._event_bus,
                "capability",
                f"approval:{new_name} (was {old_name})",
                kind="permission",
            )
        except Exception:
            pass

        # Persist to config if config_service is available and persist=True
        if persist and self._config_service:
            mode_map = {
                ApprovalMode.DEFAULT: "default",
                ApprovalMode.CONFIRM_ALL: "confirm_all",
                ApprovalMode.AUTO_APPROVE_EDITS: "auto_approve_edits",
                ApprovalMode.TRUST_ALL: "trust_all",
            }
            mode_str = mode_map.get(mode, "default")
            self._config_service.save_key(
                "kollabor.permissions.approval_mode", mode_str
            )
            logger.info(f"Approval mode persisted to config: {mode_str}")

    def set_confirmation_callback(
        self,
        callback: Callable[[Dict[str, Any]], Awaitable[PermissionDecision]],
    ) -> None:
        """
        Set callback for showing confirmation UI.

        Args:
            callback: Async function that shows confirmation and returns decision
        """
        self._confirmation_callback = callback
        logger.debug("Confirmation callback set")

    async def check_permission(
        self,
        tool_data: Dict[str, Any],
    ) -> PermissionDecision:
        """
        Check if a tool execution is permitted.

        Args:
            tool_data: Tool information from response parser

        Returns:
            PermissionDecision with allowed status and reason
        """
        self._stats["total_checks"] += 1

        tool_id = tool_data.get("id", "unknown")
        tool_type = tool_data.get("type", "unknown")
        tool_name = tool_data.get("name") or tool_type

        logger.debug(
            f"Permission check started for tool: {tool_name} (type={tool_type})"
        )

        # Step 0: Check if permission system is enabled
        permission_enabled = safe_get(
            self._config, "kollabor.permissions.enabled", True
        )
        if not permission_enabled:
            self._stats["auto_approved"] += 1
            # Emit PERMISSION_CHECK with risk info
            await self._event_bus.emit_with_hooks(
                EventType.PERMISSION_CHECK,
                {
                    "tool_id": tool_id,
                    "tool_type": tool_type,
                    "tool_name": tool_name,
                    "risk_level": "low",
                    "tool_data": tool_data,
                },
                source="permission_manager",
            )
            decision = PermissionDecision(
                allowed=True,
                reason="Permission system disabled",
                risk_level=ToolRiskLevel.LOW,
            )
            await self._emit_permission_granted(
                tool_id, tool_type, tool_name, ToolRiskLevel.LOW, decision.reason
            )
            return decision

        # Step 1: Risk assessment
        risk_result = self._risk_assessor.assess_tool(tool_data)

        # Emit PERMISSION_CHECK with risk level
        await self._event_bus.emit_with_hooks(
            EventType.PERMISSION_CHECK,
            {
                "tool_id": tool_id,
                "tool_type": tool_type,
                "tool_name": tool_name,
                "risk_level": (
                    risk_result.level.name.lower()
                    if hasattr(risk_result.level, "name")
                    else str(risk_result.level)
                ),
                "tool_data": tool_data,
            },
            source="permission_manager",
        )

        # Step 2: Check if blocked
        if risk_result.is_blocked:
            self._stats["blocked"] += 1
            decision = PermissionDecision(
                allowed=False,
                reason=risk_result.reason,
                risk_level=risk_result.level,
            )
            await self._emit_permission_denied(
                tool_id, tool_type, tool_name, risk_result.level, decision.reason
            )
            return decision

        # Step 3: Check approval mode
        if self._approval_mode == ApprovalMode.TRUST_ALL:
            self._stats["auto_approved"] += 1
            decision = PermissionDecision(
                allowed=True,
                reason="Trust all mode enabled",
                risk_level=risk_result.level,
            )
            await self._emit_permission_granted(
                tool_id, tool_type, tool_name, risk_result.level, decision.reason
            )
            return decision

        # Step 4: Check project approvals (persistent, with smart matching)
        approval_key = self._get_approval_key(tool_type, tool_name, tool_data)
        project_match = self._check_project_approval(approval_key, tool_type, tool_data)
        if project_match:
            self._stats["auto_approved"] += 1
            decision = PermissionDecision(
                allowed=True,
                reason=f"Previously approved (project: {project_match})",
                risk_level=risk_result.level,
            )
            await self._emit_permission_granted(
                tool_id, tool_type, tool_name, risk_result.level, decision.reason
            )
            return decision

        # Step 5: Check session approvals
        if approval_key in self._session_approvals:
            record = self._session_approvals[approval_key]
            if record.expires_at is None or record.expires_at > datetime.now():
                self._stats["auto_approved"] += 1
                decision = PermissionDecision(
                    allowed=True,
                    reason=f"Previously approved ({record.scope})",
                    risk_level=risk_result.level,
                )
                await self._emit_permission_granted(
                    tool_id, tool_type, tool_name, risk_result.level, decision.reason
                )
                return decision

        # Step 6: Determine if confirmation needed
        needs_confirmation = self._needs_confirmation(risk_result.level, tool_type)

        if not needs_confirmation:
            self._stats["auto_approved"] += 1
            decision = PermissionDecision(
                allowed=True,
                reason="Auto-approved based on risk level and mode",
                risk_level=risk_result.level,
            )
            await self._emit_permission_granted(
                tool_id, tool_type, tool_name, risk_result.level, decision.reason
            )
            return decision

        # Step 6: Request user confirmation
        if self._confirmation_callback:
            confirmation_details = self._build_confirmation_details(
                tool_data, risk_result
            )

            # Emit PERMISSION_CONFIRMATION event before requesting user input
            logger.debug(f"Requesting user confirmation for tool: {tool_name}")
            await self._event_bus.emit_with_hooks(
                EventType.PERMISSION_CONFIRMATION,
                {
                    "tool_id": tool_id,
                    "tool_type": tool_type,
                    "tool_name": tool_name,
                    "risk_level": risk_result.level.name,
                    "confirmation_details": confirmation_details,
                },
                source="permission_manager",
            )

            decision = await self._confirmation_callback(confirmation_details)

            if decision.allowed:
                self._stats["user_approved"] += 1
                # Record approval if "always" scope
                if confirmation_details.get("approval_scope") == "session":
                    self._record_approval(tool_type, tool_name, tool_data, "session")
                await self._emit_permission_granted(
                    tool_id, tool_type, tool_name, risk_result.level, decision.reason
                )
            else:
                self._stats["denied"] += 1
                await self._emit_permission_denied(
                    tool_id, tool_type, tool_name, risk_result.level, decision.reason
                )

            return decision

        # No callback set - deny by default
        self._stats["denied"] += 1
        decision = PermissionDecision(
            allowed=False,
            reason="Confirmation required but no UI available",
            risk_level=risk_result.level,
            requires_user_confirmation=True,
        )
        await self._emit_permission_denied(
            tool_id, tool_type, tool_name, risk_result.level, decision.reason
        )
        return decision

    def _needs_confirmation(
        self,
        risk_level: ToolRiskLevel,
        tool_type: str,
    ) -> bool:
        """Determine if confirmation is needed based on risk and mode."""
        if self._approval_mode == ApprovalMode.TRUST_ALL:
            return False

        if self._approval_mode == ApprovalMode.CONFIRM_ALL:
            return True

        if self._approval_mode == ApprovalMode.AUTO_APPROVE_EDITS:
            # Auto-approve file operations, confirm shell
            if tool_type in ("file_write", "file_edit"):
                return False
            if tool_type == "terminal":
                return True

        # DEFAULT mode: confirm HIGH risk only
        return risk_level in (ToolRiskLevel.HIGH, ToolRiskLevel.UNKNOWN)

    def _get_approval_key(
        self,
        tool_type: str,
        tool_name: str,
        tool_data: Dict[str, Any],
    ) -> str:
        """Generate key for approval lookup."""
        if tool_type == "terminal":
            # For shell commands, use root command as key
            command = tool_data.get("command", "")
            root_cmd = command.split()[0] if command else ""
            return f"terminal:{root_cmd}"

        # For file operations, use file path as key
        if tool_type in ("file_read", "file_write", "file_edit"):
            file_path = (
                tool_data.get("file_path")  # from confirmation_details
                or tool_data.get("file")  # from original tool_data
                or tool_data.get("arguments", {}).get("path", "")
            )
            if file_path:
                return f"{tool_type}:{file_path}"

        return f"{tool_type}:{tool_name}"

    def _check_project_approval(
        self,
        approval_key: str,
        tool_type: str,
        tool_data: Dict[str, Any],
    ) -> Optional[str]:
        """Check if tool is approved at project level with smart matching.

        Returns the matching approval pattern if approved, None otherwise.

        Smart matching rules:
        - Exact match always works
        - file_read:* matches all file reads EXCEPT gitignored files
        - terminal:<cmd> matches exact command, but NOT chained commands
        - fnmatch patterns supported (*, ?)
        """
        import fnmatch

        # Exact match first
        if approval_key in self._project_approvals:
            return approval_key

        # No wildcards in approvals? Done.
        wildcard_approvals = [
            p for p in self._project_approvals if "*" in p or "?" in p
        ]
        if not wildcard_approvals:
            return None

        # Terminal commands: block chained commands from wildcard matching
        if tool_type == "terminal":
            command = tool_data.get("command", "")
            if self._is_chained_command(command):
                logger.debug(
                    f"Chained command blocked from wildcard matching: {command}"
                )
                return None  # Force explicit approval for chained commands

        # File reads: check gitignore for wildcard matches
        if tool_type == "file_read":
            file_path = (
                tool_data.get("file_path")
                or tool_data.get("file")
                or tool_data.get("arguments", {}).get("file_path", "")
            )
            if file_path and self._is_gitignored(file_path):
                logger.debug(
                    f"Gitignored file blocked from wildcard matching: {file_path}"
                )
                return None  # Force explicit approval for gitignored files

        # Check wildcard patterns
        for pattern in wildcard_approvals:
            if fnmatch.fnmatch(approval_key, pattern):
                return pattern

        return None

    def _is_chained_command(self, command: str) -> bool:
        """Check if command contains chaining operators.

        Chained commands (cmd1 && cmd2, cmd1 || cmd2, cmd1 ; cmd2)
        are blocked from wildcard matching for security.

        Pipes (|) are allowed as they're typically safe data transforms.
        """
        import re

        # Look for && || ; outside of quotes
        # Simple check: if these operators exist and aren't inside quotes
        # This is a conservative check - may have false positives
        danger_patterns = [
            r"&&",  # AND chaining
            r"\|\|",  # OR chaining
            r";\s*\w",  # Semicolon followed by command
        ]

        for pattern in danger_patterns:
            if re.search(pattern, command):
                return True

        return False

    def _is_gitignored(self, file_path: str) -> bool:
        """Check if a file is gitignored.

        Returns True if file matches .gitignore patterns.
        """
        import subprocess
        from pathlib import Path

        try:
            # Use git check-ignore to see if file is ignored
            result = subprocess.run(
                ["git", "check-ignore", "-q", file_path],
                capture_output=True,
                timeout=2,
                cwd=Path.cwd(),
            )
            # Exit code 0 = ignored, 1 = not ignored
            return result.returncode == 0
        except Exception:
            # If git not available or error, assume not ignored
            return False

    def _record_approval(
        self,
        tool_type: str,
        tool_name: str,
        tool_data: Dict[str, Any],
        scope: str,
    ) -> None:
        """Record an approval for future lookups."""
        key = self._get_approval_key(tool_type, tool_name, tool_data)
        self._session_approvals[key] = ApprovalRecord(
            tool_type=tool_type,
            tool_name=tool_name,
            pattern=None,
            approved_at=datetime.now(),
            expires_at=(
                None if scope == "session" else datetime.now() + timedelta(hours=1)
            ),
            scope=scope,
        )
        logger.debug(f"Recorded approval: {key} (scope={scope})")

    def _get_project_data_dir(self) -> Path:
        """Get project-specific data directory."""
        from kollabor_config.config_utils import get_project_data_dir

        return get_project_data_dir()

    def _load_project_approvals(self) -> None:
        """Load project approvals from persistent storage."""
        try:
            project_dir = self._get_project_data_dir()
            approvals_file = project_dir / PROJECT_APPROVALS_FILE

            if approvals_file.exists():
                data = json.loads(approvals_file.read_text(encoding="utf-8"))
                self._project_approvals = set(data.get("approved_keys", []))
                logger.info(
                    f"Loaded {len(self._project_approvals)} project approvals from {approvals_file}"
                )
            else:
                self._project_approvals = set()
        except Exception as e:
            logger.warning(f"Failed to load project approvals: {e}")
            self._project_approvals = set()

    def _save_project_approvals(self) -> None:
        """Save project approvals to persistent storage."""
        try:
            project_dir = self._get_project_data_dir()
            project_dir.mkdir(parents=True, exist_ok=True)
            approvals_file = project_dir / PROJECT_APPROVALS_FILE

            data = {
                "approved_keys": list(self._project_approvals),
                "updated_at": datetime.now().isoformat(),
            }
            approvals_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info(
                f"Saved {len(self._project_approvals)} project approvals to {approvals_file}"
            )
        except Exception as e:
            logger.error(f"Failed to save project approvals: {e}")

    def _record_project_approval(
        self,
        tool_type: str,
        tool_name: str,
        tool_data: Dict[str, Any],
    ) -> None:
        """Record a project-level approval (persistent).

        Uses smart patterns:
        - file_read: approves ALL file reads (file_read:*)
          Protected by gitignore check at match time
        - terminal: approves specific root command (terminal:ls)
          Chained commands blocked at match time
        - others: exact key
        """
        if tool_type == "file_read":
            # Approve all file reads for this project
            # Gitignored files still protected at match time
            key = "file_read:*"
        else:
            # Use specific key for terminal and other types
            key = self._get_approval_key(tool_type, tool_name, tool_data)

        self._project_approvals.add(key)
        self._save_project_approvals()
        logger.info(f"Recorded project approval: {key}")

    def clear_project_approvals(self) -> None:
        """Clear all project approvals."""
        self._project_approvals.clear()
        self._save_project_approvals()
        logger.info("Cleared all project approvals")

    def get_project_approvals(self) -> List[str]:
        """Get list of project approvals."""
        return sorted(self._project_approvals)

    def _build_confirmation_details(
        self,
        tool_data: Dict[str, Any],
        risk_result: Any,  # RiskAssessmentResult
    ) -> Dict[str, Any]:
        """Build details for confirmation UI."""
        tool_type = tool_data.get("type", "unknown")

        details = {
            "tool_id": tool_data.get("id"),
            "tool_type": tool_type,
            "tool_name": tool_data.get("name", ""),
            "risk_level": risk_result.level.name,
            "risk_reason": risk_result.reason,
            "matched_pattern": risk_result.matched_pattern,
        }

        if tool_type == "terminal":
            details["command"] = tool_data.get("command", "")
            details["root_command"] = (
                details["command"].split()[0] if details["command"] else ""
            )
        elif tool_type in ("file_write", "file_edit"):
            # file_edit operations use "file" key, MCP tools use arguments.path
            details["file_path"] = tool_data.get("file", "") or tool_data.get(
                "arguments", {}
            ).get("path", "")
            details["content_preview"] = self._get_content_preview(tool_data)
        elif tool_type == "file_read":
            # File read uses "file" key (from response_parser)
            details["file_path"] = tool_data.get("file", "") or tool_data.get(
                "arguments", {}
            ).get("file_path", "")
        elif tool_type in ("mcp_tool", "mcp"):
            details["server_name"] = tool_data.get("server_name", "")
            details["mcp_tool_name"] = tool_data.get("name", "")

        return details

    def _get_content_preview(
        self,
        tool_data: Dict[str, Any],
        max_lines: int = 10,
    ) -> str:
        """Get preview of file content for confirmation."""
        content = tool_data.get("arguments", {}).get("content", "")
        lines = content.split("\n")
        if len(lines) > max_lines:
            preview_lines = lines[:max_lines]
            preview_lines.append(f"... ({len(lines) - max_lines} more lines)")
            return "\n".join(preview_lines)
        return content  # type: ignore[no-any-return]

    def get_stats(self) -> Dict[str, int]:
        """Get permission check statistics."""
        return self._stats.copy()

    def clear_session_approvals(self) -> None:
        """Clear all session-scoped approvals."""
        self._session_approvals.clear()
        logger.info("Session approvals cleared")

    async def _emit_permission_granted(
        self,
        tool_id: str,
        tool_type: str,
        tool_name: str,
        risk_level: ToolRiskLevel,
        reason: str,
    ) -> None:
        """Emit PERMISSION_GRANTED event."""
        logger.debug(f"Permission granted for tool: {tool_name} - {reason}")
        await self._event_bus.emit_with_hooks(
            EventType.PERMISSION_GRANTED,
            {
                "tool_id": tool_id,
                "tool_type": tool_type,
                "tool_name": tool_name,
                "risk_level": risk_level.name,
                "reason": reason,
            },
            source="permission_manager",
        )

    async def _emit_permission_denied(
        self,
        tool_id: str,
        tool_type: str,
        tool_name: str,
        risk_level: ToolRiskLevel,
        reason: str,
    ) -> None:
        """Emit PERMISSION_DENIED event."""
        logger.debug(f"Permission denied for tool: {tool_name} - {reason}")
        await self._event_bus.emit_with_hooks(
            EventType.PERMISSION_DENIED,
            {
                "tool_id": tool_id,
                "tool_type": tool_type,
                "tool_name": tool_name,
                "risk_level": risk_level.name,
                "reason": reason,
            },
            source="permission_manager",
        )
