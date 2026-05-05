"""Permission system - app-layer wiring.

Business logic lives in kollabor-agent and kollabor-events packages.
This module provides the PermissionHook (event bus integration) and
re-exports for backward compatibility.
"""

# Re-export from packages for convenience
from kollabor_agent.permissions import (
    PERMISSION_CONFIG_DEFAULTS,
    PermissionManager,
    RiskAssessor,
    handle_confirmation_response,
)
from kollabor_events.permissions_models import (
    ApprovalMode,
    ApprovalRecord,
    ConfirmationResponse,
    ConfirmationType,
    PermissionAuditEntry,
    PermissionDecision,
    RiskAssessmentResult,
    RiskAssessmentRules,
    ToolData,
    ToolRiskLevel,
)

from .hook import PermissionHook

__all__ = [
    "PermissionHook",
    "PermissionManager",
    "RiskAssessor",
    "handle_confirmation_response",
    "PERMISSION_CONFIG_DEFAULTS",
    "ApprovalMode",
    "ToolRiskLevel",
    "ConfirmationType",
    "ConfirmationResponse",
    "PermissionDecision",
    "ApprovalRecord",
    "RiskAssessmentResult",
    "PermissionAuditEntry",
    "RiskAssessmentRules",
    "ToolData",
]
