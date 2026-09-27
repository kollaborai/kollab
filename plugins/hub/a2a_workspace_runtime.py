"""Small, provider-free A2A skills through Kollab's normal local tool pipeline.

The remote peer supplies a skill payload, never a tool name, shell command or cwd.
Both the receiver's capability list and the normal permission hook must approve.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kollabor.llm.permissions.hook import PermissionHook
from kollabor_agent.permissions import PermissionManager, RiskAssessor
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_events.bus import EventBus
from kollabor_events.models import EventType, Hook
from kollabor_events.permissions_models import (
    PermissionDecision,
    RiskAssessmentRules,
    ToolRiskLevel,
)

SKILL_TO_TOOL = {
    "workspace.read": "file_read",
    "workspace.create": "file_create",
}
MAX_TEXT_BYTES = 32_768


class WorkspaceRequestError(ValueError):
    """The requested skill or path is outside the adapter contract."""


class _WorkspacePermissionManager(PermissionManager):
    """Load existing approvals for the bound workspace, never the launcher cwd."""

    def __init__(self, workspace: Path, *args: Any, **kwargs: Any):
        self.workspace = workspace
        super().__init__(*args, **kwargs)

    def _get_project_data_dir(self) -> Path:
        from kollabor_config.config_utils import get_project_data_dir

        return get_project_data_dir(self.workspace)


@dataclass(frozen=True)
class WorkspaceSkillRequest:
    skill: str
    path: str
    content: str | None = None

    @classmethod
    def parse(cls, payload: Any) -> WorkspaceSkillRequest:
        if not isinstance(payload, dict):
            raise WorkspaceRequestError("One structured skill payload is required")
        skill = payload.get("skill")
        expected = (
            {"skill", "path", "content"}
            if skill == "workspace.create"
            else {"skill", "path"}
        )
        if skill not in SKILL_TO_TOOL or set(payload) != expected:
            raise WorkspaceRequestError("Unsupported skill or unexpected fields")
        path = payload.get("path")
        if not isinstance(path, str) or not path or len(path) > 200:
            raise WorkspaceRequestError("A bounded relative file path is required")
        content = payload.get("content")
        if skill == "workspace.create" and (
            not isinstance(content, str)
            or len(content.encode("utf-8")) > MAX_TEXT_BYTES
        ):
            raise WorkspaceRequestError(
                "Text content must be at most 32768 UTF-8 bytes"
            )
        return cls(skill, path, content)


class WorkspaceToolRuntime:
    """A single workspace with explicit operator-enabled skills and real tools."""

    def __init__(self, workspace: Path, allowed_skills: tuple[str, ...]):
        if not workspace.is_absolute() or not workspace.is_dir():
            raise ValueError("workspace must be an existing absolute directory")
        if not allowed_skills or set(allowed_skills) - SKILL_TO_TOOL.keys():
            raise ValueError(
                "Explicit workspace.read and/or workspace.create skills required"
            )
        self.workspace = workspace.resolve()
        self.allowed_skills = frozenset(allowed_skills)
        self._lock = asyncio.Lock()
        self._started = False
        self._authorization_check: Callable[[], None] | None = None
        config = {
            "kollabor": {
                "permissions": {"enabled": True, "approval_mode": "confirm_all"}
            },
        }
        self.event_bus = EventBus(config)
        self.tool_executor = ToolExecutor(
            mcp_integration=None,
            event_bus=self.event_bus,
            workspace=self.workspace,
            config={"file_operations.max_read_size_mb": MAX_TEXT_BYTES / 1024 / 1024},
        )
        self.tool_executor.set_bundle_scope(
            [
                "file-read" if skill == "workspace.read" else "file-create"
                for skill in self.allowed_skills
            ]
        )
        self.permission_manager = _WorkspacePermissionManager(
            self.workspace,
            config,
            RiskAssessor(RiskAssessmentRules(), config),
            self.event_bus,
        )
        self.permission_manager.set_tool_executor(self.tool_executor)
        self.permission_manager.set_confirmation_callback(self._operator_policy)

    async def start(self) -> None:
        if not self._started:
            await PermissionHook(self.permission_manager).register(self.event_bus)
            await self.event_bus.register_hook(
                Hook(
                    plugin_name="a2a_workspace",
                    name="revalidate_remote_grant",
                    event_type=EventType.TOOL_CALL_PRE,
                    callback=self._revalidate_before_tool,
                    priority=1,
                    enabled=True,
                    error_action="stop",
                    retry_attempts=0,
                )
            )
            self._started = True

    async def _revalidate_before_tool(
        self, data: dict[str, Any], event: Any
    ) -> dict[str, Any]:
        # This runtime owns only the permission hook and this final guard. The
        # guard runs after any awaited permission decision and before dispatch.
        try:
            if self._authorization_check is not None:
                self._authorization_check()
        except ValueError:
            event.cancelled = True
            event.cancel_reason = "Remote grant no longer authorizes execution"
            data["permission_decision"] = {
                "allowed": False,
                "reason": event.cancel_reason,
                "risk_level": "HIGH",
            }
        return data

    async def _operator_policy(self, details: dict[str, Any]) -> PermissionDecision:
        # The enclosing skill/path gate is authoritative. No setting here enables
        # shell/MCP tools, grants trust_all, or changes the host's local policy.
        tool_type = details.get("tool_type")
        allowed = tool_type in {SKILL_TO_TOOL[s] for s in self.allowed_skills}
        return PermissionDecision(
            allowed=allowed,
            reason=(
                "Explicit A2A workspace skill policy"
                if allowed
                else "Skill not enabled locally"
            ),
            risk_level=ToolRiskLevel.MEDIUM,
        )

    def validate(self, request: WorkspaceSkillRequest) -> Path:
        if request.skill not in self.allowed_skills:
            raise WorkspaceRequestError("Skill not enabled by the workspace operator")
        path = Path(request.path)
        if path.is_absolute() or "\\" in request.path or "\x00" in request.path:
            raise WorkspaceRequestError("Only relative workspace paths are allowed")
        if any(part.startswith(".") or part == ".." for part in path.parts):
            raise WorkspaceRequestError("Hidden paths and traversal are not allowed")
        if path.suffix.lower() not in {".txt", ".md", ".json"}:
            raise WorkspaceRequestError("Only .txt, .md and .json files are supported")
        destination = self.workspace / path
        current = self.workspace
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise WorkspaceRequestError("Symlinks are not allowed")
        if not destination.resolve().is_relative_to(self.workspace):
            raise WorkspaceRequestError("Path escapes the bound workspace")
        if request.skill == "workspace.read" and destination.exists():
            if not destination.is_file() or destination.stat().st_size > MAX_TEXT_BYTES:
                raise WorkspaceRequestError(
                    "Read target must be a file of at most 32768 bytes"
                )
        return destination

    async def execute(
        self,
        request: WorkspaceSkillRequest,
        task_id: str,
        *,
        authorization_check: Callable[[], None] | None = None,
    ) -> ToolExecutionResult:
        if not self._started:
            raise RuntimeError("Workspace runtime must start its permission hook first")
        async with self._lock:
            destination = self.validate(request)
            tool_data = {
                "id": task_id,
                "type": SKILL_TO_TOOL[request.skill],
                "file": str(destination),
            }
            if request.content is not None:
                tool_data["content"] = request.content

            def revalidate() -> None:
                if authorization_check is not None:
                    authorization_check()
                self.validate(request)

            self._authorization_check = revalidate
            try:
                return await self.tool_executor.execute_tool(tool_data)
            finally:
                self._authorization_check = None
