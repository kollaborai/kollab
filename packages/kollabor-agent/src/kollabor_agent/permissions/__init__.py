"""Permission system for tool execution gating."""

from kollabor_events.permissions_config import PERMISSION_CONFIG_DEFAULTS

# Re-export shared types from kollabor-events for convenience
from kollabor_events.permissions_models import (
    ApprovalMode,
    ApprovalRecord,
    ConfirmationResponse,
    ConfirmationType,
    FileOperationConfirmation,
    MCPToolConfirmation,
    PermissionAuditEntry,
    PermissionDecision,
    RiskAssessmentResult,
    RiskAssessmentRules,
    ShellCommandConfirmation,
    ToolData,
    ToolRiskLevel,
)

from .manager import PermissionManager
from .response_handler import handle_confirmation_response
from .risk_assessor import RiskAssessor

__all__ = [
    "PermissionManager",
    "RiskAssessor",
    "handle_confirmation_response",
    "PERMISSION_CONFIG_DEFAULTS",
    "ApprovalMode",
    "ApprovalRecord",
    "ConfirmationResponse",
    "ConfirmationType",
    "FileOperationConfirmation",
    "MCPToolConfirmation",
    "PermissionAuditEntry",
    "PermissionDecision",
    "RiskAssessmentResult",
    "RiskAssessmentRules",
    "ShellCommandConfirmation",
    "ToolData",
    "ToolRiskLevel",
]
