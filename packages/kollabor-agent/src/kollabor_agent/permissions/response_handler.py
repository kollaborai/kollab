"""Handle user responses to permission confirmation dialogs."""

import logging
from typing import Any, Dict

from kollabor_events.permissions_models import (
    ApprovalMode,
    ConfirmationResponse,
    PermissionDecision,
    ToolRiskLevel,
)

from .manager import PermissionManager

logger = logging.getLogger(__name__)


async def handle_confirmation_response(
    permission_manager: PermissionManager,
    tool_data: Dict[str, Any],
    response: ConfirmationResponse,
) -> PermissionDecision:
    """
    Handle user response to confirmation dialog.

    Args:
        permission_manager: Permission manager instance
        tool_data: Tool being confirmed
        response: User's response

    Returns:
        PermissionDecision reflecting the response
    """
    # confirmation_details uses "tool_type"/"tool_name" keys, not "type"/"name"
    tool_type = tool_data.get("tool_type") or tool_data.get("type", "unknown")
    tool_name = tool_data.get("tool_name") or tool_data.get("name", "")

    # Get risk level from tool_data or default to MEDIUM
    risk_level_str = tool_data.get("risk_level", "MEDIUM")
    try:
        risk_level = ToolRiskLevel[risk_level_str]
    except (KeyError, ValueError):
        risk_level = ToolRiskLevel.MEDIUM

    if response == ConfirmationResponse.DENY:
        return PermissionDecision(
            allowed=False,
            reason="User denied execution",
            risk_level=risk_level,
        )

    if response == ConfirmationResponse.CANCEL:
        return PermissionDecision(
            allowed=False,
            reason="User cancelled operation",
            risk_level=risk_level,
        )

    if response == ConfirmationResponse.APPROVE_ONCE:
        return PermissionDecision(
            allowed=True,
            reason="User approved (once)",
            risk_level=risk_level,
        )

    if response == ConfirmationResponse.APPROVE_SESSION:
        permission_manager._record_approval(tool_type, tool_name, tool_data, "session")
        return PermissionDecision(
            allowed=True,
            reason="User approved (session)",
            risk_level=risk_level,
        )

    if response == ConfirmationResponse.APPROVE_PROJECT:
        permission_manager._record_project_approval(tool_type, tool_name, tool_data)
        return PermissionDecision(
            allowed=True,
            reason="User approved (project)",
            risk_level=risk_level,
        )

    if response == ConfirmationResponse.APPROVE_ALWAYS:
        # Switch to AUTO_APPROVE_EDITS mode for file operations
        if tool_type in ("file_write", "file_edit"):
            permission_manager.set_approval_mode(ApprovalMode.AUTO_APPROVE_EDITS)
        return PermissionDecision(
            allowed=True,
            reason="User approved (always)",
            risk_level=risk_level,
        )

    if response == ConfirmationResponse.APPROVE_TOOL_ALWAYS:
        # For MCP: whitelist this specific tool
        permission_manager._record_approval(tool_type, tool_name, tool_data, "session")
        return PermissionDecision(
            allowed=True,
            reason=f"User approved tool '{tool_name}' always",
            risk_level=risk_level,
        )

    # Default: deny
    return PermissionDecision(
        allowed=False,
        reason="Unknown response",
        risk_level=risk_level,
    )
